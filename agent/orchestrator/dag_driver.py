"""The AND-OR DAG orchestrator (LEAP direct->decompose + AlphaProof_Nexus Ralph loop + goal cache).

For each goal:
  1. **Direct attempt** via the Ralph loop (focused prover + deterministic gate + lessons-learned).
  2. On failure, ask a **Decomposer** for a blueprint: a *sketch* ledger proving the goal while citing
     child sub-lemmas (steps with justification `lemma`), plus the list of child goals.
  3. A **Reviewer** (LEAP's decomposition reviewer + elementary judge) gates the decomposition before
     it is committed: does it actually simplify, and is it elementary?
  4. Acyclicity + sketch validation, then **recurse** (DFS with backtracking) on the children, reusing
     any already-proven sub-lemma via the deep-hash goal cache (memoization).

All model calls draw from one Budget and recursion is depth-bounded, so the search always terminates.
The prover/decomposer/reviewer are Protocols, so the Codex (GPT-5.5-xHigh) implementations and the test
stubs are interchangeable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol, runtime_checkable

from agent.gates.gate import Verdict, evaluate
from agent.gates.ledger import parse_ledger, LedgerError
from agent.gates.toolkit import Toolkit, load_toolkit
from agent.orchestrator.dag import ProofDAG, goal_hash, CycleError
from agent.orchestrator.driver import Prover, Judge
from agent.orchestrator.population import Comparator, EloPopulation, Candidate
from agent.orchestrator.ralph import RalphLoop
from agent.orchestrator.state import Budget, NodeState
from agent.orchestrator.tournament import RevisionController
from agent.orchestrator.trace import RunTrace


@dataclass
class ReviewVerdict:
    useful: bool          # does the decomposition actually simplify the goal? (LEAP reviewer)
    elementary: bool      # is every proposed step within the elementary toolkit?
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.useful and self.elementary


@runtime_checkable
class Decomposer(Protocol):
    def decompose(self, goal: str, feedback: Optional[list[str]] = None) -> tuple[str, list[str]]:
        """Return (sketch_ledger_text, child_goals). The sketch cites children via `lemma` steps."""
        ...


@runtime_checkable
class Reviewer(Protocol):
    def review(self, goal: str, sketch: str, child_goals: list[str]) -> ReviewVerdict:
        ...


@dataclass
class DagResult:
    goal: str
    proven: bool
    dag: ProofDAG
    trace: RunTrace
    budget: Budget
    terminal: Optional[object] = None   # the terminal-gate result (e.g. FormalizeAuditResult)

    def proof_tree(self) -> dict:
        return self.dag.assemble(self.goal)

    @property
    def authoritative_elementary(self) -> bool:
        """Proven AND the terminal Layer-4 gate (formalize -> audit -> faithfulness) accepted it."""
        return bool(self.proven and self.terminal is not None
                    and getattr(self.terminal, "authoritative", False))


def _lemma_claims(sketch: str) -> Optional[set[str]]:
    """Normalized claims of the sketch's `lemma` steps, or None if the sketch won't parse."""
    try:
        led = parse_ledger(sketch)
    except LedgerError:
        return None
    return {goal_hash(s.claim) for s in led.steps if s.justification == "lemma"}


def _proves_goal(ledger: str, goal: str) -> bool:
    """Does `ledger` actually conclude `goal`? Soundness binding (goal<->claim).

    BOTH the ledger's stated `claim` AND its terminal `conclusion` step must deep-hash-equal the
    requested goal. Checking the top-level claim alone is insufficient: a ledger may set `claim`
    to the goal while its conclusion step proves a DIFFERENT statement (e.g. claim "G" but a
    conclusion claiming "H"), and the deterministic gate admits such a ledger because it cannot
    distinguish a genuinely-wrong conclusion from a placeholder restatement without knowing the
    requested goal. The terminal conclusion is the operative statement actually proved, so it is
    the authoritative binding to the goal. Returns False if it won't parse, has no single
    conclusion, or either the claim or the conclusion proves something else."""
    try:
        led = parse_ledger(ledger)
    except LedgerError:
        return False
    if goal_hash(led.claim) != goal_hash(goal):
        return False
    conclusions = [s for s in led.steps if s.justification == "conclusion"]
    if len(conclusions) != 1:
        return False
    return goal_hash(conclusions[0].claim) == goal_hash(goal)


def _conclusion_claim(sketch: str) -> Optional[str]:
    """The claim of the sketch's terminal `conclusion` step, or None if it won't parse / has none."""
    try:
        led = parse_ledger(sketch)
    except LedgerError:
        return None
    for s in led.steps:
        if s.justification == "conclusion":
            return s.claim
    return None


class DagDriver:
    def __init__(
        self,
        prover: Prover,
        decomposer: Optional[Decomposer] = None,
        reviewer: Optional[Reviewer] = None,
        judges: Optional[list[Judge]] = None,
        toolkit: Optional[Toolkit] = None,
        budget: Optional[Budget] = None,
        trace: Optional[RunTrace] = None,
        max_depth: int = 4,
        max_decomp_attempts: int = 2,
        ralph_episodes: int = 3,
        comparator: Optional[Comparator] = None,
        population_k: int = 0,
        population_rounds: int = 1,
        refiner: Optional[RevisionController] = None,
        terminal_gate: Optional[Callable[[str, str], object]] = None,
    ):
        self.prover = prover
        self.decomposer = decomposer
        self.reviewer = reviewer
        self.judges = judges or []
        self.toolkit = toolkit or load_toolkit()
        self.budget = budget or Budget()
        self.trace = trace or RunTrace("dag-run")
        self.max_depth = max_depth
        self.max_decomp_attempts = max_decomp_attempts
        self.ralph_episodes = ralph_episodes
        # Population/Elo search (AlphaProof_Nexus): when population_k>0 and a comparator is given,
        # generate K candidate decompositions, rank them by a pairwise-comparison Elo tournament, and
        # try them best-first instead of in arbitrary order.
        self.comparator = comparator
        self.population_k = population_k
        self.population_rounds = population_rounds
        # Autoreason incumbent-tournament revision controller (agent/orchestrator/tournament.py).
        # When set, a directly-proven node's ledger is refined without ever regressing it: a challenger
        # must beat the incumbent on a blind judge panel AND stay elementary to displace it.
        self.refiner = refiner
        # Terminal authoritative gate (PLAN.md §5 Layer 4): (root_goal, proof_text) -> result with an
        # `.authoritative` attribute. Runs once after the root is proven (formalize -> Lean audit ->
        # faithfulness). See agent/orchestrator/formalize_bridge.make_terminal_gate.
        self.terminal_gate = terminal_gate
        self.dag = ProofDAG()

    def run(self, goal: str) -> DagResult:
        proven = self._prove(goal, ancestors=set(), depth=0)

        # Terminal authoritative gate: formalize the assembled proof -> Lean Layer-4 audit -> faithfulness.
        terminal = None
        if proven and self.terminal_gate is not None:
            proof_text = self.dag.proof_bundle(goal)
            self.trace.emit("terminal_gate_start", goal=goal[:80])
            terminal = self.terminal_gate(goal, proof_text)
            self.trace.emit("terminal_gate", goal=goal[:80],
                            authoritative=bool(getattr(terminal, "authoritative", False)))

        stats = self.dag.stats()
        self.trace.emit("final", goal=goal[:80], proven=proven,
                        nodes=stats["nodes"], proven_nodes=stats["proven"],
                        cache_hits=stats["cache_hits"], **self.budget.snapshot())
        return DagResult(goal=goal, proven=proven, dag=self.dag, trace=self.trace,
                         budget=self.budget, terminal=terminal)

    def _prove(self, goal: str, ancestors: set[str], depth: int) -> bool:
        node = self.dag.get_or_create(goal)
        key = node.key

        # Memoization: a proven goal is reused; a *genuinely* failed goal is not re-attempted. Only
        # the terminal failure states short-circuit here. EXHAUSTED is deliberately NOT memoized as a
        # cached failure: a node that only failed because it hit the depth limit (or ran out of budget)
        # on one branch may still be provable on a shallower branch, so it must stay re-attemptable.
        if node.proven:
            self.trace.emit("cache_hit", goal=goal[:80])
            return True
        if node.state in (NodeState.FAILED_GAP, NodeState.FAILED_ELEMENTARY):
            return False
        if key in ancestors:               # cycle (defensive; commit guards this too)
            return False
        if depth > self.max_depth:
            # A depth-limit failure is limit-induced, not a real gap: record it as the non-terminal
            # EXHAUSTED state (and leave the node re-attemptable) so the memo check above does NOT
            # permanently block this same goal on a shallower branch (depth-limit cache poisoning).
            if not node.proven:
                node.state = NodeState.EXHAUSTED
            self.trace.emit("depth_limit", goal=goal[:80], depth=depth)
            return False
        if not self.budget.can_call():
            self.trace.emit("budget", reason="llm_calls", **self.budget.snapshot())
            return False

        node.state = NodeState.IN_PROGRESS

        # 1. Direct attempt (Ralph loop with the focused prover).
        ralph = RalphLoop(self.prover, toolkit=self.toolkit, budget=self.budget,
                          trace=self.trace, max_episodes=self.ralph_episodes, judges=self.judges)
        res = ralph.run(goal)
        node.attempts += res.episodes
        if res.success:
            # Soundness: a directly-produced ledger must actually conclude THIS node's goal. A ledger
            # that proves a different statement (claim deep-hash != goal) is not a proof of this node.
            if not _proves_goal(res.ledger, goal):
                self.trace.emit("goal_claim_mismatch", goal=goal[:80], proof_kind="direct")
                self.dag.mark_failed(goal)
                return False
            ledger = self._refine(goal, res.ledger)   # Autoreason tournament (no-regression refine)
            self.dag.mark_proven_direct(goal, ledger)
            self.trace.emit("prove_node", goal=goal[:80], proof_kind="direct")
            return True
        if res.exhausted:
            self.dag.mark_failed(goal)
            return False

        # 2. Decomposition (LEAP), with reviewer gating + backtracking. The FIRST plan is free; each
        #    further re-plan consumes the global replan budget, so re-decomposition is bounded both
        #    per-node (max_decomp_attempts) and globally (Budget.max_replan_depth).
        if self.decomposer is None:
            self.dag.mark_failed(goal)
            return False

        feedback = list(res.lessons)
        child_ancestors = ancestors | {key}

        for attempt in range(self.max_decomp_attempts):
            if attempt > 0:                                   # attempts after the first are re-plans
                if not self.budget.can_replan():
                    self.trace.emit("replan_exhausted", goal=goal[:80],
                                    replans=self.budget.replans_spent)
                    break
                self.budget.spend_replan()
                self.trace.emit("replan", goal=goal[:80], replan=self.budget.replans_spent)
            if not self.budget.can_call():
                break
            if self.population_k and self.comparator is not None:
                if self._prove_via_population(goal, feedback, child_ancestors, depth):
                    return True
            else:
                self.budget.spend_call()
                sketch, children = self.decomposer.decompose(goal, feedback or None)
                self.trace.emit("decompose", goal=goal[:80], children=len(children))
                ok, notes = self._try_decomposition(goal, sketch, children, child_ancestors, depth)
                if ok:
                    return True
                feedback = notes + feedback

        self.dag.mark_failed(goal)
        return False

    def _refine(self, goal: str, ledger: str) -> str:
        """Optionally refine a directly-proven ledger via the incumbent tournament. Monotone: the
        returned ledger is never worse than the input (a challenger must beat it on a blind panel AND
        stay elementary to displace it)."""
        if self.refiner is None or not self.budget.can_call():
            return ledger
        try:
            result = self.refiner.refine(goal, ledger, is_admissible=self._is_admissible)
        except Exception:
            return ledger
        if result.changed:
            self.trace.emit("refine", goal=goal[:80], passes=result.passes,
                            displacements=result.displacements)
        return result.content

    def _is_admissible(self, ledger: str) -> bool:
        """A candidate proof is admissible iff it passes the deterministic + soft gate (not rejected).
        Used by the refiner so a non-elementary 'improvement' can never displace an elementary proof."""
        try:
            return not evaluate(ledger, self.toolkit).rejected
        except Exception:
            return False

    def _try_decomposition(self, goal: str, sketch: str, children: list[str],
                           child_ancestors: set[str], depth: int) -> tuple[bool, list[str]]:
        """Validate + commit + recurse one candidate decomposition. Returns (success, feedback)."""
        if not children:
            return False, ["decomposition proposed no sub-lemmas"]

        # The sketch's `lemma` steps must exactly match the declared children (honest decomposition).
        claims = _lemma_claims(sketch)
        if claims is None:
            return False, ["decomposition sketch did not parse as a ledger"]
        if claims != {goal_hash(c) for c in children}:
            return False, ["sketch `lemma` steps do not match the declared child goals"]

        # LEAP reviewer + elementary judge (before committing).
        if self.reviewer is not None:
            if not self.budget.can_call():
                return False, ["budget exhausted before review"]
            self.budget.spend_call()
            review = self.reviewer.review(goal, sketch, children)
            self.trace.emit("review", goal=goal[:80], useful=review.useful,
                            elementary=review.elementary)
            if not review.ok:
                return False, review.notes

        # Acyclicity guard.
        if self.dag.would_create_cycle(goal, children, child_ancestors):
            return False, ["proposed decomposition is cyclic"]

        # The sketch itself must be a valid ledger (children admitted via `lemma` steps).
        sketch_report = evaluate(sketch, self.toolkit)
        if sketch_report.rejected:
            return False, [str(f) for f in sketch_report.rejects()]
        # Fail closed: a NEEDS_REVIEW sketch (e.g. an elastic justification routed to Layer 2) with no
        # reviewer to resolve it must NOT be admitted as a valid decomposition — mirror the direct
        # node's review-unhandled guard.
        if sketch_report.verdict is Verdict.NEEDS_REVIEW and self.reviewer is None:
            return False, ["decomposition sketch needs Layer-2 review but no reviewer is configured"]

        # Soundness: the sketch must conclude THIS goal. If its conclusion proves a different
        # statement than the parent goal, the decomposition is not a valid plan for this node.
        concl = _conclusion_claim(sketch)
        if concl is None or goal_hash(concl) != goal_hash(goal):
            return False, ["sketch conclusion does not prove the parent goal"]

        # Snapshot the node so the commit can be rolled back if a child fails to prove. Committing
        # before recursion lets the acyclicity guard see the in-flight edges, but a child failure must
        # NOT leave stale 'decomposition' metadata behind (assemble()/proof_bundle would render it).
        node = self.dag.get_or_create(goal)
        prev_proof, prev_kind = node.proof, node.proof_kind
        prev_children, prev_state = list(node.children), node.state

        try:
            self.dag.commit_decomposition(goal, sketch, children, child_ancestors)
        except CycleError:
            return False, ["commit detected a cycle"]

        # Recurse on children (DFS).
        for child_goal in children:
            if not self._prove(child_goal, child_ancestors, depth + 1):
                self.trace.emit("backtrack", goal=goal[:80])
                # Roll back the (uncompleted) commit so no stale decomposition lingers on the node.
                node.proof, node.proof_kind = prev_proof, prev_kind
                node.children, node.state = prev_children, prev_state
                return False, []
        self.dag.mark_proven_via_children(goal)
        self.trace.emit("prove_node", goal=goal[:80], proof_kind="decomposition")
        return True, []

    def _compare_budget_ok(self) -> bool:
        """One pairwise comparison costs one model call (checked + spent atomically)."""
        if self.budget.can_call():
            self.budget.spend_call()
            return True
        return False

    def _prove_via_population(self, goal: str, feedback: list[str],
                             child_ancestors: set[str], depth: int) -> bool:
        """Generate K candidate decompositions, rank them by an Elo comparison tournament, and try
        them best-first (AlphaProof_Nexus population/Elo over incomplete sketches)."""
        cands: list[Candidate] = []
        for i in range(self.population_k):
            if not self.budget.can_call():
                break
            self.budget.spend_call()
            sketch, children = self.decomposer.decompose(goal, feedback or None)
            self.trace.emit("decompose", goal=goal[:80], children=len(children))
            if children:
                cands.append(Candidate(id=f"cand{i}", content=sketch, goal=goal, children=children))
        if not cands:
            return False

        pop = EloPopulation()
        for c in cands:
            pop.add(c)
        comparisons = pop.tournament(self.comparator, rounds=self.population_rounds,
                                     budget_ok=self._compare_budget_ok)
        # Bradley-Terry latent strengths from the tournament win matrix (a stable batch estimate over
        # noisy online Elo), then PUCT-best-first expansion (exploit strength + explore under-visited).
        pop.set_ratings_from_bradley_terry()
        self.trace.emit("population", goal=goal[:80], candidates=len(cands), comparisons=comparisons)

        tried = 0
        while tried < min(self.max_decomp_attempts, len(cands)):
            cand = pop.select_puct()
            if cand is None:
                break
            tried += 1
            ok, _notes = self._try_decomposition(goal, cand.content, cand.children,
                                                 child_ancestors, depth)
            if ok:
                return True
            cand.alive = False          # don't reselect a failed candidate
        return False


# --------------------------------------------------------------------------------------------------
# Scripted stubs (offline tests / demos).
# --------------------------------------------------------------------------------------------------

class ScriptedDecomposer:
    """Returns a fixed sequence of (sketch, child_goals). Repeats the last when exhausted."""

    def __init__(self, plans: list[tuple[str, list[str]]]):
        if not plans:
            raise ValueError("ScriptedDecomposer needs at least one plan")
        self._plans = plans
        self.calls = 0

    def decompose(self, goal: str, feedback=None) -> tuple[str, list[str]]:
        idx = min(self.calls, len(self._plans) - 1)
        self.calls += 1
        return self._plans[idx]


class ScriptedReviewer:
    def __init__(self, verdicts: list[ReviewVerdict]):
        if not verdicts:
            raise ValueError("ScriptedReviewer needs at least one verdict")
        self._verdicts = verdicts
        self.calls = 0

    def review(self, goal: str, sketch: str, child_goals: list[str]) -> ReviewVerdict:
        idx = min(self.calls, len(self._verdicts) - 1)
        self.calls += 1
        return self._verdicts[idx]
