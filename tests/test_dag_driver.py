"""Tests for the DAG orchestrator: direct proof, decomposition, memoization, cycles, budgets.

All offline: scripted prover/decomposer/reviewer stand in for the Codex focused prover.
"""
import json

import pytest

from agent.orchestrator.dag_driver import (
    DagDriver, ReviewVerdict, ScriptedDecomposer, ScriptedReviewer, _proves_goal,
)
from agent.orchestrator.driver import FlatDriver, ScriptedProver
from agent.orchestrator.population import KeyComparator
from agent.orchestrator.state import Budget, NodeState
from agent.orchestrator.tournament import (
    RevisionController, ScriptedCritic, ScriptedAuthor, ScriptedSynthesizer,
)
from agent.orchestrator.trace import RunTrace
from agent.gates.toolkit import load_toolkit

TOOLKIT = load_toolkit()
OK_REVIEW = ReviewVerdict(useful=True, elementary=True)


def valid_ledger(goal: str) -> str:
    return json.dumps({"problem": "p", "claim": goal, "steps": [
        {"id": "s1", "claim": "setup", "justification": "given", "depends_on": []},
        {"id": "s2", "claim": goal, "justification": "conclusion", "depends_on": ["s1"]}]})


def valid_ledger2(goal: str) -> str:
    """A second, structurally-valid ledger for the same goal (a distinct admissible candidate)."""
    return json.dumps({"problem": "p2", "claim": goal, "steps": [
        {"id": "s1", "claim": "alt setup", "justification": "given", "depends_on": []},
        {"id": "s2", "claim": goal, "justification": "conclusion", "depends_on": ["s1"]}]})


def sketch(goal: str, children: list[str], conclusion: str | None = None) -> str:
    """A decomposition sketch. `conclusion` lets a test make the terminal step prove a statement
    other than `goal` (a goal<->claim mismatch) while keeping the ledger internally consistent."""
    concl = goal if conclusion is None else conclusion
    steps = [{"id": f"L{i}", "claim": c, "justification": "lemma", "depends_on": []}
             for i, c in enumerate(children)]
    steps.append({"id": "c", "claim": concl, "justification": "conclusion",
                  "depends_on": [f"L{i}" for i in range(len(children))]})
    return json.dumps({"problem": "p", "claim": concl, "steps": steps})


BAD = json.dumps({"problem": "p", "claim": "?", "steps": [
    {"id": "s1", "claim": "heavy", "justification": "class_field_theory", "depends_on": []},
    {"id": "s2", "claim": "?", "justification": "conclusion", "depends_on": ["s1"]}]})


class DictProver:
    """prove(goal) -> mapping[goal] or a rejected default."""
    def __init__(self, mapping: dict[str, str], default: str = BAD):
        self.mapping = mapping
        self.default = default
        self.calls = 0

    def prove(self, goal: str, feedback=None) -> str:
        self.calls += 1
        return self.mapping.get(goal, self.default)


class DictDecomposer:
    def __init__(self, mapping: dict[str, tuple[str, list[str]]]):
        self.mapping = mapping

    def decompose(self, goal: str, feedback=None):
        return self.mapping.get(goal, ("", []))


def _driver(prover, decomposer=None, reviewer=None, budget=None, **kw):
    return DagDriver(prover, decomposer=decomposer, reviewer=reviewer, toolkit=TOOLKIT,
                     budget=budget or Budget(), trace=RunTrace("t"), **kw)


# ---- direct proof ----

def test_direct_proof():
    res = _driver(DictProver({"G": valid_ledger("G")})).run("G")
    assert res.proven
    assert res.dag.get(res.dag.get_or_create("G").key).proof_kind == "direct"
    assert res.dag.stats()["proven"] == 1


# ---- decomposition ----

def test_decomposition_then_children():
    prover = DictProver({"A": valid_ledger("A"), "B": valid_ledger("B")})  # G fails direct
    decomp = ScriptedDecomposer([(sketch("G", ["A", "B"]), ["A", "B"])])
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW])).run("G")
    assert res.proven
    tree = res.proof_tree()
    assert tree["kind"] == "decomposition"
    assert {c["goal"] for c in tree["children"]} == {"A", "B"}


# ---- memoization: a shared sub-lemma is proven once and reused ----

def test_shared_lemma_memoized():
    prover = DictProver({"C": valid_ledger("C")})  # A, B, G fail direct; C proves directly
    decomp = DictDecomposer({
        "G": (sketch("G", ["A", "B"]), ["A", "B"]),
        "A": (sketch("A", ["C"]), ["C"]),
        "B": (sketch("B", ["C"]), ["C"]),
    })
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW])).run("G")
    assert res.proven
    assert res.dag.cache_hits >= 1           # C reused on the second branch
    # C exists once as a node and is proven.
    c_node = res.dag.get(res.dag.get_or_create("C").key)
    assert c_node.proven and c_node.proof_kind == "direct"


# ---- acyclicity: a self-referential decomposition cannot loop forever ----

def test_cycle_is_refused_and_terminates():
    prover = DictProver({})  # everything fails direct
    decomp = DictDecomposer({
        "G": (sketch("G", ["A"]), ["A"]),
        "A": (sketch("A", ["G"]), ["G"]),  # would close a cycle back to G
    })
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW])).run("G")
    assert not res.proven  # and crucially, it returned (no infinite recursion)


# ---- reviewer veto ----

def test_reviewer_rejection_blocks_commit():
    prover = DictProver({"A": valid_ledger("A")})
    decomp = ScriptedDecomposer([(sketch("G", ["A"]), ["A"])])
    bad_review = ScriptedReviewer([ReviewVerdict(useful=False, elementary=True, notes=["trivial"])])
    res = _driver(prover, decomposer=decomp, reviewer=bad_review,
                  max_decomp_attempts=2).run("G")
    assert not res.proven


def test_reviewer_non_elementary_blocks_commit():
    prover = DictProver({"A": valid_ledger("A")})
    decomp = ScriptedDecomposer([(sketch("G", ["A"]), ["A"])])
    rev = ScriptedReviewer([ReviewVerdict(useful=True, elementary=False, notes=["uses ANT"])])
    assert not _driver(prover, decomposer=decomp, reviewer=rev, max_decomp_attempts=2).run("G").proven


# ---- sketch/children consistency ----

def test_sketch_lemma_mismatch_rejected():
    prover = DictProver({"A": valid_ledger("A")})
    # sketch cites lemma "X" but declares child "A" -> mismatch.
    decomp = ScriptedDecomposer([(sketch("G", ["X"]), ["A"])])
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                  max_decomp_attempts=2).run("G")
    assert not res.proven


# ---- no decomposer: direct failure is terminal ----

def test_no_decomposer_direct_failure():
    res = _driver(DictProver({})).run("G")
    assert not res.proven
    assert res.dag.get(res.dag.get_or_create("G").key).state in (
        NodeState.FAILED_GAP, NodeState.FAILED_ELEMENTARY)


# ---- depth limit ----

def test_depth_limit():
    prover = DictProver({"A": valid_ledger("A")})
    decomp = ScriptedDecomposer([(sketch("G", ["A"]), ["A"])])
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                  max_depth=0).run("G")
    assert not res.proven  # children live at depth 1 > max_depth 0


# ---- budget always terminates ----

def test_tiny_budget_terminates():
    prover = DictProver({})
    decomp = ScriptedDecomposer([(sketch("G", ["A"]), ["A"])])
    budget = Budget(max_llm_calls=2)
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                  budget=budget).run("G")
    assert not res.proven
    assert budget.calls_spent <= 2


def test_trace_has_final_event():
    res = _driver(DictProver({"G": valid_ledger("G")})).run("G")
    assert res.trace.by_kind("final")
    assert res.trace.by_kind("final")[0].data["proven"] is True


# ---- Autoreason refiner wired into the driver (no-regression refinement of a direct proof) ----

def _refiner(score_map):
    judge = KeyComparator(lambda c: score_map.get(c.content, 0.0))
    return RevisionController(ScriptedCritic([["x"]]), ScriptedAuthor([valid_ledger2("G")]),
                             ScriptedSynthesizer([valid_ledger2("G")]), [judge], seed=0)


def test_refiner_improves_a_direct_proof():
    a, b = valid_ledger("G"), valid_ledger2("G")
    res = _driver(DictProver({"G": a}), refiner=_refiner({a: 1.0, b: 5.0})).run("G")
    assert res.proven
    node = res.dag.get(res.dag.get_or_create("G").key)
    assert node.proof_kind == "direct"
    assert node.proof == b               # displaced by the judge-preferred admissible candidate


def test_refiner_never_regresses_a_direct_proof():
    a, b = valid_ledger("G"), valid_ledger2("G")
    res = _driver(DictProver({"G": a}), refiner=_refiner({a: 10.0, b: 1.0})).run("G")
    node = res.dag.get(res.dag.get_or_create("G").key)
    assert node.proof == a               # incumbent held (do-nothing wins)


# ---- max_replan_depth bounds re-decomposition globally ----

def test_max_replan_depth_caps_replans():
    prover = DictProver({})                                   # G fails direct; A is unprovable
    decomp = DictDecomposer({"G": (sketch("G", ["A"]), ["A"])})
    budget = Budget(max_replan_depth=1)
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                  budget=budget, max_decomp_attempts=5).run("G")
    assert not res.proven
    assert budget.replans_spent == budget.max_replan_depth   # the cap was reached, not exceeded
    assert len(res.trace.by_kind("replan")) == 1
    assert res.trace.by_kind("replan_exhausted")             # at least one node hit the cap


def test_zero_replan_depth_allows_only_the_first_plan():
    prover = DictProver({})
    decomp = DictDecomposer({"G": (sketch("G", ["A"]), ["A"])})
    budget = Budget(max_replan_depth=0)
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                  budget=budget, max_decomp_attempts=5).run("G")
    assert not res.proven
    assert budget.replans_spent == 0
    assert res.trace.by_kind("replan") == []


# ---- L5 soundness: goal<->claim binding (a proof must conclude the requested goal) ----

def mismatched_ledger(goal: str, proved: str) -> str:
    """A structurally valid, gate-passing ledger that concludes `proved` (a DIFFERENT statement)
    instead of the requested `goal`. Internally consistent (claim == conclusion == `proved`) so the
    deterministic gate admits it; only the driver's goal-binding should reject it for `goal`."""
    return json.dumps({"problem": "p", "claim": proved, "steps": [
        {"id": "s1", "claim": "setup", "justification": "given", "depends_on": []},
        {"id": "s2", "claim": proved, "justification": "conclusion", "depends_on": ["s1"]}]})


def claim_matches_goal_but_concludes_other(goal: str, concluded: str) -> str:
    """A gate-passing ledger whose TOP-LEVEL `claim` equals the requested `goal` (so a claim-only
    binding is satisfied) but whose terminal `conclusion` step proves a DIFFERENT statement.

    This is the residual attack: setting `claim == goal` while the conclusion proves `concluded`
    sneaks a proof of the wrong statement past any binding that only checks the top-level claim.
    The deterministic gate admits it because the fresh conclusion restates neither the goal nor an
    intermediate body step. Only a binding that checks the TERMINAL CONCLUSION against the goal
    catches it."""
    return json.dumps({"problem": goal, "claim": goal, "steps": [
        {"id": "a", "claim": "1 = 1", "justification": "given", "depends_on": []},
        {"id": "c", "claim": concluded, "justification": "conclusion", "depends_on": ["a"]}]})


def test_direct_proof_of_wrong_goal_is_rejected():
    # The prover returns a perfectly valid ledger -- but it proves "H", not the requested "G".
    prover = DictProver({"G": mismatched_ledger("G", proved="H")})
    res = _driver(prover).run("G")
    assert not res.proven
    assert res.trace.by_kind("goal_claim_mismatch")


def test_direct_proof_claim_matches_goal_but_concludes_other_is_rejected():
    # Residual L5 trigger: claim == goal "G" (so a claim-only check passes), but the terminal
    # conclusion proves a fresh statement "H". The gate admits it, yet the DagDriver must reject it
    # because the proof concludes a DIFFERENT statement than the requested goal.
    attack = claim_matches_goal_but_concludes_other("G", concluded="H")
    # Sanity: the deterministic gate (lenient, goal-agnostic) DOES admit this ledger ...
    from agent.gates.gate import evaluate
    assert not evaluate(attack, TOOLKIT).rejected
    # ... but the driver's terminal-conclusion goal-binding rejects it for goal "G".
    prover = DictProver({"G": attack})
    res = _driver(prover).run("G")
    assert not res.proven
    assert res.trace.by_kind("goal_claim_mismatch")


def test_proves_goal_requires_terminal_conclusion_to_match():
    # Both the top-level claim AND the terminal conclusion must bind to the goal.
    assert _proves_goal(valid_ledger("G"), "G")                          # claim==concl==goal
    assert not _proves_goal(mismatched_ledger("G", proved="H"), "G")     # claim & concl == "H"
    # claim == goal but conclusion proves something else -> NOT a proof of the goal.
    assert not _proves_goal(claim_matches_goal_but_concludes_other("G", concluded="H"), "G")
    # conclusion == goal but top-level claim mislabeled -> also rejected (both must bind).
    mislabeled = json.dumps({"problem": "p", "claim": "H", "steps": [
        {"id": "s1", "claim": "setup", "justification": "given", "depends_on": []},
        {"id": "s2", "claim": "G", "justification": "conclusion", "depends_on": ["s1"]}]})
    assert not _proves_goal(mislabeled, "G")
    assert not _proves_goal("not a ledger at all", "G")                  # unparseable -> False


def test_decomposition_concluding_wrong_goal_is_rejected():
    # The sketch's children/lemmas line up, but its conclusion proves "WRONG", not the parent "G".
    prover = DictProver({"A": valid_ledger("A")})
    bad_sketch = sketch("G", ["A"], conclusion="WRONG")
    decomp = ScriptedDecomposer([(bad_sketch, ["A"])])
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                  max_decomp_attempts=2).run("G")
    assert not res.proven


# ---- L5: depth-limit failure must NOT poison a shallower retry of the same goal ----

def test_depth_limit_failure_does_not_block_shallower_retry():
    # G decomposes to [A]; A decomposes to [B]; B proves directly. With max_depth=1, B is first
    # reached at depth 2 (> 1) on the G->A->B branch and hits the limit. A direct retry of B at a
    # shallow depth (run("B")) must still succeed -- the limit-induced failure must not be memoized
    # as a terminal failure.
    prover = DictProver({"B": valid_ledger("B")})
    decomp = DictDecomposer({
        "G": (sketch("G", ["A"]), ["A"]),
        "A": (sketch("A", ["B"]), ["B"]),
    })
    driver = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                     max_depth=1)
    # First: prove G. B is reached at depth 2 and is limited (left non-terminal, not FAILED).
    assert not driver.run("G").proven
    b_node = driver.dag.get(driver.dag.get_or_create("B").key)
    assert b_node.state is NodeState.EXHAUSTED          # limit-induced, re-attemptable
    assert b_node.state not in (NodeState.FAILED_GAP, NodeState.FAILED_ELEMENTARY)
    # Now: a shallow direct attempt at B must succeed (the memo did not poison it).
    assert driver._prove("B", ancestors=set(), depth=0)
    assert driver.dag.get(b_node.key).proven


# ---- L5: a failed decomposition must roll back (no stale children / proof on the node) ----

def test_failed_decomposition_leaves_no_stale_children():
    # G decomposes to [A]; A is unprovable (no direct proof, no decomposer plan for A). The
    # decomposition is committed before recursion, but the child failure must roll it back so the
    # node carries no stale 'decomposition' metadata that assemble() would render.
    prover = DictProver({})                              # nothing proves directly
    decomp = DictDecomposer({"G": (sketch("G", ["A"]), ["A"])})
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                  max_decomp_attempts=1).run("G")
    assert not res.proven
    g_node = res.dag.get(res.dag.get_or_create("G").key)
    assert g_node.children == []                         # rolled back
    assert g_node.proof is None and g_node.proof_kind is None
    # The assembled tree must not advertise a decomposition for a node that never proved.
    tree = res.proof_tree()
    assert tree.get("kind") is None
    assert "children" not in tree


# ---- L5: FlatDriver goal<->claim binding (a ledger for a DIFFERENT problem is not accepted) ----

def test_flat_driver_rejects_proof_of_different_problem():
    # A structurally valid ledger, but written for problem "other" while we requested "p".
    wrong = json.dumps({"problem": "other", "claim": "c", "steps": [
        {"id": "s1", "claim": "setup", "justification": "given", "depends_on": []},
        {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]}]})
    # No repair budget so the mismatch is terminal (FAILED_GAP), not an endless repair loop.
    driver = FlatDriver(ScriptedProver([wrong]), toolkit=TOOLKIT,
                        budget=Budget(max_repair_iters=0), trace=RunTrace("flat"))
    res = driver.run("p")
    assert not res.proven
    assert res.state is NodeState.FAILED_GAP
    assert res.trace.by_kind("goal_claim_mismatch")


def test_flat_driver_accepts_matching_problem():
    ok = json.dumps({"problem": "p", "claim": "c", "steps": [
        {"id": "s1", "claim": "setup", "justification": "given", "depends_on": []},
        {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]}]})
    res = FlatDriver(ScriptedProver([ok]), toolkit=TOOLKIT, trace=RunTrace("flat")).run("p")
    assert res.proven
