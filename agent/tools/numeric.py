"""Numeric / witness search (Layer 3 grounding).

Deterministic, exact-integer checks that kill false statements and false lemmas early, and let the
gate re-run the finite case-checks a ledger claims rather than trusting prose:

  - find_integer_solutions   : enumerate integer solutions of a polynomial equation over a box
  - find_points_where_nonneg : box points where expr >= 0 (used to falsify a "strict decrease")
  - verify_solution_set      : confirm a claimed *complete* solution set (no missing, no spurious)
  - verify_residue_cover     : confirm a case split's residues cover a complete residue system

Expressions are parsed by a restricted AST walk (Python's `ast`, never eval/exec/sympify) and limited
to **integer** polynomial arithmetic (+ - * and non-negative integer powers, integer coefficients
only) so evaluation is exact and safe (no arbitrary code, no transcendental functions, no
float/rational leaks). Untrusted, model-controlled strings reach this parser, so it must never
execute code embedded in the input: only +,-,*,** over integer literals and declared symbols are
translated into sympy objects; calls, attribute access, and every other construct are rejected at the
AST level before any value is built. Boxes are capped both in point-count and in integer magnitude to
keep a "cheap" check cheap.
"""
from __future__ import annotations

import ast
import itertools
from dataclasses import dataclass, field
from typing import Iterable

import sympy

# Guard against accidental combinatorial blowups in a "cheap" check.
MAX_BOX_POINTS = 5_000_000
MAX_POW_EXPONENT = 64
MAX_ABS_BOUND = 10**9  # bound magnitude cap (huge-magnitude bignum arithmetic = soft DoS)


class NumericError(ValueError):
    """Raised on an unparseable/unsafe expression or an over-large search box."""


@dataclass
class SolutionCheck:
    ok: bool
    solutions: list[dict[str, int]]
    counterexamples: list[dict[str, int]] = field(default_factory=list)  # actual, not claimed
    spurious_claims: list[dict[str, int]] = field(default_factory=list)  # claimed, not a real solution
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


# Operators allowed in the polynomial AST. Numeric leaves are handled separately (integers only);
# Pow is validated separately (non-negative integer exponent only).
_ALLOWED_NODES = (sympy.Add, sympy.Mul, sympy.Pow, sympy.Symbol)


def _build_from_ast(node: ast.AST, syms: dict[str, sympy.Symbol]) -> sympy.Expr:
    """Recursively translate a *validated* Python AST node into a sympy Expr.

    SECURITY: this is the only place an untrusted expression is turned into a value. It does NOT use
    eval/exec/sympify/parse_expr — every node type is matched explicitly and anything outside the
    integer-polynomial grammar (calls, attribute access, subscripts, names that aren't declared
    variables, non-integer literals, ...) raises NumericError *before* any sympy object capable of
    leaking module globals is constructed. Crucially, validation and construction happen during this
    structural walk, so no arbitrary Python is ever executed: a literal like 'os.system("...")'
    surfaces as an ast.Call/ast.Attribute node and is rejected, never invoked.
    """
    if isinstance(node, ast.Expression):
        return _build_from_ast(node.body, syms)

    # Integer literal. (ast.Constant covers Python 3.8+; reject bools and non-int constants.)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, int):
            raise NumericError(f"non-integer numeric leaf not allowed: {node.value!r}")
        return sympy.Integer(node.value)

    # Declared variable. Any other bare name is an undeclared symbol (or an attempt to reach a
    # builtin/constructor by name) and is refused before it can be turned into a value.
    if isinstance(node, ast.Name):
        if node.id not in syms:
            raise NumericError(f"expression uses undeclared symbol: {node.id!r}")
        return syms[node.id]

    # Unary +/- (e.g. "-x", "+3"); unary anything-else (e.g. ~, not) is rejected.
    if isinstance(node, ast.UnaryOp):
        operand = _build_from_ast(node.operand, syms)
        if isinstance(node.op, ast.UAdd):
            return operand
        if isinstance(node.op, ast.USub):
            return -operand
        raise NumericError(f"disallowed unary operator: {type(node.op).__name__}")

    # Binary +, -, *, ** only. Division, modulo, bit-ops, matmul, etc. are rejected.
    if isinstance(node, ast.BinOp):
        left = _build_from_ast(node.left, syms)
        right = _build_from_ast(node.right, syms)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Pow):
            return left ** right
        raise NumericError(f"disallowed binary operator: {type(node.op).__name__}")

    raise NumericError(f"disallowed syntax in expression: {type(node).__name__}")


def _safe_parse(text: str, syms: dict[str, sympy.Symbol]) -> sympy.Expr:
    """Parse a single sub-expression with NO eval/__builtins__/sympify reachable.

    We compile the text to a Python AST in 'eval' mode (which only *parses* — it never executes),
    then translate the tree node-by-node through `_build_from_ast`. Because translation rejects every
    construct outside the +,-,*,** / integer / declared-symbol grammar before building it, arbitrary
    code embedded as literal call/attribute syntax (e.g. 'Integer(Symbol.__new__.__globals__...)')
    is refused at the AST level and never evaluated.
    """
    # ast.parse('eval') rejects leading/trailing whitespace (and the '=' split path hands us sides
    # like ' y**3'); strip it so well-formed expressions with surrounding spaces still parse.
    stripped = text.strip()
    if not stripped:
        raise NumericError("could not parse expression: empty sub-expression")
    try:
        tree = ast.parse(stripped, mode="eval")
    except SyntaxError as e:
        raise NumericError(f"could not parse expression {text!r}: {e}") from e
    return _build_from_ast(tree, syms)


def _parse(expression: str, variables: list[str]) -> tuple[sympy.Expr, list[sympy.Symbol]]:
    if not variables:
        raise NumericError("at least one variable is required")
    syms = {v: sympy.Symbol(v, integer=True) for v in variables}
    try:
        if "=" in expression:
            lhs, rhs = expression.split("=", 1)
            expr = _safe_parse(lhs, syms) - _safe_parse(rhs, syms)
        else:
            expr = _safe_parse(expression, syms)
    except Exception as e:  # noqa: BLE001 - _safe_parse already raises NumericError, but sympy
        # arithmetic on the built nodes (e.g. the lhs-rhs subtraction) could surface other errors;
        # the documented contract is that an unparseable/unsafe expression becomes a NumericError,
        # never an uncaught exception.
        raise NumericError(f"could not parse expression {expression!r}: {e}") from e

    declared = set(syms.values())
    extra = expr.free_symbols - declared
    if extra:
        raise NumericError(f"expression uses undeclared symbols: {sorted(map(str, extra))}")

    for node in sympy.preorder_traversal(expr):
        if node.is_number:
            # Integer leaves only: reject Float/Rational so nothing is silently floated and the
            # "exact integer arithmetic" guarantee holds (a Float coefficient could make a
            # non-decrease look like a decrease).
            if not node.is_Integer:
                raise NumericError(f"non-integer numeric leaf not allowed: {node}")
            continue
        if isinstance(node, sympy.Pow):
            _base, exp = node.as_base_exp()
            if not (exp.is_Integer and 0 <= int(exp) <= MAX_POW_EXPONENT):
                raise NumericError(
                    f"only non-negative integer powers up to {MAX_POW_EXPONENT} are allowed; got {node}"
                )
            continue
        if not isinstance(node, _ALLOWED_NODES):
            raise NumericError(f"disallowed operation in expression: {type(node).__name__} ({node})")

    ordered = [syms[v] for v in variables]
    return expr, ordered


def _compile(expression: str, variables: list[str]):
    """Parse + validate + build an exact-integer evaluator (pure-Python operators, no float/numpy)."""
    expr, ordered = _parse(expression, variables)
    f = sympy.lambdify(ordered, expr, modules=[{}])
    return expr, ordered, f


def _require_bounds(variables: list[str], bounds: dict[str, tuple[int, int]]) -> None:
    missing = [v for v in variables if v not in bounds]
    if missing:
        raise NumericError(f"no bounds given for variables: {missing}")
    for v in variables:
        lo, hi = bounds[v]
        if not (isinstance(lo, int) and isinstance(hi, int)) or isinstance(lo, bool) or isinstance(hi, bool):
            raise NumericError(f"bounds for {v!r} must be integers: {(lo, hi)}")
        if hi < lo:
            raise NumericError(f"bound for {v!r} is empty: [{lo}, {hi}]")
        if abs(lo) > MAX_ABS_BOUND or abs(hi) > MAX_ABS_BOUND:
            raise NumericError(f"bound for {v!r} exceeds MAX_ABS_BOUND={MAX_ABS_BOUND}")


def _box_points(bounds: dict[str, tuple[int, int]], variables: list[str]) -> int:
    total = 1
    for v in variables:
        lo, hi = bounds[v]
        total *= (hi - lo + 1)
    return total


def _check_box(variables: list[str], bounds: dict[str, tuple[int, int]]) -> None:
    _require_bounds(variables, bounds)
    n = _box_points(bounds, variables)
    if n > MAX_BOX_POINTS:
        raise NumericError(f"search box has {n} points, exceeding MAX_BOX_POINTS={MAX_BOX_POINTS}")


def _iter_box(bounds: dict[str, tuple[int, int]], variables: list[str]) -> Iterable[tuple[int, ...]]:
    ranges = [range(bounds[v][0], bounds[v][1] + 1) for v in variables]
    yield from itertools.product(*ranges)


def find_integer_solutions(
    expression: str,
    variables: list[str],
    bounds: dict[str, tuple[int, int]],
) -> list[dict[str, int]]:
    """All integer (v1,...,vn) in the closed box with expression == 0. Exact integer arithmetic."""
    _check_box(variables, bounds)
    _expr, _ordered, f = _compile(expression, variables)
    out: list[dict[str, int]] = []
    for point in _iter_box(bounds, variables):
        if f(*point) == 0:
            out.append({v: int(x) for v, x in zip(variables, point)})
    return out


def find_points_where_nonneg(
    expression: str,
    variables: list[str],
    bounds: dict[str, tuple[int, int]],
    limit: int = 8,
) -> list[dict[str, int]]:
    """Up to `limit` box points where expression >= 0 (used to falsify a claimed strict decrease)."""
    _check_box(variables, bounds)
    _expr, _ordered, f = _compile(expression, variables)
    out: list[dict[str, int]] = []
    for point in _iter_box(bounds, variables):
        if f(*point) >= 0:
            out.append({v: int(x) for v, x in zip(variables, point)})
            if len(out) >= limit:
                break
    return out


def _key(a: dict[str, int], variables: list[str]) -> tuple[int, ...]:
    return tuple(int(a[v]) for v in variables)


def verify_solution_set(
    expression: str,
    variables: list[str],
    bounds: dict[str, tuple[int, int]],
    claimed: Iterable[dict[str, int]],
) -> SolutionCheck:
    """Confirm `claimed` is the complete, correct solution set within the box:
      - no *missing* solution (an actual solution in the box not in `claimed`) -> counterexamples;
      - no *spurious* claim (a claimed assignment that is not actually a solution) -> spurious_claims.
    """
    _check_box(variables, bounds)
    _expr, _ordered, f = _compile(expression, variables)

    claimed_list = list(claimed)
    actual = [
        {v: int(x) for v, x in zip(variables, pt)}
        for pt in _iter_box(bounds, variables)
        if f(*pt) == 0
    ]
    claimed_keys = {_key(c, variables) for c in claimed_list}
    counter = [s for s in actual if _key(s, variables) not in claimed_keys]
    spurious = [c for c in claimed_list if f(*_key(c, variables)) != 0]
    return SolutionCheck(
        ok=not counter and not spurious,
        solutions=actual,
        counterexamples=counter,
        spurious_claims=spurious,
        box={v: tuple(bounds[v]) for v in variables},
    )


def verify_residue_cover(modulus: int, residues: Iterable[int]) -> CoverCheck:
    """Confirm the residues cover a complete residue system mod `modulus`."""
    if not isinstance(modulus, int) or isinstance(modulus, bool):
        raise NumericError(f"modulus must be an int; got {modulus!r}")
    if modulus < 1:
        raise NumericError(f"modulus must be >= 1; got {modulus}")
    covered: set[int] = set()
    for r in residues:
        if not isinstance(r, int) or isinstance(r, bool):
            raise NumericError(f"residues must be integers; got {r!r}")
        covered.add(r % modulus)
    missing = sorted(set(range(modulus)) - covered)
    return CoverCheck(ok=not missing, modulus=modulus, covered=sorted(covered), missing=missing)
