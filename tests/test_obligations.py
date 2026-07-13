"""Tests for obligation content checks (case-cover, descent, bounding, split-coprimality)."""
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


# ----------------------------------------------------------------------------------------------------
# (B) ENFORCE descent strict-decrease by default + route an unverifiable descent to NEEDS_REVIEW.
# ----------------------------------------------------------------------------------------------------

def _descent_step(descent_ob, sid="s1"):
    return {"id": sid, "claim": "descend", "justification": "descent", "depends_on": [],
            "obligations": {"descent": descent_ob}}


def test_descent_without_measure_is_review_not_passed(toolkit):
    # A descent that only SELF-ASSERTS the booleans (no measure_expr/next_expr) must NOT silently pass:
    # it is routed to NEEDS_REVIEW via a REVIEW finding (an unverified descent is flagged, not admitted
    # as deterministically proven). Pre-change this emitted NO finding at all (silent pass).
    led = _ledger([
        _descent_step({"measure": "x", "strictly_decreases": True, "stays_in_domain": True}),
        {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]},
    ])
    findings = check_obligations(led, toolkit)
    assert "descent_unverified_measure" in _codes(findings, Severity.REVIEW)
    # It is a soft REVIEW, never a hard REJECT (a self-asserted-true descent is not itself wrong).
    assert not _codes(findings, Severity.REJECT)


def test_descent_non_decreasing_measure_fails_by_default(toolkit):
    # next = x (NOT < measure = x) -> the strict-decrease check runs BY DEFAULT and REJECTS. Pre-change
    # the equality 'measure_expr'/'next_expr' were supplied so this path already rejected; we keep it
    # green to pin "runs by default, no opt-in flag required".
    led = _ledger([
        _descent_step({
            "measure": "x", "strictly_decreases": True, "stays_in_domain": True,
            "measure_expr": "x", "next_expr": "x", "variables": ["x"],
            "sample_bounds": {"x": [0, 5]}}),
        {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]},
    ])
    assert "descent_no_decrease" in _codes(check_obligations(led, toolkit))


def test_descent_expr_without_box_is_rejected_not_silently_passed(toolkit):
    # A descent that supplies measure_expr/next_expr but OMITS the sample_bounds box it needs is a
    # malformed checkable obligation -> deterministic REJECT (descent_expr_error). Pre-change this was a
    # SILENT PASS (the numeric check only ran when all four fields were present).
    led = _ledger([
        _descent_step({
            "measure": "x", "strictly_decreases": True, "stays_in_domain": True,
            "measure_expr": "x", "next_expr": "x - 1", "variables": ["x"]}),  # no sample_bounds
        {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]},
    ])
    assert "descent_expr_error" in _codes(check_obligations(led, toolkit))


def test_descent_half_specified_measure_is_rejected(toolkit):
    # Only next_expr (no measure_expr): a half-specified measure cannot be checked -> REJECT, not a
    # silent pass nor a crash.
    led = _ledger([
        _descent_step({
            "measure": "x", "strictly_decreases": True, "stays_in_domain": True,
            "next_expr": "x - 1", "variables": ["x"], "sample_bounds": {"x": [0, 5]}}),
        {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]},
    ])
    assert "descent_expr_error" in _codes(check_obligations(led, toolkit))


# ----------------------------------------------------------------------------------------------------
# (B) bounding obligation now has a REAL content checker.
# ----------------------------------------------------------------------------------------------------

def _bounding_step(bounding_ob, sid="s1"):
    return {"id": sid, "claim": "bound it", "justification": "bounding", "depends_on": [],
            "obligations": {"bounding": bounding_ob}}


def test_bounding_symbolic_valid_passes_content_check(toolkit):
    # A well-formed symbolic strict bound ('n < n+1', strict=True) has no concrete value to refute, so
    # the deterministic content checker raises NO finding (it stays a REVIEW-routed elastic step
    # upstream, but check_obligations itself must not REJECT a genuinely-elementary bound).
    led = _ledger([
        _bounding_step({"inequality": "n < n+1", "strict": True}),
        {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]},
    ])
    assert not _codes(check_obligations(led, toolkit))


def test_bounding_concrete_violated_rejects(toolkit):
    # A CONCRETE bound that is numerically FALSE ('5 < 3') must FAIL (verified via the no-eval
    # evaluator). Pre-change 'bounding' had NO content checker, so this passed on presence alone.
    led = _ledger([
        _bounding_step({"inequality": "5 < 3", "strict": True}),
        {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]},
    ])
    assert "bounding_violated" in _codes(check_obligations(led, toolkit))


def test_bounding_concrete_true_passes(toolkit):
    # A concrete TRUE bound ('2 < 3') passes the content check.
    led = _ledger([
        _bounding_step({"inequality": "2 < 3", "strict": True}),
        {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]},
    ])
    assert not _codes(check_obligations(led, toolkit))


def test_bounding_strictness_mismatch_rejects(toolkit):
    # Declaring strict=True on a non-strict operator ('2 <= 3') is an inconsistent obligation -> FAIL.
    led = _ledger([
        _bounding_step({"inequality": "2 <= 3", "strict": True}),
        {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]},
    ])
    assert "bounding_strictness_mismatch" in _codes(check_obligations(led, toolkit))


def test_bounding_malformed_inequality_rejects(toolkit):
    # No comparison operator at all -> malformed, must FAIL (not pass on presence).
    led = _ledger([
        _bounding_step({"inequality": "n plus one", "strict": True}),
        {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]},
    ])
    assert "bounding_malformed" in _codes(check_obligations(led, toolkit))


def test_bounding_malicious_side_rejects_and_does_not_execute(toolkit, monkeypatch):
    # A prover-controlled inequality side carrying a Python injection must become a deterministic
    # REJECT (bounding_malformed) and must NOT execute during parsing.
    import os

    called = {"hit": False}
    monkeypatch.setattr(os, "getpid", lambda: called.__setitem__("hit", True) or 1)
    led = _ledger([
        _bounding_step({"inequality": '__import__("os").getpid() < 3', "strict": True}),
        {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]},
    ])
    assert "bounding_malformed" in _codes(check_obligations(led, toolkit))
    assert called["hit"] is False


def test_bounding_caret_exponent_is_not_over_rejected():
    """Regression: a valid bound written with caret exponents ('2^n < 3^n') must NOT be hard-rejected
    as malformed. Caret is exponentiation in elementary NT; before the _to_pow normalization '^' parsed
    as BitXor -> a disallowed node -> a spurious bounding_malformed REJECT, shrinking elementary reach."""
    from agent.gates.obligations import _check_bounding
    for ineq in ("2^n < 3^n", "x^2 + y^2 < z^2", "2^n < 2^(n+1)", "3^2 < 4^2"):
        findings = _check_bounding("s1", {"bounding": {"inequality": ineq, "strict": True}})
        assert findings == [], f"valid caret bound {ineq!r} was over-rejected: {[f.code for f in findings]}"


def test_bounding_caret_still_catches_violated_and_malicious():
    """The caret normalization must not loosen the genuine checks: a concretely-FALSE caret bound is
    still REJECTed (bounding_violated), and a malicious side is still REJECTed (bounding_malformed) and
    NEVER executed (no eval)."""
    from agent.gates.obligations import _check_bounding
    violated = _check_bounding("s1", {"bounding": {"inequality": "5^2 < 3^2", "strict": True}})
    assert any(f.code == "bounding_violated" for f in violated)
    malicious = _check_bounding("s1", {"bounding": {"inequality": '__import__("os") < 3', "strict": True}})
    assert any(f.code == "bounding_malformed" for f in malicious)


def test_ledger_wide_obligation_work_budget_blocks_stacked_checks(monkeypatch, toolkit):
    import agent.gates.obligations as obligations_mod

    calls: list[int] = []

    def fake_cover(modulus, residues):
        calls.append(modulus)
        return obligations_mod.numeric.CoverCheck(
            ok=True, modulus=modulus, covered=list(residues), missing=[])

    monkeypatch.setattr(obligations_mod, "MAX_TOTAL_OBLIGATION_WORK", 6)
    monkeypatch.setattr(obligations_mod.numeric, "verify_residue_cover", fake_cover)
    led = _ledger([
        {"id": "s1", "claim": "split once", "justification": "case_split", "depends_on": [],
         "obligations": {"case_cover": {"modulus": 3, "residues": [0, 1, 2]}}},
        {"id": "s2", "claim": "split twice", "justification": "case_split", "depends_on": ["s1"],
         "obligations": {"case_cover": {"modulus": 3, "residues": [0, 1, 2]}}},
        {"id": "s3", "claim": "c", "justification": "conclusion", "depends_on": ["s2"]},
    ])
    findings = check_obligations(led, toolkit)
    assert calls == [3]
    assert "obligation_work_budget_exceeded" in _codes(findings)
