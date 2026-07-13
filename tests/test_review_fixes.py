"""Regression tests for the adversarial code-review findings (see research/docs/engine_review.md).

Each test maps to a fix item (A1-A6, B*, C1). These guard the verifier's own correctness: a bug here
is a false-accept (an invalid/non-elementary proof passing) or a verifier crash.
"""
import json

import pytest

from agent.gates.gate import evaluate, Verdict
from agent.gates.ledger import parse_ledger, validate_structure, LedgerError
from agent.gates.report import Severity
from agent.gates.scanner import scan_prose, ELASTIC_JUSTIFICATIONS
from agent.tools import numeric
from agent.tools.numeric import (
    find_integer_solutions, verify_solution_set, verify_residue_cover, NumericError,
)
from agent.orchestrator import (
    FlatDriver, Budget, BudgetExceeded, RunTrace, NodeState, JudgeVerdict,
    ScriptedProver, ScriptedJudge,
)
from agent.gates.toolkit import load_toolkit

TOOLKIT = load_toolkit()


def _codes(report, sev=Severity.REJECT):
    return {f.code for f in report.findings if f.severity is sev}


def _descent_ledger(descent_ob):
    return {
        "problem": "p", "claim": "c",
        "steps": [
            {"id": "s1", "claim": "descend", "justification": "descent", "depends_on": [],
             "obligations": {"descent": descent_ob}},
            {"id": "s2", "claim": "c", "justification": "conclusion", "depends_on": ["s1"]},
        ],
    }


# ---- A1: float / rational leaves must not slip through (unsound-descent false-accept) ----

@pytest.mark.parametrize("expr", ["x + 1.5", "x**1.5", "x/2", "0.3333333333333333*3*x", "x + 2*y/3"])
def test_a1_non_integer_leaf_rejected(expr):
    vs = ["x", "y"] if "y" in expr else ["x"]
    with pytest.raises(NumericError):
        find_integer_solutions(expr, vs, {v: (0, 3) for v in vs})


def test_a1_float_coefficient_descent_is_rejected_end_to_end():
    # next = 0.999...*x is NOT < x (prover meant coefficient 1); the float must not pass as a decrease.
    led = _descent_ledger({
        "measure": "x", "strictly_decreases": True, "stays_in_domain": True,
        "measure_expr": "x", "next_expr": "0.3333333333333333*3*x",
        "variables": ["x"], "sample_bounds": {"x": [1, 10]},
    })
    rep = evaluate(led, TOOLKIT)
    assert rep.rejected
    assert "descent_expr_error" in _codes(rep)


def test_a1_integer_coefficients_still_work():
    assert find_integer_solutions("2*x - y", ["x", "y"], {"x": (0, 3), "y": (0, 6)})


# ---- A2: judge panel truncated by budget must NOT yield PROVEN ----

def test_a2_truncated_judge_panel_is_exhausted_not_proven():
    good = json.dumps({"problem": "p", "claim": "p", "steps": [
        {"id": "s1", "claim": "x", "justification": "given", "depends_on": []},
        {"id": "s2", "claim": "p", "justification": "conclusion", "depends_on": ["s1"]}]})
    judge = ScriptedJudge("J", [JudgeVerdict("J", elementary=False, no_gaps=False)])
    res = FlatDriver(ScriptedProver([good]), judges=[judge], toolkit=TOOLKIT,
                     budget=Budget(max_llm_calls=1), trace=RunTrace("t")).run("p")
    assert res.state is NodeState.EXHAUSTED
    assert res.state is not NodeState.PROVEN


# ---- A3: malformed-but-schema-valid input -> deterministic REJECT, never a crash ----

def test_a3a_descent_missing_sample_bound_rejects_not_crashes():
    led = _descent_ledger({
        "measure": "x", "strictly_decreases": True, "stays_in_domain": True,
        "measure_expr": "x + y", "next_expr": "x", "variables": ["x", "y"],
        "sample_bounds": {"x": [0, 2]},  # 'y' missing
    })
    rep = evaluate(led, TOOLKIT)  # must not raise
    assert rep.rejected
    assert "descent_expr_error" in _codes(rep)


def test_a3b_deep_acyclic_chain_does_not_recurse():
    # ~1500 deep > default recursion limit (1000); iterative cycle check must handle it.
    n = 1500
    steps = [{"id": "s0", "claim": "c", "justification": "conclusion", "depends_on": ["s1"]}]
    for i in range(1, n):
        steps.append({"id": f"s{i}", "claim": "x", "justification": "algebra",
                      "depends_on": [f"s{i+1}"]})
    steps.append({"id": f"s{n}", "claim": "base", "justification": "given", "depends_on": []})
    rep = evaluate({"problem": "p", "claim": "c", "steps": steps}, TOOLKIT)  # must not raise
    assert rep.verdict is Verdict.PASSED_DETERMINISTIC


def test_a3_oversized_ledger_rejected_by_schema():
    steps = [{"id": f"s{i}", "claim": "x", "justification": "algebra", "depends_on": []}
             for i in range(2500)]
    steps.append({"id": "c", "claim": "done", "justification": "conclusion", "depends_on": ["s0"]})
    rep = evaluate({"problem": "p", "claim": "c", "steps": steps}, TOOLKIT)
    assert rep.rejected  # maxItems exceeded -> parse_error


def test_a3_self_loop_is_a_cycle():
    led = parse_ledger({"problem": "p", "claim": "c", "steps": [
        {"id": "a", "claim": "x", "justification": "algebra", "depends_on": ["a"]},
        {"id": "c", "claim": "c", "justification": "conclusion", "depends_on": ["a"]}]})
    assert "cycle" in {f.code for f in validate_structure(led, TOOLKIT)
                       if f.severity is Severity.REJECT}


# ---- A4: NEEDS_REVIEW with no judges must NOT be PROVEN ----

def test_a4_review_without_judges_not_proven():
    review_ledger = json.dumps({"problem": "p", "claim": "p", "steps": [
        {"id": "s1", "claim": "bound it", "justification": "bounding", "depends_on": [],
         "obligations": {"bounding": {"inequality": "n < n+1", "strict": True}}},
        {"id": "s2", "claim": "p", "justification": "conclusion", "depends_on": ["s1"]}]})
    res = FlatDriver(ScriptedProver([review_ledger]), judges=None, toolkit=TOOLKIT,
                     trace=RunTrace("t")).run("p")
    assert res.state is not NodeState.PROVEN
    assert res.state is NodeState.EXHAUSTED


# ---- A5: split-coprimality must cite an ancestor (premise), not an unrelated step ----

def test_a5_split_coprimality_must_be_ancestor():
    led = {"problem": "p", "claim": "c", "steps": [
        {"id": "g", "claim": "gcd(p,q)=1", "justification": "gcd_coprimality", "depends_on": []},
        {"id": "x", "claim": "unrelated", "justification": "algebra", "depends_on": []},
        {"id": "sp", "claim": "split squares", "justification": "euclid_splitting",
         "depends_on": ["x"], "obligations": {"split_coprimality": {"coprimality_from": "g"}}},
        {"id": "c", "claim": "c", "justification": "conclusion", "depends_on": ["sp"]}]}
    rep = evaluate(led, TOOLKIT)
    assert rep.rejected
    assert "split_coprimality_unrelated" in _codes(rep)


def test_a5_split_coprimality_ancestor_ok():
    led = {"problem": "p", "claim": "c", "steps": [
        {"id": "g", "claim": "gcd(p,q)=1", "justification": "gcd_coprimality", "depends_on": []},
        {"id": "sp", "claim": "split squares", "justification": "euclid_splitting",
         "depends_on": ["g"], "obligations": {"split_coprimality": {"coprimality_from": "g"}}},
        {"id": "c", "claim": "c", "justification": "conclusion", "depends_on": ["sp"]}]}
    rep = evaluate(led, TOOLKIT)
    assert "split_coprimality_unrelated" not in _codes(rep)


# ---- A6: descent / vieta routed to adversarial review ----

def test_a6_descent_is_elastic():
    assert "descent" in ELASTIC_JUSTIFICATIONS and "vieta_jumping" in ELASTIC_JUSTIFICATIONS
    led = parse_ledger(_descent_ledger({
        "measure": "x", "strictly_decreases": True, "stays_in_domain": True}))
    findings = scan_prose(led, TOOLKIT)
    assert any(f.code == "elastic_justification" for f in findings)


# ---- B3 / B4 / B5: numeric primitive hardening ----

def test_b3_huge_magnitude_bound_rejected():
    with pytest.raises(NumericError):
        find_integer_solutions("x", ["x"], {"x": (10**12, 10**12 + 3)})


def test_b4_non_integer_residues_rejected():
    with pytest.raises(NumericError):
        verify_residue_cover(4, [0.9, 1.9, 2.9, 3.9])
    with pytest.raises(NumericError):
        verify_residue_cover(2, [True, False])


def test_b5_spurious_claim_detected():
    res = verify_solution_set("x - 1", ["x"], {"x": (0, 3)}, claimed=[{"x": 1}, {"x": 2}])
    assert not res.ok
    assert {"x": 2} in res.spurious_claims


def test_b5_correct_complete_claim_ok():
    res = verify_solution_set("x - 1", ["x"], {"x": (0, 3)}, claimed=[{"x": 1}])
    assert res.ok and not res.spurious_claims and not res.counterexamples


# ---- B6: a conclusion that depends on nothing (with other steps) is disconnected ----

def test_b6_disconnected_conclusion_rejected():
    led = parse_ledger({"problem": "p", "claim": "c", "steps": [
        {"id": "s1", "claim": "real work", "justification": "congruence", "depends_on": []},
        {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": []}]})
    assert "disconnected_conclusion" in {f.code for f in validate_structure(led, TOOLKIT)
                                         if f.severity is Severity.REJECT}


# ---- B8 / B9: schema hardening ----

def test_b8_unknown_obligation_key_rejected():
    with pytest.raises(LedgerError):
        parse_ledger({"problem": "p", "claim": "c", "steps": [
            {"id": "s1", "claim": "x", "justification": "given", "depends_on": [],
             "obligations": {"GARBAGE": 1}},
            {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]}]})


def test_b9_case_cover_modulus_one_rejected_by_schema():
    with pytest.raises(LedgerError):
        parse_ledger({"problem": "p", "claim": "c", "steps": [
            {"id": "s1", "claim": "split", "justification": "case_split", "depends_on": [],
             "obligations": {"case_cover": {"modulus": 1, "residues": [0]}}},
            {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]}]})


# ---- B11: budget guards ----

def test_b11_spend_repair_guarded():
    b = Budget(max_repair_iters=0)
    assert not b.can_repair()
    with pytest.raises(BudgetExceeded):
        b.spend_repair()


def test_b11_spend_call_guarded():
    b = Budget(max_llm_calls=1)
    b.spend_call()
    with pytest.raises(BudgetExceeded):
        b.spend_call()
