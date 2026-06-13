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
from typing import Optional, Protocol, runtime_checkable

from agent.gates.gate import evaluate
from agent.gates.ledger import parse_ledger, LedgerError
from agent.gates.toolkit import Toolkit, load_toolkit
from agent.orchestrator.dag import ProofDAG, goal_hash, CycleError
from agent.orchestrator.driver import Prover, Judge
from agent.orchestrator.ralph import RalphLoop
from agent.orchestrator.state import Budget, NodeState
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

    def proof_tree(self) -> dict:
        return self.dag.assemble(self.goal)


def _lemma_claims(sketch: str) -> Optional[set[str]]:
    """Normalized claims of the sketch's `lemma` steps, or None if the sketch won't parse."""
    try:
        led = parse_ledger(sketch)
    except LedgerError:
        return None
    return {goal_hash(s.claim) for s in led.steps if s.justification == "lemma"}


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
        self.dag = ProofDAG()

    def run(self, goal: str) -> DagResult:
        proven = self._prove(goal, ancestors=set(), depth=0)
        stats = self.dag.stats()
        self.trace.emit("final", goal=goal[:80], proven=proven,
                        nodes=stats["nodes"], proven_nodes=stats["proven"],
                        cache_hits=stats["cache_hits"], **self.budget.snapshot())
        return DagResult(goal=goal, proven=proven, dag=self.dag, trace=self.trace,
                         budget=self.budget)

    def _prove(self, goal: str, ancestors: set[str], depth: int) -> bool:
        node = self.dag.get_or_create(goal)
        key = node.key

        # Memoization: a proven goal is reused; a known-failed goal is not re-attempted.
        if node.proven:
            self.trace.emit("cache_hit", goal=goal[:80])
            return True
        if node.state in (NodeState.FAILED_GAP, NodeState.FAILED_ELEMENTARY):
            return False
        if key in ancestors:               # cycle (defensive; commit guards this too)
            return False
        if depth > self.max_depth:
            self.dag.mark_failed(goal)
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
            self.dag.mark_proven_direct(goal, res.ledger)
            self.trace.emit("prove_node", goal=goal[:80], proof_kind="direct")
            return True
        if res.exhausted:
            self.dag.mark_failed(goal)
            return False

        # 2. Decomposition (LEAP), bounded attempts with reviewer gating + backtracking.
        if self.decomposer is None:
            self.dag.mark_failed(goal)
            return False

        feedback = list(res.lessons)
        child_ancestors = ancestors | {key}
        for _attempt in range(self.max_decomp_attempts):
            if not self.budget.can_call():
                break
            self.budget.spend_call()
            sketch, children = self.decomposer.decompose(goal, feedback or None)
            self.trace.emit("decompose", goal=goal[:80], children=len(children))

            if not children:
                feedback = ["decomposition proposed no sub-lemmas"] + feedback
                continue

            # The sketch's `lemma` steps must exactly match the declared children (honest decomposition).
            claims = _lemma_claims(sketch)
            if claims is None:
                feedback = ["decomposition sketch did not parse as a ledger"] + feedback
                continue
            if claims != {goal_hash(c) for c in children}:
                feedback = ["sketch `lemma` steps do not match the declared child goals"] + feedback
                continue

            # LEAP reviewer + elementary judge (before committing).
            if self.reviewer is not None:
                if not self.budget.can_call():
                    break
                self.budget.spend_call()
                review = self.reviewer.review(goal, sketch, children)
                self.trace.emit("review", goal=goal[:80], useful=review.useful,
                                elementary=review.elementary)
                if not review.ok:
                    feedback = review.notes + feedback
                    continue

            # Acyclicity guard.
            if self.dag.would_create_cycle(goal, children, child_ancestors):
                feedback = ["proposed decomposition is cyclic"] + feedback
                continue

            # The sketch itself must be a valid ledger (children admitted via `lemma` steps).
            sketch_report = evaluate(sketch, self.toolkit)
            if sketch_report.rejected:
                feedback = [str(f) for f in sketch_report.rejects()] + feedback
                continue

            try:
                self.dag.commit_decomposition(goal, sketch, children, child_ancestors)
            except CycleError:
                feedback = ["commit detected a cycle"] + feedback
                continue

            # Recurse on children (DFS).
            all_ok = True
            for child_goal in children:
                if not self._prove(child_goal, child_ancestors, depth + 1):
                    all_ok = False
                    break
            if all_ok:
                self.dag.mark_proven_via_children(goal)
                self.trace.emit("prove_node", goal=goal[:80], proof_kind="decomposition")
                return True
            self.trace.emit("backtrack", goal=goal[:80])

        self.dag.mark_failed(goal)
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
