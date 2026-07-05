"""Final-answer equivalence scoring (MathArena ArXivMath style).

ArXivMath is a *final-answer* benchmark: each item ships a gold `answer` string and grading checks
whether a candidate answer is mathematically equivalent to it. This mirrors MathArena's grader —
normalize a LaTeX-ish answer, then test SYMBOLIC equivalence with SymPy, with numeric, set/tuple, and
normalized-string fallbacks. Deterministic and offline (SymPy is already a dependency).
"""
from __future__ import annotations

import re
from typing import Optional

from sympy import Function, Max, Min, Pow, preorder_traversal, simplify
from sympy.core.function import AppliedUndef
from sympy.parsing.sympy_parser import (
    parse_expr, standard_transformations, implicit_multiplication_application, convert_xor,
)

# Implicit multiplication ("2sqrt(2)", "2x") + caret-as-power, so LaTeX-ish answers parse.
_TRANSFORMS = standard_transformations + (implicit_multiplication_application, convert_xor)

# parse_expr() uses eval(), and by DEFAULT seeds its global namespace with `from sympy import *`
# PLUS every Python builtin function (print, exec, open, __import__, ...). Answer strings are
# UNTRUSTED model/benchmark output, so "print('x')" or "__import__('os').system(...)" would execute
# during parsing. We instead pass a locked-down global namespace: the SymPy symbols only, with
# `__builtins__` emptied so unknown names like `print` resolve to nothing (parse fails -> None)
# rather than to a dangerous callable. No side effect occurs.
_SAFE_GLOBALS: dict = {}
exec("from sympy import *", _SAFE_GLOBALS)        # noqa: S102 - fixed trusted literal, not user input
_SAFE_GLOBALS["max"], _SAFE_GLOBALS["min"] = Max, Min   # parse_expr's default max/min aliases

# DoS guard, PART 1 (make construction inert). The star-import puts ~170 SymPy `Function` classes in
# scope (factorial, gamma, fibonacci, bernoulli, catalan, harmonic, primepi, prime, nextprime, loggamma,
# ...). MANY of these EVALUATE EAGERLY at parse time even under evaluate=False when handed a bare integer
# literal — e.g. `fibonacci(100000000)` materializes a ~20-million-digit integer during PARSING and hangs
# the grader before any tree walk can inspect it. The old approach enumerated a handful of names to
# rebind; that is fundamentally incomplete against `from sympy import *` (every un-listed function was a
# live DoS). Instead rebind EVERY `Function` CLASS to an inert `UndefinedFunction` of the same name, so a
# call `f(...)` builds an inert `AppliedUndef` node that NEVER evaluates its argument at parse time,
# regardless of which function is named. (`sqrt`/`Max`/`Min` are plain functions, not `Function` classes,
# so they are untouched and legitimate `sqrt(2)` answers still grade.) The materialization budget below
# (PART 2) is the LOAD-BEARING guard; this rebind just guarantees the tree is reachable to bound.
for _name, _val in list(_SAFE_GLOBALS.items()):
    if isinstance(_val, type) and issubclass(_val, Function) and _val is not Function:
        _SAFE_GLOBALS[_name] = Function(_name)     # inert UndefinedFunction of the same name

_SAFE_GLOBALS["__builtins__"] = {}                # block print/exec/open/eval/__import__/etc.

# Deterministic (Windows-safe, no signal/timeout) DoS caps for the untrusted answer parse.
_MAX_ANSWER_LEN = 2000    # an answer string this long is not a real final answer; decline it.
# DoS guard, PART 2 (the LOAD-BEARING construction bound; see `_within_materialization_budget`). These
# cap how big an integer the evaluate=True reparse could materialize, INDEPENDENT of which function or
# operator is used, so no combinatorial callable can starve the guard by not being on a name list.
_MAX_POW_EXP = 10 ** 4        # a single Pow with a concrete |exponent| bigger than this -> decline.
_MAX_MATERIALIZE = 10 ** 6    # product of (|exp|+1) over all Pow nodes -> stops product-of-powers.
_MAX_FN_ARG = 10 ** 4         # a function application with a numeric-literal |argument| bigger -> decline.

# LaTeX noise that carries no math meaning.
_LATEX_NOISE = [r"\left", r"\right", r"\displaystyle", r"\,", r"\!", r"\;", r"\:",
                r"\quad", r"\qquad", r"\medspace", r"\thinspace"]
_FRAC = re.compile(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
_SQRT_N = re.compile(r"\\sqrt\s*\[([^\]]*)\]\s*\{([^{}]*)\}")
_SQRT = re.compile(r"\\sqrt\s*\{([^{}]*)\}")
_LEAD = re.compile(r"^\s*(?:the\s+)?(?:final\s+)?answer\s*(?:is|:)?\s*", re.IGNORECASE)
# A comma used as a thousands GROUPING separator: 1-3 digits, then one or more groups of exactly 3
# digits (so "1,000" / "12,345,678" collapse, but "3,5" and a set like "1, 2, 3" are untouched).
_THOUSANDS = re.compile(r"(?<!\d)(\d{1,3}(?:,\d{3})+)(?!\d)")


def _strip_thousands(s: str) -> str:
    return _THOUSANDS.sub(lambda m: m.group(1).replace(",", ""), s)


def _clean(s: str) -> str:
    s = (s or "").strip()
    s = _LEAD.sub("", s)
    for d in ("$$", "$", r"\(", r"\)", r"\[", r"\]"):
        s = s.replace(d, "")
    for tok in _LATEX_NOISE:
        s = s.replace(tok, " ")
    s = s.strip().rstrip(".")
    return re.sub(r"\s+", " ", s).strip()


def _latexish_to_sympy(s: str) -> str:
    prev = None
    while prev != s:                       # resolve a few levels of nested \frac / \sqrt
        prev = s
        s = _FRAC.sub(r"(((\1))/((\2)))", s)
        s = _SQRT_N.sub(r"((\2)**(1/(\1)))", s)
        s = _SQRT.sub(r"sqrt((\1))", s)
    s = s.replace(r"\cdot", "*").replace(r"\times", "*").replace(r"\div", "/")
    s = s.replace(r"\pi", "pi").replace(r"\infty", "oo").replace(r"\ ", " ")
    # Any LaTeX command still present carries math meaning we did NOT translate (e.g. \pm, \mp,
    # \sqrt with no braces). Silently deleting it mis-grades the answer: \pm 1 would become "1" and
    # compare equal to 1, even though \pm 1 means {1, -1}. Refuse to parse instead (caught by
    # _to_expr -> None), so the grader declines rather than fabricating an equivalence.
    if re.search(r"\\[a-zA-Z]+", s):
        raise ValueError(f"unsupported LaTeX command in answer: {s!r}")
    s = s.replace("{", "(").replace("}", ")")
    return s


def _numeric_magnitude(node) -> Optional[float]:
    """|value| of a purely-numeric SymPy node as a float, or None if it is symbolic. Returns +inf when
    the value is finite-but-astronomical (float() overflows) so the caller treats it as over-budget."""
    if not node.is_number:                        # a symbol (e.g. x, n) — not a materialization cost.
        return None
    try:
        return abs(float(node))
    except (OverflowError, ValueError, TypeError):
        return float("inf")                       # concrete but too large to even float() -> over cap.


def _within_materialization_budget(inert) -> bool:
    """THE LOAD-BEARING DoS BOUND (construction-based, name-agnostic). Walk the ``evaluate=False`` parse
    tree and decide whether the ``evaluate=True`` reparse could materialize an astronomically large
    value. Returns False (decline) — WITHOUT ever evaluating anything — if any of:

      (a) a ``Pow`` node has a concrete integer exponent with |exp| > ``_MAX_POW_EXP`` (a single huge
          power like ``10**(10**8)`` / ``2**(2**40)``);
      (b) the product of ``(|exp| + 1)`` over ALL concrete-exponent ``Pow`` nodes exceeds
          ``_MAX_MATERIALIZE`` (a product-of-powers ``2**9999 * 3**9999 * ...`` whose factors are each
          UNDER the per-Pow cap but whose product still materializes a giant integer — the gap the old
          per-Pow-only check missed);
      (c) a function application (inert ``AppliedUndef`` — every star-imported ``Function`` is rebound to
          one, PART 1) has a numeric-literal argument with |arg| > ``_MAX_FN_ARG`` (``fibonacci(10**8)`` /
          ``bernoulli(10**8)`` / ``catalan(10**8)`` / ... — caught by ARGUMENT SIZE, never by enumerating
          which function was called, so no un-listed callable can starve it).

    Cannot be starved: a huge integer can only enter the reparse via a big power (a/b) or a big-argument
    function call (c); (a)+(b) bound both power channels multiplicatively and (c) bounds every function
    channel by argument magnitude regardless of the callee's identity. The walk is a single
    ``preorder_traversal`` that visits repeated nodes WITH MULTIPLICITY — crucially, ``a**k * a**k * ...``
    (SAME base) keeps N distinct ``Pow`` occurrences in the ``evaluate=False`` ``Mul`` even though
    ``atoms(Pow)`` would DEDUP them to one, so the multiplicative budget (b) counts every factor and the
    same-base product cannot slip the budget. ``_numeric_magnitude`` uses ``float()``, which never
    materializes the full integer, so evaluating an exponent's magnitude is itself cheap for (a)."""
    budget = 1
    for node in preorder_traversal(inert):
        if isinstance(node, Pow):
            mag = _numeric_magnitude(node.exp)
            if mag is None:                       # symbolic exponent (x**n) is fine.
                continue
            if mag > _MAX_POW_EXP:                # (a) single huge power.
                return False
            budget *= (int(mag) + 1)
            if budget > _MAX_MATERIALIZE:         # (b) product-of-powers materialization budget.
                return False
        elif isinstance(node, AppliedUndef):      # (c) big-argument function call, name-agnostic.
            for arg in node.args:
                mag = _numeric_magnitude(arg)
                if mag is not None and mag > _MAX_FN_ARG:
                    return False
    return True


def _to_expr(s: str):
    try:
        cleaned = _strip_thousands(_clean(s))
        text = _latexish_to_sympy(cleaned)
        if len(text) > _MAX_ANSWER_LEN:           # absurdly long -> decline before parsing.
            return None
        # DoS guard (deterministic, no timeout): parse UNEVALUATED first so huge power towers and every
        # function call stay inert (evaluate=False keeps Integer literals from collapsing, and PART 1
        # rebound every Function class to an inert UndefinedFunction so no function EVALUATES ITS ARGUMENT
        # at parse time), then refuse before evaluating if the tree exceeds the materialization budget.
        # global_dict is the locked-down SymPy namespace (no Python builtins) so eval() in parse_expr
        # cannot execute untrusted callables. Pass a copy: parse_expr may inject names.
        inert = parse_expr(text, transformations=_TRANSFORMS,
                           global_dict=dict(_SAFE_GLOBALS), evaluate=False)
        if not _within_materialization_budget(inert):
            return None
        return parse_expr(text, transformations=_TRANSFORMS,
                          global_dict=dict(_SAFE_GLOBALS), evaluate=True)
    except Exception:
        return None


def _as_number(s: str) -> Optional[float]:
    expr = _to_expr(s)
    if expr is None:
        return None
    try:
        if expr.free_symbols:
            return None
        return float(expr.evalf())
    except Exception:
        return None


def _split_top(inner: str) -> list[str]:
    """Split on top-level commas (ignoring commas inside (), [], {})."""
    out, depth, cur = [], 0, []
    for ch in inner:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur)); cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur))
    return [x.strip() for x in out if x.strip()]


def _split_collection(s: str) -> Optional[tuple[str, list[str]]]:
    """Classify `s` as a collection ONLY when its delimiters are EXPLICIT: `{...}` is a set and
    `(...)`/`[...]` a tuple. A bare comma is NOT enough — `3,5` is the number 3.5 in many locales and
    `1,000` is a thousands-grouped integer, so treating any comma'd string as a set silently
    mis-grades them. Numeric/expression parsing (which strips thousands separators) runs first in
    `answers_equivalent`, so by the time we get here a bare-comma string is genuinely ambiguous and
    we decline to call it a collection."""
    s = _clean(s)
    if len(s) >= 2 and s[0] == "{" and s[-1] == "}":
        return ("set", _split_top(s[1:-1]))
    if len(s) >= 2 and ((s[0] == "(" and s[-1] == ")") or (s[0] == "[" and s[-1] == "]")) and "," in s:
        # NOTE: a bracketed list "[1, 2]" grades as the ORDERED pair (1, 2) here (same kind as
        # "(1, 2)"). This is a deliberate v1 simplification: a closed-interval answer like [1, 2]
        # (all reals between 1 and 2) is NOT modeled and would need a distinct collection kind.
        return ("tuple", _split_top(s[1:-1]))
    return None


def answers_equivalent(pred: Optional[str], gold: Optional[str], *, atol: float = 1e-9) -> bool:
    """True iff candidate answer `pred` is mathematically equivalent to gold `gold`."""
    if pred is None or gold is None:
        return False
    p, g = _clean(pred), _clean(gold)
    if p == g and p != "":
        return True

    pc, gc = _split_collection(pred), _split_collection(gold)
    if pc and gc:
        kp, ep = pc
        kg, eg = gc
        if kp != kg or len(ep) != len(eg):
            return False
        if kp == "tuple":                          # ordered
            return all(answers_equivalent(a, b, atol=atol) for a, b in zip(ep, eg))
        remaining = list(eg)                       # unordered set: greedy match
        for a in ep:
            hit = next((b for b in remaining if answers_equivalent(a, b, atol=atol)), None)
            if hit is None:
                return False
            remaining.remove(hit)
        return True

    np_, ng = _as_number(pred), _as_number(gold)
    if np_ is not None and ng is not None:
        return abs(np_ - ng) <= atol * max(1.0, abs(ng))

    ep, eg = _to_expr(pred), _to_expr(gold)
    if ep is not None and eg is not None:
        try:
            if simplify(ep - eg) == 0:
                return True
        except Exception:
            pass
        try:
            return bool(ep.equals(eg))
        except Exception:
            return False
    return False
