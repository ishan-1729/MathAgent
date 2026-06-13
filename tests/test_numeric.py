"""Tests for the numeric/witness tool (exact integer search + case-cover verification)."""
import pytest
from hypothesis import given, strategies as st

from agent.tools import numeric
from agent.tools.numeric import (
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
