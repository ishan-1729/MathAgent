"""Numeric / witness search (Layer 3 grounding).

Deterministic, exact-integer checks that kill false statements and false lemmas early, and let the
gate re-run the finite case-checks a ledger claims rather than trusting prose:

  - find_integer_solutions : enumerate integer solutions of a polynomial equation over a box
  - verify_solution_set    : confirm a claimed *complete* solution set has no counterexample in a box
  - verify_residue_cover   : confirm a case split's residues cover a complete residue system

Expressions are parsed with sympy and restricted to integer polynomial arithmetic (+ - * and
non-negative integer powers) so evaluation is exact and safe (no arbitrary code, no transcendental
functions). Boxes are size-capped to avoid runaway enumeration.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Iterable, Optional

import sympy

# Guard against accidental combinatorial blowups in a "cheap" check.
MAX_BOX_POINTS = 5_000_000
MAX_POW_EXPONENT = 64


class NumericError(ValueError):
    """Raised on an unparseable/unsafe expression or an over-large search box."""


@dataclass
class SolutionCheck:
    ok: bool
    solutions: list[dict[str, int]]
    counterexamples: list[dict[str, int]] = field(default_factory=list)  # found but not claimed
    box: dict[str, tuple[int, int]] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.ok


@dataclass
class CoverCheck:
    ok: bool
    modulus: int
    covered: list[int]
    missing: list[int] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok


_ALLOWED_NODES = (
    sympy.Add,
    sympy.Mul,
    sympy.Pow,
    sympy.Symbol,
    sympy.Integer,
    sympy.Rational,  # only as exponent-free coefficients; powers are validated separately
)


def _parse(expression: str, variables: list[str]) -> tuple[sympy.Expr, list[sympy.Symbol]]:
    if not variables:
        raise NumericError("at least one variable is required")
    syms = {v: sympy.Symbol(v, integer=True) for v in variables}
    try:
        if "=" in expression:
            lhs, rhs = expression.split("=", 1)
            expr = sympy.sympify(lhs, locals=syms) - sympy.sympify(rhs, locals=syms)
        else:
            expr = sympy.sympify(expression, locals=syms)
    except (sympy.SympifyError, SyntaxError, TypeError) as e:
        raise NumericError(f"could not parse expression {expression!r}: {e}") from e

    declared = set(syms.values())
    extra = expr.free_symbols - declared
    if extra:
        raise NumericError(f"expression uses undeclared symbols: {sorted(map(str, extra))}")

    for node in sympy.preorder_traversal(expr):
        if node.is_number:
            continue
        if isinstance(node, sympy.Pow):
            base, exp = node.as_base_exp()
            if not (exp.is_Integer and int(exp) >= 0 and int(exp) <= MAX_POW_EXPONENT):
                raise NumericError(
                    f"only non-negative integer powers up to {MAX_POW_EXPONENT} are allowed; got {node}"
                )
            continue
        if not isinstance(node, _ALLOWED_NODES):
            raise NumericError(f"disallowed operation in expression: {type(node).__name__} ({node})")

    ordered = [syms[v] for v in variables]
    return expr, ordered


def _box_points(bounds: dict[str, tuple[int, int]], variables: list[str]) -> int:
    total = 1
    for v in variables:
        lo, hi = bounds[v]
        if hi < lo:
            raise NumericError(f"bound for {v!r} is empty: [{lo}, {hi}]")
        total *= (hi - lo + 1)
    return total


def _iter_box(bounds: dict[str, tuple[int, int]], variables: list[str]) -> Iterable[tuple[int, ...]]:
    ranges = [range(bounds[v][0], bounds[v][1] + 1) for v in variables]
    yield from itertools.product(*ranges)


def find_integer_solutions(
    expression: str,
    variables: list[str],
    bounds: dict[str, tuple[int, int]],
) -> list[dict[str, int]]:
    """All integer (v1,...,vn) in the closed box with expression == 0. Exact integer arithmetic."""
    missing = [v for v in variables if v not in bounds]
    if missing:
        raise NumericError(f"no bounds given for variables: {missing}")

    n = _box_points(bounds, variables)
    if n > MAX_BOX_POINTS:
        raise NumericError(f"search box has {n} points, exceeding MAX_BOX_POINTS={MAX_BOX_POINTS}")

    expr, ordered = _parse(expression, variables)
    # Pure-Python evaluation => exact integer arithmetic (no float error, no numpy).
    f = sympy.lambdify(ordered, expr, modules=[{}])

    out: list[dict[str, int]] = []
    for point in _iter_box(bounds, variables):
        if f(*point) == 0:
            out.append({v: int(x) for v, x in zip(variables, point)})
    return out


def _normalize_assignment(a: dict[str, int], variables: list[str]) -> tuple[int, ...]:
    return tuple(int(a[v]) for v in variables)


def verify_solution_set(
    expression: str,
    variables: list[str],
    bounds: dict[str, tuple[int, int]],
    claimed: Iterable[dict[str, int]],
) -> SolutionCheck:
    """Confirm `claimed` is the complete solution set *within the box*: ok iff every actual solution
    in the box is among `claimed`. Returns any counterexamples (actual solutions not claimed)."""
    actual = find_integer_solutions(expression, variables, bounds)
    claimed_keys = {_normalize_assignment(c, variables) for c in claimed}
    counter = [s for s in actual if _normalize_assignment(s, variables) not in claimed_keys]
    return SolutionCheck(
        ok=not counter,
        solutions=actual,
        counterexamples=counter,
        box={v: tuple(bounds[v]) for v in variables},
    )


def verify_residue_cover(modulus: int, residues: Iterable[int]) -> CoverCheck:
    """Confirm the residues cover a complete residue system mod `modulus`."""
    if modulus < 1:
        raise NumericError(f"modulus must be >= 1; got {modulus}")
    covered = {int(r) % modulus for r in residues}
    missing = sorted(set(range(modulus)) - covered)
    return CoverCheck(
        ok=not missing,
        modulus=modulus,
        covered=sorted(covered),
        missing=missing,
    )
