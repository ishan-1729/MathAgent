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
from typing import Iterable, Optional

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


def _to_pow(text: str) -> str:
    """Normalize caret exponentiation ('2^n') to Python power ('2**n') before the no-eval parse.

    In elementary number-theory obligation expressions (bounding inequalities, descent measures) '^'
    ALWAYS means exponentiation, never bitwise XOR. Without this, ``ast.parse`` reads '^' as
    ``ast.BitXor`` -> a disallowed node -> a spurious 'malformed' REJECT of a perfectly valid bound
    like '2^n < 3^n'. Mirrors the caret handling in agent/tools/answer_check.py. Purely textual; it
    does not make anything executable (the restricted AST still rejects calls/attributes/subscripts).
    """
    return text.replace("^", "**")


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
    # Caret-as-exponent ('2^n' -> '2**n') so a valid bound is not mis-parsed as BitXor (see _to_pow).
    stripped = _to_pow(text.strip())
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
    # Validate each claimed assignment BEFORE keying it (mirrors verify_residue_cover's int guard):
    # every variable must be present with a genuine int value — bool is excluded (it subclasses int)
    # and float is rejected, so a claimed {"x": 2.4} cannot int()-truncate to the root x=2 and
    # silently verify (the witness-evolution reward hack), and a missing key cannot leak a raw
    # KeyError through _key. A non-integer / malformed claim is an UNREPRESENTABLE spec ->
    # NumericError (fail closed), exactly like the residue path: check_witness_spec records it as a
    # spec-level error (the REJECTED-control taxonomy — type-garbage is an error row and a HARD
    # failure callers must not reward, distinct from a well-typed-but-wrong SPURIOUS claim).
    for c in claimed_list:
        if not isinstance(c, dict):
            raise NumericError(f"claimed entry must be a variable->int object; got {c!r}")
        for v in variables:
            if v not in c:
                raise NumericError(f"claimed entry {c!r} is missing variable {v!r}")
            if not isinstance(c[v], int) or isinstance(c[v], bool):
                raise NumericError(f"claimed values must be integers; got {v}={c[v]!r}")
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


def verify_descent_decreases(
    measure_expr: str,
    next_expr: str,
    variables: list[str],
    bounds: dict[str, tuple[int, int]],
) -> SolutionCheck:
    """Confirm `next_expr < measure_expr` at EVERY point of the closed box (strict descent).

    Returns a SolutionCheck whose ``ok`` is True iff the constructed next-value's measure is strictly
    below the current measure everywhere in the box. ``counterexamples`` holds up to a few witnesses
    where ``next - measure >= 0`` (i.e. where descent does NOT strictly decrease). Both expressions are
    parsed by the same restricted no-eval integer AST as everything else here; nothing is ever
    eval/exec'd. A vacuous (empty/zero-width) measure or next expression that fails to parse raises
    NumericError, surfacing as an obligation failure upstream rather than a silent pass.
    """
    # ``(next) - (measure) >= 0`` anywhere is a descent violation. find_points_where_nonneg parses
    # the combined expression through the restricted AST, so measure/next are validated there too.
    bad = find_points_where_nonneg(f"({next_expr}) - ({measure_expr})", variables, bounds)
    return SolutionCheck(
        ok=not bad,
        solutions=[],
        counterexamples=bad,
        box={v: tuple(bounds[v]) for v in variables},
    )


# Comparison operators a concrete-integer bound may use, longest-token-first so '<=' is matched
# before '<'. '==' / '!=' are deliberately excluded: a "bound" is an order relation, and an equality
# is not a bounding obligation. The boolean it evaluates to is the STRICT vs non-strict relation.
_CONCRETE_OPS: tuple[tuple[str, bool], ...] = (
    ("<=", False),
    (">=", False),
    ("<", True),
    (">", True),
)


class SymbolicExpression(NumericError):
    """Raised when an expression is well-formed but NOT a closed integer constant (it has a free
    variable). It is a NumericError subclass so existing ``except NumericError`` paths still catch it,
    but lets a caller distinguish "symbolic, can't decide numerically" from "malformed"."""


def _name_ids(node: ast.AST) -> set[str]:
    """Collect every ``ast.Name`` id in a parsed expression WITHOUT building or executing any value.

    Only ``node.id`` strings are read off the structural tree (no eval/exec), so this is safe to run
    on untrusted input purely to learn which identifiers appear before deciding concrete vs symbolic.
    """
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def evaluate_integer_constant(expression: str) -> int:
    """Evaluate a CLOSED integer expression (no free symbols) to its exact int via the no-eval AST.

    The expression is parsed by the same restricted integer-polynomial grammar as everything else here
    (``_safe_parse`` — never eval/exec/sympify), then required to be variable-free so it collapses to a
    single integer. A symbolic expression (any free symbol) raises ``SymbolicExpression``; a non-integer
    leaf or any disallowed construct raises ``NumericError``. Nothing in the string is ever executed.
    Used to numerically confirm a *concrete* bounding inequality without trusting prose.
    """
    stripped = _to_pow((expression or "").strip())   # caret-as-exponent: '2^n' -> '2**n' (see _to_pow)
    if not stripped:
        raise NumericError("could not parse expression: empty sub-expression")
    try:
        tree = ast.parse(stripped, mode="eval")
    except SyntaxError as e:
        raise NumericError(f"could not parse expression {expression!r}: {e}") from e
    # Declare exactly the bare names that appear, so each is treated as a (free) symbol rather than an
    # "undeclared symbol" hard error. We build the expression FIRST (so the restricted AST walker still
    # rejects calls / attributes / subscripts / division as malformed BEFORE we ever decide "symbolic"),
    # and only a structurally-valid expression with a REMAINING free symbol is "symbolic, not concrete".
    syms = {name: sympy.Symbol(name, integer=True) for name in _name_ids(tree)}
    expr = _build_from_ast(tree, syms)
    if expr.free_symbols:
        raise SymbolicExpression(
            f"expression is not a closed integer constant (free symbols "
            f"{sorted(map(str, expr.free_symbols))})"
        )
    for node in sympy.preorder_traversal(expr):
        if node.is_number:
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
    value = sympy.Integer(expr)
    if not value.is_Integer:
        raise NumericError(f"expression did not evaluate to an integer: {expr}")
    return int(value)


def split_inequality(text: str) -> tuple[str, str, str, bool]:
    """Split a single-relation inequality string into (lhs, rhs, op, strict).

    Returns the left side, the right side, the comparison operator token ('<','>','<=','>='), and
    whether the relation is STRICT ('<'/'>' -> True, '<='/'>=' -> False). Exactly one comparison
    operator must be present (chained comparisons such as ``a < b < c`` and equalities are rejected).
    Raises ``NumericError`` on a malformed relation; this only SPLITS the surface form (it does not
    evaluate either side).
    """
    if not isinstance(text, str):
        raise NumericError(f"inequality must be a string; got {type(text).__name__}")
    matches = [op for op, _strict in _CONCRETE_OPS if op in text]
    # '<' / '>' are substrings of '<=' / '>=', so collapse those before counting distinct relations.
    distinct = set(matches)
    if "<=" in distinct:
        distinct.discard("<")
    if ">=" in distinct:
        distinct.discard(">")
    if len(distinct) != 1:
        raise NumericError(
            f"inequality must contain exactly one comparison operator (<,>,<=,>=); got {text!r}"
        )
    op = next(iter(distinct))
    strict = dict(_CONCRETE_OPS)[op]
    parts = text.split(op)
    if len(parts) != 2:
        raise NumericError(f"chained/ambiguous comparison not allowed: {text!r}")
    lhs, rhs = parts[0].strip(), parts[1].strip()
    if not lhs or not rhs:
        raise NumericError(f"inequality has an empty side: {text!r}")
    return lhs, rhs, op, strict


def concrete_inequality_holds(lhs: str, rhs: str, op: str) -> Optional[bool]:
    """If both sides are CLOSED integer constants, return whether ``lhs op rhs`` holds; else None.

    A return of ``None`` means the bound is symbolic (at least one side has a free variable) and so
    cannot be numerically decided here — the caller must treat that as "shape-valid, not numerically
    refutable", NOT as a pass of a concrete check. Any *malformed* side (unparseable / non-integer /
    disallowed construct) propagates as a ``NumericError``. No eval, ever.
    """
    try:
        lv = evaluate_integer_constant(lhs)
        rv = evaluate_integer_constant(rhs)
    except SymbolicExpression:
        # At least one side is symbolic (has a free variable) -> not numerically decidable here.
        return None
    # Any OTHER NumericError (unparseable / non-integer leaf / disallowed construct) is a genuinely
    # malformed side and propagates to the caller as a hard failure.
    if op == "<":
        return lv < rv
    if op == ">":
        return lv > rv
    if op == "<=":
        return lv <= rv
    if op == ">=":
        return lv >= rv
    raise NumericError(f"unsupported comparison operator: {op!r}")


# --------------------------------------------------------------------------------------------------
# Witness/construction SPEC checker (Layer-3 evolution mode). A "spec" is a small JSON object that
# NAMES one exact-integer obligation kind and its data; it is parsed with json.loads (data only — no
# code) and dispatched to the checkers above. Every embedded EXPRESSION still goes through the
# restricted no-eval AST in `_safe_parse`, so a spec can never smuggle executable code: json.loads
# yields plain str/int/list/dict, and any expression string is only ever handed to the AST walker.
# --------------------------------------------------------------------------------------------------
@dataclass
class SpecCheck:
    ok: bool
    kind: str
    detail: str = ""
    error: Optional[str] = None  # set iff the spec was malformed/unparseable (a HARD failure)

    def __bool__(self) -> bool:
        return self.ok


# Imported lazily to keep the module import light and avoid a top-level json dependency surprise.
def _spec_obj(spec):
    import json as _json

    if isinstance(spec, dict):
        return spec
    if not isinstance(spec, str):
        raise NumericError(f"witness spec must be a JSON object or string; got {type(spec).__name__}")
    text = spec.strip()
    if not text:
        raise NumericError("witness spec is empty")
    try:
        obj = _json.loads(text)
    except Exception as e:  # noqa: BLE001 - any JSON error is a malformed spec
        raise NumericError(f"could not parse witness spec JSON: {e}") from e
    if not isinstance(obj, dict):
        raise NumericError("witness spec must be a JSON object")
    return obj


def _coerce_box(raw, variables: list[str]) -> dict[str, tuple[int, int]]:
    """Coerce a JSON ``{var: [lo, hi]}`` mapping into the {var: (lo,hi)} bounds dict (validated)."""
    if not isinstance(raw, dict):
        raise NumericError("spec 'bounds' must be an object {var: [lo, hi]}")
    box: dict[str, tuple[int, int]] = {}
    for v in variables:
        if v not in raw:
            raise NumericError(f"spec 'bounds' missing variable {v!r}")
        pair = raw[v]
        if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
            raise NumericError(f"spec bound for {v!r} must be a [lo, hi] pair")
        lo, hi = pair
        if not (isinstance(lo, int) and isinstance(hi, int)) or isinstance(lo, bool) or isinstance(hi, bool):
            raise NumericError(f"spec bound for {v!r} must be integers")
        box[v] = (int(lo), int(hi))
    return box


def check_witness_spec(spec) -> SpecCheck:
    """Score a NUMERIC witness/construction spec with the exact-integer checker (no eval, ever).

    ``spec`` is a JSON object (or its text) of one of these shapes — the ``kind`` field selects the
    checker; everything else is plain data and embedded expressions go through the restricted AST:

      {"kind": "residue_cover", "modulus": M, "residues": [r0, r1, ...]}
          -> verify_residue_cover: the residues must cover a complete residue system mod M.

      {"kind": "descent", "measure_expr": "...", "next_expr": "...",
       "variables": ["x", ...], "bounds": {"x": [lo, hi], ...}}
          -> verify_descent_decreases: next-value's measure < current measure everywhere in the box.

      {"kind": "solution_set", "expression": "...", "variables": [...],
       "bounds": {...}, "claimed": [{"x": .., ...}, ...]}
          -> verify_solution_set: the claimed set is exactly the box's solution set (no missing/spurious).

    Returns a SpecCheck. A malformed/unparseable spec sets ``error`` and ``ok=False`` (a HARD failure —
    callers must NOT reward it). This NEVER eval/exec/imports the spec: json.loads parses data only, and
    each expression string is handed solely to the no-eval AST in `_safe_parse`.
    """
    try:
        obj = _spec_obj(spec)
        kind = obj.get("kind")
        if kind == "residue_cover":
            res = verify_residue_cover(obj["modulus"], obj["residues"])
            if res.modulus < 2:
                return SpecCheck(False, "residue_cover", error=f"vacuous modulus {res.modulus} (need >= 2)")
            detail = f"mod {res.modulus} covered={res.covered} missing={res.missing}"
            return SpecCheck(bool(res.ok), "residue_cover", detail=detail)
        if kind == "descent":
            variables = list(obj["variables"])
            box = _coerce_box(obj.get("bounds", {}), variables)
            res = verify_descent_decreases(obj["measure_expr"], obj["next_expr"], variables, box)
            detail = ("strict decrease verified over box" if res.ok
                      else f"non-decrease at e.g. {res.counterexamples[0]}")
            return SpecCheck(bool(res.ok), "descent", detail=detail)
        if kind == "solution_set":
            variables = list(obj["variables"])
            box = _coerce_box(obj.get("bounds", {}), variables)
            claimed = obj.get("claimed", [])
            if not isinstance(claimed, list):
                raise NumericError("spec 'claimed' must be a list of assignments")
            res = verify_solution_set(obj["expression"], variables, box, claimed)
            detail = (f"solution set verified ({len(res.solutions)} sols)" if res.ok
                      else f"missing={res.counterexamples} spurious={res.spurious_claims}")
            return SpecCheck(bool(res.ok), "solution_set", detail=detail)
        return SpecCheck(False, str(kind), error=f"unknown witness-spec kind {kind!r}")
    except NumericError as e:
        kind = ""
        if isinstance(spec, dict):
            kind = str(spec.get("kind", ""))
        return SpecCheck(False, kind, error=str(e))
    except Exception as e:  # noqa: BLE001 - a KeyError/TypeError from a missing field is still a
        # malformed spec; surface it as a HARD failure (never an uncaught crash, never a silent pass).
        return SpecCheck(False, "", error=f"{type(e).__name__}: {e}")
