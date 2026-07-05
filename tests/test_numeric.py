"""Tests for the numeric/witness tool (exact integer search + case-cover verification)."""
import pytest
from hypothesis import given, strategies as st

from agent.tools import numeric
from agent.tools.numeric import (
    check_witness_spec,
    find_integer_solutions,
    verify_solution_set,
    verify_residue_cover,
    NumericError,
)


def test_find_solutions_x2_plus_1_eq_y3():
    sols = find_integer_solutions("x**2 + 1 - y**3", ["x", "y"],
                                  {"x": (-50, 50), "y": (-10, 10)})
    assert {"x": 0, "y": 1} in sols
    # (0,1) is the only solution in this box.
    assert sols == [{"x": 0, "y": 1}]


def test_equation_form_with_equals_sign():
    sols = find_integer_solutions("x**2 + 1 = y**3", ["x", "y"],
                                  {"x": (-50, 50), "y": (-10, 10)})
    assert sols == [{"x": 0, "y": 1}]


def test_find_solutions_linear():
    sols = find_integer_solutions("2*x - y", ["x", "y"], {"x": (0, 3), "y": (0, 6)})
    assert {"x": 0, "y": 0} in sols
    assert {"x": 3, "y": 6} in sols
    assert {"x": 1, "y": 3} not in sols  # y must equal 2x


def test_verify_solution_set_ok():
    res = verify_solution_set("x**2 + 1 - y**3", ["x", "y"],
                              {"x": (-50, 50), "y": (-10, 10)}, claimed=[{"x": 0, "y": 1}])
    assert res.ok
    assert not res.counterexamples
    assert bool(res) is True


def test_verify_solution_set_finds_counterexample():
    # Claim "no solutions" but (0,1) exists -> counterexample.
    res = verify_solution_set("x**2 + 1 - y**3", ["x", "y"],
                              {"x": (-50, 50), "y": (-10, 10)}, claimed=[])
    assert not res.ok
    assert {"x": 0, "y": 1} in res.counterexamples


# --- FIX 1: verify_solution_set must validate claimed assignments are genuine integers.
#     A non-integer / malformed claim raises NumericError (an UNREPRESENTABLE spec — the same
#     taxonomy as verify_residue_cover and the REJECTED witness controls: check_witness_spec records
#     it as a spec-level error row, a HARD failure fitness must not reward), never a silent
#     int()-truncation onto a real root. ---

def test_verify_solution_set_rejects_float_claim():
    # {"x": 2.4} previously int()-truncated to the root x=2 and VERIFIED — the reward hack.
    with pytest.raises(NumericError, match="must be integers"):
        verify_solution_set("x - 2", ["x"], {"x": (0, 5)}, claimed=[{"x": 2.4}])


def test_verify_solution_set_rejects_bool_claim():
    # bool subclasses int; it must be rejected exactly like verify_residue_cover's guard.
    with pytest.raises(NumericError, match="must be integers"):
        verify_solution_set("x - 1", ["x"], {"x": (0, 5)}, claimed=[{"x": True}])


def test_verify_solution_set_rejects_missing_variable_claim():
    # A claim missing a declared variable is a typed NumericError, never a raw KeyError through _key.
    with pytest.raises(NumericError, match="missing variable"):
        verify_solution_set("x + y", ["x", "y"], {"x": (0, 2), "y": (-2, 0)}, claimed=[{"x": 0}])


def test_check_witness_spec_records_nonint_claim_as_error_row():
    # End-to-end taxonomy pin: a solution_set spec whose claim carries type-garbage (float) is an
    # ERROR row (ok=False, error set) — the same recorded-rejection shape as the shipped
    # tensor_rank_444_REJECTED control (string "2+3i" claim) — not a spurious-claim pass-through.
    spec = {"kind": "solution_set", "expression": "x - 2", "variables": ["x"],
            "bounds": {"x": [0, 5]}, "claimed": [{"x": 2.4}]}
    res = check_witness_spec(spec)
    assert res.ok is False
    assert res.error is not None and "integers" in res.error


def test_verify_solution_set_integer_claim_still_ok():
    # The legitimate integer path is unchanged: the exact root as an int still verifies.
    res = verify_solution_set("x - 2", ["x"], {"x": (0, 5)}, claimed=[{"x": 2}])
    assert res.ok is True
    assert not res.spurious_claims and not res.counterexamples


def test_residue_cover_complete():
    assert verify_residue_cover(2, [0, 1]).ok
    assert verify_residue_cover(4, [0, 1, 2, 3]).ok
    assert verify_residue_cover(3, [3, 4, 5]).ok  # residues reduced mod 3 -> {0,1,2}


def test_residue_cover_incomplete():
    res = verify_residue_cover(4, [0, 1, 2])
    assert not res.ok
    assert res.missing == [3]


def test_residue_cover_bad_modulus():
    with pytest.raises(NumericError):
        verify_residue_cover(0, [0])


def test_rejects_undeclared_symbol():
    with pytest.raises(NumericError, match="undeclared"):
        find_integer_solutions("x + z", ["x"], {"x": (0, 2)})


def test_rejects_transcendental_function():
    with pytest.raises(NumericError):
        find_integer_solutions("sin(x)", ["x"], {"x": (0, 2)})


def test_rejects_negative_power():
    with pytest.raises(NumericError):
        find_integer_solutions("x**(-1)", ["x"], {"x": (1, 3)})


def test_rejects_oversized_box():
    with pytest.raises(NumericError, match="MAX_BOX_POINTS"):
        find_integer_solutions("x - y", ["x", "y"],
                               {"x": (0, 10_000), "y": (0, 10_000)})


def test_missing_bounds():
    with pytest.raises(NumericError):
        find_integer_solutions("x + y", ["x", "y"], {"x": (0, 2)})


def test_empty_bound_range():
    with pytest.raises(NumericError):
        find_integer_solutions("x", ["x"], {"x": (5, 1)})


def test_exact_integer_arithmetic_no_float_error():
    # Large powers would lose precision under float; exact ints must still find the root.
    sols = find_integer_solutions("x**6 - 64", ["x"], {"x": (-5, 5)})
    assert {"x": 2} in sols and {"x": -2} in sols


@given(a=st.integers(min_value=-20, max_value=20))
def test_property_linear_root(a):
    sols = find_integer_solutions(f"x - ({a})", ["x"], {"x": (-25, 25)})
    assert sols == [{"x": a}]


@given(m=st.integers(min_value=1, max_value=12))
def test_property_full_range_covers(m):
    assert verify_residue_cover(m, list(range(m))).ok


@given(m=st.integers(min_value=2, max_value=12), drop=st.integers(min_value=0, max_value=11))
def test_property_missing_one_residue_fails(m, drop):
    drop = drop % m
    residues = [r for r in range(m) if r != drop]
    res = verify_residue_cover(m, residues)
    assert not res.ok
    assert drop in res.missing


# --- Security: parsing must never eval/exec arbitrary Python (sympify RCE regression) ---

def test_malicious_import_call_raises_and_does_not_execute(monkeypatch):
    # A payload that, under the old sympify->eval path, would call os.getpid() DURING parsing.
    # The restricted parser must reject it as a NumericError and must NOT invoke os at all.
    import os

    called = {"hit": False}

    def _boom():
        called["hit"] = True
        return 1234

    monkeypatch.setattr(os, "getpid", _boom)
    with pytest.raises(NumericError):
        find_integer_solutions('__import__("os").getpid()', ["x"], {"x": (0, 2)})
    assert called["hit"] is False


def test_malicious_payload_in_sum_does_not_execute(monkeypatch):
    # The injection embedded inside an otherwise-valid expression must also be inert.
    import os

    called = {"hit": False}
    monkeypatch.setattr(os, "system", lambda *_a, **_k: called.__setitem__("hit", True) or 0)
    with pytest.raises(NumericError):
        find_integer_solutions('x + __import__("os").system("echo pwned")', ["x"], {"x": (0, 2)})
    assert called["hit"] is False


def test_malicious_payload_writes_no_file(tmp_path):
    # Execution-would-leave-a-sentinel test: if the string were eval'd, the file would appear.
    sentinel = tmp_path / "pwned.txt"
    payload = f'__import__("pathlib").Path({str(sentinel)!r}).write_text("x")'
    with pytest.raises(NumericError):
        find_integer_solutions(payload, ["x"], {"x": (0, 1)})
    assert not sentinel.exists()


def test_malicious_payload_on_equals_path_does_not_execute(monkeypatch):
    # The '=' split path is parsed the same restricted way; injection on either side is inert.
    import os

    called = {"hit": False}
    monkeypatch.setattr(os, "getpid", lambda: called.__setitem__("hit", True) or 7)
    with pytest.raises(NumericError):
        find_integer_solutions('x = __import__("os").getpid()', ["x"], {"x": (0, 2)})
    assert called["hit"] is False


def test_rejects_function_call_syntax():
    # Bare function-call syntax (no implicit-call transformation) is not parseable to a polynomial.
    with pytest.raises(NumericError):
        find_integer_solutions("foo(x)", ["x"], {"x": (0, 2)})


def test_rejects_attribute_access():
    with pytest.raises(NumericError):
        find_integer_solutions("x.__class__", ["x"], {"x": (0, 2)})


def test_constructor_globals_leak_blocked(monkeypatch, tmp_path):
    # Exact residual exploit from the verifier: an explicitly-written Integer(...) call whose
    # argument reaches sympy's module __builtins__ via Symbol.__new__.__globals__, bypassing the
    # old empty-__builtins__ sandbox. The AST parser must reject this Call node WITHOUT executing
    # os.system / os.makedirs, so no side effect occurs.
    import os

    sys_called = {"hit": False}
    monkeypatch.setattr(os, "system", lambda *_a, **_k: sys_called.__setitem__("hit", True) or 0)
    payload = (
        'Integer(Symbol.__new__.__globals__["__builtins__"]'
        '["__import__"]("os").system("echo RCE"))'
    )
    with pytest.raises(NumericError):
        numeric.find_points_where_nonneg(f"({payload}) - (1)", ["x"], {"x": (0, 0)})
    assert sys_called["hit"] is False

    # makedirs variant: confirm real arbitrary code does not run by asserting the dir is not created.
    target = tmp_path / "rce_dir"
    payload2 = (
        'Integer(Symbol.__new__.__globals__["__builtins__"]'
        f'["__import__"]("os").makedirs({str(target)!r}))'
    )
    with pytest.raises(NumericError):
        numeric.find_points_where_nonneg(f"({payload2}) - (1)", ["x"], {"x": (0, 0)})
    assert not target.exists()


def test_rejects_subscript_and_division():
    # Subscript (used to index into __globals__) and true division are outside the polynomial
    # grammar and must be rejected at the AST level.
    with pytest.raises(NumericError):
        find_integer_solutions("x[0]", ["x"], {"x": (0, 2)})
    with pytest.raises(NumericError):
        find_integer_solutions("x / 2", ["x"], {"x": (0, 2)})


def test_valid_polynomials_still_parse_after_hardening():
    # Sanity: the legitimate integer-polynomial path is unchanged by the parser swap.
    assert find_integer_solutions("x**2 + 1 - y**3", ["x", "y"],
                                  {"x": (-50, 50), "y": (-10, 10)}) == [{"x": 0, "y": 1}]
    assert find_integer_solutions("x**2 + 1 = y**3", ["x", "y"],
                                  {"x": (-50, 50), "y": (-10, 10)}) == [{"x": 0, "y": 1}]
    nonneg = numeric.find_points_where_nonneg("(x - 1) - (x)", ["x"], {"x": (0, 5)})
    assert nonneg == []  # x-1 < x everywhere -> never >= 0


# --- Latent NameError fix: SpecCheck.error: Optional[str] must resolve (`Optional` imported) ---

def test_optional_annotation_resolves_on_speccheck():
    # SpecCheck.error is annotated Optional[str]; under PEP 563 string annotations the missing
    # `from typing import Optional` was a latent NameError surfacing only when the annotation is
    # resolved. get_type_hints forces resolution -> must NOT raise NameError.
    import typing
    hints = typing.get_type_hints(numeric.SpecCheck)
    assert hints["error"] == typing.Optional[str]


def test_optional_is_importable_from_numeric_namespace():
    # Direct evidence that `Optional` is now bound in the module namespace.
    assert numeric.Optional is not None
    # And a SpecCheck carrying an error still constructs/behaves (the malformed-spec HARD-failure path).
    sc = numeric.SpecCheck(False, "descent", error="boom")
    assert sc.error == "boom" and bool(sc) is False


# --- evaluate_integer_constant / concrete_inequality_holds (the bounding content checker primitives) ---

def test_evaluate_integer_constant_exact():
    assert numeric.evaluate_integer_constant("2 + 3 * 4") == 14
    assert numeric.evaluate_integer_constant("-(2**3)") == -8


def test_evaluate_integer_constant_symbolic_raises_symbolic():
    # A free variable -> SymbolicExpression (a NumericError subclass), distinct from "malformed".
    with pytest.raises(numeric.SymbolicExpression):
        numeric.evaluate_integer_constant("n + 1")


@pytest.mark.parametrize("bad", [
    '__import__("os").getpid()', "x.__class__", "x[0]", "x / 2", "x + 1.5",
])
def test_evaluate_integer_constant_malformed_raises_numeric_not_symbolic(bad, monkeypatch):
    # A non-polynomial / injection side is MALFORMED (NumericError) and must NOT be mistaken for a
    # benign symbolic side; it must never execute.
    import os

    called = {"hit": False}
    monkeypatch.setattr(os, "getpid", lambda: called.__setitem__("hit", True) or 1)
    with pytest.raises(NumericError) as ei:
        numeric.evaluate_integer_constant(bad)
    assert not isinstance(ei.value, numeric.SymbolicExpression)
    assert called["hit"] is False


def test_split_inequality_forms():
    assert numeric.split_inequality("n < n+1") == ("n", "n+1", "<", True)
    assert numeric.split_inequality("2 <= 3") == ("2", "3", "<=", False)
    assert numeric.split_inequality("a >= b") == ("a", "b", ">=", False)


@pytest.mark.parametrize("bad", ["n n", "a < b < c", "a == b", "< 3", "3 <"])
def test_split_inequality_malformed_raises(bad):
    with pytest.raises(NumericError):
        numeric.split_inequality(bad)


def test_concrete_inequality_holds_decides_and_defers():
    assert numeric.concrete_inequality_holds("2", "3", "<") is True
    assert numeric.concrete_inequality_holds("5", "3", "<") is False
    assert numeric.concrete_inequality_holds("5", "5", "<=") is True
    assert numeric.concrete_inequality_holds("5", "5", "<") is False
    # Symbolic side -> None (not numerically decidable here), never a false True/False.
    assert numeric.concrete_inequality_holds("n", "n+1", "<") is None


def test_concrete_inequality_malformed_side_raises():
    with pytest.raises(NumericError):
        numeric.concrete_inequality_holds("x / 2", "3", "<")
