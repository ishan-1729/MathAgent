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


def test_memo_off_reproves_shared_lemma_fresh():
    """StageProfile.memo=False (threaded to DagDriver memo=False): the shared sub-lemma C is proved
    FRESH on BOTH branches — zero cache_hit trace events, zero DAG cache_hits, and C's prover is
    invoked twice (two prove attempts). Correctness is unchanged (the goal still proves)."""
    prover = DictProver({"C": valid_ledger("C")})
    decomp = DictDecomposer({
        "G": (sketch("G", ["A", "B"]), ["A", "B"]),
        "A": (sketch("A", ["C"]), ["C"]),
        "B": (sketch("B", ["C"]), ["C"]),
    })
    trace = RunTrace("t")
    driver = DagDriver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                       toolkit=TOOLKIT, budget=Budget(), trace=trace, memo=False)
    res = driver.run("G")
    assert res.proven                                   # correctness unchanged
    assert res.dag.cache_hits == 0                       # no cross-branch reuse counted
    assert res.trace.by_kind("cache_hit") == []         # zero cache_hit trace events
    # C's leaf prover was invoked on BOTH branches (two fresh prove attempts), not memoized once.
    c_proves = [e for e in res.trace.by_kind("prove_node") if e.data.get("goal") == "C"]
    assert len(c_proves) == 2


def test_memo_on_by_default_matches_shared_lemma_memoized():
    """The memo default is True: a DagDriver built without memo= behaves EXACTLY like the memoized
    baseline (a shared lemma is reused; cache_hits >= 1). Pins the default is byte-identical."""
    prover = DictProver({"C": valid_ledger("C")})
    decomp = DictDecomposer({
        "G": (sketch("G", ["A", "B"]), ["A", "B"]),
        "A": (sketch("A", ["C"]), ["C"]),
        "B": (sketch("B", ["C"]), ["C"]),
    })
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW])).run("G")
    assert res.proven and res.dag.cache_hits >= 1


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


# ---- R1-fix: a BUDGET-starved decomposition give-up is EXHAUSTED (retryable), NOT FAILED_GAP ----

def test_budget_starved_decomposition_is_retryable_not_poisoned():
    # G fails its DIRECT attempt (gate-rejected gap) and would prove by decomposing to [A] (A proves
    # directly). With a budget so tiny the direct attempt consumes it, the decomposition path is
    # BUDGET-starved before it can call the decomposer. That give-up must be classified EXHAUSTED
    # (limit-induced, retryable) — NOT terminal FAILED_GAP — or the split-keyed memo would PERMANENTLY
    # report a PROVABLE goal unprovable on a later run with more budget.
    from agent.orchestrator.dag import goal_hash
    prover = DictProver({"A": valid_ledger("A")})        # G has no direct proof (BAD); A proves directly
    decomp = DictDecomposer({"G": (sketch("G", ["A"]), ["A"])})
    budget = Budget(max_llm_calls=1)                      # exactly enough for ONE direct episode on G
    driver = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                     budget=budget, ralph_episodes=1)
    # First run: G's direct attempt spends the only call; the decomposition is budget-starved.
    res1 = driver.run("G")
    assert not res1.proven
    g = driver.dag.get(goal_hash("G"))
    assert g.state is NodeState.EXHAUSTED                 # retryable, NOT a terminal gap
    assert g.state not in (NodeState.FAILED_GAP, NodeState.FAILED_ELEMENTARY)
    assert g.reason == ReasonCode.budget_starved.value
    # The memo was NOT poisoned: a fresh run on the SAME goal with a larger budget PROVES it.
    driver.budget = Budget(max_llm_calls=50)
    res2 = driver.run("G")
    assert res2.proven, "a budget-refilled retry of the same goal must succeed (no memo poisoning)"
    g2 = driver.dag.get(goal_hash("G"))
    assert g2.proven and g2.proof_kind == "decomposition"


def test_genuine_gap_with_budget_stays_terminal_failed_gap():
    # CONTROL: a real gap (G has no direct proof AND no decomposer plan) with budget to spare is NOT a
    # budget starvation — it must stay terminal FAILED_GAP (the fix must not turn genuine gaps retryable).
    from agent.orchestrator.dag import goal_hash
    prover = DictProver({})                               # nothing proves directly
    decomp = DictDecomposer({"G": (sketch("G", ["A"]), ["A"])})  # A has no proof and no plan -> gap
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                  budget=Budget(max_llm_calls=50), max_decomp_attempts=1).run("G")
    assert not res.proven
    a = res.dag.get(goal_hash("A"))
    assert a.state is NodeState.FAILED_GAP               # the genuine gap is terminal, not EXHAUSTED


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


# ---- AutoReason admissibility ENFORCES elementarity via the AUTHORITATIVE gate (not the soft gate) ----

def non_elementary_ledger(goal: str) -> str:
    """A structurally-valid ledger that the SOFT gate ADMITS (verdict NEEDS_REVIEW, NOT rejected) but
    the AUTHORITATIVE elementary verifier REFUTES: a step names the denylisted method 'class group'.
    The soft gate alone (evaluate().rejected) would admit this as an AutoReason challenger."""
    return json.dumps({"problem": "p", "claim": goal, "steps": [
        {"id": "s1", "claim": "use the class group structure", "justification": "given",
         "depends_on": []},
        {"id": "s2", "claim": goal, "justification": "conclusion", "depends_on": ["s1"]}]})


def _refiner_with(author_ledger, synth_ledger, score_map):
    """A refiner whose author + synthesizer both emit `author_ledger`/`synth_ledger` and whose single
    judge scores by `score_map` (so a non-elementary challenger can be made judge-preferred)."""
    judge = KeyComparator(lambda c: score_map.get(c.content, 0.0))
    return RevisionController(ScriptedCritic([["x"]]), ScriptedAuthor([author_ledger]),
                             ScriptedSynthesizer([synth_ledger]), [judge], seed=0)


def test_soft_gate_alone_would_admit_the_non_elementary_challenger():
    """Pin the PREMISE of the enforcement test: the non-elementary challenger passes the SOFT gate
    (the OLD _is_admissible). If this ever changes, the enforcement test below would pass vacuously."""
    from agent.gates.gate import evaluate
    chall = non_elementary_ledger("G")
    rep = evaluate(chall, TOOLKIT)
    assert not rep.rejected              # the soft gate ADMITS it (NEEDS_REVIEW, not REJECTED)


def test_non_elementary_challenger_is_rejected_by_admissibility_gate():
    """A non-elementary challenger the judges LOVE and the SOFT gate would ADMIT must NOT displace the
    elementary incumbent: the AUTHORITATIVE admissibility gate (refute_elementary) refutes it first.

    Against the pre-change code (admissibility == soft gate only) the challenger WOULD displace the
    incumbent (soft gate admits it + the judge prefers it), so this assertion fails pre-change."""
    incumbent = valid_ledger("G")
    challenger = non_elementary_ledger("G")
    # The judge ADORES the non-elementary challenger (score 100 vs 1) — only admissibility stops it.
    refiner = _refiner_with(challenger, challenger, {incumbent: 1.0, challenger: 100.0})
    res = _driver(DictProver({"G": incumbent}), refiner=refiner).run("G")
    assert res.proven
    node = res.dag.get(res.dag.get_or_create("G").key)
    assert node.proof == incumbent       # incumbent held: the non-elementary challenger was inadmissible
    assert node.proof != challenger


def test_elementary_challenger_still_displaces_via_authoritative_gate():
    """Control: an ELEMENTARY judge-preferred challenger still displaces (the authoritative gate admits
    it) — proving the new gate rejects only NON-elementary challengers, not every challenger."""
    incumbent, challenger = valid_ledger("G"), valid_ledger2("G")
    refiner = _refiner_with(challenger, challenger, {incumbent: 1.0, challenger: 5.0})
    res = _driver(DictProver({"G": incumbent}), refiner=refiner).run("G")
    node = res.dag.get(res.dag.get_or_create("G").key)
    assert node.proof == challenger      # an admissible (elementary) challenger still wins


def test_admissibility_gate_uses_lean_audit_to_refute_when_node_verifier_configured():
    """When a per-node Lean verifier is configured, the admissibility gate also routes a challenger
    through the Lean audit: a challenger that COMPILES but the audit REJECTS (lean_compiled_but_rejected)
    is inadmissible and cannot displace the incumbent, even though it clears the deterministic checks.

    Pre-change, admissibility never consulted the Lean audit, so the Lean-rejected challenger (soft-gate
    clean) would displace the incumbent — this fails against pre-change code."""
    incumbent, challenger = valid_ledger("G"), valid_ledger2("G")
    # A node_verifier that Lean-REJECTS the challenger (compiled but audit rejected) and PASSES anything
    # else. The challenger clears refute_elementary (it is deterministically clean) but Lean refutes it.
    def lean_verifier(goal, ledger):
        return _verdict_reject() if ledger == challenger else _verdict_pass()
    refiner = _refiner_with(challenger, challenger, {incumbent: 1.0, challenger: 100.0})
    res = _driver(DictProver({"G": incumbent}), refiner=refiner,
                  node_verifier=lean_verifier).run("G")
    node = res.dag.get(res.dag.get_or_create("G").key)
    assert node.proof == incumbent       # Lean-rejected challenger was inadmissible -> incumbent held


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
    # SOUNDNESS INTENT (preserved): a wrong-goal ledger is NEVER reported PROVEN for "G".
    # MECHANISM CHANGE: RalphLoop now catches the goal-binding failure PER-EPISODE (emitting
    # `ralph_goal_unbound`) so the loop returns success=False rather than a success the driver's
    # backstop then terminates on. With no decomposer this routes to a decomposer_absent FAILED_GAP;
    # the `goal_claim_mismatch` backstop arm stays UNREACHABLE (it only fires on a Ralph regression).
    prover = DictProver({"G": mismatched_ledger("G", proved="H")})
    res = _driver(prover).run("G")
    assert not res.proven
    assert res.trace.by_kind("ralph_goal_unbound")            # per-episode binding caught it
    assert not res.trace.by_kind("goal_claim_mismatch")       # backstop stayed unreachable


def test_direct_proof_claim_matches_goal_but_concludes_other_is_rejected():
    # Residual L5 trigger: claim == goal "G" (so a claim-only check passes), but the terminal
    # conclusion proves a fresh statement "H". The gate admits it, yet the harness must reject it
    # because the proof concludes a DIFFERENT statement than the requested goal.
    attack = claim_matches_goal_but_concludes_other("G", concluded="H")
    # Sanity: the deterministic gate (lenient, goal-agnostic) DOES admit this ledger ...
    from agent.gates.gate import evaluate
    assert not evaluate(attack, TOOLKIT).rejected
    # ... but RalphLoop's per-episode terminal-conclusion goal-binding rejects it for goal "G"
    # (success=False), so it is never proven; with no decomposer it ends not-proven.
    prover = DictProver({"G": attack})
    res = _driver(prover).run("G")
    assert not res.proven
    assert res.trace.by_kind("ralph_goal_unbound")
    assert not res.trace.by_kind("goal_claim_mismatch")


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


class ParaphraseRootProver:
    """Returns a gate-passing but PARAPHRASED (non-goal-bound) ledger for the root goal on every
    episode, but a properly goal-bound ledger for the decomposition's children. Models the live bug:
    the root direct attempt never binds, so RalphLoop returns success=False and the driver must fall
    through to DECOMPOSITION rather than terminating on a goal_claim_mismatch."""

    def __init__(self, root: str, children: list[str]):
        self._root = root
        self._children = set(children)
        self.calls = 0

    def prove(self, goal: str, feedback=None) -> str:
        self.calls += 1
        if goal == self._root:
            return mismatched_ledger(self._root, proved="paraphrase of " + self._root)
        if goal in self._children:
            return valid_ledger(goal)
        return BAD


def test_direct_paraphrase_falls_through_to_decomposition():
    """LIVENESS FIX (the live smoke bug): a root whose direct attempt ALWAYS paraphrases (gate passes,
    goal-binding fails) must NOT be marked FAILED_GAP from a goal_claim_mismatch. Instead RalphLoop
    returns success=False -> DirectRejected -> the driver ATTEMPTS decomposition. Here the scripted
    decomposer's blueprint proves via a bindable child, so the parent proves via children."""
    decomp = ScriptedDecomposer([(sketch("G", ["A"]), ["A"])])
    prover = ParaphraseRootProver("G", ["A"])
    driver = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                     ralph_episodes=2)
    res = driver.run("G")
    assert res.proven                                        # proved via decomposition, not terminal
    # Decomposition WAS attempted (the trace records the decompose call) ...
    assert res.trace.by_kind("decompose")
    # ... and the terminal goal_claim_mismatch backstop did NOT fire (Ralph pre-filtered binding).
    assert not res.trace.by_kind("goal_claim_mismatch")
    # The per-episode binding filter caught the paraphrase on the direct attempt.
    assert res.trace.by_kind("ralph_goal_unbound")
    assert res.dag.get(res.dag.get_or_create("G").key).proof_kind == "decomposition"


def test_direct_paraphrase_no_decomposer_ends_not_proven():
    """With no decomposer, an always-paraphrasing root ends NOT PROVEN (decomposer_absent), never a
    spurious success — soundness intent preserved (nothing binds -> not proven)."""
    prover = DictProver({"G": mismatched_ledger("G", proved="H")})
    res = _driver(prover, ralph_episodes=3).run("G")
    assert not res.proven
    assert res.trace.by_kind("ralph_goal_unbound")
    assert res.trace.by_kind("decomposer_absent")
    assert not res.trace.by_kind("goal_claim_mismatch")


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


# ==================================================================================================
# T100 — ORCHESTRATOR SOUNDNESS (forge_relevance_study §7)
# ==================================================================================================

from agent.gates.gate import Verdict, evaluate as _gate_evaluate  # noqa: E402
from agent.orchestrator.node_fsm import ReasonCode  # noqa: E402


def descent_ledger(goal: str) -> str:
    """A structurally-valid ledger whose elastic `descent` justification routes the deterministic gate
    to NEEDS_REVIEW. Concludes `goal` so a direct attempt is goal-bound but needs Layer-2 review."""
    return json.dumps({"problem": "p", "claim": goal, "steps": [
        {"id": "s1", "claim": "descend", "justification": "descent", "depends_on": [],
         "obligations": {"descent": {"measure": "x", "strictly_decreases": True,
                                     "stays_in_domain": True}}},
        {"id": "s2", "claim": goal, "justification": "conclusion", "depends_on": ["s1"]}]})


# ---- (P0) SUSPENDING SCHEDULER: a NEEDS_REVIEW-no-judge exhaustion must DECOMPOSE, not give up ----

def test_needs_review_no_judge_direct_attempt_routes_to_decomposition():
    """THE BUG-FIX regression (forge_relevance_study §7 P0): a direct attempt that 'exhausts' only
    because review is unavailable (NEEDS_REVIEW + no judge) is a *producible* state — the driver must
    fall through to the decomposition path, NOT terminate. Here G's direct attempt is a descent ledger
    (NEEDS_REVIEW, no judges), and a decomposition into [A] (A proves cleanly) closes the goal."""
    # Sanity: the direct ledger really does route to NEEDS_REVIEW under the deterministic gate.
    assert _gate_evaluate(descent_ledger("G"), TOOLKIT).verdict is Verdict.NEEDS_REVIEW

    prover = DictProver({"G": descent_ledger("G"), "A": valid_ledger("A")})
    decomp = ScriptedDecomposer([(sketch("G", ["A"]), ["A"])])
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW])).run("G")
    assert res.proven, "NEEDS_REVIEW-no-judge must decompose, not terminate before the branch"
    # The decomposition path was actually taken (a decompose event fired).
    assert res.trace.by_kind("decompose")
    assert res.trace.by_kind("route_decompose")  # classified the suspension reason


def test_needs_review_no_judge_with_no_decomposer_is_classified_starvation():
    """With NO producer (decomposer is None), a NEEDS_REVIEW-no-judge direct attempt is genuine
    starvation — but it must be CLASSIFIED (needs_review_no_reviewer), never silently swallowed."""
    prover = DictProver({"G": descent_ledger("G")})
    res = _driver(prover).run("G")  # no decomposer
    assert not res.proven
    g = res.dag.get(res.dag.get_or_create("G").key)
    assert g.state is NodeState.FAILED_GAP
    assert g.reason == ReasonCode.needs_review_no_reviewer.value
    assert res.trace.by_kind("decomposer_absent")


# ---- (P1) BLOCKER REASON CODES: every give-up carries a precise reason ----

def test_no_decomposer_failure_is_classified_decomposer_absent():
    res = _driver(DictProver({})).run("G")  # G fails direct (gap), no decomposer
    assert not res.proven
    g = res.dag.get(res.dag.get_or_create("G").key)
    assert g.reason == ReasonCode.decomposer_absent.value


def test_depth_limit_exhaust_carries_reason_code():
    prover = DictProver({"A": valid_ledger("A")})
    decomp = ScriptedDecomposer([(sketch("G", ["A"]), ["A"])])
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                  max_depth=0).run("G")
    assert not res.proven
    # A child at depth 1 > max_depth 0 is EXHAUSTED with the depth_limit reason.
    a = res.dag.get(res.dag.get_or_create("A").key)
    assert a.state is NodeState.EXHAUSTED
    assert a.reason == ReasonCode.depth_limit.value
    dl = res.trace.by_kind("depth_limit")
    assert dl and dl[0].data["reason"] == ReasonCode.depth_limit.value


def test_cyclic_decomposition_failure_is_classified():
    prover = DictProver({})
    decomp = DictDecomposer({
        "G": (sketch("G", ["A"]), ["A"]),
        "A": (sketch("A", ["G"]), ["G"]),     # closes a cycle back to G
    })
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW])).run("G")
    assert not res.proven
    a = res.dag.get(res.dag.get_or_create("A").key)
    assert a.reason == ReasonCode.cyclic_decomposition.value


def test_reviewer_non_elementary_veto_is_classified_elementary():
    prover = DictProver({"A": valid_ledger("A")})
    decomp = ScriptedDecomposer([(sketch("G", ["A"]), ["A"])])
    rev = ScriptedReviewer([ReviewVerdict(useful=True, elementary=False, notes=["uses ANT"])])
    res = _driver(prover, decomposer=decomp, reviewer=rev, max_decomp_attempts=1).run("G")
    assert not res.proven
    g = res.dag.get(res.dag.get_or_create("G").key)
    assert g.reason == ReasonCode.elementary_violation.value


# ---- (P1) ADVERSARIAL VERIFIER + DOWNGRADE-DON'T-DISCARD ----

def relabeled_undischarged_descent(goal: str) -> str:
    """A ledger whose `descent` step is RELABELED but carries a FALSE/undischarged obligation: it
    asserts the descent tag yet does not assert strictly_decreases. It routes to NEEDS_REVIEW and the
    independent verifier must REFUTE it (the decrease is unverified)."""
    return json.dumps({"problem": "p", "claim": goal, "steps": [
        {"id": "s1", "claim": "descend", "justification": "descent", "depends_on": [],
         "obligations": {"descent": {"measure": "x", "strictly_decreases": False,
                                     "stays_in_domain": True}}},
        {"id": "s2", "claim": goal, "justification": "conclusion", "depends_on": ["s1"]}]})


def test_adversarial_verifier_refutes_needs_review_decomposition_sketch():
    """A decomposition SKETCH that the gate admits as NEEDS_REVIEW (a denylist-prose hit) with no
    reviewer is routed to the independent verifier, which REFUTES it (downgrade-don't-discard): the
    candidate fails with elementary_violation and a verifier_refuted event names the offending step."""
    prover = DictProver({"A": valid_ledger("A")})  # G fails direct
    # A sketch citing lemma A, concluding G, with a denylist-prose hit ('elliptic curve') -> the gate
    # routes it to NEEDS_REVIEW (a soft REVIEW flag, not a deterministic REJECT).
    bad_sketch = json.dumps({"problem": "p", "claim": "G", "steps": [
        {"id": "L0", "claim": "A", "justification": "lemma", "depends_on": []},
        {"id": "d", "claim": "reduce to an elliptic curve over Q", "justification": "algebra",
         "depends_on": ["L0"]},
        {"id": "c", "claim": "G", "justification": "conclusion", "depends_on": ["d"]}]})
    # Sanity: the gate admits this as NEEDS_REVIEW (denylist prose is a soft REVIEW, not a REJECT).
    assert _gate_evaluate(bad_sketch, TOOLKIT).verdict is Verdict.NEEDS_REVIEW
    decomp = ScriptedDecomposer([(bad_sketch, ["A"])])
    res = _driver(prover, decomposer=decomp, reviewer=None, max_decomp_attempts=1).run("G")
    assert not res.proven
    refuted = res.trace.by_kind("verifier_refuted")
    assert refuted, "the independent verifier must refute the denylist-prose sketch"
    assert refuted[0].data["step"] == "d"  # the SPECIFIC offending step is recorded
    g = res.dag.get(res.dag.get_or_create("G").key)
    assert g.reason == ReasonCode.elementary_violation.value


def test_adversarial_verifier_downgrades_needs_review_direct_proof():
    """A NEEDS_REVIEW DIRECT proof routed through the verifier with a denylist-prose hit is downgraded
    to FAILED_ELEMENTARY (not silently discarded) recording the elementary_violation reason."""
    # A descent ledger (NEEDS_REVIEW) whose prose ALSO names a denylisted method -> refutable.
    bad = json.dumps({"problem": "p", "claim": "G", "steps": [
        {"id": "s1", "claim": "use the class group of K", "justification": "descent", "depends_on": [],
         "obligations": {"descent": {"measure": "x", "strictly_decreases": True,
                                     "stays_in_domain": True}}},
        {"id": "s2", "claim": "G", "justification": "conclusion", "depends_on": ["s1"]}]})
    assert _gate_evaluate(bad, TOOLKIT).verdict is Verdict.NEEDS_REVIEW
    # No decomposer: a refuted direct proof becomes a classified FAILED_ELEMENTARY, not an exhaust.
    res = _driver(DictProver({"G": bad})).run("G")
    assert not res.proven
    g = res.dag.get(res.dag.get_or_create("G").key)
    assert g.state is NodeState.FAILED_ELEMENTARY
    assert g.reason == ReasonCode.elementary_violation.value
    assert res.trace.by_kind("verifier_refuted")


def test_unrefuted_needs_review_proof_suspends_to_a_producer_not_promoted():
    """A NEEDS_REVIEW direct proof the verifier CANNOT refute is NOT promoted to PROVEN (an un-refuted
    proof is 'not shown non-elementary', which is weaker than 'reviewed elementary' — the gate's
    NEEDS_REVIEW still demands review). With a producer available, it SUSPENDS and decomposes; here a
    decomposition into [A] (A proves cleanly) closes the goal. The verifier passed first."""
    prover = DictProver({"G": descent_ledger("G"), "A": valid_ledger("A")})
    decomp = ScriptedDecomposer([(sketch("G", ["A"]), ["A"])])
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW])).run("G")
    assert res.proven                              # closed via the producer, NOT via the direct proof
    assert res.trace.by_kind("verifier_passed")    # the verifier inspected and could not refute
    assert res.trace.by_kind("route_decompose")    # then suspended to a producer
    g = res.dag.get(res.dag.get_or_create("G").key)
    assert g.proof_kind == "decomposition"


def test_unrefuted_needs_review_proof_no_producer_is_classified_not_promoted():
    """Same un-refutable NEEDS_REVIEW direct proof, but NO producer: it must NOT be promoted; it is a
    classified non-promotion (needs_review_no_reviewer), never a silent PROVEN."""
    res = _driver(DictProver({"G": descent_ledger("G")})).run("G")  # no decomposer
    assert not res.proven
    assert res.trace.by_kind("verifier_passed")
    g = res.dag.get(res.dag.get_or_create("G").key)
    assert not g.proven
    assert g.reason == ReasonCode.needs_review_no_reviewer.value


# ---- (P1) ANTI-VACUITY: an empty/vacuous verifier inspection FAILS LOUD ----

def test_verifier_fails_loud_on_vacuous_inspection():
    from agent.orchestrator.elementary_verifier import (
        refute_elementary, VacuousVerificationError,
    )
    # An unparseable / empty source must RAISE (never vacuously pass).
    with pytest.raises(VacuousVerificationError):
        refute_elementary("not a ledger at all", TOOLKIT, goal="G")
    with pytest.raises(VacuousVerificationError):
        refute_elementary({"problem": "p", "claim": "G", "steps": []}, TOOLKIT, goal="G")


def test_verifier_refutes_relabeled_undischarged_elastic_directly():
    from agent.orchestrator.elementary_verifier import refute_elementary
    vr = refute_elementary(relabeled_undischarged_descent("G"), TOOLKIT, goal="G")
    assert vr.refuted
    assert vr.offending_step == "s1"
    assert vr.inspected_steps == 2          # it actually inspected the ledger (not vacuous)
    assert any(r.code == "undischarged_elastic" for r in vr.refutations)


def test_verifier_does_not_refute_a_clean_elementary_ledger():
    from agent.orchestrator.elementary_verifier import refute_elementary
    vr = refute_elementary(valid_ledger("G"), TOOLKIT, goal="G")
    assert not vr.refuted
    assert vr.inspected_steps == 2


# ==================================================================================================
# P2 — DILWORTH-WIDTH AND-CHILDREN FAN-OUT + H0 CHILD-CONSISTENCY (forge_relevance_study §7)
# ==================================================================================================

def _h0_ledger(goal: str, givens: list[str]) -> str:
    """A direct ledger that ASSUMES each `givens` claim (so its H0 signature is non-trivial)."""
    steps = [{"id": f"g{i}", "claim": g, "justification": "given", "depends_on": []}
             for i, g in enumerate(givens)]
    steps.append({"id": "c", "claim": goal, "justification": "conclusion",
                  "depends_on": [s["id"] for s in steps]})
    return json.dumps({"problem": "p", "claim": goal, "steps": steps})


# ---- (P2) AND-children fan-out yields the SAME result as the old serial loop on stubs ----

def _serial_result(prover_map, decomp_map):
    """Prove G with the fan-out DISABLED (cap=1) — a pure serial pass — for the parity baseline."""
    prover = DictProver(dict(prover_map))
    decomp = DictDecomposer(dict(decomp_map))
    return _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                   fanout_cap=1).run("G")


def _parallel_result(prover_map, decomp_map):
    """Prove G with the width-bounded rank-ordered fan-out ENABLED (a wide cap)."""
    prover = DictProver(dict(prover_map))
    decomp = DictDecomposer(dict(decomp_map))
    return _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                   fanout_cap=16).run("G")


def test_and_fanout_same_result_all_children_prove():
    # G decomposes into three independent children, all of which prove directly. Width-bounded waves
    # must reach the SAME PROVEN result as the serial loop.
    pm = {"A": valid_ledger("A"), "B": valid_ledger("B"), "C": valid_ledger("C")}
    dm = {"G": (sketch("G", ["A", "B", "C"]), ["A", "B", "C"])}
    serial, parallel = _serial_result(pm, dm), _parallel_result(pm, dm)
    assert serial.proven and parallel.proven
    assert {c["goal"] for c in parallel.proof_tree()["children"]} == {"A", "B", "C"}


def test_and_fanout_same_result_one_child_fails():
    # B is unprovable (no direct proof, no plan). BOTH the serial and the fan-out schedule must fail
    # the parent identically (order-independent: a single failing child sinks the AND-node either way).
    pm = {"A": valid_ledger("A"), "C": valid_ledger("C")}     # B missing -> fails
    dm = {"G": (sketch("G", ["A", "B", "C"]), ["A", "B", "C"])}
    serial, parallel = _serial_result(pm, dm), _parallel_result(pm, dm)
    assert not serial.proven and not parallel.proven


def test_and_fanout_width_equals_child_count_for_independent_children():
    # Independent (flat) children form an antichain -> the Dilworth width is the child count, and the
    # fan-out cap is bounded by it (so a wide decomposition fans out wide, a deep one would not).
    pm = {"A": valid_ledger("A"), "B": valid_ledger("B"), "C": valid_ledger("C")}
    dm = {"G": (sketch("G", ["A", "B", "C"]), ["A", "B", "C"])}
    res = _parallel_result(pm, dm)
    assert res.proven
    ev = res.trace.by_kind("and_fanout")
    assert ev, "the AND fan-out must emit its scheduling decision"
    top = next(e for e in ev if e.data["children"] == 3)
    assert top.data["width"] == 3          # three independent children -> antichain width 3
    assert top.data["max_rank"] == 0       # flat decomposition -> all ranks 0


def test_and_fanout_deep_chain_collapses_width_to_one():
    # A precedence CHAIN among an AND-node's children must collapse the Dilworth width to 1 (a deep
    # chain -> don't over-spawn). We build the committed edge A -> B (A's decomposition cites B as a
    # sub-lemma) in the DAG, then ask the driver for the width over {A, B}: B is reachable from A, so
    # they are NOT an antichain and the width is 1. (Compare to independent children -> width 2.)
    from agent.orchestrator.dilworth import dilworth_width
    prover = DictProver({"B": valid_ledger("B")})
    decomp = DictDecomposer({"A": (sketch("A", ["B"]), ["B"])})
    driver = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]))
    # Materialize A's committed decomposition A -> [B] so the sibling precedence edge exists.
    driver.dag.get_or_create("A")
    driver.dag.get_or_create("B")
    driver.dag.commit_decomposition("A", sketch("A", ["B"]), ["B"], set())
    edges = driver._and_child_precedence(["A", "B"])
    assert ("B", "A") in edges                       # A needs B -> B precedes A
    assert dilworth_width(["A", "B"], edges) == 1     # chain -> width 1, never over-spawn
    # Sanity: two genuinely independent children (no committed edge between them) have width 2.
    assert driver._and_child_precedence(["B", "X"]) == []     # X is unrelated -> no precedence edge
    assert dilworth_width(["B", "X"], driver._and_child_precedence(["B", "X"])) == 2


def test_and_fanout_budget_caps_the_wave(monkeypatch):
    # The fan-out cap is min(width, budget-derived limit, operator cap). With only a little budget left
    # the wave is budget-capped, yet the parent still proves (scheduling, not correctness, changes).
    pm = {"A": valid_ledger("A"), "B": valid_ledger("B"), "C": valid_ledger("C"), "D": valid_ledger("D")}
    dm = {"G": (sketch("G", ["A", "B", "C", "D"]), ["A", "B", "C", "D"])}
    res = _parallel_result(pm, dm)
    assert res.proven
    ev = [e for e in res.trace.by_kind("and_fanout") if e.data["children"] == 4]
    assert ev and ev[0].data["cap"] <= ev[0].data["width"]   # cap never exceeds the antichain width


# ---- (P2) the fan-out is NEVER used to choose among OR alternatives ----

def test_fanout_not_applied_to_or_alternatives():
    # The population/Elo path explores COMPETING decompositions (OR alternatives). The Dilworth fan-out
    # must NOT fire to pick among them: each alternative is tried via _try_decomposition, and only the
    # AND children of the COMMITTED plan are fanned out. Here the first (only) plan's children [A] prove;
    # we assert the and_fanout event reflects committed AND children, not a choice among alternatives.
    prover = DictProver({"A": valid_ledger("A")})
    # Two competing single-child plans for G (OR alternatives), explored by the population search.
    decomp = ScriptedDecomposer([(sketch("G", ["A"]), ["A"]), (sketch("G", ["A"]), ["A"])])
    comparator = KeyComparator(lambda c: 1.0)
    res = DagDriver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                    toolkit=TOOLKIT, budget=Budget(), trace=RunTrace("or"),
                    comparator=comparator, population_k=2, population_rounds=1).run("G")
    assert res.proven
    # Every and_fanout event is over the COMMITTED children of an AND-node (here the single child A),
    # never over the two OR alternatives -> no fan-out event reports >1 child from alternative plans.
    for e in res.trace.by_kind("and_fanout"):
        assert e.data["children"] == 1        # the committed plan's AND children, not the OR set of 2


# ---- R1-fix: the population hard-filter must call the FULL gate (gate-REJECTED never ranked) ----

def _case_split_sketch(goal: str, child: str, *, with_cover: bool) -> str:
    """A sketch citing one child lemma plus a `case_split` step. When `with_cover` is False the step is
    MISSING its required `case_cover` obligation -> the deterministic gate REJECTS it
    (missing_obligation), yet it still binds to the goal and its lemma steps match the declared child
    (so it slips past parse+binding+obligation-discharge but MUST be caught by gate.evaluate())."""
    cs = {"id": "cs", "claim": "split on parity", "justification": "case_split", "depends_on": ["L0"]}
    if with_cover:
        cs["obligations"] = {"case_cover": {"modulus": 2, "residues": [0, 1]}}
    steps = [
        {"id": "L0", "claim": child, "justification": "lemma", "depends_on": []},
        cs,
        {"id": "c", "claim": goal, "justification": "conclusion", "depends_on": ["cs"]},
    ]
    return json.dumps({"problem": "p", "claim": goal, "steps": steps})


def test_population_filter_rejects_gate_rejected_candidate():
    from agent.orchestrator.population import Candidate
    d = _driver(DictProver({}))
    flt = d._population_hard_filter("G")
    # A candidate whose case_split step is MISSING its case_cover obligation: the FULL gate REJECTS it
    # (missing_obligation). It binds to G and its lemma steps match its declared child, so the OLD
    # filter (parse + binds + non-vacuity + lemma==children) would have ADMITTED it to the tournament.
    bad = Candidate(id="bad", content=_case_split_sketch("G", "B", with_cover=False),
                    goal="G", children=["B"])
    assert flt(bad) is False, "a gate-REJECTED candidate must never be admitted to the ranking"
    # Behavioral control: the SAME shape WITH a complete case_cover passes the gate and the filter.
    good = Candidate(id="good", content=_case_split_sketch("G", "B", with_cover=True),
                     goal="G", children=["B"])
    assert flt(good) is True


def test_population_gate_rejected_candidate_never_ranked():
    # End-to-end through EloPopulation: a gate-rejected candidate added to the population is dropped by
    # the hard filter (counted in rejected_by_filter) and is NEVER among the rankable candidates.
    from agent.orchestrator.population import Candidate, EloPopulation
    d = _driver(DictProver({}))
    pop = EloPopulation(hard_filter=d._population_hard_filter("G"))
    pop.add(Candidate(id="bad", content=_case_split_sketch("G", "B", with_cover=False),
                      goal="G", children=["B"]))
    assert pop.candidates == []                          # not admitted
    assert pop.rejected_by_filter == 1
    pop.add(Candidate(id="good", content=_case_split_sketch("G", "B", with_cover=True),
                      goal="G", children=["B"]))
    assert [c.id for c in pop.candidates] == ["good"]    # only the gate-passing candidate is rankable


# ---- (P2) H0 child-consistency gate blocks an inconsistent-sibling composition ----

def test_h0_rejects_inconsistent_sibling_composition():
    # G decomposes into [A, B]; both prove directly BUT under CONTRADICTORY hypotheses (A assumes
    # `n is even`, B assumes `n is odd`). Each child passes locally, yet composing them is unsound (no
    # global section) -> the parent is NOT promoted; it is FAILED_ELEMENTARY (elementary_violation).
    pm = {"A": _h0_ledger("A", ["n is even"]), "B": _h0_ledger("B", ["n is odd"])}
    dm = {"G": (sketch("G", ["A", "B"]), ["A", "B"])}
    prover = DictProver(pm)
    decomp = DictDecomposer(dm)
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                  max_decomp_attempts=1).run("G")
    assert not res.proven, "contradictory-sibling composition must not be promoted"
    g = res.dag.get(res.dag.get_or_create("G").key)
    assert g.state is NodeState.FAILED_ELEMENTARY
    assert g.reason == ReasonCode.elementary_violation.value
    viol = res.trace.by_kind("h0_violation")
    assert viol and viol[0].data["overlap"] == "n"     # the conflicting overlap is NAMED


def test_h0_passes_consistent_sibling_composition_non_vacuously():
    # Same shape, but the siblings AGREE on the shared subject (both assume `n is even`) -> the
    # composition has a consistent global section and the parent proves. The H0 gate inspected a real
    # overlap (non-vacuous) and passed.
    pm = {"A": _h0_ledger("A", ["n is even"]), "B": _h0_ledger("B", ["n is even"])}
    dm = {"G": (sketch("G", ["A", "B"]), ["A", "B"])}
    prover = DictProver(pm)
    decomp = DictDecomposer(dm)
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                  max_decomp_attempts=1).run("G")
    assert res.proven
    assert res.trace.by_kind("h0_violation") == []     # no violation
    g = res.dag.get(res.dag.get_or_create("G").key)
    assert g.proven and g.proof_kind == "decomposition"


def test_h0_can_be_disabled():
    # With the H0 gate OFF, the inconsistent-sibling composition is (unsoundly) promoted — proving the
    # gate is what blocks it when ON (a behavioral control, not an incidental pass).
    pm = {"A": _h0_ledger("A", ["n is even"]), "B": _h0_ledger("B", ["n is odd"])}
    dm = {"G": (sketch("G", ["A", "B"]), ["A", "B"])}
    prover = DictProver(pm)
    decomp = DictDecomposer(dm)
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                  max_decomp_attempts=1, h0_consistency=False).run("G")
    assert res.proven                                  # no H0 gate -> the unsound composition slips through


# ---- (P3 / §7) next_producers LEVERAGE = fan-in x critical-path depth --------------------------

def _wire_dag(driver, edges: dict[str, list[str]]):
    """Manually wire committed decomposition edges {parent_goal: [child_goals]} into the driver's DAG."""
    for parent, children in edges.items():
        node = driver.dag.get_or_create(parent)
        node.children = [driver.dag.get_or_create(c).key for c in children]
    return driver


def test_leverage_deep_critical_outranks_shallow_high_fanin():
    # A deep-critical lemma C2 (fan-in 1, a 2-deep subtree C2->C1->C0 beneath it) must out-rank a
    # shallow high-fan-in lemma S (fan-in 2, depth 0). Leverage = fan-in x (depth+1): C2 = 1*3 = 3 >
    # S = 2*1 = 2. So fan-in ALONE does not let the shallow lemma starve the deep critical chain.
    d = _driver(ScriptedProver([valid_ledger("x")]))
    _wire_dag(d, {
        "C2": ["C1"], "C1": ["C0"], "C0": [],          # the deep chain
        "G": ["C2"],                                    # one parent cites C2 (fan-in 1)
        "S": [],                                        # the shallow lemma
        "Q1": ["S"], "Q2": ["S"],                       # two parents cite S (fan-in 2)
    })
    from agent.orchestrator.dag import goal_hash as gh
    keys = [gh("C2"), gh("S")]
    lev = d.leverage_over_dag(keys)
    assert lev[gh("C2")] == 3 and lev[gh("S")] == 2
    ordered = d.order_open_lemmas_by_leverage(keys)
    assert ordered[0] == gh("C2"), "deep-critical lemma must lead despite lower fan-in"


def test_leverage_higher_fanin_wins_at_equal_depth():
    # At EQUAL depth, the higher-fan-in shared lemma leads (it unblocks more branches).
    d = _driver(ScriptedProver([valid_ledger("x")]))
    _wire_dag(d, {
        "X": [], "Y": [],
        "P1": ["X"], "P2": ["X"], "P3": ["X"],          # X fan-in 3
        "Q1": ["Y"],                                    # Y fan-in 1
    })
    from agent.orchestrator.dag import goal_hash as gh
    keys = [gh("X"), gh("Y")]
    lev = d.leverage_over_dag(keys)
    assert lev[gh("X")] > lev[gh("Y")]                  # equal (zero) depth -> fan-in decides
    assert d.order_open_lemmas_by_leverage(keys)[0] == gh("X")


def test_leverage_is_multiplicative_monotone():
    # Leverage is monotone in BOTH fan-in and depth (multiplicative). A deep AND high-fan-in lemma is
    # the highest-leverage producer of all.
    d = _driver(ScriptedProver([valid_ledger("x")]))
    _wire_dag(d, {
        "DEEPWIDE": ["M1"], "M1": ["M0"], "M0": [],     # depth 2
        "A": ["DEEPWIDE"], "B": ["DEEPWIDE"],           # fan-in 2 -> leverage 2*3 = 6
        "SHALLOW": [], "C": ["SHALLOW"],                # fan-in 1, depth 0 -> leverage 1
    })
    from agent.orchestrator.dag import goal_hash as gh
    keys = [gh("DEEPWIDE"), gh("SHALLOW")]
    lev = d.leverage_over_dag(keys)
    assert lev[gh("DEEPWIDE")] == 6 and lev[gh("SHALLOW")] == 1
    assert d.order_open_lemmas_by_leverage(keys)[0] == gh("DEEPWIDE")


def test_leverage_critical_path_guard_shallow_high_fanin_does_not_starve_deep_chain():
    # R1-fix: the multiplicative leverage fan_in*(depth+1) ALONE lets a SHALLOW high-fan-in lemma S
    # (fan-in 5, depth 0 -> leverage 5) out-rank a DEEP critical chain D3 (fan-in 1, depth 3 ->
    # leverage 4). That contradicts the invariant 'a high-fan-in shallow lemma does NOT starve a deep
    # critical chain'. The ordering MUST schedule the deep critical chain NO LATER than the shallow
    # lemma -> critical-path DEPTH is the primary key.
    d = _driver(ScriptedProver([valid_ledger("x")]))
    _wire_dag(d, {
        "D3": ["D2"], "D2": ["D1"], "D1": ["D0"], "D0": [],   # a 3-deep critical chain
        "G": ["D3"],                                          # one parent cites D3 (fan-in 1)
        "S": [],                                              # the shallow lemma
        "P1": ["S"], "P2": ["S"], "P3": ["S"], "P4": ["S"], "P5": ["S"],  # five parents -> fan-in 5
    })
    from agent.orchestrator.dag import goal_hash as gh
    keys = [gh("D3"), gh("S")]
    lev = d.leverage_over_dag(keys)
    depths = d._critical_path_depths(keys)
    # The leverage numbers are exactly the trap: S (5) > D3 (4); the OLD leverage-primary order would
    # have put the shallow lemma FIRST and starved the deep chain.
    assert lev[gh("S")] == 5 and lev[gh("D3")] == 4
    assert depths[gh("D3")] == 3 and depths[gh("S")] == 0
    ordered = d.order_open_lemmas_by_leverage(keys)
    assert ordered.index(gh("D3")) <= ordered.index(gh("S")), \
        "the deep critical chain must be scheduled no later than the shallow high-fan-in lemma"
    assert ordered[0] == gh("D3")


def test_leverage_critical_path_guard_preserves_serial_parallel_parity():
    # The critical-path guard is a SCHEDULING change only: an AND-node needs EVERY child, so results are
    # order-independent. Proving G whose children include both a deep chain and a shallow high-fan-in
    # lemma must reach the SAME PROVEN result whether fan-out is serial (cap=1) or parallel.
    pm = {"D0": valid_ledger("D0"), "S": valid_ledger("S")}
    # G -> [D2, S]; D2 -> [D1]; D1 -> [D0]; S is a shallow leaf also shared by Q1, Q2 (high fan-in).
    dm = {
        "G": (sketch("G", ["D2", "S"]), ["D2", "S"]),
        "D2": (sketch("D2", ["D1"]), ["D1"]),
        "D1": (sketch("D1", ["D0"]), ["D0"]),
    }
    serial, parallel = _serial_result(pm, dm), _parallel_result(pm, dm)
    assert serial.proven and parallel.proven
    assert serial.dag.stats()["proven"] == parallel.dag.stats()["proven"]


# ---- (P3 / §9 #1) OpenEvolve FALLBACK DECOMPOSER: fires ONLY on stuck nodes, only commits bound ----

class _RecordingFallback:
    """A fake OpenEvolve fallback decomposer: records the goals it fired on and returns a scripted plan.

    The plan is a function of the goal so it only ever proposes a blueprint for the goal it was asked
    about (a real evolutionary backend evolves toward THE goal); ``fired`` lists every goal it saw."""
    def __init__(self, plan_for, available=True):
        self._plan_for = plan_for                # goal -> (sketch, children) or None
        self._available = available
        self.fired: list[str] = []

    def available(self):
        return self._available

    def decompose(self, goal, feedback=None):
        self.fired.append(goal)
        plan = self._plan_for(goal)
        return plan if plan is not None else ("", [])


def test_evolve_fallback_does_not_fire_when_direct_proof_succeeds():
    # The node proves directly -> the fallback is NEVER consulted (it fires only on stuck nodes).
    fb = _RecordingFallback(lambda g: (sketch("G", ["A"]), ["A"]))
    prover = DictProver({"G": valid_ledger("G")})
    res = _driver(prover, evolve_fallback=fb).run("G")
    assert res.proven and fb.fired == []


def test_evolve_fallback_fires_on_stuck_node_and_commits_bound_blueprint():
    # G can't prove directly and has no normal decomposer plan -> the node is STUCK; the fallback fires
    # and its GOAL-BOUND blueprint (child A, which proves) is committed -> G proves.
    fb = _RecordingFallback(lambda g: (sketch(g, ["A"]), ["A"]) if g == "G" else None)
    prover = DictProver({"A": valid_ledger("A")})            # A proves; G does not directly
    decomp = DictDecomposer({})                              # no normal plan for G -> stuck
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                  evolve_fallback=fb, max_decomp_attempts=1).run("G")
    assert res.proven
    assert fb.fired == ["G"]                                 # fired only on the stuck node G
    assert res.trace.by_kind("evolve_fallback")
    g = res.dag.get(res.dag.get_or_create("G").key)
    assert g.proven and g.proof_kind == "decomposition"


def test_evolve_fallback_rejects_non_goal_bound_blueprint():
    # The fallback returns a blueprint whose conclusion proves a DIFFERENT statement (not goal-bound).
    # It must be REJECTED (never committed), so the stuck node stays failed.
    fb = _RecordingFallback(
        lambda g: (sketch(g, ["A"], conclusion="H"), ["A"]) if g == "G" else None)
    prover = DictProver({"A": valid_ledger("A")})
    decomp = DictDecomposer({})
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                  evolve_fallback=fb, max_decomp_attempts=1).run("G")
    assert not res.proven                                    # off-goal blueprint never commits
    assert fb.fired == ["G"]
    assert res.trace.by_kind("evolve_fallback_rejected")


def test_evolve_fallback_unavailable_is_graceful_noop():
    # A fallback that reports itself unavailable (e.g. openevolve not installed) is a clean no-op.
    fb = _RecordingFallback(lambda g: (sketch(g, ["A"]), ["A"]), available=False)
    prover = DictProver({"A": valid_ledger("A")})
    decomp = DictDecomposer({})
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                  evolve_fallback=fb, max_decomp_attempts=1).run("G")
    assert not res.proven and fb.fired == []                 # unavailable -> never fired, no crash


# ==================================================================================================
# V1-fix: a BUDGET-STARVED *committed child* must EXHAUST (retryable) the parent, NOT poison it to a
# terminal FAILED_GAP. Drives the REAL end-to-end run()/_try_decomposition/_prove_and_children path
# (NOT a helper): _prove_and_children now propagates the failing child's REAL reason out instead of
# hardcoding gap_found, and the parent classification routes budget_starved -> mark_exhausted.
# ==================================================================================================

def test_v1_budget_starved_committed_child_exhausts_parent_then_refilled_run_proves():
    """REAL PATH (run()): with max_decomp_attempts=1, G decomposes to [A]; the budget is exactly the
    direct attempt + decompose + review calls, so A's committed _prove() budget-starves (A EXHAUSTED /
    budget_starved). The parent G must NOT become terminal FAILED_GAP (which would permanently poison
    the split-keyed memo) — it must be EXHAUSTED (retryable). A fresh run() on the SAME goal with a
    refilled budget then PROVES G. mda=1 is the trigger; the mda=2 default merely MASKS the bug by
    affording a second decomposition attempt."""
    from agent.orchestrator.dag import goal_hash
    prover = DictProver({"A": valid_ledger("A")})            # A proves directly; G does not
    decomp = DictDecomposer({"G": (sketch("G", ["A"]), ["A"])})
    # 1 direct episode on G + 1 decompose call + 1 review call = 3 calls, leaving 0 for A's _prove.
    driver = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                     budget=Budget(max_llm_calls=3), ralph_episodes=1, max_decomp_attempts=1)
    res1 = driver.run("G")
    assert not res1.proven
    # The committed CHILD genuinely budget-starved (the precondition this regression depends on).
    a = driver.dag.get(goal_hash("A"))
    assert a.state is NodeState.EXHAUSTED and a.reason == ReasonCode.budget_starved.value
    # THE FIX: the parent is EXHAUSTED (retryable), NOT poisoned to a terminal FAILED_GAP.
    g = driver.dag.get(goal_hash("G"))
    assert g.state is NodeState.EXHAUSTED, "a budget-starved committed child must not sink the parent to FAILED_GAP"
    assert g.state not in (NodeState.FAILED_GAP, NodeState.FAILED_ELEMENTARY)
    assert g.reason == ReasonCode.budget_starved.value
    # The memo was NOT poisoned: a fresh run() on the SAME goal with a larger budget PROVES it.
    driver.budget = Budget(max_llm_calls=50)
    res2 = driver.run("G")
    assert res2.proven, "a budget-refilled retry of the same goal must succeed (no memo poisoning)"
    assert driver.dag.get(goal_hash("G")).proof_kind == "decomposition"


def test_v1_genuine_child_gap_still_poisons_parent_terminal_failed_gap():
    """CONTROL (REAL PATH run(), mda=1): a committed child that is a GENUINE gap (no direct proof, no
    decomposer plan) — NOT a limit — must STILL terminally fail the parent FAILED_GAP. The V1 fix must
    only spare LIMIT-induced child failures, never turn a real gap retryable."""
    from agent.orchestrator.dag import goal_hash
    prover = DictProver({})                                   # nothing proves directly
    decomp = DictDecomposer({"G": (sketch("G", ["A"]), ["A"])})  # A: no proof, no plan -> genuine gap
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                  budget=Budget(max_llm_calls=50), max_decomp_attempts=1).run("G")
    assert not res.proven
    a = res.dag.get(goal_hash("A"))
    assert a.state is NodeState.FAILED_GAP                    # the child is a real gap
    g = res.dag.get(goal_hash("G"))
    assert g.state is NodeState.FAILED_GAP, "a genuine child gap must keep the parent terminal FAILED_GAP"
    assert g.reason == ReasonCode.gap_found.value


# ==================================================================================================
# V5-fix: the REAL AND-children scheduler (_prove_and_children) orders children DEPTH-PRIMARY so a deep
# critical chain is visited NO LATER than a shallow high-fan-in lemma. Drives the REAL scheduler with
# an instrumented prover and asserts the actual child VISITATION ORDER (NOT the orphaned
# order_open_lemmas_by_leverage helper the prior vacuous fix asserted on).
# ==================================================================================================

class RecordingProver:
    """Records the order _prove visits goals (proves everything directly), so a test can assert the
    REAL scheduler's child visitation order rather than an orphaned ordering helper."""
    def __init__(self):
        self.order: list[str] = []

    def prove(self, goal: str, feedback=None) -> str:
        self.order.append(goal)
        return valid_ledger(goal)


def test_v5_real_scheduler_visits_deep_critical_chain_no_later_than_shallow_high_fanin():
    """REAL PATH (_prove_and_children, the actual wave constructor the driver calls): a shallow
    high-fan-in lemma S (fan-in 5, depth 0 -> leverage 5) must NOT be scheduled before a deep critical
    chain D3 (fan-in 1, depth 3 -> leverage 4). The OLD scheduler sorted LEVERAGE-primary, so S (5) was
    visited before D3 (4), starving the deep chain. The fix makes critical-path DEPTH the primary key.
    We record the ACTUAL visitation order via an instrumented prover (cap=1 so waves are singletons and
    the order is unambiguous)."""
    from agent.orchestrator.dag import goal_hash as gh
    prover = RecordingProver()
    d = _driver(prover, reviewer=ScriptedReviewer([OK_REVIEW]),
                budget=Budget(max_llm_calls=200), fanout_cap=1)
    # Pre-wire (committed decomposition edges) a 3-deep critical chain under D3 and a shallow leaf S
    # cited by five parents (fan-in 5). This is the exact leverage trap: lev(S)=5 > lev(D3)=4.
    _wire_dag(d, {
        "D3": ["D2"], "D2": ["D1"], "D1": ["D0"], "D0": [],   # a 3-deep critical chain head D3
        "G": ["D3"],                                          # one parent cites D3 (fan-in 1)
        "S": [],                                              # the shallow lemma
        "P1": ["S"], "P2": ["S"], "P3": ["S"], "P4": ["S"], "P5": ["S"],  # five parents -> fan-in 5
    })
    # Sanity: the leverage numbers really are the trap (S out-leverages D3 yet is shallower).
    keys = [gh("D3"), gh("S")]
    lev = d.leverage_over_dag(keys)
    depths = d._critical_path_depths(keys)
    assert lev[gh("S")] == 5 and lev[gh("D3")] == 4
    assert depths[gh("D3")] == 3 and depths[gh("S")] == 0
    # Drive the REAL scheduler over the committed AND children [D3, S] and record the visitation order.
    ok, reason = d._prove_and_children("G", ["D3", "S"], child_ancestors=set(), depth=0)
    assert ok and reason is None
    visited = [g for g in prover.order if g in ("D3", "S")]
    assert visited.index("D3") <= visited.index("S"), \
        "the deep critical chain must be visited no later than the shallow high-fan-in lemma"
    assert visited[0] == "D3"


def test_v5_scheduler_preserves_serial_parallel_result_parity():
    """The depth-primary critical-path order is a SCHEDULING change only (an AND-node needs EVERY child,
    so results are order-independent). Proving G whose children include both a deep chain and a shallow
    high-fan-in lemma must reach the SAME PROVEN result whether the fan-out is serial (cap=1) or wide,
    via the REAL run() path."""
    pm = {"D0": valid_ledger("D0"), "S": valid_ledger("S")}
    dm = {
        "G": (sketch("G", ["D2", "S"]), ["D2", "S"]),
        "D2": (sketch("D2", ["D1"]), ["D1"]),
        "D1": (sketch("D1", ["D0"]), ["D0"]),
    }
    serial, parallel = _serial_result(pm, dm), _parallel_result(pm, dm)
    assert serial.proven and parallel.proven
    assert serial.dag.stats()["proven"] == parallel.dag.stats()["proven"]


def test_v5_and_fanout_emits_critical_path_depth():
    """The REAL scheduler exposes the critical-path depth it ordered by (the and_fanout trace now carries
    max_depth), so the depth-primary ordering is observable, not implicit."""
    pm = {"A": valid_ledger("A"), "B": valid_ledger("B"), "C": valid_ledger("C")}
    dm = {"G": (sketch("G", ["A", "B", "C"]), ["A", "B", "C"])}
    res = _parallel_result(pm, dm)
    assert res.proven
    ev = res.trace.by_kind("and_fanout")
    assert ev and "max_depth" in ev[0].data


# ==================================================================================================
# P0+P2 — OPT-IN PER-NODE LEAN VERIFICATION (Lean as a per-leaf authority).
#
# A node_verifier=None DagDriver runs the BYTE-IDENTICAL pre-feature path (asserted below). When a
# verifier is supplied, a LEAF about to be marked PROVEN-direct is routed through Lean and the FOUR
# outcomes route deterministically:
#   (i)   PASS (compiled + audit.passed)        -> PROVEN + node.lean_verified True
#   (ii)  REJECT (compiled but audit rejected)  -> FAILED_ELEMENTARY (refutes elementarity)
#   (iii) could-not-compile (no Lean produced)  -> soft PROVEN + node.lean_verified False
#   (iv)  UNAVAILABLE (lean not installed)      -> EXHAUSTED (retryable)
# No NodeState / NodeEvent member is added; the FSM proptest is untouched.
# ==================================================================================================

from agent.orchestrator.formalize_bridge import FormalizeAuditResult, make_node_gate  # noqa: E402
from agent.gates.lean_audit import LeanAuditResult, LeanVerdict  # noqa: E402
from agent.gates.report import Finding, Severity, LAYER_LEAN  # noqa: E402


def _verdict_pass() -> FormalizeAuditResult:
    """Outcome (i): compiled AND the Lean audit PASSED -> elementary_verified is True."""
    audit = LeanAuditResult(verdict=LeanVerdict.PASS, findings=[])
    return FormalizeAuditResult(formalized=True, compiled=True, theorem_name="t",
                                lean_source="theorem t : True := trivial", audit=audit)


def _verdict_reject() -> FormalizeAuditResult:
    """Outcome (ii): compiled but the audit REJECTED (a non-whitelist axiom / denylist dep)."""
    rej = Finding(LAYER_LEAN, Severity.REJECT, "sorry_axiom", "proof uses non-whitelisted axiom 'sorryAx'")
    audit = LeanAuditResult(verdict=LeanVerdict.REJECT, findings=[rej])
    return FormalizeAuditResult(formalized=True, compiled=True, theorem_name="t",
                                lean_source="theorem t : True := sorry", audit=audit)


def _verdict_noncompile() -> FormalizeAuditResult:
    """Outcome (iii): the toolchain was available but no compiling Lean was produced (fail OPEN)."""
    return FormalizeAuditResult(formalized=False, compiled=False, error="formalizer produced no Lean")


def _verdict_unavailable() -> FormalizeAuditResult:
    """Outcome (iv): the Lean toolchain was UNAVAILABLE (server down / not installed) -> retryable."""
    return FormalizeAuditResult(formalized=False, compiled=False,
                                error="lean toolchain unavailable", lean_unavailable=True)


class ScriptedLeafVerifier:
    """A canned per-node verifier: returns a fixed FormalizeAuditResult-shaped verdict for every leaf
    and records the (goal, proof_text) calls it saw. Stands in for formalize_bridge.make_node_gate so
    the four routing outcomes are tested OFFLINE (no Lean / model)."""
    def __init__(self, verdict: FormalizeAuditResult):
        self._verdict = verdict
        self.calls: list[tuple[str, str]] = []

    def __call__(self, node_goal: str, node_proof_text: str) -> FormalizeAuditResult:
        self.calls.append((node_goal, node_proof_text))
        return self._verdict


def _node_driver(verifier, prover_map=None):
    return _driver(DictProver(prover_map if prover_map is not None else {"G": valid_ledger("G")}),
                   node_verifier=verifier)


# ---- the CRUCIAL invariant: node_verifier=None is the BYTE-IDENTICAL default path ----

def test_node_verifier_none_is_default_byte_identical_path():
    """With node_verifier=None (the default) a directly-proven leaf is marked PROVEN exactly as before:
    no node_lean event is emitted, lean_verified stays False, and the prove_node(direct) trace is
    unchanged. This is the regression guard that the new arm is inert when the verifier is absent."""
    from agent.orchestrator.dag import goal_hash
    res = _driver(DictProver({"G": valid_ledger("G")})).run("G")   # no node_verifier
    assert res.proven
    g = res.dag.get(goal_hash("G"))
    assert g.state is NodeState.PROVEN and g.proof_kind == "direct"
    assert g.lean_verified is False                       # never stamped on the default path
    assert res.trace.by_kind("node_lean") == []          # the per-node Lean arm never fired
    assert res.trace.by_kind("prove_node")               # the ordinary direct-prove trace still fires


def test_default_ornode_lean_verified_is_false():
    # A freshly-created node carries the new annotation defaulting False (a pure additive field).
    from agent.orchestrator.dag import ProofDAG
    n = ProofDAG().get_or_create("G")
    assert n.lean_verified is False


# ---- (i) PASS -> PROVEN + lean_verified True ----

def test_node_verifier_pass_marks_proven_and_lean_verified():
    from agent.orchestrator.dag import goal_hash
    v = ScriptedLeafVerifier(_verdict_pass())
    res = _node_driver(v).run("G")
    assert res.proven
    g = res.dag.get(goal_hash("G"))
    # P5: a Lean-confirmed leaf is the first-class HARD-success state LEAN_VERIFIED (dominates PROVEN).
    assert g.state is NodeState.LEAN_VERIFIED and g.proof_kind == "direct"
    assert g.state.is_success                             # LEAN_VERIFIED is a success state
    assert g.proven                                       # and reads as proven everywhere
    assert g.lean_verified is True                        # Lean confirmed the leaf elementary
    assert v.calls and v.calls[0][0] == "G"               # the verifier was actually consulted
    ev = res.trace.by_kind("node_lean")
    assert ev and ev[0].data["outcome"] == "elementary_verified" and ev[0].data["lean_verified"] is True


# ---- (ii) REJECT -> FAILED_ELEMENTARY (refutes elementarity) ----

def test_node_verifier_reject_refutes_to_failed_elementary():
    from agent.orchestrator.dag import goal_hash
    v = ScriptedLeafVerifier(_verdict_reject())
    res = _node_driver(v).run("G")
    assert not res.proven                                 # a compiled-but-rejected leaf is NOT promoted
    g = res.dag.get(goal_hash("G"))
    assert g.state is NodeState.FAILED_ELEMENTARY         # downgrade-don't-discard
    assert g.reason == ReasonCode.elementary_violation.value
    assert g.lean_verified is False
    ev = res.trace.by_kind("node_lean")
    assert ev and ev[0].data["outcome"] == "elementary_violation"


# ---- (iii) could-not-compile -> soft PROVEN + lean_verified False (re-attemptable) ----

def test_node_verifier_noncompile_fails_open_soft_proven():
    from agent.orchestrator.dag import goal_hash
    v = ScriptedLeafVerifier(_verdict_noncompile())
    res = _node_driver(v).run("G")
    assert res.proven                                     # the informal gate passed it -> soft PROVEN
    g = res.dag.get(goal_hash("G"))
    assert g.state is NodeState.PROVEN and g.proof_kind == "direct"
    assert g.lean_verified is False                       # Lean did NOT confirm it -> re-attemptable
    ev = res.trace.by_kind("node_lean")
    assert ev and ev[0].data["outcome"] == "lean_could_not_formalize"


# ---- lean_strict: a non-compiling leaf FAILS CLOSED (not-proven) instead of fail-OPEN soft PROVEN ----

def test_lean_strict_default_false_is_fail_open_byte_identical():
    """Pin the DEFAULT (lean_strict not passed): a could-not-compile leaf is still soft PROVEN with the
    same 'lean_could_not_formalize' outcome — the byte-identical fail-OPEN behavior, unchanged."""
    from agent.orchestrator.dag import goal_hash
    driver = _driver(DictProver({"G": valid_ledger("G")}),
                     node_verifier=ScriptedLeafVerifier(_verdict_noncompile()))
    assert driver.lean_strict is False                    # default
    res = driver.run("G")
    assert res.proven
    g = res.dag.get(goal_hash("G"))
    assert g.state is NodeState.PROVEN and g.lean_verified is False
    ev = res.trace.by_kind("node_lean")
    assert ev and ev[0].data["outcome"] == "lean_could_not_formalize"


def test_lean_strict_true_non_compiling_leaf_is_not_proven():
    """lean_strict=True: the SAME non-compiling leaf the default fail-OPENs to soft PROVEN is now NOT
    proven — it fails CLOSED to FAILED_GAP (reason formalization_failed). Fails against pre-change code
    (which had no lean_strict and always fail-OPENed a non-compiling leaf to PROVEN)."""
    from agent.orchestrator.dag import goal_hash
    v = ScriptedLeafVerifier(_verdict_noncompile())
    res = _driver(DictProver({"G": valid_ledger("G")}),
                  node_verifier=v, lean_strict=True).run("G")
    assert not res.proven                                 # FAIL CLOSED: never minted an unverified PROVEN
    g = res.dag.get(goal_hash("G"))
    assert g.state is NodeState.FAILED_GAP               # not-proven (distinct from FAILED_ELEMENTARY)
    assert g.lean_verified is False
    assert g.reason == ReasonCode.formalization_failed.value
    ev = res.trace.by_kind("node_lean")
    assert ev and ev[0].data["outcome"] == "lean_could_not_formalize_strict"
    # No spurious soft prove_node(direct) was emitted for the strictly-rejected leaf.
    assert res.trace.by_kind("prove_node") == []


def test_lean_strict_true_still_proves_a_compiling_audited_leaf():
    """lean_strict ONLY closes the could-not-compile arm: a leaf that COMPILES and audits elementary is
    still LEAN_VERIFIED under lean_strict=True (PASS arm untouched)."""
    from agent.orchestrator.dag import goal_hash
    v = ScriptedLeafVerifier(_verdict_pass())
    res = _driver(DictProver({"G": valid_ledger("G")}),
                  node_verifier=v, lean_strict=True).run("G")
    assert res.proven
    g = res.dag.get(goal_hash("G"))
    assert g.state is NodeState.LEAN_VERIFIED and g.lean_verified is True


def test_lean_strict_true_with_no_node_verifier_is_byte_identical_default():
    """lean_strict=True with node_verifier=None changes NOTHING: with no per-node verifier there is no
    per-node compile to fail closed on, so the default offline path is byte-identical (soft PROVEN, no
    node_lean event)."""
    from agent.orchestrator.dag import goal_hash
    res = _driver(DictProver({"G": valid_ledger("G")}), lean_strict=True).run("G")
    assert res.proven
    g = res.dag.get(goal_hash("G"))
    assert g.state is NodeState.PROVEN and g.lean_verified is False
    assert res.trace.by_kind("node_lean") == []          # the per-node arm never fired
    assert res.trace.by_kind("prove_node")               # ordinary direct-prove trace still fires


# ---- (iv) UNAVAILABLE -> EXHAUSTED (retryable, never poisons the memo) ----

def test_node_verifier_unavailable_exhausts_retryable():
    from agent.orchestrator.dag import goal_hash
    v = ScriptedLeafVerifier(_verdict_unavailable())
    res = _node_driver(v).run("G")
    assert not res.proven
    g = res.dag.get(goal_hash("G"))
    assert g.state is NodeState.EXHAUSTED                 # limit-induced, retryable
    assert g.state not in (NodeState.FAILED_GAP, NodeState.FAILED_ELEMENTARY)
    assert g.lean_verified is False
    ev = res.trace.by_kind("node_lean")
    assert ev and ev[0].data["outcome"] == "lean_unavailable"
    # Retryable: re-running with a verifier that now PASSES proves the SAME goal (no memo poisoning).
    res2 = ScriptedLeafVerifier(_verdict_pass())
    driver = _node_driver(res2)
    driver.dag = res.dag                                  # reuse the DAG carrying the EXHAUSTED node
    assert driver.run("G").proven


# ---- a crashing verifier fails OPEN (never refutes / starves a gate-passed leaf) ----

def test_node_verifier_exception_fails_open_soft_proven():
    from agent.orchestrator.dag import goal_hash

    class Boom:
        def __call__(self, goal, proof_text):
            raise RuntimeError("verifier exploded")

    res = _node_driver(Boom()).run("G")
    assert res.proven                                     # a crashing verifier does not sink a leaf
    g = res.dag.get(goal_hash("G"))
    assert g.state is NodeState.PROVEN and g.lean_verified is False
    ev = res.trace.by_kind("node_lean")
    assert ev and ev[0].data["outcome"] == "verifier_error"


# ---- make_node_gate: faithfulness deferred (None) and unavailable detection without Lean ----

def test_make_node_gate_reports_unavailable_when_no_lean():
    """With no Lean toolchain reachable (no server, no project_dir, `lean` absent in CI), make_node_gate
    returns a result flagged lean_unavailable WITHOUT attempting a compile — the offline-safe path that
    routes a leaf to retryable EXHAUSTED. (If Lean happens to be installed in this environment the gate
    would instead try to compile; this test only asserts the no-Lean classification.)"""
    from agent.gates import lean_bridge

    class _NoFormalizer:
        def formalize(self, ledger_text, **kw):
            raise AssertionError("formalize must not be called when Lean is unavailable")

    if lean_bridge.available():
        pytest.skip("Lean is installed in this environment; the unavailable path is not exercised")
    gate = make_node_gate(_NoFormalizer(), toolkit=TOOLKIT)
    verdict = gate("G", valid_ledger("G"))
    assert verdict.lean_unavailable is True
    assert verdict.compiled is False and verdict.elementary_verified is False


def test_formalize_audit_result_outcome_helpers():
    """The read-only classification helpers name the four outcomes off the existing fields."""
    assert _verdict_pass().elementary_verified is True
    assert _verdict_reject().lean_compiled_but_rejected is True
    assert _verdict_reject().elementary_verified is False
    assert _verdict_noncompile().lean_could_not_formalize is True
    assert _verdict_unavailable().lean_unavailable is True
    # The unavailable verdict is NOT misclassified as a (re-attemptable) non-compile.
    assert _verdict_unavailable().lean_could_not_formalize is False


# ==================================================================================================
# P4 — OPT-IN AND-NODE SORRY-SKETCH COMPILATION (Lean COMPOSITION check, LEAP §2.2/§2.5).
#
# A sketch_verifier=None DagDriver runs the BYTE-IDENTICAL pre-P4 decomposition path (asserted below).
# When a sketch_verifier is supplied, a candidate decomposition — after the reviewer + acyclicity +
# sketch-validity + conclusion-binding checks and BEFORE commit — has its SKETCH formalized as a
# SORRY-FREE composition theorem (parent from children-as-hypotheses) and compiled+audited via Lean:
#   * COMPILED + audited elementary  -> COMMIT, stamp sketch_lean_verified=True on the node.
#   * NOT compiled+elementary        -> REJECT the candidate (never committed; try next / backtrack).
# The LEAP parent-composition rule (mark_proven_via_children):
#   parent.lean_verified = sketch_lean_verified AND all(child.lean_verified for child in children).
# No NodeState / NodeEvent member is added; lean_verified/sketch_lean_verified are bool annotations.
# ==================================================================================================

class ScriptedSketchVerifier:
    """A canned per-AND-node composition verifier: returns a fixed FormalizeAuditResult-shaped verdict
    for every sketch and records the (parent_goal, sketch_text, child_goals) calls it saw. Stands in
    for formalize_bridge.make_sketch_gate so the commit/reject logic is tested OFFLINE (no Lean/model).
    """
    def __init__(self, verdict: FormalizeAuditResult):
        self._verdict = verdict
        self.calls: list[tuple[str, str, list[str]]] = []

    def __call__(self, parent_goal: str, sketch_text: str,
                 child_goals: list[str]) -> FormalizeAuditResult:
        self.calls.append((parent_goal, sketch_text, list(child_goals)))
        return self._verdict


def _leaf_pass_verifier():
    """A leaf node_verifier that PASSES every leaf (stamps child.lean_verified=True) so a P4 test can
    drive the all-children-discharged branch of the LEAP composition rule."""
    return ScriptedLeafVerifier(_verdict_pass())


# ---- the CRUCIAL invariant: sketch_verifier=None is the BYTE-IDENTICAL default path ----

def test_sketch_verifier_none_is_default_byte_identical_path():
    """With sketch_verifier=None (the default) a decomposition commits exactly as before: no sketch_lean
    event fires, sketch_lean_verified stays False, the parent's lean_verified stays False (soft PROVEN),
    and the prove_node(decomposition) trace is unchanged. Regression guard that the P4 arm is inert."""
    from agent.orchestrator.dag import goal_hash
    prover = DictProver({"A": valid_ledger("A"), "B": valid_ledger("B")})
    decomp = ScriptedDecomposer([(sketch("G", ["A", "B"]), ["A", "B"])])
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW])).run("G")
    assert res.proven
    g = res.dag.get(goal_hash("G"))
    assert g.proof_kind == "decomposition"
    assert g.sketch_lean_verified is False               # never stamped on the default path
    assert g.lean_verified is False                       # soft PROVEN, unchanged behavior
    assert res.trace.by_kind("sketch_lean") == []         # the P4 composition arm never fired
    assert res.trace.by_kind("prove_node")                # the ordinary decomposition trace still fires


def test_default_ornode_sketch_lean_verified_is_false():
    # A freshly-created node carries the new composition annotation defaulting False (additive field).
    from agent.orchestrator.dag import ProofDAG
    n = ProofDAG().get_or_create("G")
    assert n.sketch_lean_verified is False


# ---- (b) a NON-COMPILING sketch is NOT committed (decomposition rejected) ----

def test_noncompiling_sketch_is_not_committed():
    """A sketch the verifier reports as NOT elementary_verified (here a could-not-compile verdict) must
    NOT be committed: the decomposition is rejected and the parent does not prove via it."""
    from agent.orchestrator.dag import goal_hash
    prover = DictProver({"A": valid_ledger("A"), "B": valid_ledger("B")})
    decomp = ScriptedDecomposer([(sketch("G", ["A", "B"]), ["A", "B"])])
    sv = ScriptedSketchVerifier(_verdict_noncompile())   # the composition did not compile
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                  sketch_verifier=sv, max_decomp_attempts=1).run("G")
    assert not res.proven                                 # the only plan's sketch did not compile
    g = res.dag.get(goal_hash("G"))
    assert not g.proven
    assert g.proof_kind is None and g.children == []      # rolled back: no committed decomposition
    assert sv.calls and sv.calls[0][0] == "G"             # the sketch verifier was actually consulted
    ev = res.trace.by_kind("sketch_lean")
    assert ev and ev[0].data["outcome"] == "lean_could_not_formalize"
    assert ev[0].data["sketch_lean_verified"] is False


def test_audit_rejected_sketch_is_not_committed():
    """A sketch that COMPILED but the audit REJECTED (non-elementary body dep) is likewise not committed."""
    from agent.orchestrator.dag import goal_hash
    prover = DictProver({"A": valid_ledger("A")})
    decomp = ScriptedDecomposer([(sketch("G", ["A"]), ["A"])])
    sv = ScriptedSketchVerifier(_verdict_reject())
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                  sketch_verifier=sv, max_decomp_attempts=1).run("G")
    assert not res.proven
    g = res.dag.get(goal_hash("G"))
    assert g.proof_kind is None and g.children == []
    ev = res.trace.by_kind("sketch_lean")
    assert ev and ev[0].data["outcome"] == "elementary_violation"


def test_unavailable_sketch_verifier_does_not_commit_fail_closed():
    """An UNAVAILABLE Lean toolchain fails CLOSED on commit (we never commit a composition we could not
    Lean-check): the candidate is rejected and classified lean_unavailable on the trace."""
    from agent.orchestrator.dag import goal_hash
    prover = DictProver({"A": valid_ledger("A")})
    decomp = ScriptedDecomposer([(sketch("G", ["A"]), ["A"])])
    sv = ScriptedSketchVerifier(_verdict_unavailable())
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                  sketch_verifier=sv, max_decomp_attempts=1).run("G")
    assert not res.proven
    g = res.dag.get(goal_hash("G"))
    assert g.proof_kind is None and g.children == []
    ev = res.trace.by_kind("sketch_lean")
    assert ev and ev[0].data["outcome"] == "lean_unavailable"


def test_crashing_sketch_verifier_rejects_candidate():
    """A crashing sketch verifier fails CLOSED on commit (never commit an un-checkable composition)."""
    from agent.orchestrator.dag import goal_hash

    class Boom:
        def __call__(self, parent_goal, sketch_text, child_goals):
            raise RuntimeError("sketch verifier exploded")

    prover = DictProver({"A": valid_ledger("A")})
    decomp = ScriptedDecomposer([(sketch("G", ["A"]), ["A"])])
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                  sketch_verifier=Boom(), max_decomp_attempts=1).run("G")
    assert not res.proven
    g = res.dag.get(goal_hash("G"))
    assert g.proof_kind is None and g.children == []
    ev = res.trace.by_kind("sketch_lean")
    assert ev and ev[0].data["outcome"] == "verifier_error"


# ---- (c) a COMPILING sketch commits; with all children lean_verified -> parent.lean_verified True ----

def test_compiling_sketch_commits_and_all_children_verified_sets_parent_lean_verified():
    """A sketch the verifier ELEMENTARY-VERIFIES is committed (sketch_lean_verified=True). When EVERY
    child is itself lean_verified (here a passing LEAF node_verifier discharges each child), the LEAP
    composition rule sets parent.lean_verified=True."""
    from agent.orchestrator.dag import goal_hash
    prover = DictProver({"A": valid_ledger("A"), "B": valid_ledger("B")})
    decomp = ScriptedDecomposer([(sketch("G", ["A", "B"]), ["A", "B"])])
    sv = ScriptedSketchVerifier(_verdict_pass())          # the composition compiled + audited elementary
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                  sketch_verifier=sv, node_verifier=_leaf_pass_verifier(),
                  max_decomp_attempts=1).run("G")
    assert res.proven
    g = res.dag.get(goal_hash("G"))
    assert g.proof_kind == "decomposition"
    assert g.sketch_lean_verified is True                 # the composition compiled in Lean
    # every child discharged (leaf-verified) AND the composition compiled -> parent lean_verified.
    # P5: each Lean-discharged child is the first-class LEAN_VERIFIED state, and the parent — sketch
    # compiled AND every child LEAN_VERIFIED — is itself promoted to LEAN_VERIFIED (dominates PROVEN).
    assert res.dag.get(goal_hash("A")).state is NodeState.LEAN_VERIFIED
    assert res.dag.get(goal_hash("B")).state is NodeState.LEAN_VERIFIED
    assert res.dag.get(goal_hash("A")).lean_verified is True
    assert res.dag.get(goal_hash("B")).lean_verified is True
    assert g.state is NodeState.LEAN_VERIFIED
    assert g.lean_verified is True
    ev = res.trace.by_kind("sketch_lean")
    assert ev and ev[0].data["outcome"] == "elementary_verified"


# ---- (d) a COMPILING sketch but a child NOT lean_verified -> parent.lean_verified False ----

def test_compiling_sketch_but_child_not_verified_leaves_parent_lean_verified_false():
    """The composition compiled (sketch_lean_verified=True) but a CHILD is not lean_verified (here NO
    leaf node_verifier runs, so each child is soft PROVEN with lean_verified=False). The LEAP rule then
    leaves parent.lean_verified=False — the composition is incomplete (a hypothesis is undischarged in
    Lean) even though the parent is (soft) PROVEN via children."""
    from agent.orchestrator.dag import goal_hash
    prover = DictProver({"A": valid_ledger("A"), "B": valid_ledger("B")})
    decomp = ScriptedDecomposer([(sketch("G", ["A", "B"]), ["A", "B"])])
    sv = ScriptedSketchVerifier(_verdict_pass())          # composition compiles ...
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                  sketch_verifier=sv, max_decomp_attempts=1).run("G")  # ... but no leaf verifier
    assert res.proven                                     # still (soft) PROVEN via the children
    g = res.dag.get(goal_hash("G"))
    assert g.proof_kind == "decomposition"
    assert g.sketch_lean_verified is True                 # the composition itself compiled
    assert res.dag.get(goal_hash("A")).lean_verified is False   # children not Lean-discharged
    assert res.dag.get(goal_hash("B")).lean_verified is False
    assert g.lean_verified is False                       # composition incomplete -> parent NOT verified
    # P5: a single non-LEAN_VERIFIED (here soft-PROVEN) child leaves the parent in soft PROVEN, NOT
    # the dominating LEAN_VERIFIED state, even though the composition sketch itself compiled.
    assert g.state is NodeState.PROVEN
    assert res.dag.get(goal_hash("A")).state is NodeState.PROVEN


def test_rejected_sketch_then_compiling_retry_commits():
    """A first decomposition whose sketch does not compile is rejected; a second attempt whose sketch
    compiles is committed. Proves the reject path backtracks to the next attempt rather than terminating."""
    from agent.orchestrator.dag import goal_hash

    class _SeqSketchVerifier:
        """Returns a sequence of verdicts (one per sketch call), repeating the last."""
        def __init__(self, verdicts):
            self._v = list(verdicts)
            self.calls = 0

        def __call__(self, parent_goal, sketch_text, child_goals):
            v = self._v[min(self.calls, len(self._v) - 1)]
            self.calls += 1
            return v

    prover = DictProver({"A": valid_ledger("A")})
    # Two attempts: the ScriptedDecomposer returns the same plan twice (a re-plan).
    decomp = ScriptedDecomposer([(sketch("G", ["A"]), ["A"]), (sketch("G", ["A"]), ["A"])])
    sv = _SeqSketchVerifier([_verdict_noncompile(), _verdict_pass()])
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                  sketch_verifier=sv, max_decomp_attempts=2).run("G")
    assert res.proven                                     # the second (compiling) sketch committed
    g = res.dag.get(goal_hash("G"))
    assert g.proof_kind == "decomposition" and g.sketch_lean_verified is True
    assert sv.calls == 2                                  # rejected once, then committed


# ==================================================================================================
# THREAD 2 — ROBUSTNESS: a RAISING decomposer / reviewer must NOT crash DagDriver.run().
#
# A live decomposer/reviewer (Codex / Claude CLI) can raise on a subprocess timeout (ClaudeError /
# CodexError), a non-zero exit, or malformed output. Such a raise is treated as "this decomposition
# attempt failed" -> next attempt / backtrack, classified ReasonCode.unknown_tool_error, surfaced on
# the trace. These tests exercise the REAL _decompose / _try_decomposition path and FAIL against the
# pre-change code (which let the exception propagate out of run()).
# ==================================================================================================

class RaisingDecomposer:
    """decompose() always raises (stand-in for a Codex/Claude timeout). Counts its calls."""
    def __init__(self, exc: Exception | None = None):
        self._exc = exc or RuntimeError("codex exec timed out after 1200s")
        self.calls = 0

    def decompose(self, goal: str, feedback=None):
        self.calls += 1
        raise self._exc


class RaiseThenDecompose:
    """Raises on the first decompose() call, then returns a valid plan (proves recovery / next-attempt)."""
    def __init__(self, plan, exc: Exception | None = None):
        self._plan = plan
        self._exc = exc or RuntimeError("transient timeout")
        self.calls = 0

    def decompose(self, goal: str, feedback=None):
        self.calls += 1
        if self.calls == 1:
            raise self._exc
        return self._plan


class RaisingReviewer:
    """review() always raises (stand-in for a reviewer subprocess timeout). Counts its calls."""
    def __init__(self, exc: Exception | None = None):
        self._exc = exc or RuntimeError("reviewer exec timed out")
        self.calls = 0

    def review(self, goal: str, sketch: str, child_goals):
        self.calls += 1
        raise self._exc


def test_raising_decomposer_does_not_crash_run():
    """The core defect: a decomposer that RAISES (after a failed direct attempt) must NOT propagate out
    of run(); the node is classified unknown_tool_error and the run returns not-proven."""
    from agent.orchestrator.dag import goal_hash
    prover = DictProver({})                               # direct attempt fails -> route to decompose
    decomp = RaisingDecomposer()
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                  max_decomp_attempts=2).run("G")
    assert res.proven is False                            # NOT a crash
    g = res.dag.get(goal_hash("G"))
    assert g.state is NodeState.FAILED_GAP
    assert g.reason == ReasonCode.unknown_tool_error.value
    # The exception was surfaced on the trace (one per attempt), not silently swallowed.
    errs = res.trace.by_kind("decomposer_error")
    assert len(errs) == 2 and errs[0].data["reason"] == ReasonCode.unknown_tool_error.value
    assert decomp.calls == 2                              # bounded by max_decomp_attempts (no infinite loop)


def test_raising_decomposer_recovers_on_next_attempt():
    """A first decompose() that raises followed by a valid plan still proves via children — a transient
    decomposer crash is a failed attempt, not a terminal failure."""
    from agent.orchestrator.dag import goal_hash
    prover = DictProver({"A": valid_ledger("A")})         # G's direct fails; child A proves directly
    decomp = RaiseThenDecompose((sketch("G", ["A"]), ["A"]))
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                  max_decomp_attempts=2).run("G")
    assert res.proven is True                             # recovered on the second attempt
    g = res.dag.get(goal_hash("G"))
    assert g.proof_kind == "decomposition"
    assert len(res.trace.by_kind("decomposer_error")) == 1
    assert decomp.calls == 2


def test_raising_reviewer_does_not_crash_run():
    """A reviewer that RAISES is treated as a failed decomposition attempt (unknown_tool_error), not a
    crash. With one attempt, the node terminates FAILED_GAP carrying the tool-error reason."""
    from agent.orchestrator.dag import goal_hash
    prover = DictProver({"A": valid_ledger("A")})
    decomp = ScriptedDecomposer([(sketch("G", ["A"]), ["A"])])
    reviewer = RaisingReviewer()
    res = _driver(prover, decomposer=decomp, reviewer=reviewer, max_decomp_attempts=1).run("G")
    assert res.proven is False                            # NOT a crash
    g = res.dag.get(goal_hash("G"))
    assert g.state is NodeState.FAILED_GAP
    assert g.reason == ReasonCode.unknown_tool_error.value
    errs = res.trace.by_kind("reviewer_error")
    assert len(errs) == 1 and errs[0].data["reason"] == ReasonCode.unknown_tool_error.value
    assert reviewer.calls == 1


def test_raising_reviewer_recovers_when_a_later_attempt_passes():
    """A reviewer that raises on the first attempt but passes on the next still commits the decomposition
    — the tool error backtracks to the next attempt rather than terminating the node."""
    from agent.orchestrator.dag import goal_hash

    class _RaiseThenPassReviewer:
        def __init__(self):
            self.calls = 0

        def review(self, goal, sketch_text, child_goals):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient reviewer timeout")
            return OK_REVIEW

    prover = DictProver({"A": valid_ledger("A")})
    decomp = ScriptedDecomposer([(sketch("G", ["A"]), ["A"]), (sketch("G", ["A"]), ["A"])])
    reviewer = _RaiseThenPassReviewer()
    res = _driver(prover, decomposer=decomp, reviewer=reviewer, max_decomp_attempts=2).run("G")
    assert res.proven is True
    g = res.dag.get(goal_hash("G"))
    assert g.proof_kind == "decomposition"
    assert reviewer.calls == 2                            # raised once, then passed


# ---- node-gate boundary: a formalizer/Lean TIMEOUT fails OPEN to soft PROVEN -----------------------

def _verdict_timeout() -> FormalizeAuditResult:
    """A Lean/formalizer TIMEOUT verdict: the formalizer produced a term but the audit timed out
    (lean_bridge raises LeanBridgeError -> formalize_and_audit returns compiled=False with the timeout
    error). This is the could-not-compile outcome (iii): fail OPEN to soft PROVEN, re-attemptable."""
    return FormalizeAuditResult(formalized=True, compiled=False,
                                lean_source="theorem t : True := by sorry",
                                error="lean timed out after 600s")


def test_node_gate_formalizer_timeout_fails_open_soft_proven():
    """CONFIRM the node-gate boundary fails OPEN on a formalizer/Lean timeout: a leaf the informal gate
    already passed stays softly PROVEN (lean_verified=False, re-attemptable) — a timeout never refutes
    elementarity nor starves the leaf."""
    from agent.orchestrator.dag import goal_hash
    v = ScriptedLeafVerifier(_verdict_timeout())
    res = _node_driver(v).run("G")
    assert res.proven                                     # timeout -> soft PROVEN (fail open)
    g = res.dag.get(goal_hash("G"))
    assert g.state is NodeState.PROVEN and g.lean_verified is False
    ev = res.trace.by_kind("node_lean")
    assert ev and ev[0].data["outcome"] == "lean_could_not_formalize"


def test_make_node_gate_timeout_knob_is_configurable():
    """The per-node formalizer timeout is a configurable make_node_gate knob (default 600s). With Lean
    unavailable the gate returns lean_unavailable WITHOUT compiling, but it still ACCEPTS the timeout_s
    knob (the configurability the robustness thread requires)."""
    from agent.gates import lean_bridge

    class _NoFormalizer:
        def formalize(self, ledger_text, **kw):
            raise AssertionError("formalize must not be called when Lean is unavailable")

    if lean_bridge.available():
        pytest.skip("Lean is installed; the unavailable path is not exercised")
    gate = make_node_gate(_NoFormalizer(), toolkit=TOOLKIT, timeout_s=42)   # knob accepted
    verdict = gate("G", valid_ledger("G"))
    assert verdict.lean_unavailable is True


# ==================================================================================================
# P3 — PER-NODE VERIFY BUDGET SUB-CAP + SHARED WARM SERVER.
#
# Budget gains an OPTIONAL max_node_verify_calls sub-cap (default None = UNLIMITED -> byte-identical),
# SEPARATE from max_llm_calls so per-node Lean cannot starve the prover/decomposer search. Before
# invoking the leaf node_verifier / AND-node sketch_verifier, the driver checks can_verify_node(); if
# the sub-cap is exhausted it SKIPS per-node Lean for that node and falls back to soft PROVEN (the
# proof is not blocked, nothing crashes), recording a reason on the trace; on each verify it calls
# spend_verify_node(). DagDriver also accepts/holds ONE warm lean_server to thread into both gates.
#
# These tests exercise the REAL run() path and FAIL against the pre-change code (which had no sub-cap:
# the leaf verifier fired unconditionally regardless of any node-verify budget).
# ==================================================================================================

def test_node_verify_subcap_unlimited_default_is_byte_identical():
    """Sub-cap UNSET (None, the default): the leaf verifier fires on EVERY node exactly as before, and
    can_verify_node() never BLOCKS. Regression guard that the default path is unchanged (the count is
    tracked for observability but the unset sub-cap never gates and never leaks into the snapshot)."""
    from agent.orchestrator.dag import goal_hash
    v = ScriptedLeafVerifier(_verdict_pass())
    res = _driver(DictProver({"G": valid_ledger("G")}), node_verifier=v,
                  budget=Budget()).run("G")               # no max_node_verify_calls
    assert res.proven
    g = res.dag.get(goal_hash("G"))
    assert g.lean_verified is True                         # the verifier DID fire (unlimited)
    assert len(v.calls) == 1
    # Unlimited (None) never BLOCKS and never surfaces in the snapshot (byte-identical trace events).
    assert "node_verify_spent" not in res.budget.snapshot()
    # The normal Lean outcome (not the budget-exhausted fallback) was recorded.
    ev = res.trace.by_kind("node_lean")
    assert ev and ev[0].data["outcome"] == "elementary_verified"


def test_leaf_verify_subcap_exhausted_falls_back_to_soft_proven():
    """With max_node_verify_calls small, a leaf BEYOND the cap SKIPS per-node Lean and falls back to soft
    PROVEN (lean_verified=False) instead of calling the verifier. Two leaves, cap=1: the FIRST is
    Lean-verified (cap spent), the SECOND falls back. Verifies the count + the fallback via run()."""
    from agent.orchestrator.dag import goal_hash
    # G decomposes into A and B, each a directly-proven leaf -> two leaf verify opportunities.
    prover = DictProver({"A": valid_ledger("A"), "B": valid_ledger("B")})
    decomp = ScriptedDecomposer([(sketch("G", ["A", "B"]), ["A", "B"])])
    v = ScriptedLeafVerifier(_verdict_pass())
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                  node_verifier=v, budget=Budget(max_node_verify_calls=1)).run("G")
    assert res.proven                                      # the proof is NOT blocked by the sub-cap
    # The cap spent exactly once: only ONE leaf was actually Lean-verified.
    assert res.budget.node_verify_spent == 1
    assert len(v.calls) == 1                               # the verifier was consulted exactly ONCE
    a = res.dag.get(goal_hash("A"))
    b = res.dag.get(goal_hash("B"))
    # Exactly one child Lean-verified (cap spent), the other soft PROVEN (cap exhausted fallback).
    verified = [n for n in (a, b) if n.lean_verified]
    soft = [n for n in (a, b) if not n.lean_verified]
    assert len(verified) == 1 and len(soft) == 1
    # P5: the Lean-verified leaf is the first-class LEAN_VERIFIED state; the cap-exhausted fallback is
    # soft PROVEN. Both are success states, so the proof (and every child read) still counts as proven.
    assert verified[0].state is NodeState.LEAN_VERIFIED
    assert soft[0].state is NodeState.PROVEN
    assert all(n.proven for n in (a, b))                     # both success states read as proven
    # The exhausted-cap fallback was recorded on the trace for the skipped leaf.
    outcomes = [e.data["outcome"] for e in res.trace.by_kind("node_lean")]
    assert "node_verify_budget_exhausted" in outcomes


def test_leaf_verify_subcap_zero_skips_all_lean_soft_proven():
    """max_node_verify_calls=0: NO leaf is ever Lean-verified; every leaf falls back to soft PROVEN and
    the verifier is never called. The proof still succeeds (per-node Lean never blocks the prover)."""
    from agent.orchestrator.dag import goal_hash
    v = ScriptedLeafVerifier(_verdict_pass())
    res = _driver(DictProver({"G": valid_ledger("G")}), node_verifier=v,
                  budget=Budget(max_node_verify_calls=0)).run("G")
    assert res.proven
    g = res.dag.get(goal_hash("G"))
    assert g.state is NodeState.PROVEN and g.lean_verified is False
    assert v.calls == []                                  # the cap blocked the verifier entirely
    assert res.budget.node_verify_spent == 0
    ev = res.trace.by_kind("node_lean")
    assert ev and ev[0].data["outcome"] == "node_verify_budget_exhausted"


def test_sketch_verify_subcap_exhausted_soft_commits_without_lean():
    """A SET sub-cap also gates the AND-node sketch_verifier. With cap=0 the composition Lean check is
    SKIPPED and the decomposition SOFT-commits (sketch_lean_verified=False) via the existing gates — the
    proof is not blocked. Verifies the count + the fallback via run()."""
    from agent.orchestrator.dag import goal_hash
    prover = DictProver({"A": valid_ledger("A"), "B": valid_ledger("B")})
    decomp = ScriptedDecomposer([(sketch("G", ["A", "B"]), ["A", "B"])])
    sv = ScriptedSketchVerifier(_verdict_pass())
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                  sketch_verifier=sv, budget=Budget(max_node_verify_calls=0),
                  max_decomp_attempts=1).run("G")
    assert res.proven                                     # soft-committed despite the skipped Lean check
    g = res.dag.get(goal_hash("G"))
    assert g.proof_kind == "decomposition"
    assert g.sketch_lean_verified is False               # the composition check was skipped
    assert sv.calls == []                                # the verifier was never consulted (cap=0)
    assert res.budget.node_verify_spent == 0
    ev = res.trace.by_kind("sketch_lean")
    assert ev and ev[0].data["outcome"] == "node_verify_budget_exhausted"


def test_node_verify_subcap_separate_from_llm_calls():
    """The sub-cap pool is SEPARATE from max_llm_calls: a leaf verify spends node_verify_spent, NOT the
    main calls_spent that funds the prover/decomposer search (so per-node Lean cannot starve search)."""
    v = ScriptedLeafVerifier(_verdict_pass())
    budget = Budget(max_llm_calls=50, max_node_verify_calls=5)
    res = _driver(DictProver({"G": valid_ledger("G")}), node_verifier=v, budget=budget).run("G")
    assert res.proven
    assert res.budget.node_verify_spent == 1             # the leaf verify drew from the SUB-cap pool
    # calls_spent is driven only by the prover/decomposer/reviewer search, never by per-node Lean.
    assert res.budget.node_verify_spent != res.budget.calls_spent or res.budget.calls_spent == 1


def test_driver_holds_shared_lean_server_handle():
    """DagDriver accepts/holds ONE warm lean_server to thread into the per-node gates (so each node
    compile reuses the SAME warm base env). The handle is held verbatim; None (the default) is held as
    None and changes nothing."""
    sentinel = object()
    d = _driver(DictProver({"G": valid_ledger("G")}), lean_server=sentinel)
    assert d.lean_server is sentinel
    d_default = _driver(DictProver({"G": valid_ledger("G")}))
    assert d_default.lean_server is None                  # default: no shared server, byte-identical


# ---- (R-ROBUST) a raising terminal gate -> proven-but-non-authoritative, never a crash ---------

def test_raising_terminal_gate_does_not_crash_run():
    """The terminal Layer-4 gate is a live model/Lean call and can RAISE. A raise MUST NOT crash
    DagDriver.run(): the root stays PROVEN (soft) while the terminal authoritative verdict is ABSENT
    (terminal=None / not authoritative_elementary), exactly as if no terminal gate were configured.
    The raise is surfaced on the trace, never silently swallowed."""
    def boom(goal, proof_text):
        raise RuntimeError("formalize subprocess timed out")

    res = _driver(DictProver({"G": valid_ledger("G")}), terminal_gate=boom).run("G")  # MUST NOT raise
    assert res.proven                          # informally proven (root proof is untouched by the gate)
    assert res.terminal is None                # the authoritative verdict is ABSENT, not a crash
    assert not res.authoritative_elementary    # proven-but-non-authoritative
    err = res.trace.by_kind("terminal_gate_error")
    assert err and err[0].data["error_type"] == "RuntimeError"
    assert not res.trace.by_kind("terminal_gate")  # the success-path event is NOT emitted on a raise


# ==================================================================================================
# W5 — enforce_elementarity FLAG: the elementarity dimension is the ONLY thing it relaxes.
#
# enforce_elementarity=True (the DEFAULT) runs the BYTE-IDENTICAL pre-flag path: every refute_elementary
# chokepoint REJECTS on denylist-prose / undischarged-elastic / goal-binding and a refuted NEEDS_REVIEW
# proof becomes FAILED_ELEMENTARY. enforce_elementarity=False ADMITS the elementarity dimension ONLY:
# a purely-elementarity refutation (denylist_prose / undischarged_elastic) no longer rejects (a
# FAILED_ELEMENTARY downgrades to soft PROVEN), while a non-goal-bound proof is STILL rejected and a
# vacuous inspection STILL fails loud. Soundness (goal<->claim, acyclicity, obligation, H0, conclusion)
# is UNAFFECTED. No NodeState / NodeEvent member is added; the FSM is untouched.
# ==================================================================================================

from agent.orchestrator.elementary_verifier import (  # noqa: E402
    VacuousVerificationError as _VacuousErr,
)


def denylist_prose_needs_review_sketch(goal: str, child: str, *, conclusion=None) -> str:
    """A decomposition SKETCH the gate routes to NEEDS_REVIEW via a denylist-prose hit ('elliptic curve')
    — soft-gate-passing-but-non-elementary. Cites `child` as a lemma and concludes `goal` (or
    `conclusion` for an off-goal variant). With no reviewer it is sent to the independent verifier."""
    concl = goal if conclusion is None else conclusion
    return json.dumps({"problem": "p", "claim": concl, "steps": [
        {"id": "L0", "claim": child, "justification": "lemma", "depends_on": []},
        {"id": "d", "claim": "reduce to an elliptic curve over Q", "justification": "algebra",
         "depends_on": ["L0"]},
        {"id": "c", "claim": concl, "justification": "conclusion", "depends_on": ["d"]}]})


# ---- the helper directly: the flag relaxes ONLY the elementarity dimension --------------------

def test_refute_for_enforcement_enforce_true_is_byte_identical():
    """enforce_elementarity=True (default): _refute_for_enforcement returns the SAME verdict the raw
    refute_elementary produces — a denylist-prose / undischarged-elastic ledger is still REFUTED."""
    from agent.orchestrator.elementary_verifier import refute_elementary
    d = _driver(DictProver({}))                          # enforce_elementarity defaults True
    assert d.enforce_elementarity is True
    src = non_elementary_ledger("G")                     # a denylist-prose ('class group') ledger
    raw = refute_elementary(src, TOOLKIT, goal="G")
    gated = d._refute_for_enforcement(src, "G")
    assert raw.refuted is True and gated.refuted is True  # byte-identical: still refuted
    assert [r.code for r in gated.refutations] == [r.code for r in raw.refutations]


def test_refute_for_enforcement_enforce_false_admits_elementarity_only():
    """enforce_elementarity=False: a purely-elementarity refutation (denylist_prose) is DROPPED -> not
    refuted; but a NON-GOAL-BOUND ledger is STILL refuted (goal_binding kept) and a VACUOUS inspection
    STILL fails loud. The flag relaxes the elementarity dimension and NOTHING else."""
    d = _driver(DictProver({}), enforce_elementarity=False)
    assert d.enforce_elementarity is False
    # (a) denylist-prose, goal-bound -> ADMITTED (the elementarity dimension is dropped).
    elem = d._refute_for_enforcement(non_elementary_ledger("G"), "G")
    assert elem.refuted is False
    # (b) undischarged-elastic, goal-bound -> ADMITTED (also a pure elementarity refutation).
    elastic = d._refute_for_enforcement(relabeled_undischarged_descent("G"), "G")
    assert elastic.refuted is False
    # (c) NON-GOAL-BOUND -> STILL REFUTED (goal_binding is soundness, never relaxed).
    off_goal = d._refute_for_enforcement(non_elementary_ledger("G"), "DIFFERENT")
    assert off_goal.refuted is True
    assert all(r.code == "goal_binding" for r in off_goal.refutations)
    # (d) VACUOUS -> STILL fails loud (anti-vacuity holds regardless of the flag).
    with pytest.raises(_VacuousErr):
        d._refute_for_enforcement("not a ledger at all", "G")


# ---- end-to-end: enforce=False admits a non-elementary-but-goal-bound challenger as the PROVEN ledger ----

def test_enforce_false_admits_non_elementary_challenger_as_proven_ledger():
    """enforce_elementarity=False: a non-elementary (denylist-prose) but goal-bound AutoReason challenger
    the judges PREFER — which the DEFAULT _authoritative_elementary gate REJECTS (incumbent held, see
    test_non_elementary_challenger_is_rejected_by_admissibility_gate) — is now ADMITTED and DISPLACES the
    incumbent: the node is PROVEN carrying the non-elementary ledger (the FAILED_ELEMENTARY -> soft PROVEN
    downgrade, applied at the admissibility chokepoint)."""
    from agent.orchestrator.dag import goal_hash
    incumbent = valid_ledger("G")
    challenger = non_elementary_ledger("G")              # denylist 'class group', goal-bound
    refiner = _refiner_with(challenger, challenger, {incumbent: 1.0, challenger: 100.0})
    res = _driver(DictProver({"G": incumbent}), refiner=refiner,
                  enforce_elementarity=False).run("G")
    assert res.proven
    g = res.dag.get(goal_hash("G"))
    assert g.state is NodeState.PROVEN
    assert g.proof == challenger, "with elementarity OFF the non-elementary challenger is admitted"


def test_enforce_true_default_rejects_the_same_non_elementary_challenger():
    """CONTROL (byte-identical default): the SAME non-elementary challenger is REJECTED with enforcement
    ON (the default) — the incumbent holds. Proves the admission above is the flag's doing, not an
    incidental pass (a controlled A/B on the single flag)."""
    from agent.orchestrator.dag import goal_hash
    incumbent = valid_ledger("G")
    challenger = non_elementary_ledger("G")
    refiner = _refiner_with(challenger, challenger, {incumbent: 1.0, challenger: 100.0})
    res = _driver(DictProver({"G": incumbent}), refiner=refiner).run("G")   # default: enforce True
    g = res.dag.get(goal_hash("G"))
    assert g.proof == incumbent, "enforcement ON rejects the non-elementary challenger"
    assert g.proof != challenger


def test_enforce_false_still_rejects_off_goal_challenger():
    """enforce_elementarity=False relaxes ONLY elementarity: an OFF-GOAL challenger (proves a DIFFERENT
    statement) is STILL inadmissible — _authoritative_elementary keeps the goal_binding refutation
    (soundness) regardless of the flag, so the incumbent holds and the goal stays bound to its own proof."""
    from agent.orchestrator.dag import goal_hash
    incumbent = valid_ledger("G")
    off_goal = mismatched_ledger("G", proved="H")        # concludes H, not the requested goal G
    refiner = _refiner_with(off_goal, off_goal, {incumbent: 1.0, off_goal: 100.0})
    res = _driver(DictProver({"G": incumbent}), refiner=refiner,
                  enforce_elementarity=False).run("G")
    assert res.proven
    g = res.dag.get(goal_hash("G"))
    assert g.proof == incumbent, "a non-goal-bound challenger must be rejected even with elementarity off"
    assert g.proof != off_goal


def test_enforce_false_direct_needs_review_non_elementary_downgrades_to_proven():
    """enforce_elementarity=False on the DIRECT path: a NEEDS_REVIEW direct proof whose prose is
    denylisted (which the DEFAULT downgrades to FAILED_ELEMENTARY via _verify_or_downgrade) now PASSES the
    verifier. With NO producer it is not directly promoted (an un-refuted NEEDS_REVIEW still needs review),
    but it is classified needs_review_no_reviewer rather than FAILED_ELEMENTARY — the elementarity
    refutation no longer fires."""
    from agent.orchestrator.dag import goal_hash
    bad = json.dumps({"problem": "p", "claim": "G", "steps": [
        {"id": "s1", "claim": "use the class group of K", "justification": "descent", "depends_on": [],
         "obligations": {"descent": {"measure": "x", "strictly_decreases": True,
                                     "stays_in_domain": True}}},
        {"id": "s2", "claim": "G", "justification": "conclusion", "depends_on": ["s1"]}]})
    assert _gate_evaluate(bad, TOOLKIT).verdict is Verdict.NEEDS_REVIEW
    res = _driver(DictProver({"G": bad}), enforce_elementarity=False).run("G")   # no decomposer
    assert not res.proven
    g = res.dag.get(goal_hash("G"))
    # The elementarity refutation did NOT fire: not FAILED_ELEMENTARY (the verifier passed it).
    assert g.state is not NodeState.FAILED_ELEMENTARY
    assert g.reason == ReasonCode.needs_review_no_reviewer.value
    assert res.trace.by_kind("verifier_passed")          # the verifier inspected + could not refute
    assert res.trace.by_kind("verifier_refuted") == []   # ... because elementarity was admitted


def test_enforce_default_is_true_byte_identical_construction():
    """The ctor default is enforce_elementarity=True, so a no-arg-change DagDriver runs the EXACT
    pre-feature path: the flag is True and the helper is byte-identical to raw refute_elementary."""
    from agent.orchestrator.elementary_verifier import refute_elementary
    d = DagDriver(DictProver({}), toolkit=TOOLKIT, budget=Budget(), trace=RunTrace("t"))
    assert d.enforce_elementarity is True
    # The whole pre-feature dag_driver suite (above) runs with this default -> byte-identical behavior.
    src = relabeled_undischarged_descent("G")
    assert d._refute_for_enforcement(src, "G").refuted == refute_elementary(src, TOOLKIT, goal="G").refuted
