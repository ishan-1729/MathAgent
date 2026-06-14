"""Tests for obligation content checks (case-cover, descent, split-coprimality)."""
from agent.gates.ledger import parse_ledger
from agent.gates.obligations import check_obligations
from agent.gates.report import Severity


def _codes(findings, severity=Severity.REJECT):
    return {f.code for f in findings if f.severity is severity}


def _ledger(steps):
    return parse_ledger({"problem": "p", "claim": "c", "steps": steps})


def test_case_cover_complete_ok(toolkit):
    led = _ledger([
        {"id": "s1", "claim": "split mod 3", "justification": "case_split", "depends_on": [],
         "obligations": {"case_cover": {"modulus": 3, "residues": [0, 1, 2]}}},
        {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]},
    ])
    assert not _codes(check_obligations(led, toolkit))


def test_case_cover_incomplete_rejects(toolkit):
    led = _ledger([
        {"id": "s1", "claim": "split mod 4", "justification": "case_split", "depends_on": [],
         "obligations": {"case_cover": {"modulus": 4, "residues": [0, 1, 2]}}},
        {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]},
    ])
    assert "case_cover_incomplete" in _codes(check_obligations(led, toolkit))


def test_descent_must_assert_decrease(toolkit):
    led = _ledger([
        {"id": "s1", "claim": "descend", "justification": "descent", "depends_on": [],
         "obligations": {"descent": {"measure": "x", "strictly_decreases": False,
                                     "stays_in_domain": True}}},
        {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]},
    ])
    assert "descent_not_decreasing" in _codes(check_obligations(led, toolkit))


def test_descent_must_stay_in_domain(toolkit):
    led = _ledger([
        {"id": "s1", "claim": "descend", "justification": "descent", "depends_on": [],
         "obligations": {"descent": {"measure": "x", "strictly_decreases": True,
                                     "stays_in_domain": False}}},
        {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]},
    ])
    assert "descent_leaves_domain" in _codes(check_obligations(led, toolkit))


def test_descent_numeric_spotcheck_pass(toolkit):
    # next = x-1 strictly less than measure = x everywhere -> ok.
    led = _ledger([
        {"id": "s1", "claim": "descend", "justification": "descent", "depends_on": [],
         "obligations": {"descent": {
             "measure": "x", "strictly_decreases": True, "stays_in_domain": True,
             "measure_expr": "x", "next_expr": "x - 1", "variables": ["x"],
             "sample_bounds": {"x": [1, 20]}}}},
        {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]},
    ])
    assert not _codes(check_obligations(led, toolkit))


def test_descent_numeric_spotcheck_fail(toolkit):
    # next = x+1 is NOT < measure = x -> reject.
    led = _ledger([
        {"id": "s1", "claim": "descend", "justification": "descent", "depends_on": [],
         "obligations": {"descent": {
             "measure": "x", "strictly_decreases": True, "stays_in_domain": True,
             "measure_expr": "x", "next_expr": "x + 1", "variables": ["x"],
             "sample_bounds": {"x": [1, 20]}}}},
        {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]},
    ])
    assert "descent_no_decrease" in _codes(check_obligations(led, toolkit))


def test_split_coprimality_ok(toolkit):
    led = _ledger([
        {"id": "s1", "claim": "gcd(p,q)=1", "justification": "gcd_coprimality", "depends_on": []},
        {"id": "s2", "claim": "split squares", "justification": "euclid_splitting",
         "depends_on": ["s1"], "obligations": {"split_coprimality": {"coprimality_from": "s1"}}},
        {"id": "s3", "claim": "done", "justification": "conclusion", "depends_on": ["s2"]},
    ])
    assert not _codes(check_obligations(led, toolkit))


def test_split_coprimality_wrong_kind(toolkit):
    led = _ledger([
        {"id": "s1", "claim": "some algebra", "justification": "algebra", "depends_on": []},
        {"id": "s2", "claim": "split squares", "justification": "euclid_splitting",
         "depends_on": ["s1"], "obligations": {"split_coprimality": {"coprimality_from": "s1"}}},
        {"id": "s3", "claim": "done", "justification": "conclusion", "depends_on": ["s2"]},
    ])
    assert "split_coprimality_wrong_kind" in _codes(check_obligations(led, toolkit))


def test_split_coprimality_dangling(toolkit):
    led = _ledger([
        {"id": "s2", "claim": "split squares", "justification": "euclid_splitting",
         "depends_on": [], "obligations": {"split_coprimality": {"coprimality_from": "ghost"}}},
        {"id": "s3", "claim": "done", "justification": "conclusion", "depends_on": ["s2"]},
    ])
    assert "split_coprimality_dangling" in _codes(check_obligations(led, toolkit))


def test_descent_malicious_expr_rejects_and_does_not_execute(toolkit, monkeypatch):
    # A prover-controlled next_expr/measure_expr carrying a Python injection must become a
    # deterministic REJECT (descent_expr_error) and must NOT execute during parsing.
    import os

    called = {"hit": False}
    monkeypatch.setattr(os, "getpid", lambda: called.__setitem__("hit", True) or 1)
    led = _ledger([
        {"id": "s1", "claim": "descend", "justification": "descent", "depends_on": [],
         "obligations": {"descent": {
             "measure": "x", "strictly_decreases": True, "stays_in_domain": True,
             "measure_expr": "x", "next_expr": '__import__("os").getpid()',
             "variables": ["x"], "sample_bounds": {"x": [1, 5]}}}},
        {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]},
    ])
    findings = check_obligations(led, toolkit)
    assert "descent_expr_error" in _codes(findings)
    assert called["hit"] is False


def test_descent_constructor_globals_leak_blocked(toolkit, monkeypatch):
    # The exact residual gate exploit: a model-controlled next_expr that reaches sympy's module
    # __builtins__ via Symbol.__new__.__globals__. It must become a deterministic descent_expr_error
    # REJECT and must NOT execute os.system during the descent spot-check.
    import os

    called = {"hit": False}
    monkeypatch.setattr(os, "system", lambda *_a, **_k: called.__setitem__("hit", True) or 0)
    payload = (
        'Integer(Symbol.__new__.__globals__["__builtins__"]'
        '["__import__"]("os").system("echo RCE_GATE_EXACT"))'
    )
    led = _ledger([
        {"id": "s1", "claim": "descend", "justification": "descent", "depends_on": [],
         "obligations": {"descent": {
             "measure": "x", "strictly_decreases": True, "stays_in_domain": True,
             "measure_expr": "1", "next_expr": payload,
             "variables": ["x"], "sample_bounds": {"x": [0, 0]}}}},
        {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]},
    ])
    findings = check_obligations(led, toolkit)
    assert "descent_expr_error" in _codes(findings)
    assert called["hit"] is False


def test_descent_numeric_spotcheck_still_passes_after_hardening(toolkit):
    # Legitimate descent spot-check behavior is unchanged by the parser hardening.
    led = _ledger([
        {"id": "s1", "claim": "descend", "justification": "descent", "depends_on": [],
         "obligations": {"descent": {
             "measure": "x", "strictly_decreases": True, "stays_in_domain": True,
             "measure_expr": "x", "next_expr": "x - 1", "variables": ["x"],
             "sample_bounds": {"x": [1, 20]}}}},
        {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]},
    ])
    assert not _codes(check_obligations(led, toolkit))
