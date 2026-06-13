"""Final-answer equivalence scoring (MathArena ArXivMath style).

ArXivMath is a *final-answer* benchmark: each item ships a gold `answer` string and grading checks
whether a candidate answer is mathematically equivalent to it. This mirrors MathArena's grader —
normalize a LaTeX-ish answer, then test SYMBOLIC equivalence with SymPy, with numeric, set/tuple, and
normalized-string fallbacks. Deterministic and offline (SymPy is already a dependency).
"""
from __future__ import annotations

import re
from typing import Optional

from sympy import simplify
from sympy.parsing.sympy_parser import (
    parse_expr, standard_transformations, implicit_multiplication_application, convert_xor,
)

# Implicit multiplication ("2sqrt(2)", "2x") + caret-as-power, so LaTeX-ish answers parse.
_TRANSFORMS = standard_transformations + (implicit_multiplication_application, convert_xor)

# LaTeX noise that carries no math meaning.
_LATEX_NOISE = [r"\left", r"\right", r"\displaystyle", r"\,", r"\!", r"\;", r"\:",
                r"\quad", r"\qquad", r"\medspace", r"\thinspace"]
_FRAC = re.compile(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
_SQRT_N = re.compile(r"\\sqrt\s*\[([^\]]*)\]\s*\{([^{}]*)\}")
_SQRT = re.compile(r"\\sqrt\s*\{([^{}]*)\}")
_LEAD = re.compile(r"^\s*(?:the\s+)?(?:final\s+)?answer\s*(?:is|:)?\s*", re.IGNORECASE)


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
    s = re.sub(r"\\[a-zA-Z]+", "", s)      # drop any remaining LaTeX command words
    s = s.replace("{", "(").replace("}", ")")
    return s


def _to_expr(s: str):
    try:
        return parse_expr(_latexish_to_sympy(_clean(s)), transformations=_TRANSFORMS, evaluate=True)
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
    s = _clean(s)
    if len(s) >= 2 and s[0] == "{" and s[-1] == "}":
        return ("set", _split_top(s[1:-1]))
    if len(s) >= 2 and s[0] == "(" and s[-1] == ")" and "," in s:
        return ("tuple", _split_top(s[1:-1]))
    if "," in s and "=" not in s:
        return ("set", _split_top(s))
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
