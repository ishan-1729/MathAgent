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
from agent.orchestrator.dag import ProofDAG, goal_hash, proof_context_hash, CycleError
from agent.orchestrator.dilworth import dilworth_width, upward_rank, CyclicPosetError
from agent.orchestrator.driver import Prover, Judge
from agent.orchestrator.elementary_verifier import (
    refute_elementary, VacuousVerificationError,
)
from agent.orchestrator.h0_consistency import check_children_consistency
from agent.orchestrator.node_fsm import Action, NodeEvent, ReasonCode, node_transition
from agent.orchestrator.population import Comparator, EloPopulation, Candidate
from agent.orchestrator.ralph import RalphLoop, RalphResult
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
        fanout_cap: int = 16,
        h0_consistency: bool = True,
        evolve_fallback: Optional[Decomposer] = None,
        node_verifier: Optional[Callable[[str, str], object]] = None,
        sketch_verifier: Optional[Callable[[str, str, list[str]], object]] = None,
        lean_strict: bool = False,
        lean_server: Optional[object] = None,
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
        # Autoreason incumbent-tournament revision controller (agent/orchestrator/tournament.py). When
        # set, a directly-proven node's ledger is refined: a challenger must beat the incumbent on the
        # blind judge panel AND clear the AUTHORITATIVE elementary admissibility gate (deterministic
        # refutation + the Lean audit when a node_verifier is configured) to displace it. No-regression
        # is RELATIVE to that panel + gate, not absolute — a single soft judge could still prefer a
        # worse-but-admissible rewrite; see tournament.RevisionController and self._admissibility_gate.
        self.refiner = refiner
        # Terminal authoritative gate (PLAN.md §5 Layer 4): (root_goal, proof_text) -> result with an
        # `.authoritative` attribute. Runs once after the root is proven (formalize -> Lean audit ->
        # faithfulness). See agent/orchestrator/formalize_bridge.make_terminal_gate.
        self.terminal_gate = terminal_gate
        # Split-keyed memo (T100 P1 item 4): the DAG is keyed on (semantic_goal_hash,
        # proof_context_hash). A PROVEN minted under this toolkit/gate/denylist context is shared
        # across branches; a context change invalidates a stale PROVEN. The context hash is computed
        # ONCE from the loaded toolkit and stamped onto every certificate the DAG mints.
        self.context_hash = proof_context_hash(self.toolkit)
        self.dag = ProofDAG(context=self.context_hash)
        # P2 (forge_relevance_study §7): the SAFE parallel fan-out CAP for proving committed AND
        # children. The number of children proved in one wave is bounded by the Dilworth antichain
        # WIDTH of the open, all-deps-met children (a deep chain -> width 1, never over-spawn) AND a
        # budget-derived limit AND this operator ceiling. Scheduling-only: width-bounded rank-ordered
        # waves yield the SAME proven/failed result as the old serial loop (order-independent).
        self.fanout_cap = max(1, int(fanout_cap))
        # H0 child-consistency gate (forge_relevance_study §4.3.4): before an AND-node is promoted by
        # composing its children, the children's assumption/binding/lemma signatures must agree on
        # overlaps (a 0-cocycle check). A sibling proven under a hypothesis that CONTRADICTS another's
        # is unsound to compose even though each child passed locally.
        self.h0_consistency = h0_consistency
        # OpenEvolve FALLBACK decomposer (P3 / brief §9 #1, openevolve_stacking_brief §9 P2): fires ONLY
        # on STUCK nodes (after the normal direct + decomposer attempts have all failed). A flag-gated,
        # OPTIONAL Decomposer (typically OpenEvolveBackend). Its evolved blueprint is committed ONLY if
        # it is goal-bound and its obligations are discharged — reusing the SAME Phase-1 binding +
        # acyclicity/strict-simpler-child + H0 checks as a normal decomposition (no weaker gate). None =>
        # disabled (graceful no-op); also a no-op if the backend reports itself unavailable.
        self.evolve_fallback = evolve_fallback
        # OPT-IN per-node Lean authority (P0/P2): a callable (node_goal, node_proof_text) -> verdict
        # (a FormalizeAuditResult, e.g. from formalize_bridge.make_node_gate). When set, a LEAF about to
        # be marked PROVEN-direct is additionally routed through Lean: a compiled+audited leaf is stamped
        # lean_verified; a compiled-but-REJECTED leaf REFUTES elementarity (downgrade to
        # FAILED_ELEMENTARY); a non-compiling formalization FAILS OPEN (soft PROVEN, re-attemptable); an
        # UNAVAILABLE toolchain is a retryable EXHAUSTED. When None (the default) `_prove` runs the
        # BYTE-IDENTICAL code path it ran before this feature existed — nothing in the default path calls
        # the verifier or touches lean_verified.
        self.node_verifier = node_verifier
        # OPT-IN per-AND-node Lean COMPOSITION authority (P4, LEAP §2.2/§2.5): a callable
        # (parent_goal, sketch_text, child_goals) -> verdict (a FormalizeAuditResult, e.g. from
        # formalize_bridge.make_sketch_gate). When set, a candidate decomposition — AFTER it passes the
        # reviewer + acyclicity + sketch-validity + conclusion-binding checks and BEFORE commit — has its
        # SKETCH formalized as a SORRY-FREE composition theorem (the parent from children-as-hypotheses)
        # and compiled+audited via Lean. The decomposition is COMMITTED only if the sketch compiled +
        # audited elementary (res.elementary_verified True); a non-compiling/rejected sketch REJECTS the
        # candidate (backtrack / next attempt) and is never committed. A committed sketch stamps
        # sketch_lean_verified=True on the node, which mark_proven_via_children reads for the LEAP
        # parent-composition rule. When None (the default) _try_decomposition runs the BYTE-IDENTICAL
        # pre-P4 path — nothing calls the verifier or touches sketch_lean_verified.
        self.sketch_verifier = sketch_verifier
        # OPT-IN LEAN-STRICT mode (closes the gap-#1 fail-OPEN on inability to formalize). When False
        # (the DEFAULT) a per-node leaf/sketch whose formalization could-not-COMPILE is accepted as soft
        # PROVEN (the informal gate already passed it) — the BYTE-IDENTICAL fail-OPEN behavior of the
        # pre-flag code. When True, that same non-compiling formalization is NOT accepted as proven: it
        # fails CLOSED, routing the leaf to a non-proven outcome (FAILED_GAP, reason formalization_failed)
        # and the AND-node sketch to a REJECT of the candidate, exactly like a compiled-but-rejected
        # outcome — EXCEPT it is a "could not formalize" gap, not an elementarity refutation. lean_strict
        # ONLY ever changes the (iii) could-not-compile arm; it leaves PASS / REJECT / UNAVAILABLE and the
        # whole verifier-None default path untouched. prove.py passes lean_strict=True under --lean-strict.
        self.lean_strict = bool(lean_strict)
        # SHARED WARM Lean server (P3): ONE persistent LeanServer (Mathlib + #audit loaded once) held by
        # the driver so EVERY per-node compile (the leaf node_verifier AND the AND-node sketch_verifier)
        # reuses the SAME warm base environment instead of reloading Mathlib (~40-60s) per node. The gates
        # are constructed externally with this server threaded in (make_node_gate/make_sketch_gate accept a
        # `server=`), so the driver merely holds the single handle (e.g. to close it after a run). None (the
        # default) changes NOTHING: with no per-node verifier configured there is no per-node compile, and
        # the default offline path is byte-identical.
        self.lean_server = lean_server

    def run(self, goal: str) -> DagResult:
        proven = self._prove(goal, ancestors=set(), depth=0)

        # Terminal authoritative gate: formalize the assembled proof -> Lean Layer-4 audit -> faithfulness.
        terminal = None
        if proven and self.terminal_gate is not None:
            proof_text = self.dag.proof_bundle(goal)
            self.trace.emit("terminal_gate_start", goal=goal[:80])
            # ROBUSTNESS (R-ROBUST): the terminal gate is a live model/Lean call (formalize -> audit ->
            # faithfulness) and can RAISE (subprocess timeout, non-zero exit, toolchain failure, malformed
            # output). A raise here MUST NOT crash the whole run: catch it so the root stays PROVEN (soft)
            # while the terminal AUTHORITATIVE verdict is simply ABSENT (terminal=None -> not
            # authoritative_elementary), exactly as if no terminal gate had been configured. The proof is
            # informally proven but not Layer-4-authoritative; the exception is surfaced on the trace,
            # never silently swallowed. A single model/Lean call never raises out of run().
            try:
                terminal = self.terminal_gate(goal, proof_text)
            except Exception as e:
                terminal = None
                self.trace.emit("terminal_gate_error", goal=goal[:80],
                                error_type=type(e).__name__, detail=str(e)[:160],
                                reason=ReasonCode.unknown_tool_error.value)
            else:
                self.trace.emit("terminal_gate", goal=goal[:80],
                                authoritative=bool(getattr(terminal, "authoritative", False)))

        stats = self.dag.stats()
        self.trace.emit("final", goal=goal[:80], proven=proven,
                        nodes=stats["nodes"], proven_nodes=stats["proven"],
                        cache_hits=stats["cache_hits"], **self.budget.snapshot())
        return DagResult(goal=goal, proven=proven, dag=self.dag, trace=self.trace,
                         budget=self.budget, terminal=terminal)

    # ---- direct-attempt outcome -> FSM event classification -----------------------------------
    def _direct_event(self, res: RalphResult, goal: str) -> NodeEvent:
        """Map a Ralph direct-attempt result to the NodeEvent the FSM table decides on.

        Order matters: a goal<->claim mismatch on a 'successful' ledger is a soundness failure that
        overrides success; a NEEDS_REVIEW-no-judge exhaustion is a *producible* state (decompose),
        distinct from a genuine budget starvation (exhaust)."""
        if res.success:
            if not _proves_goal(res.ledger, goal):
                return NodeEvent.GoalClaimMismatch
            return NodeEvent.DirectProven
        if res.exhausted:
            # Distinguish "needs a producer" from genuine starvation. The Ralph loop sets exhausted on
            # a NEEDS_REVIEW-with-no-judge (review_unhandled): that is NOT starvation — it needs a
            # decomposition/producer. Reserve DirectExhaustedBudget for true budget starvation.
            if (res.report is not None and res.report.verdict is Verdict.NEEDS_REVIEW
                    and not self.judges):
                return NodeEvent.DirectNeedsReviewNoJudge
            return NodeEvent.DirectExhaustedBudget
        # Plain failure (gate rejects / judge gaps that did not exhaust budget): a real gap to try to
        # producer around via decomposition.
        return NodeEvent.DirectRejected

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
            # Decide via the FSM: a depth-limit hit on an IN_PROGRESS node -> EXHAUSTED (limit-induced,
            # retryable on a shallower branch), carrying the depth_limit reason code. Leaving it
            # re-attemptable means the memo check above does NOT poison a shallower retry of this goal.
            _state, action = node_transition(NodeState.IN_PROGRESS, NodeEvent.DepthLimit)
            assert action is Action.EXHAUST_STARVED
            if not node.proven:
                self.dag.mark_exhausted(goal, reason=ReasonCode.depth_limit.value)
            self.trace.emit("depth_limit", goal=goal[:80], depth=depth,
                            reason=ReasonCode.depth_limit.value)
            return False
        if not self.budget.can_call():
            self.dag.mark_exhausted(goal, reason=ReasonCode.budget_starved.value)
            self.trace.emit("budget", reason="llm_calls", **self.budget.snapshot())
            return False

        node.state = NodeState.IN_PROGRESS

        # 1. Direct attempt (Ralph loop with the focused prover).
        ralph = RalphLoop(self.prover, toolkit=self.toolkit, budget=self.budget,
                          trace=self.trace, max_episodes=self.ralph_episodes, judges=self.judges)
        res = ralph.run(goal)
        node.attempts += res.episodes

        # DECIDE the next move via the TOTAL FSM table; the orchestrator EXECUTES the returned Action.
        event = self._direct_event(res, goal)
        _next, action = node_transition(NodeState.IN_PROGRESS, event)

        if action is Action.ACCEPT_PROVEN:
            # A directly-proven, goal-bound ledger. Before promoting, route a NEEDS_REVIEW proof
            # through the INDEPENDENT adversarial verifier (downgrade-don't-discard).
            if res.report is not None and res.report.verdict is Verdict.NEEDS_REVIEW:
                if not self._verify_or_downgrade(goal, res):
                    return False
            ledger = self._refine(goal, res.ledger)   # Autoreason tournament (no-regression refine)
            # OPT-IN per-node Lean authority (P2): when a node_verifier is configured, route this LEAF
            # through Lean before/at promotion. When it is None (the default) this is a no-op and the
            # arm below is the BYTE-IDENTICAL pre-feature path (mark_proven_direct + the same trace).
            if self.node_verifier is not None:
                return self._verify_leaf_and_route(goal, ledger)
            self.dag.mark_proven_direct(goal, ledger)
            self.trace.emit("prove_node", goal=goal[:80], proof_kind="direct")
            return True

        if action is Action.MARK_FAILED_GAP and event is NodeEvent.GoalClaimMismatch:
            self.trace.emit("goal_claim_mismatch", goal=goal[:80], proof_kind="direct",
                            reason=ReasonCode.gap_found.value)
            self.dag.mark_failed(goal, reason=ReasonCode.gap_found.value)
            return False

        if action is Action.EXHAUST_STARVED:
            # Genuine starvation on the direct attempt: no budget left to even decompose.
            self.dag.mark_exhausted(goal, reason=ReasonCode.budget_starved.value)
            self.trace.emit("budget", reason="llm_calls_direct", goal=goal[:80],
                            **self.budget.snapshot())
            return False

        # The remaining direct-attempt actions (DirectRejected / DirectNeedsReviewNoJudge) BOTH route
        # to DECOMPOSE. The bug-fix is here: a NEEDS_REVIEW-no-judge exhaustion is a *producible*
        # state, so it falls through to the decomposition path instead of terminating.
        assert action is Action.DECOMPOSE, f"unexpected action {action!r} for event {event!r}"
        if event is NodeEvent.DirectNeedsReviewNoJudge:
            # ADVERSARIAL VERIFIER + DOWNGRADE-DON'T-DISCARD: route the NEEDS_REVIEW direct proof
            # through the INDEPENDENT refuting check FIRST. If it refutes (or its inspection is
            # vacuous), the node is FAILED_ELEMENTARY recording the SPECIFIC offending step — a logged
            # gap, not a silent discard. If it CANNOT refute, the proof is still unreviewed, so we do
            # not promote it directly: we suspend and route to a producer (decomposition).
            if not self._verify_or_downgrade(goal, res):
                # Re-feed a DirectNeedsReviewNoJudge node through the FSM as an ElementaryViolation so
                # the terminal state is decided by the table (FAILED_ELEMENTARY), not ad hoc here.
                _s, vaction = node_transition(NodeState.IN_PROGRESS, NodeEvent.ElementaryViolation)
                assert vaction is Action.MARK_FAILED_ELEMENTARY
                return False
            self.trace.emit("route_decompose", goal=goal[:80],
                            reason=ReasonCode.needs_review_no_reviewer.value)

        return self._decompose(goal, res, ancestors, key, depth, direct_event=event)

    def _decompose(self, goal: str, res: RalphResult, ancestors: set[str], key: str,
                   depth: int, direct_event: NodeEvent) -> bool:
        """Execute the DECOMPOSE action: ask a producer (decomposer) for a blueprint and recurse,
        with reviewer gating + bounded re-planning. Returns True iff the node proved via children.

        Every terminal give-up here is classified with a ReasonCode so nothing gives up unclassified
        (T100 P1 item 3)."""
        # No producer at all -> genuine starvation for this node. Classify WHY: if the direct attempt
        # only stalled on missing review, the reason is 'needs_review_no_reviewer'; else 'decomposer_absent'.
        if self.decomposer is None:
            reason = (ReasonCode.needs_review_no_reviewer.value
                      if direct_event is NodeEvent.DirectNeedsReviewNoJudge
                      else ReasonCode.decomposer_absent.value)
            self.dag.mark_failed(goal, reason=reason)
            self.trace.emit("decomposer_absent", goal=goal[:80], reason=reason)
            return False

        feedback = list(res.lessons)
        child_ancestors = ancestors | {key}
        last_reason = ReasonCode.gap_found.value

        for attempt in range(self.max_decomp_attempts):
            if attempt > 0:                                   # attempts after the first are re-plans
                if not self.budget.can_replan():
                    self.trace.emit("replan_exhausted", goal=goal[:80],
                                    replans=self.budget.replans_spent)
                    last_reason = ReasonCode.budget_starved.value
                    break
                self.budget.spend_replan()
                self.trace.emit("replan", goal=goal[:80], replan=self.budget.replans_spent)
            if not self.budget.can_call():
                last_reason = ReasonCode.budget_starved.value
                break
            if self.population_k and self.comparator is not None:
                if self._prove_via_population(goal, feedback, child_ancestors, depth):
                    return True
            else:
                self.budget.spend_call()
                # ROBUSTNESS (Thread 2): a live decomposer (Codex/Claude CLI) can RAISE (subprocess
                # timeout, non-zero exit, malformed output). Treat a raise as "this decomposition
                # attempt failed" -> classify unknown_tool_error and fall through to the next attempt
                # (re-plan) / terminal backtrack, exactly like a rejected blueprint. The exception is
                # surfaced on the trace, never silently swallowed; the budget unit is already spent so
                # the loop stays bounded and terminates.
                try:
                    sketch, children = self.decomposer.decompose(goal, feedback or None)
                except Exception as e:
                    self.trace.emit("decomposer_error", goal=goal[:80],
                                    error_type=type(e).__name__, detail=str(e)[:160],
                                    reason=ReasonCode.unknown_tool_error.value)
                    last_reason = ReasonCode.unknown_tool_error.value
                    feedback = ([f"decomposer call failed ({type(e).__name__}): "
                                 f"{str(e)[:160]}"] + feedback)
                    continue
                self.trace.emit("decompose", goal=goal[:80], children=len(children))
                ok, notes, reason = self._try_decomposition(goal, sketch, children,
                                                            child_ancestors, depth)
                if ok:
                    return True
                last_reason = reason or last_reason
                feedback = notes + feedback

        # STUCK node: the direct attempt failed AND every normal decomposition attempt failed. As a LAST
        # resort fire the OpenEvolve fallback decomposer (flag-gated). Its evolved blueprint is committed
        # ONLY through the SAME _try_decomposition gate (goal-binding + acyclicity + strict-simpler-child
        # + sketch validity + H0) plus an explicit goal-bound + obligation-discharge pre-check, so a
        # gameable blueprint can never be committed. Graceful no-op if the backend is unavailable.
        if self._evolve_fallback_available() and self.budget.can_call():
            if self._try_evolve_fallback(goal, feedback, child_ancestors, depth):
                return True

        # Classify the terminal outcome via the FSM table so the state is the table's, not ad hoc:
        #   * a BUDGET-starved give-up (every decomposition attempt was blocked by an empty LLM-call /
        #     re-plan budget, not by a real logical gap) is LIMIT-INDUCED -> EXHAUSTED (retryable on a
        #     later run with more budget). Routing it to mark_failed (FAILED_GAP) here would POISON the
        #     split-keyed memo: a PROVABLE goal would be permanently reported unprovable even after the
        #     budget is refilled. The direct-attempt EXHAUST_STARVED arm (DirectExhaustedBudget) already
        #     does this; a decomposition starvation is the same limit-induced class.
        #   * an exclusively-elementary-violation failure is a real-but-non-elementary outcome
        #     (FAILED_ELEMENTARY);
        #   * any other classified gap (a genuine gap with a producer present and budget remaining) stays
        #     terminal FAILED_GAP.
        if last_reason == ReasonCode.budget_starved.value:
            _s, baction = node_transition(NodeState.IN_PROGRESS, NodeEvent.DirectExhaustedBudget)
            assert baction is Action.EXHAUST_STARVED
            self.dag.mark_exhausted(goal, reason=last_reason)
            self.trace.emit("budget", reason="llm_calls_decompose", goal=goal[:80],
                            **self.budget.snapshot())
        elif last_reason == ReasonCode.elementary_violation.value:
            _s, vaction = node_transition(NodeState.IN_PROGRESS, NodeEvent.ElementaryViolation)
            assert vaction is Action.MARK_FAILED_ELEMENTARY
            self.dag.mark_failed(goal, elementary=True, reason=last_reason)
        else:
            self.dag.mark_failed(goal, reason=last_reason)
        return False

    def _evolve_fallback_available(self) -> bool:
        """True iff an OpenEvolve fallback decomposer is configured AND reports itself available.

        Decoupled + graceful: a backend exposing ``available()`` that returns False (e.g. the optional
        ``openevolve`` package is not installed) is treated as a no-op fallback, never an error."""
        fb = self.evolve_fallback
        if fb is None:
            return False
        avail = getattr(fb, "available", None)
        if callable(avail):
            try:
                return bool(avail())
            except Exception:
                return False
        return True

    def _blueprint_is_goal_bound_and_discharged(self, goal: str, sketch: str,
                                                children: list[str]) -> bool:
        """DETERMINISTIC pre-check for a fallback blueprint: goal-bound (claim+conclusion bind by
        ``goal_hash``, non-vacuous) AND obligations discharged (the sketch's ``lemma`` steps EXACTLY
        match the declared child goals). Reuses the bridge's pure ``binds_to_goal``/``is_vacuous`` so the
        fallback applies the SAME non-gameable binding as the evolutionary fitness and the Elo pre-gate.
        A blueprint failing this is NEVER committed (the brief's hard requirement for evolved output)."""
        try:
            from agent.tools.openevolve_bridge import binds_to_goal, is_vacuous
        except Exception:
            return False
        try:
            led = parse_ledger(sketch)
        except LedgerError:
            return False
        if not binds_to_goal(led, goal) or is_vacuous(led):
            return False
        lemma_keys = _lemma_claims(sketch)
        if lemma_keys is None:
            return False
        return bool(children) and lemma_keys == {goal_hash(cg) for cg in children}

    def _try_evolve_fallback(self, goal: str, feedback: list[str], child_ancestors: set[str],
                             depth: int) -> bool:
        """Fire the OpenEvolve fallback decomposer on a STUCK node and try to commit its blueprint.

        Spends one call, asks the fallback for an evolved blueprint, and commits it ONLY if it passes
        the deterministic goal-bound + obligation-discharge pre-check AND the full _try_decomposition
        gate (acyclicity / strict-simpler-child / sketch validity / H0). Returns True iff the node proved
        via the evolved blueprint. Any backend exception is a graceful failure (the node stays stuck)."""
        self.budget.spend_call()
        try:
            sketch, children = self.evolve_fallback.decompose(goal, feedback or None)
        except Exception as e:
            self.trace.emit("evolve_fallback_error", goal=goal[:80], detail=str(e)[:120])
            return False
        # HARD pre-gate: a non-goal-bound / obligation-undischarged evolved blueprint is never committed.
        if not self._blueprint_is_goal_bound_and_discharged(goal, sketch, children):
            self.trace.emit("evolve_fallback_rejected", goal=goal[:80],
                            children=len(children) if children else 0,
                            reason=ReasonCode.gap_found.value)
            return False
        self.trace.emit("evolve_fallback", goal=goal[:80], children=len(children))
        ok, _notes, _reason = self._try_decomposition(goal, sketch, children, child_ancestors, depth)
        if ok:
            self.trace.emit("prove_node", goal=goal[:80], proof_kind="evolve_fallback")
        return ok

    def _refine(self, goal: str, ledger: str) -> str:
        """Optionally refine a proven champion via the AutoReason incumbent tournament. No-regression
        RELATIVE TO THE ADMISSIBILITY GATE: a challenger must beat the incumbent on the blind judge
        panel AND clear the AUTHORITATIVE elementary admissibility gate to displace it (see
        `_admissibility_gate`). It is monotone only relative to that panel + gate, not in any absolute
        sense — a possibly-single soft judge could still prefer a worse-but-elementary rewrite.

        EXPLORE/EXPLOIT separation (P3 / brief §9 #6): this is the *exploit* stage that runs AFTER the
        *explore* stage (OpenEvolve/search produced the champion). The refined output is registered with
        the explore/exploit barrier so it can NEVER be fed back into the OpenEvolve evolutionary archive
        as a seed — the two stages stay strictly one-directional (explore -> exploit, never back)."""
        if self.refiner is None or not self.budget.can_call():
            return ledger
        try:
            # GOAL-BOUND AUTHORITATIVE admissibility: a challenger must clear the authoritative
            # elementary check (deterministic refutation + the Lean audit when configured) for THIS
            # goal, not merely the soft gate. Bind the goal into the gate closure here so the refiner
            # (which calls is_admissible(ledger) with only the ledger) routes every challenger through it.
            result = self.refiner.refine(goal, ledger,
                                         is_admissible=self._admissibility_gate(goal))
        except Exception:
            return ledger
        if result.changed:
            self.trace.emit("refine", goal=goal[:80], passes=result.passes,
                            displacements=result.displacements)
            # Bar the refined (exploit-side) artifact from ever re-seeding the explore archive.
            self._bar_from_archive(result.content)
        return result.content

    def _bar_from_archive(self, refined: str) -> None:
        """Register an AutoReason-refined artifact with the explore/exploit barrier (one-directional).

        Best-effort + decoupled: if the optional evolve bridge is absent, the barrier simply does not
        exist and there is nothing to register against — the separation is vacuously held."""
        try:
            from agent.tools.openevolve_bridge import explore_exploit_barrier
        except Exception:
            return
        explore_exploit_barrier().mark_refined(refined)

    def _soft_gate_admits(self, ledger: str) -> bool:
        """Necessary (NOT sufficient) admissibility: the deterministic + soft gate does not REJECT.

        By its own docstring (`agent/gates/gate.py`) passing this gate does NOT certify "elementary" —
        it only rules out structural / obligation / denylist-prose REJECTs. It is a necessary first
        filter; the authoritative elementarity decision is made by `_authoritative_elementary` below."""
        try:
            return not evaluate(ledger, self.toolkit).rejected
        except Exception:
            return False

    def _authoritative_elementary(self, goal: str, ledger: str) -> bool:
        """The AUTHORITATIVE elementarity decision for an AutoReason challenger (not the soft gate).

        Routes the challenger through the same authoritative checks the prover/decomposer paths use:

          1. The DETERMINISTIC adversarial verifier `elementary_verifier.refute_elementary` (goal-bound):
             a challenger it REFUTES (denylist-prose, undischarged elastic, goal-binding violation) is
             NOT elementary and is rejected. A vacuous inspection (empty/unparseable) FAILS LOUD -> not
             admissible (never a silent pass).
          2. When a per-node Lean verifier is configured (`self.node_verifier is not None`), the Lean
             audit: a challenger that COMPILES but the Lean audit REJECTS (`lean_compiled_but_rejected`:
             sorry / denylist / non-whitelist axiom) REFUTES elementarity -> rejected. A non-compiling
             or UNAVAILABLE Lean result does NOT block admission (Lean is used only to REFUTE here, never
             to REQUIRE — mirroring the leaf path's fail-OPEN-on-inability-to-formalize), and a crashing
             verifier never refutes (fail open). The Lean audit is only consulted at all when a verifier
             is wired in; with no verifier this clause is inert (deterministic refutation only).

        Returns True iff NOTHING authoritatively refuted the challenger's elementarity."""
        try:
            verdict = refute_elementary(ledger, self.toolkit, goal=goal)
        except VacuousVerificationError:
            return False
        except Exception:
            return False
        if verdict.refuted:
            return False
        # Optional Lean authority: only REFUTE (a compiled-but-rejected challenger), never REQUIRE.
        if self.node_verifier is not None:
            try:
                lv = self.node_verifier(goal, ledger)
            except Exception:
                return True   # a crashing verifier never refutes a deterministically-clean challenger
            if bool(getattr(lv, "lean_compiled_but_rejected", False)):
                return False
        return True

    def _admissibility_gate(self, goal: str) -> Callable[[str], bool]:
        """Build the goal-bound admissibility predicate the AutoReason refiner uses to ADMIT challengers.

        A challenger is admissible iff it passes the soft gate (necessary first filter) AND clears the
        AUTHORITATIVE elementary check (`_authoritative_elementary`). So a non-elementary challenger that
        the SOFT GATE ALONE would have admitted is REJECTED here and can never displace the incumbent —
        'stay elementary to displace' becomes ENFORCED, not asserted."""
        def _ok(ledger: str) -> bool:
            return self._soft_gate_admits(ledger) and self._authoritative_elementary(goal, ledger)
        return _ok

    def _verify_or_downgrade(self, goal: str, res: RalphResult) -> bool:
        """Route a NEEDS_REVIEW direct proof through the INDEPENDENT adversarial verifier
        (downgrade-don't-discard). Returns True if the proof survives refutation (may be promoted),
        False if it was refuted -> the node is FAILED_ELEMENTARY recording the SPECIFIC offending step
        (a logged gap, never a silent discard).

        ANTI-VACUITY: a vacuous inspection (empty/unparseable ledger) FAILS LOUD — it is treated as a
        refutation (FAILED_ELEMENTARY), never as a pass."""
        source = res.report.ledger if (res.report and res.report.ledger is not None) else res.ledger
        try:
            verdict = refute_elementary(source, self.toolkit, goal=goal)
        except VacuousVerificationError as e:
            # Fail loud: the verifier had nothing to inspect -> do NOT promote. Mark FAILED_ELEMENTARY.
            self.dag.mark_failed(goal, elementary=True,
                                 reason=ReasonCode.elementary_violation.value)
            self.trace.emit("verifier_vacuous", goal=goal[:80], detail=str(e),
                            reason=ReasonCode.elementary_violation.value)
            return False
        if verdict.refuted:
            self.dag.mark_failed(goal, elementary=True,
                                 reason=ReasonCode.elementary_violation.value)
            self.trace.emit("verifier_refuted", goal=goal[:80], step=verdict.offending_step,
                            detail=verdict.summary(), reason=ReasonCode.elementary_violation.value)
            return False
        self.trace.emit("verifier_passed", goal=goal[:80], inspected=verdict.inspected_steps)
        return True

    def _verify_leaf_and_route(self, goal: str, ledger: str) -> bool:
        """OPT-IN per-node Lean authority (P2): route a LEAF that the existing path already accepted
        (a goal-bound, gate-passed direct ledger about to be marked PROVEN) through the configured
        `node_verifier` and classify the FOUR mutually-exclusive outcomes. Only ever called when
        `self.node_verifier is not None`; the None default never reaches here (byte-identical path).

        Fail-CLOSED on the elementarity claim, fail-OPEN on inability to formalize:
          (i)   PASS  (compiled AND audit.passed / .elementary_verified) -> mark_proven_direct AND
                set node.lean_verified=True. Lean CONFIRMED the leaf is elementary.
          (ii)  REJECT (compiled but the audit rejected: sorry / denylist / non-whitelist axiom) ->
                this REFUTES elementarity -> mark_failed(elementary=True, elementary_violation)
                (downgrade-don't-discard; the offending dep is recorded on the trace).
          (iii) COULD-NOT-COMPILE (no Lean produced / a compile error) -> FAIL OPEN: still
                mark_proven_direct (the informal gate already passed it) with lean_verified=False
                (re-attemptable; do NOT poison search).
          (iv)  UNAVAILABLE (server down / lean not installed) -> mark_exhausted (a retryable,
                budget_starved-equivalent 'lean_unavailable'); the leaf is re-attemptable when Lean is
                back. NOT a terminal failure.

        The verifier is independent; any unexpected exception in it is treated as fail-OPEN (soft
        PROVEN) so a flaky verifier never poisons a goal that the informal gate already admitted.

        PER-NODE VERIFY SUB-CAP (P3): before invoking the verifier, check the budget's OPTIONAL
        max_node_verify_calls sub-cap. If it is EXHAUSTED, SKIP per-node Lean for this leaf and fall
        back to soft PROVEN (lean_verified=False, re-attemptable) — do NOT block the proof or crash, so
        per-node Lean can never starve the prover/decomposer search. When the sub-cap is unlimited
        (None, the default) can_verify_node() is always True and this is byte-identical to before."""
        if not self.budget.can_verify_node():
            # Sub-cap exhausted: do NOT spend a per-node Lean compile here. Fall back to the soft-PROVEN
            # path the informal gate already justified (lean_verified stays False -> re-attemptable on a
            # later run with more node-verify budget). Bounded + non-blocking by construction.
            self.dag.mark_proven_direct(goal, ledger)
            self.trace.emit("node_lean", goal=goal[:80], outcome="node_verify_budget_exhausted",
                            lean_verified=False, reason=ReasonCode.budget_starved.value)
            self.trace.emit("prove_node", goal=goal[:80], proof_kind="direct")
            return True
        self.budget.spend_verify_node()
        try:
            verdict = self.node_verifier(goal, ledger)
        except Exception as e:
            # A crashing verifier must not refute or starve: the informal gate already passed this leaf.
            # Fail OPEN -> soft PROVEN (lean_verified=False), exactly as a non-compile outcome.
            self.dag.mark_proven_direct(goal, ledger)
            self.trace.emit("node_lean", goal=goal[:80], outcome="verifier_error",
                            lean_verified=False, detail=str(e)[:120])
            self.trace.emit("prove_node", goal=goal[:80], proof_kind="direct")
            return True

        unavailable = bool(getattr(verdict, "lean_unavailable", False))
        elementary_verified = bool(getattr(verdict, "elementary_verified", False))
        compiled = bool(getattr(verdict, "compiled", False))
        audit = getattr(verdict, "audit", None)
        # 'compiled-but-rejected' is the hard refutation; prefer the read-only helper if the verdict
        # exposes it, else derive it from (compiled AND audit ran AND audit did not pass).
        rejected = bool(getattr(verdict, "lean_compiled_but_rejected",
                                compiled and audit is not None and not bool(getattr(audit, "passed", False))))

        # (iv) UNAVAILABLE -> retryable EXHAUSTED (do NOT poison; re-attemptable when Lean is back).
        if unavailable:
            self.dag.mark_exhausted(goal, reason=ReasonCode.budget_starved.value)
            self.trace.emit("node_lean", goal=goal[:80], outcome="lean_unavailable",
                            lean_verified=False, reason="lean_unavailable")
            return False

        # (i) PASS -> promote to the first-class HARD-success state LEAN_VERIFIED (dominates PROVEN)
        # AND stamp the lean_verified annotation (set in lockstep by mark_proven_direct).
        if elementary_verified:
            node = self.dag.mark_proven_direct(goal, ledger, lean_verified=True)
            assert node.state is NodeState.LEAN_VERIFIED
            self.trace.emit("node_lean", goal=goal[:80], outcome="elementary_verified",
                            lean_verified=True)
            self.trace.emit("prove_node", goal=goal[:80], proof_kind="direct")
            return True

        # (ii) REJECT (compiled but audit refuted) -> downgrade-don't-discard to FAILED_ELEMENTARY.
        if rejected:
            detail = ""
            if audit is not None:
                try:
                    detail = "; ".join(str(f) for f in audit.rejects())[:200]
                except Exception:
                    detail = (audit.summary() if hasattr(audit, "summary") else "")[:200]
            self.dag.mark_failed(goal, elementary=True,
                                 reason=ReasonCode.elementary_violation.value)
            self.trace.emit("node_lean", goal=goal[:80], outcome="elementary_violation",
                            lean_verified=False, detail=detail,
                            reason=ReasonCode.elementary_violation.value)
            return False

        # (iii) COULD-NOT-COMPILE (no Lean / compile error). DEFAULT: FAIL OPEN -> soft PROVEN,
        # re-attemptable (the informal gate already passed it). This is the gap-#1 fail-open the
        # lean_strict flag closes.
        if self.lean_strict:
            # FAIL CLOSED: a leaf we could not Lean-check is NOT accepted as proven. Route it to a
            # non-proven FAILED_GAP (reason formalization_failed) — distinct from an elementarity
            # refutation (ii). Re-attemptable on a later run; never mints an unverified PROVEN.
            self.dag.mark_failed(goal, reason=ReasonCode.formalization_failed.value)
            self.trace.emit("node_lean", goal=goal[:80], outcome="lean_could_not_formalize_strict",
                            lean_verified=False, reason=ReasonCode.formalization_failed.value)
            return False
        self.dag.mark_proven_direct(goal, ledger)
        self.trace.emit("node_lean", goal=goal[:80], outcome="lean_could_not_formalize",
                        lean_verified=False)
        self.trace.emit("prove_node", goal=goal[:80], proof_kind="direct")
        return True

    def _verify_sketch(self, goal: str, sketch: str,
                       children: list[str]) -> tuple[bool, bool, list[str]]:
        """OPT-IN per-AND-node Lean COMPOSITION check (P4, LEAP §2.2/§2.5). Only ever called when
        `self.sketch_verifier is not None`. Formalizes this candidate's sketch as a SORRY-FREE
        composition theorem (parent from children-as-hypotheses), compiles + audits it, and decides
        whether the decomposition may be COMMITTED.

        Returns ``(committed, sketch_lean_verified, reject_notes)``:
          * COMMIT (True, True, [])  iff the composition compiled AND audited elementary
            (`res.elementary_verified`): the parent provably follows from the children-as-hypotheses
            and the body's own deps/axioms are elementary. The node is stamped sketch_lean_verified.
          * REJECT (False, False, notes) otherwise — a non-compiling / audit-rejected / unavailable
            sketch is NOT a Lean-valid composition, so this candidate is dropped (the caller tries the
            next attempt / backtracks). The decomposition is never committed without a compiling sketch.

        FAIL-CLOSED on commit: an UNAVAILABLE toolchain or a crashing verifier rejects the candidate
        (we never commit a composition we could not Lean-check). This only fires when a sketch_verifier
        is explicitly configured; the None default never reaches here (byte-identical path).

        PER-NODE VERIFY SUB-CAP (P3): before invoking the verifier, check the budget's OPTIONAL
        max_node_verify_calls sub-cap. If it is EXHAUSTED, SKIP the per-node Lean composition check and
        fall back to a SOFT COMMIT (committed=True, sketch_lean_verified=False) — the decomposition still
        commits via the SAME reviewer/acyclicity/binding gates the default path uses, just without the
        extra Lean composition authority, so per-node Lean can never starve the prover/decomposer
        search. This does NOT loosen any deterministic gate; it only declines the OPT-IN Lean overlay.
        When the sub-cap is unlimited (None, the default) can_verify_node() is always True."""
        if not self.budget.can_verify_node():
            # Sub-cap exhausted: skip the Lean composition compile. Soft-commit (no sketch_lean stamp)
            # exactly as the byte-identical pre-P4 path would, so the proof is not blocked or crashed.
            self.trace.emit("sketch_lean", goal=goal[:80], outcome="node_verify_budget_exhausted",
                            sketch_lean_verified=False, children=len(children),
                            reason=ReasonCode.budget_starved.value)
            return True, False, []
        self.budget.spend_verify_node()
        try:
            verdict = self.sketch_verifier(goal, sketch, children)
        except Exception as e:
            self.trace.emit("sketch_lean", goal=goal[:80], outcome="verifier_error",
                            sketch_lean_verified=False, detail=str(e)[:120])
            return False, False, [f"sketch verifier error: {str(e)[:120]}"]

        elementary_verified = bool(getattr(verdict, "elementary_verified", False))
        if elementary_verified:
            self.trace.emit("sketch_lean", goal=goal[:80], outcome="elementary_verified",
                            sketch_lean_verified=True, children=len(children))
            return True, True, []

        # Not compiled+elementary -> never commit. Classify for the trace (unavailable vs rejected vs
        # non-compile) without changing the (uniform) reject decision.
        if bool(getattr(verdict, "lean_unavailable", False)):
            outcome, note = "lean_unavailable", "lean toolchain unavailable for the composition check"
        elif bool(getattr(verdict, "lean_compiled_but_rejected", False)):
            outcome, note = "elementary_violation", "composition compiled but the audit rejected it"
        else:
            outcome, note = "lean_could_not_formalize", "composition sketch did not compile"
        self.trace.emit("sketch_lean", goal=goal[:80], outcome=outcome,
                        sketch_lean_verified=False, children=len(children))
        return False, False, [note]

    def _try_decomposition(self, goal: str, sketch: str, children: list[str],
                           child_ancestors: set[str], depth: int) -> tuple[bool, list[str], Optional[str]]:
        """Validate + commit + recurse one candidate decomposition. Returns (success, feedback, reason)
        where `reason` is a ReasonCode.value classifying why this candidate failed (None on success)."""
        if not children:
            return False, ["decomposition proposed no sub-lemmas"], ReasonCode.gap_found.value

        # The sketch's `lemma` steps must exactly match the declared children (honest decomposition).
        claims = _lemma_claims(sketch)
        if claims is None:
            return (False, ["decomposition sketch did not parse as a ledger"],
                    ReasonCode.gap_found.value)
        if claims != {goal_hash(c) for c in children}:
            return (False, ["sketch `lemma` steps do not match the declared child goals"],
                    ReasonCode.gap_found.value)

        # LEAP reviewer + elementary judge (before committing).
        if self.reviewer is not None:
            if not self.budget.can_call():
                return (False, ["budget exhausted before review"], ReasonCode.budget_starved.value)
            self.budget.spend_call()
            # ROBUSTNESS (Thread 2): a live reviewer (Codex/Claude CLI) can RAISE (subprocess timeout,
            # non-zero exit, malformed output). Treat a raise as "this decomposition attempt failed
            # review" -> reject this candidate (caller tries the next attempt / backtracks), classified
            # unknown_tool_error. The exception is surfaced on the trace, never silently swallowed.
            try:
                review = self.reviewer.review(goal, sketch, children)
            except Exception as e:
                self.trace.emit("reviewer_error", goal=goal[:80],
                                error_type=type(e).__name__, detail=str(e)[:160],
                                reason=ReasonCode.unknown_tool_error.value)
                return (False, [f"reviewer call failed ({type(e).__name__}): {str(e)[:160]}"],
                        ReasonCode.unknown_tool_error.value)
            self.trace.emit("review", goal=goal[:80], useful=review.useful,
                            elementary=review.elementary)
            if not review.ok:
                reason = (ReasonCode.elementary_violation.value if not review.elementary
                          else ReasonCode.gap_found.value)
                return False, review.notes, reason

        # Acyclicity guard.
        if self.dag.would_create_cycle(goal, children, child_ancestors):
            return (False, ["proposed decomposition is cyclic"],
                    ReasonCode.cyclic_decomposition.value)

        # The sketch itself must be a valid ledger (children admitted via `lemma` steps).
        sketch_report = evaluate(sketch, self.toolkit)
        if sketch_report.rejected:
            return False, [str(f) for f in sketch_report.rejects()], ReasonCode.gap_found.value
        # Fail closed: a NEEDS_REVIEW sketch (e.g. an elastic justification routed to Layer 2) must be
        # adjudicated. With no reviewer, the OLD behaviour rejected outright. Now the INDEPENDENT
        # adversarial verifier gets a refuting pass first: an undischarged-elastic / denylist-prose /
        # goal-binding violation is a SPECIFIC elementary refutation (downgrade-don't-discard); only an
        # un-refuted-but-still-unreviewed sketch is rejected as needing a reviewer.
        if sketch_report.verdict is Verdict.NEEDS_REVIEW and self.reviewer is None:
            try:
                vr = refute_elementary(sketch_report.ledger or sketch, self.toolkit, goal=goal)
            except VacuousVerificationError:
                return (False, ["decomposition sketch verification was vacuous"],
                        ReasonCode.elementary_violation.value)
            if vr.refuted:
                self.trace.emit("verifier_refuted", goal=goal[:80], step=vr.offending_step,
                                detail=vr.summary(), proof_kind="decomposition",
                                reason=ReasonCode.elementary_violation.value)
                return False, [str(r) for r in vr.refutations], ReasonCode.elementary_violation.value
            return (False, ["decomposition sketch needs Layer-2 review but no reviewer is configured"],
                    ReasonCode.needs_review_no_reviewer.value)

        # Soundness: the sketch must conclude THIS goal. If its conclusion proves a different
        # statement than the parent goal, the decomposition is not a valid plan for this node.
        concl = _conclusion_claim(sketch)
        if concl is None or goal_hash(concl) != goal_hash(goal):
            return (False, ["sketch conclusion does not prove the parent goal"],
                    ReasonCode.gap_found.value)

        # P4 (LEAP §2.2/§2.5 COMPOSITION check): when a sketch_verifier is configured, formalize this
        # candidate's SKETCH as a SORRY-FREE Lean theorem proving the parent ASSUMING the children as
        # hypotheses, then compile + audit it. COMMIT the decomposition ONLY if the composition compiled
        # + audited elementary (res.elementary_verified True); a non-compiling/rejected sketch REJECTS
        # this candidate (caller tries the next attempt / backtracks). When None (the default) this is a
        # no-op and the path below is the BYTE-IDENTICAL pre-P4 commit. A passing sketch sets
        # sketch_ok=True here and is stamped onto the node AFTER commit (read by the LEAP composition
        # rule in mark_proven_via_children).
        sketch_ok = False
        if self.sketch_verifier is not None:
            committed, sketch_ok, sk_reject = self._verify_sketch(goal, sketch, children)
            if not committed:
                return False, sk_reject, ReasonCode.gap_found.value

        # Snapshot the node so the commit can be rolled back if a child fails to prove. Committing
        # before recursion lets the acyclicity guard see the in-flight edges, but a child failure must
        # NOT leave stale 'decomposition' metadata behind (assemble()/proof_bundle would render it).
        node = self.dag.get_or_create(goal)
        prev_proof, prev_kind = node.proof, node.proof_kind
        prev_children, prev_state = list(node.children), node.state
        prev_sketch_lean = node.sketch_lean_verified

        try:
            self.dag.commit_decomposition(goal, sketch, children, child_ancestors)
        except CycleError:
            return False, ["commit detected a cycle"], ReasonCode.cyclic_decomposition.value
        # Stamp the composition's Lean verdict on the committed node (P4). It is read by the LEAP
        # parent-composition rule when the children all prove (mark_proven_via_children) and is rolled
        # back together with the rest of the commit if a child / H0 fails below.
        node.sketch_lean_verified = sketch_ok

        # Prove the committed AND children in width-bounded waves ordered by upward_rank (deepest
        # critical chain first), the Dilworth antichain width capping the parallel fan-out. This is
        # SCHEDULING-ONLY and BEHAVIOR-PRESERVING: each `_prove` is idempotent (memoized), child
        # results are order-independent, and we still short-circuit on the first failure exactly like
        # the old serial for-loop -> the SAME proven/failed outcome, only the visitation order/grouping
        # changes. (Used ONLY for committed AND children — never to pick among OR alternatives.)
        child_ok, child_reason = self._prove_and_children(goal, children, child_ancestors, depth)
        if not child_ok:
            self.trace.emit("backtrack", goal=goal[:80], child_reason=child_reason)
            # Roll back the (uncompleted) commit so no stale decomposition lingers on the node.
            node.proof, node.proof_kind = prev_proof, prev_kind
            node.children, node.state = prev_children, prev_state
            node.sketch_lean_verified = prev_sketch_lean
            # V1 FIX: propagate the failing CHILD's REAL reason instead of hardcoding gap_found. A
            # committed child that only EXHAUSTED on a LIMIT (budget_starved / depth_limit) is NOT a
            # genuine logical gap in the parent's plan — it is retryable. Hardcoding gap_found here
            # made the caller route the PROVABLE parent to terminal FAILED_GAP, permanently poisoning
            # the split-keyed memo so a budget-refilled retry of the SAME goal could never prove it
            # (e.g. with max_decomp_attempts=1, a single budget-starved child sank the parent forever).
            # The caller (_decompose) routes a budget_starved reason to mark_exhausted (retryable
            # EXHAUSTED); a genuine child gap stays gap_found -> the parent's terminal FAILED_GAP.
            return False, [], child_reason or ReasonCode.gap_found.value

        # H0 child-consistency gate (0-cocycle / global-section check) BEFORE composing the children
        # into the parent's proof. Each child passed LOCALLY; this checks they agree on overlaps so the
        # composition has a consistent global section. A contradiction (e.g. one child assumes `n is
        # even`, a sibling `n is odd`) is unsound to compose -> do NOT promote: mark FAILED_ELEMENTARY.
        h0 = self._check_h0_consistency(goal, children)
        if h0 is not None and not h0.consistent:
            node.proof, node.proof_kind = prev_proof, prev_kind
            node.children, node.state = prev_children, prev_state
            node.sketch_lean_verified = prev_sketch_lean
            self.trace.emit("h0_violation", goal=goal[:80], overlap=h0.offending_overlap,
                            detail=h0.summary(), reason=ReasonCode.elementary_violation.value)
            return False, [str(c) for c in h0.conflicts] or [h0.summary()], \
                ReasonCode.elementary_violation.value

        self.dag.mark_proven_via_children(goal)
        self.trace.emit("prove_node", goal=goal[:80], proof_kind="decomposition")
        return True, [], None

    # ---- next_producers leverage = fan-in x critical-path depth (P3 / §7) -----------------------
    def _fan_in(self, keys: list[str]) -> dict[str, int]:
        """In-degree of each key = the number of DISTINCT parent nodes that cite this node as a child
        (i.e. how many decomposition branches share this ``semantic_goal_hash``). Memoization makes one
        proven lemma reusable across ALL branches, so a high fan-in lemma is a high-leverage producer.
        Computed over the WHOLE DAG (not just ``keys``) so a shared lemma's true fan-in is seen."""
        keyset = set(keys)
        parents: dict[str, set[str]] = {k: set() for k in keys}
        for parent_key, node in self.dag.nodes.items():
            for ckey in node.children:
                if ckey in keyset:
                    parents[ckey].add(parent_key)
        return {k: len(parents[k]) for k in keys}

    def _subtree_depth(self, key: str) -> int:
        """Critical-path depth = the longest chain of committed sub-lemmas BENEATH ``key`` in the FULL
        DAG (a leaf with no sub-lemmas has depth 0). Iterative DFS with a visited-guard so a (defensive)
        cycle cannot loop forever; memoization keeps it linear over the committed decomposition edges.
        Computed over the WHOLE DAG so a node's true critical-path depth is seen even when its
        descendants are not in the candidate set being ranked."""
        memo: dict[str, int] = {}

        def depth(k: str, stack: frozenset) -> int:
            if k in memo:
                return memo[k]
            node = self.dag.get(k)
            if node is None or k in stack or not node.children:
                return 0
            d = 1 + max((depth(c, stack | {k}) for c in node.children), default=0)
            memo[k] = d
            return d

        return depth(key, frozenset())

    def _critical_path_depths(self, keys: list[str]) -> dict[str, int]:
        return {k: self._subtree_depth(k) for k in keys}

    def leverage_over_dag(self, keys: list[str]) -> dict[str, int]:
        """Leverage of each OPEN shared sub-lemma = fan-in (#distinct parents citing it) MULTIPLIED BY
        its critical-path depth (longest committed sub-lemma chain beneath it in the FULL DAG) (P3 / §7).

        Fan-in ALONE lets a shallow high-fan-in lemma starve a deep critical chain; weighting leverage
        by critical-path depth fixes that — a deep critical-path lemma can out-rank a shallow high-fan-in
        one. A lemma with depth 0 (a leaf with no sub-lemmas below it) still gets a baseline leverage of
        its fan-in (we use ``depth + 1`` so a leaf is not zeroed out, but a deeper node strictly dominates
        an equally-fanned-in shallower node). Returns ``{key: leverage}`` over exactly ``keys``."""
        if not keys:
            return {}
        depths = self._critical_path_depths(keys)
        fan_in = self._fan_in(keys)
        # leverage = fan_in * (depth + 1): multiplicative, monotone in BOTH fan-in and depth, so a deep
        # critical chain is not starved by a shallow high-fan-in lemma.
        return {k: fan_in.get(k, 0) * (depths.get(k, 0) + 1) for k in keys}

    def order_open_lemmas_by_leverage(self, keys: list[str]) -> list[str]:
        """The OPEN shared sub-lemmas ordered so a deep CRITICAL chain is scheduled FIRST (never starved
        by a shallow high-fan-in lemma).

        CRITICAL-PATH GUARD (P3 / §7 invariant): the multiplicative leverage `fan_in*(depth+1)` ALONE
        still lets a shallow high-fan-in lemma (fan_in 5, depth 0 -> 5) out-rank a deeper critical chain
        (fan_in 1, depth 3 -> 4), contradicting the invariant 'a high-fan-in shallow lemma does NOT
        starve a deep critical chain'. So the PRIMARY ordering key is the critical-path DEPTH (the
        irreducible-sequential length below the lemma — the part that cannot be parallelized away);
        leverage (fan_in x depth) breaks ties among equally-deep lemmas, then key for stability. A deep
        critical lemma therefore always sorts ahead of a shallower one regardless of fan-in.

        Results are ORDER-INDEPENDENT (an AND-node needs EVERY child proven), so this only changes the
        visitation order, never which goals prove — serial/parallel parity is preserved."""
        if not keys:
            return []
        lev = self.leverage_over_dag(keys)
        depths = self._critical_path_depths(keys)
        # Primary: deepest critical path first (protect the irreducible-sequential chain). Then leverage
        # (fan-in x depth) among equal-depth lemmas. Then key for a stable, deterministic order.
        return sorted(keys, key=lambda k: (-depths.get(k, 0), -lev.get(k, 0), k))

    # ---- width-bounded, rank-ordered AND-children scheduling ------------------------------------
    def _and_child_precedence(self, children: list[str]) -> list[tuple[str, str]]:
        """Precedence edges among the committed AND children for the Dilworth/rank computation. An
        edge `(u, v)` ("u precedes v") exists iff child `v` already (transitively, via committed
        decomposition edges) NEEDS child `u` — i.e. u is a sub-lemma of v in the current DAG, so u must
        prove before v. For a flat decomposition this is empty (the children are precedence-independent
        by construction -> the acyclicity guard) so the width is exactly the child count. A genuine
        chain among shared sub-lemmas yields edges -> width collapses toward 1 (don't over-spawn)."""
        keys = {c: goal_hash(c) for c in children}
        edges: list[tuple[str, str]] = []
        for v in children:
            for u in children:
                if u is v:
                    continue
                # v's subtree reaches u  =>  u must be proven before v  =>  edge u -> v.
                if self.dag.reaches(keys[v], keys[u]):
                    edges.append((u, v))
        return edges

    def _budget_fanout_limit(self) -> int:
        """A budget-derived ceiling on concurrent child-proving: never schedule a wave wider than the
        remaining LLM-call budget could service. Keeps budget/rate-limit caps as scheduler inputs
        (forge_relevance_study §7) rather than only CPU cores. At least 1 so progress is always made."""
        remaining = self.budget.max_llm_calls - self.budget.calls_spent
        return max(1, remaining)

    def _failing_child_reason(self, child_goal: str) -> str:
        """Classify WHY a committed child failed, read from the child node the driver just left behind.

        A child left EXHAUSTED (budget_starved / depth_limit) is a LIMIT-induced failure: the parent's
        plan is NOT a genuine gap, so we propagate budget_starved (retryable) up so the parent is
        EXHAUSTED, not poisoned to terminal FAILED_GAP (V1). A child terminally FAILED (a real gap /
        non-elementary) propagates its own gap/elementary reason. A missing/odd node defaults to a
        genuine gap (never spuriously retryable)."""
        node = self.dag.get(goal_hash(child_goal))
        if node is None:
            return ReasonCode.gap_found.value
        if node.state is NodeState.EXHAUSTED:
            # EXHAUSTED is only ever reached via a LIMIT (budget_starved / depth_limit) — both retryable.
            # Normalize to budget_starved so the parent classifier in _decompose routes it to
            # mark_exhausted (a retryable EXHAUSTED parent), never to a terminal FAILED_GAP (V1).
            return ReasonCode.budget_starved.value
        if node.state is NodeState.FAILED_ELEMENTARY:
            return ReasonCode.elementary_violation.value
        # FAILED_GAP / IN_PROGRESS / anything else -> a genuine gap for this plan.
        return node.reason or ReasonCode.gap_found.value

    def _prove_and_children(self, goal: str, children: list[str], child_ancestors: set[str],
                            depth: int) -> tuple[bool, Optional[str]]:
        """Prove every committed AND child, scheduling them in width-bounded waves ordered DEPTH-FIRST
        (the critical-path guard), the Dilworth antichain width capping the parallel fan-out. Returns
        ``(all_proven, failing_reason)``: ``(True, None)`` iff ALL children proved; on the first failure
        ``(False, <ReasonCode.value>)`` carrying the failing child's REAL reason (V1 propagation).
        Short-circuits on the first failure (same as the serial loop). Behavior-preserving on
        deterministic stubs (only the visitation order/grouping changes; results are order-independent).

        CRITICAL-PATH GUARD (V5 / P3 §7): the scheduler orders children DEPTH-PRIMARY — the same
        (-critical_path_depth, -leverage, key) order as ``order_open_lemmas_by_leverage`` — so a deep
        critical chain is visited NO LATER than a shallow high-fan-in lemma. Leverage = fan-in x
        (critical-path depth + 1) ALONE still lets a shallow high-fan-in lemma out-rank a deeper chain;
        making critical-path DEPTH the primary key fixes that. Results are order-independent (an AND-node
        needs EVERY child proven), so this only changes the visitation order, never which goals prove —
        serial/parallel parity is preserved."""
        edges = self._and_child_precedence(children)
        try:
            width = dilworth_width(children, edges)
        except CyclicPosetError:
            # Defensive: the committed children should be acyclic (commit guards it). If they are not,
            # fall back to the serial order rather than guessing a width.
            width = 1
        ranks = upward_rank(children, edges)
        # Cap = min(antichain width, budget-derived limit, operator ceiling). A deep chain -> width 1.
        cap = max(1, min(width, self._budget_fanout_limit(), self.fanout_cap))
        # DEPTH-PRIMARY critical-path order (V5): protect the irreducible-sequential chain. Primary key
        # is the critical-path DEPTH over the committed DAG; leverage (fan-in x depth) breaks ties among
        # equal-depth children; within-decomposition upward_rank then goal text for a stable order.
        ckeys = {c: goal_hash(c) for c in children}
        child_keys = [ckeys[c] for c in children]
        depths = self._critical_path_depths(child_keys)
        lev = self.leverage_over_dag(child_keys)
        ordered = sorted(
            children,
            key=lambda c: (-depths.get(ckeys[c], 0), -lev.get(ckeys[c], 0), -ranks.get(c, 0), c))
        self.trace.emit("and_fanout", goal=goal[:80], children=len(children),
                        width=width, cap=cap, max_rank=max(ranks.values(), default=0),
                        max_leverage=max(lev.values(), default=0),
                        max_depth=max(depths.values(), default=0))
        # Width-bounded waves. Proving is order-independent and idempotent, so grouping into waves of
        # size <= cap yields the same result as proving them one-by-one; we short-circuit on first fail.
        for start in range(0, len(ordered), cap):
            wave = ordered[start:start + cap]
            for child_goal in wave:
                if not self._prove(child_goal, child_ancestors, depth + 1):
                    return False, self._failing_child_reason(child_goal)
        return True, None

    def _child_proof_map(self, children: list[str]) -> dict[str, Optional[str]]:
        """The proven artifact (direct ledger or decomposition sketch) of each child, for the H0 gate."""
        out: dict[str, Optional[str]] = {}
        for c in children:
            node = self.dag.get(goal_hash(c))
            out[c] = node.proof if node is not None else None
        return out

    def _check_h0_consistency(self, goal: str, children: list[str]):
        """Run the H0 child-consistency gate over the (now all-proven) children. Returns the H0Result,
        or None when the gate is disabled. ANTI-VACUITY is the gate's own concern: a check that could
        inspect nothing returns consistent=False (reason vacuous_check), never a vacuous pass."""
        if not self.h0_consistency:
            return None
        return check_children_consistency(children, self._child_proof_map(children))

    def _compare_budget_ok(self) -> bool:
        """One pairwise comparison costs one model call (checked + spent atomically)."""
        if self.budget.can_call():
            self.budget.spend_call()
            return True
        return False

    def _population_hard_filter(self, goal: str) -> Callable[[Candidate], bool]:
        """A DETERMINISTIC hard pre-gate (goal-binding + obligation-discharge) for the Elo tournament.

        A candidate decomposition is admitted to the ranking ONLY if (P3 / openevolve_stacking_brief §9
        #5):
          * its sketch PARSES, and
          * the FULL deterministic gate does NOT REJECT it — we call ``gate.evaluate()`` (the same gate
            commit uses, semantics UNCHANGED) and admit ONLY a candidate whose verdict is
            PASSED_DETERMINISTIC or NEEDS_REVIEW. A candidate the gate REJECTS (e.g. a ``case_split``
            step missing its ``case_cover`` obligation -> verdict REJECTED / ``missing_obligation``) is
            dropped HERE so it is NEVER ranked, instead of being admitted to the tournament and only
            blocked later at commit, and
          * it binds to the goal by deterministic ``goal_hash`` equality on BOTH the top-level claim
            and the terminal conclusion, and is NOT vacuous (no LLM vote — the same non-gameable check
            the evolve fitness uses), and
          * its declared obligations are discharged structurally: the sketch's ``lemma`` steps (the
            deferred obligations) EXACTLY match the candidate's declared child goals (an honest
            decomposition — no hidden/over-claimed sub-lemma).

        A candidate that fails ANY clause is NEVER ranked or selected — the Elo/BT/PUCT machinery only
        ever sees gate-passing candidates. Reuses the bridge's pure ``binds_to_goal``/``is_vacuous`` so
        the filter and the evolutionary fitness apply the SAME deterministic binding."""
        from agent.tools.openevolve_bridge import binds_to_goal, is_vacuous

        def _ok(c: Candidate) -> bool:
            try:
                led = parse_ledger(c.content)
            except LedgerError:
                return False
            # FULL GATE (semantics unchanged — we only call it): a candidate the deterministic gate
            # REJECTS is dropped before ranking. Admit ONLY PASSED_DETERMINISTIC / NEEDS_REVIEW. A gate
            # internal error fails closed (rejected) inside evaluate(); any unexpected exception here is
            # treated as a rejection too (no fail-open admission).
            try:
                if evaluate(c.content, self.toolkit).rejected:
                    return False
            except Exception:
                return False
            if not binds_to_goal(led, goal) or is_vacuous(led):
                return False
            # Obligation discharge: the sketch's `lemma` steps must match the declared child goals.
            lemma_keys = _lemma_claims(c.content)
            if lemma_keys is None:
                return False
            return lemma_keys == {goal_hash(cg) for cg in c.children}

        return _ok

    def _prove_via_population(self, goal: str, feedback: list[str],
                             child_ancestors: set[str], depth: int) -> bool:
        """Generate K candidate decompositions, rank them by an Elo comparison tournament, and try
        them best-first (AlphaProof_Nexus population/Elo over incomplete sketches).

        Candidates pass a DETERMINISTIC hard pre-gate (goal-binding + obligation-discharge) BEFORE the
        tournament — a gate-gaming candidate is never ranked or selected (P3 / brief §9 #5)."""
        cands: list[Candidate] = []
        for i in range(self.population_k):
            if not self.budget.can_call():
                break
            self.budget.spend_call()
            # ROBUSTNESS (Thread 2): a raising decomposer must not crash candidate generation; skip the
            # failed candidate (its budget unit is already spent) and surface it on the trace.
            try:
                sketch, children = self.decomposer.decompose(goal, feedback or None)
            except Exception as e:
                self.trace.emit("decomposer_error", goal=goal[:80],
                                error_type=type(e).__name__, detail=str(e)[:160],
                                reason=ReasonCode.unknown_tool_error.value)
                continue
            self.trace.emit("decompose", goal=goal[:80], children=len(children))
            if children:
                cands.append(Candidate(id=f"cand{i}", content=sketch, goal=goal, children=children))
        if not cands:
            return False

        # Hard pre-gate: a candidate failing goal-binding / obligation-discharge is NEVER admitted, so
        # the Elo/BT/PUCT machinery (unchanged) only ranks gate-passing candidates.
        pop = EloPopulation(hard_filter=self._population_hard_filter(goal))
        for c in cands:
            pop.add(c)
        if not pop.candidates:
            # Every candidate failed the hard filter — nothing safe to rank.
            self.trace.emit("population_all_filtered", goal=goal[:80],
                            candidates=len(cands), rejected=pop.rejected_by_filter)
            return False
        comparisons = pop.tournament(self.comparator, rounds=self.population_rounds,
                                     budget_ok=self._compare_budget_ok, trace=self.trace)
        # Bradley-Terry latent strengths from the tournament win matrix (a stable batch estimate over
        # noisy online Elo), then PUCT-best-first expansion (exploit strength + explore under-visited).
        pop.set_ratings_from_bradley_terry()
        self.trace.emit("population", goal=goal[:80], candidates=len(cands), comparisons=comparisons)

        tried = 0
        while tried < min(self.max_decomp_attempts, len(pop.candidates)):
            cand = pop.select_puct()
            if cand is None:
                break
            tried += 1
            ok, _notes, _reason = self._try_decomposition(goal, cand.content, cand.children,
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
