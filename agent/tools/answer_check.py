"""Final-answer equivalence scoring (MathArena ArXivMath style).

ArXivMath is a *final-answer* benchmark: each item ships a gold `answer` string and grading checks
whether a candidate answer is mathematically equivalent to it. This mirrors MathArena's grader —
normalize a LaTeX-ish answer, then test SYMBOLIC equivalence with SymPy, with numeric, set/tuple, and
normalized-string fallbacks. Deterministic and offline (SymPy is already a dependency).
"""
from __future__ import annotations

import ast
import io
import math
import re
import token
import tokenize as py_tokenize
from typing import Optional

from sympy import (
    Abs, E, Float, I, Integer, Max, Min, Mod, Rational, Symbol, acos, asin, atan, ceiling,
    cos, exp, floor, log, oo, pi, sign, sin, sqrt, tan,
)

# Untrusted answers are parsed as Python expression *data* (`ast.parse`) and converted node by node
# into a very small, explicit SymPy vocabulary.  There is no eval-backed SymPy parser and no callable
# namespace for an answer to reach transitively.  Unknown calls, attributes, indexing, comprehensions,
# lambdas and all statement syntax are rejected before any SymPy object is constructed.
_SAFE_CONSTANTS = {"pi": pi, "E": E, "I": I, "oo": oo, "infinity": oo}
_SAFE_FUNCTIONS = {
    "sqrt": sqrt, "abs": Abs, "Abs": Abs,
    "sin": sin, "cos": cos, "tan": tan,
    "asin": asin, "acos": acos, "atan": atan,
    "exp": exp, "log": log, "ln": log, "floor": floor, "ceiling": ceiling, "sign": sign,
    "min": Min, "Min": Min, "max": Max, "Max": Max,
}

# Deterministic (Windows-safe, no signal/timeout) DoS caps for the untrusted answer parse.
_MAX_ANSWER_LEN = 2000    # an answer string this long is not a real final answer; decline it.
# Structural caps are checked on the inert Python AST before approved SymPy constructors run.
_MAX_POW_EXP = 10 ** 4        # a single Pow with a concrete |exponent| bigger than this -> decline.
_MAX_MATERIALIZE = 10 ** 6    # cumulative concrete-exponent construction work across Pow nodes.
_MAX_AST_NODES = 512
_MAX_AST_DEPTH = 64
_MAX_RESULT_BITS = 10 ** 6    # estimated exact-integer materialization cap (~125 KiB).
_MAX_EAGER_FUNCTION_ARG_BITS = 2048
_MAX_EAGER_FUNCTION_WORK = 8192
_MAX_FUNCTION_ARGS = 32
_MAX_DELIMITER_DEPTH = 64
_MAX_COLLECTION_ITEMS = 32
_MAX_COMPARISON_WORK = 4096

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
_DECIMAL_LITERAL = re.compile(
    r"(?i)(?:(?P<int>\d+)(?:\.(?P<frac>\d*))?|\.(?P<only_frac>\d+))"
    r"(?:e(?P<exp>[+-]?\d+))?"
)


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


def _with_implicit_multiplication(text: str) -> str:
    """Insert multiplication between adjacent atoms (`2x`, `2sqrt(2)`, `(x+1)(x-1)`).

    Python's tokenizer is data-only.  Keeping an identifier followed by ``(`` intact is important:
    the AST converter can then reject an unknown function call instead of silently treating it as a
    symbol times a parenthesized expression.
    """
    # Preserve SymPy's former implicit-application precedence without calling its eval-backed
    # parser.  A bare unary function consumes the following multiplicative term, including powers:
    # `sin x^2` -> `sin(x^2)`, `sin 2xyz` -> `sin(2xyz)`, but `sin x/y` -> `sin(x)/y`.
    fn_names = "|".join(sorted(map(re.escape, _SAFE_FUNCTIONS), key=len, reverse=True))
    bare_fn = re.compile(rf"(?<![A-Za-z_])(?P<fn>{fn_names})\s+")
    for _ in range(_MAX_AST_NODES):
        chosen: Optional[tuple[re.Match[str], int]] = None
        # Work inside-out so `sin cos x` becomes `sin(cos(x))` deterministically.
        for match in reversed(list(bare_fn.finditer(text))):
            start = match.end()
            if start >= len(text) or text[start] in "(+-/% ,)]}":
                continue
            stack: list[str] = []
            pairs = {")": "(", "]": "[", "}": "{"}
            end = start
            while end < len(text):
                ch = text[end]
                if ch in "([{":
                    stack.append(ch)
                elif ch in pairs:
                    if not stack:
                        break
                    if stack.pop() != pairs[ch]:
                        break
                elif not stack and ch in "+-/% ,":
                    # Whitespace is implicit multiplication, not a boundary.  Comma/division/mod
                    # and additive operators bind outside the bare function application.
                    if ch != " ":
                        break
                end += 1
            if end > start and text[start:end].strip():
                chosen = (match, end)
                break
        if chosen is None:
            break
        match, end = chosen
        arg = text[match.end():end].rstrip()
        text = text[:match.start()] + match.group("fn") + f"({arg})" + text[end:]
    else:
        raise ValueError("too many implicit function applications")

    toks = [tok for tok in py_tokenize.generate_tokens(
        io.StringIO(text.replace("^", "**")).readline)
            if tok.type not in {
                token.ENDMARKER, token.NEWLINE, token.NL, token.INDENT, token.DEDENT,
            }]
    if any(tok.type == token.COMMENT for tok in toks):
        raise ValueError("comments are not answer syntax")

    protected = set(_SAFE_FUNCTIONS) | set(_SAFE_CONSTANTS)
    expanded: list[tuple[int, str]] = []
    for i, cur in enumerate(toks):
        followed_by_lparen = bool(
            i + 1 < len(toks) and toks[i + 1].type == token.OP and toks[i + 1].string == "(")
        if (cur.type == token.NAME and re.fullmatch(r"[A-Za-z]{2,}", cur.string)
                and cur.string not in protected and not followed_by_lparen):
            for j, char in enumerate(cur.string):
                if j:
                    expanded.append((token.OP, "*"))
                expanded.append((token.NAME, char))
        else:
            expanded.append((cur.type, cur.string))

    significant = {token.NAME, token.NUMBER}
    out: list[tuple[int, str]] = []
    prev: Optional[tuple[int, str]] = None
    for cur in expanded:
        cur_type, cur_string = cur
        prev_ends = bool(prev and (prev[0] in significant or
                                   (prev[0] == token.OP and prev[1] in {")", "]", "}"})))
        cur_starts = cur_type in significant or (cur_type == token.OP and cur_string == "(")
        # Approved function names retain call syntax.  A one-letter mathematical symbol or known
        # constant followed by parentheses is conventional implicit multiplication (`x(x+1)`,
        # `pi(x+1)`).  Longer unknown names retain Call syntax and are rejected by the AST converter,
        # so evaluator-looking input such as `parse_expr(...)` cannot be reinterpreted as harmless.
        is_call = bool(
            prev and prev[0] == token.NAME and cur_type == token.OP and cur_string == "("
            and prev[1] not in _SAFE_CONSTANTS and len(prev[1]) != 1
        )
        if prev_ends and cur_starts and not is_call:
            out.append((token.OP, "*"))
        out.append(cur)
        prev = cur
    return py_tokenize.untokenize(out)


def _bounded_numeric_value(node: ast.AST, cap: float = _MAX_POW_EXP + 1.0) -> Optional[float]:
    """Signed numeric value saturated to ``[-cap, cap]``; symbolic/complex nodes return None."""
    def _clamp(value: float) -> float:
        if not math.isfinite(value):
            return math.copysign(cap, value)
        return max(-cap, min(cap, value))

    if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
        try:
            return _clamp(float(node.value))
        except (OverflowError, ValueError):
            return cap if node.value >= 0 else -cap
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _bounded_numeric_value(node.operand, cap)
        if value is None:
            return None
        return value if isinstance(node.op, ast.UAdd) else -value
    if not isinstance(node, ast.BinOp):
        return None
    left = _bounded_numeric_value(node.left, cap)
    right = _bounded_numeric_value(node.right, cap)
    if left is None or right is None:
        return None
    if isinstance(node.op, ast.Add):
        return _clamp(left + right)
    if isinstance(node.op, ast.Sub):
        return _clamp(left - right)
    if isinstance(node.op, ast.Mult):
        return _clamp(left * right)
    if isinstance(node.op, ast.Div):
        return cap if right == 0 else _clamp(left / right)
    if isinstance(node.op, ast.Pow):
        if abs(right) > _MAX_POW_EXP:
            return math.copysign(cap, right)
        if left == 0:
            return 0.0 if right > 0 else cap
        if right == 0:
            return 1.0
        sign = 1.0
        if left < 0:
            if not right.is_integer():
                return None
            sign = -1.0 if int(right) % 2 else 1.0
        try:
            logmag = right * math.log(abs(left))
            if logmag >= math.log(cap):
                return sign * cap
            return _clamp(sign * (abs(left) ** right))
        except (OverflowError, ValueError):
            return sign * cap
    if isinstance(node.op, ast.Mod):
        return min(cap, abs(right))
    return None


def _bounded_numeric_magnitude(node: ast.AST,
                               cap: float = _MAX_POW_EXP + 1.0) -> Optional[float]:
    value = _bounded_numeric_value(node, cap)
    return abs(value) if value is not None else None


def _estimated_integer_bits(node: ast.AST) -> Optional[int]:
    """Upper-bound bits for exact-integer arithmetic, saturated above the materialization cap."""
    cap = _MAX_RESULT_BITS + 1
    if isinstance(node, ast.Constant) and type(node.value) is int:
        return min(cap, abs(node.value).bit_length() or 1)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return _estimated_integer_bits(node.operand)
    if not isinstance(node, ast.BinOp):
        return None
    left, right = _estimated_integer_bits(node.left), _estimated_integer_bits(node.right)
    if isinstance(node.op, (ast.Add, ast.Sub)) and left is not None and right is not None:
        return min(cap, max(left, right) + 1)
    if isinstance(node.op, ast.Mult) and left is not None and right is not None:
        return min(cap, left + right)
    if isinstance(node.op, ast.Div) and left is not None and right is not None:
        return min(cap, max(left, right))
    if isinstance(node.op, ast.Pow) and left is not None:
        exp = _bounded_numeric_magnitude(node.right)
        if exp is not None:
            return min(cap, left * int(exp))
    if isinstance(node.op, ast.Mod) and left is not None and right is not None:
        return min(cap, max(left, right))
    return None


def _within_materialization_budget(tree: ast.AST) -> bool:
    """Validate syntax, depth and exact-number construction cost before creating SymPy objects."""
    nodes = list(ast.walk(tree))
    if len(nodes) > _MAX_AST_NODES:
        return False
    work = 0
    eager_work = 0
    stack = [(tree, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > _MAX_AST_DEPTH:
            return False
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            mag = _bounded_numeric_magnitude(node.right)
            if mag is not None:
                if mag > _MAX_POW_EXP:
                    return False
                # Add estimated construction work. Multiplying exponent costs grossly rejects safe
                # independent powers (2**1000 * 3**1000); exact result bit growth is separately and
                # more accurately bounded by _estimated_integer_bits below.
                work += int(mag) + 1
                if work > _MAX_MATERIALIZE:
                    return False
            exponent = _bounded_numeric_value(node.right)
            base_bits = _estimated_integer_bits(node.left)
            if (exponent is not None and not float(exponent).is_integer()
                    and base_bits is not None):
                if base_bits > _MAX_EAGER_FUNCTION_ARG_BITS:
                    return False
                eager_work += base_bits
        if isinstance(node, ast.Call):
            # Approved SymPy functions are still eager constructors. In particular sqrt/Log may try
            # expensive perfect-power/factor simplification on a concrete integer. Bound both each
            # argument and the cumulative exact-number work before constructing any SymPy object.
            if not node.args or node.keywords or len(node.args) > _MAX_FUNCTION_ARGS:
                return False
            if (isinstance(node.func, ast.Name)
                    and node.func.id not in {"min", "Min", "max", "Max"}
                    and len(node.args) != 1):
                return False
            for argument in node.args:
                argument_bits = _estimated_integer_bits(argument)
                if argument_bits is None:
                    continue
                if argument_bits > _MAX_EAGER_FUNCTION_ARG_BITS:
                    return False
                eager_work += argument_bits
        if eager_work > _MAX_EAGER_FUNCTION_WORK:
            return False
        bits = _estimated_integer_bits(node)
        if bits is not None and bits > _MAX_RESULT_BITS:
            return False
    return True


def _exact_decimal_literal(literal: str) -> Rational:
    """Convert one finite Python decimal/scientific token to an exact Rational.

    Python's AST stores ``9007199254740993.0`` as an already-rounded binary float, so using
    ``node.value`` irreversibly loses the answer the user wrote.  Parse the inert source spelling
    instead and reject shifts whose exact materialization would exceed the existing integer cap.
    """
    compact = literal.replace("_", "")
    match = _DECIMAL_LITERAL.fullmatch(compact)
    if match is None:
        raise ValueError("unsupported decimal literal")
    integer = match.group("int") or ""
    fraction = match.group("frac")
    if fraction is None:
        fraction = match.group("only_frac") or ""
    significand_text = (integer + fraction) or "0"
    exponent_text = match.group("exp") or "0"
    # A seven-plus digit exponent is already far beyond the ~300k decimal-place shift compatible
    # with the one-million-bit exact-result cap.  Bound before int()/pow() can materialize anything.
    exponent_digits = exponent_text.lstrip("+-0")
    if len(exponent_digits) > 7:
        raise ValueError("decimal exponent exceeds materialization limit")
    exponent = int(exponent_text)
    shift = exponent - len(fraction)
    max_decimal_shift = _MAX_RESULT_BITS // 3 + 2
    if abs(shift) > max_decimal_shift:
        raise ValueError("decimal exponent exceeds materialization limit")
    significand = int(significand_text)
    sig_bits = significand.bit_length() or 1
    shifted_bits = math.ceil(abs(shift) * math.log2(10))
    if sig_bits + shifted_bits > _MAX_RESULT_BITS:
        raise ValueError("exact decimal exceeds materialization limit")
    if shift >= 0:
        return Rational(significand * (10 ** shift))
    return Rational(significand, 10 ** (-shift))


def _ast_to_sympy(node: ast.AST, source: str):
    """Convert the explicitly allowed expression AST to SymPy; every other node raises."""
    if isinstance(node, ast.Expression):
        return _ast_to_sympy(node.body, source)
    if isinstance(node, ast.Constant):
        if type(node.value) is int:
            return Integer(node.value)
        if type(node.value) is float and math.isfinite(node.value):
            literal = ast.get_source_segment(source, node)
            if literal is None:
                raise ValueError("decimal literal has no source spelling")
            return _exact_decimal_literal(literal)
        # Overflowed scientific notation (for example 1e999999) reaches the AST as infinity, but its
        # source spelling is still available.  Route it through the bounded exact parser so it is
        # rejected by construction rather than treated as the mathematical infinity constant.
        if type(node.value) is float:
            literal = ast.get_source_segment(source, node)
            if literal is not None:
                return _exact_decimal_literal(literal)
        raise ValueError("unsupported literal")
    if isinstance(node, ast.Name):
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", node.id):
            raise ValueError("unsupported symbol")
        if node.id in _SAFE_FUNCTIONS:
            raise ValueError("function name used without an argument")
        return _SAFE_CONSTANTS.get(node.id, Symbol(node.id))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _ast_to_sympy(node.operand, source)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp):
        left = _ast_to_sympy(node.left, source)
        right = _ast_to_sympy(node.right, source)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.Pow):
            _guard_sympy_pow(left, right)
            return left ** right
        if isinstance(node.op, ast.Mod):
            return Mod(left, right)
        raise ValueError("unsupported operator")
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _SAFE_FUNCTIONS:
            raise ValueError("unsupported function")
        if node.keywords or not node.args:
            raise ValueError("unsupported call shape")
        args = [_ast_to_sympy(arg, source) for arg in node.args]
        return _SAFE_FUNCTIONS[node.func.id](*args)
    raise ValueError(f"unsupported syntax: {type(node).__name__}")


def _guard_sympy_pow(base, exponent) -> None:
    """Last-mile guard after safe calls fold, but before SymPy can materialize an exact power."""
    if isinstance(exponent, Rational):
        if abs(exponent) > Integer(_MAX_POW_EXP):
            raise ValueError("concrete exponent exceeds materialization limit")
    elif isinstance(exponent, Float):
        value = float(exponent)
        if not math.isfinite(value) or abs(value) > _MAX_POW_EXP:
            raise ValueError("concrete exponent exceeds materialization limit")

    # SymPy eagerly simplifies exact radicals (a rational base to a non-integer rational/float power),
    # which can invoke costly perfect-power/factor routines even when the final AST is small. Guard the
    # already-built integer/rational base before `**` triggers that work.
    fractional_exponent = (
        isinstance(exponent, Rational) and not isinstance(exponent, Integer)
    ) or isinstance(exponent, Float)
    if fractional_exponent and isinstance(base, Rational):
        base_bits = max(
            abs(int(base.p)).bit_length() or 1,
            abs(int(base.q)).bit_length() or 1,
        )
        if base_bits > _MAX_EAGER_FUNCTION_ARG_BITS:
            raise ValueError("exact radical argument exceeds eager-construction limit")

    if isinstance(exponent, Integer) and isinstance(base, Rational):
        exp = abs(int(exponent))
        numerator_bits = abs(int(base.p)).bit_length() or 1
        denominator_bits = abs(int(base.q)).bit_length() or 1
        if max(numerator_bits, denominator_bits) * exp > _MAX_RESULT_BITS:
            raise ValueError("exact power result exceeds integer materialization limit")
    elif (isinstance(exponent, Integer) and getattr(base, "is_number", False)
          and not getattr(base, "atoms", lambda *_args: set())(Float)):
        # Exact Gaussian/algebraic/transcendental numeric expressions are not Rational instances,
        # yet SymPy may still expand their integer powers eagerly.  Bound coefficient height and
        # expression complexity conservatively from the already-constructed base before `**` runs.
        exp = abs(int(exponent))
        atoms = getattr(base, "atoms", lambda *_args: set())(Rational)
        atom_bits = 1
        for atom in atoms:
            atom_bits = max(
                atom_bits,
                abs(int(atom.p)).bit_length() or 1,
                abs(int(atom.q)).bit_length() or 1,
            )
        try:
            complexity = max(1, min(_MAX_AST_NODES, int(base.count_ops()) + 1))
        except Exception:
            complexity = _MAX_AST_NODES
        if atom_bits * complexity * exp > _MAX_RESULT_BITS:
            raise ValueError("exact numeric power exceeds materialization limit")


def _to_expr(s: str):
    try:
        cleaned = _strip_thousands(_clean(s))
        if len(cleaned) > _MAX_ANSWER_LEN:        # decline before the iterative LaTeX transform.
            return None
        text = _latexish_to_sympy(cleaned)
        if len(text) > _MAX_ANSWER_LEN:
            return None
        pythonish = _with_implicit_multiplication(text)
        tree = ast.parse(pythonish, mode="eval")
        if not _within_materialization_budget(tree):
            return None
        return _ast_to_sympy(tree, pythonish)
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


def _has_explicit_approximation_literal(s: str) -> bool:
    """Whether the sanitized input explicitly contains a decimal/scientific number token.

    Numeric tolerance is an opt-in interpretation for written approximations such as
    ``sqrt(2)`` versus ``1.4142135624``.  It must never swallow an exact rational perturbation between
    two exact irrational expressions (for example ``pi + 1/10**10`` versus ``pi``).
    """
    try:
        cleaned = _strip_thousands(_clean(s))
        text = _with_implicit_multiplication(_latexish_to_sympy(cleaned))
        tokens = py_tokenize.generate_tokens(io.StringIO(text.replace("^", "**")).readline)
        return any(tok.type == token.NUMBER
                   and ("." in tok.string or "e" in tok.string.lower()) for tok in tokens)
    except Exception:
        return False


def _split_top(inner: str) -> Optional[list[str]]:
    """Split top-level commas, returning ``None`` for an empty interior element."""
    out, depth, cur = [], 0, []
    for ch in inner:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            part = "".join(cur).strip()
            if not part:
                return None
            out.append(part); cur = []
        else:
            cur.append(ch)
    tail = "".join(cur).strip()
    if tail:
        out.append(tail)
    elif out and not inner.rstrip().endswith(","):
        return None
    return out


def _split_collection(s: str) -> Optional[tuple[str, list[str]]]:
    """Classify `s` as a collection ONLY when its delimiters are EXPLICIT: `{...}` is a set and
    `(...)`/`[...]` a tuple. A bare comma is NOT enough — `3,5` is the number 3.5 in many locales and
    `1,000` is a thousands-grouped integer, so treating any comma'd string as a set silently
    mis-grades them. Numeric/expression parsing (which strips thousands separators) runs first in
    `answers_equivalent`, so by the time we get here a bare-comma string is genuinely ambiguous and
    we decline to call it a collection."""
    s = _clean(s)
    if len(s) >= 2 and s[0] == "{" and s[-1] == "}":
        parts = _split_top(s[1:-1])
        return ("invalid", []) if parts is None else ("set", parts)
    if len(s) >= 2 and ((s[0] == "(" and s[-1] == ")") or (s[0] == "[" and s[-1] == "]")) and "," in s:
        parts = _split_top(s[1:-1])
        if parts is None:
            return ("invalid", [])
        # Brackets commonly denote a closed interval, while parentheses may denote an ordered tuple
        # (or open interval). Conflating them produces false accepts, so preserve the delimiter kind.
        return ("bracket" if s[0] == "[" else "tuple", parts)
    return None


def _within_delimiter_budget(s: str) -> bool:
    """Reject malformed or deeply nested collection/expression wrappers before recursion."""
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[str] = []
    for ch in s:
        if ch in "([{":
            stack.append(ch)
            if len(stack) > _MAX_DELIMITER_DEPTH:
                return False
        elif ch in pairs:
            if not stack or stack.pop() != pairs[ch]:
                return False
    return not stack


class _ComparisonContext:
    """Hard cap and memo for recursive collection comparisons."""

    def __init__(self) -> None:
        self.remaining = _MAX_COMPARISON_WORK
        self.memo: dict[tuple[str, str, float], bool] = {}


def answers_equivalent(pred: Optional[str], gold: Optional[str], *, atol: float = 1e-9) -> bool:
    """True iff candidate answer ``pred`` is conservatively equivalent to ``gold``.

    ``atol`` is a public grading boundary, not an arbitrary arithmetic multiplier. Reject booleans,
    non-numbers, non-finite values, negatives, and relative tolerances of 100% or more before they can
    make unrelated approximations equivalent (or fail later with a raw conversion exception).
    """
    if (isinstance(atol, bool) or not isinstance(atol, (int, float))
            or not math.isfinite(float(atol)) or not 0 <= float(atol) < 1):
        raise ValueError("atol must be a finite number in [0, 1)")
    return _answers_equivalent(pred, gold, atol=float(atol), context=_ComparisonContext())


def _answers_equivalent(pred: Optional[str], gold: Optional[str], *, atol: float,
                        context: _ComparisonContext) -> bool:
    if context.remaining <= 0:
        return False
    context.remaining -= 1
    if (not isinstance(pred, str) or not isinstance(gold, str)
            or len(pred) > _MAX_ANSWER_LEN or len(gold) > _MAX_ANSWER_LEN):
        return False
    memo_key = (pred, gold, float(atol))
    if memo_key in context.memo:
        return context.memo[memo_key]
    p, g = _clean(pred), _clean(gold)
    if p == g and p != "":
        context.memo[memo_key] = True
        return True
    if not _within_delimiter_budget(p) or not _within_delimiter_budget(g):
        return False

    pc, gc = _split_collection(pred), _split_collection(gold)
    # Collection-vs-scalar is a type mismatch, even when removing delimiters would leave the same
    # token (e.g. ``{1}`` versus ``1``). Falling through to expression parsing would translate
    # braces to parentheses and incorrectly accept a singleton set as its sole element.
    if (pc is None) != (gc is None):
        return False
    if pc and gc:
        kp, ep = pc
        kg, eg = gc
        if (kp == "invalid" or kg == "invalid" or kp != kg
                or len(ep) > _MAX_COLLECTION_ITEMS or len(eg) > _MAX_COLLECTION_ITEMS):
            return False
        if kp != "set":                            # ordered tuple/bracket form
            result = (len(ep) == len(eg) and all(
                _answers_equivalent(a, b, atol=atol, context=context)
                for a, b in zip(ep, eg)))
            context.memo[memo_key] = result
            return result

        def unique(items: list[str]) -> list[str]:
            out: list[str] = []
            for item in items:
                if not any(_answers_equivalent(item, prior, atol=atol, context=context)
                           for prior in out):
                    out.append(item)
            return out

        left, right = unique(ep), unique(eg)
        if len(left) != len(right):
            return False
        # Maximum bipartite matching avoids the greedy-order bug when approximate equivalence gives
        # one element several possible partners. Duplicate set entries were removed above.
        edges = [[_answers_equivalent(a, b, atol=atol, context=context) for b in right]
                 for a in left]
        matched_left = [-1] * len(right)

        def augment(i: int, seen: set[int]) -> bool:
            for j, equivalent in enumerate(edges[i]):
                if not equivalent or j in seen:
                    continue
                seen.add(j)
                if matched_left[j] < 0 or augment(matched_left[j], seen):
                    matched_left[j] = i
                    return True
            return False

        result = all(augment(i, set()) for i in range(len(left)))
        context.memo[memo_key] = result
        return result

    ep, eg = _to_expr(pred), _to_expr(gold)

    # EXACT numeric path, taken BEFORE the float-tolerance branch below. float() collapses distinct
    # integers past 2**53 onto one value, and the relative tolerance opens a wide window for large
    # golds, so the tolerance branch OVER-ACCEPTS large-integer / rational answers (frequent in number
    # theory: e.g. 9007199254740993 vs 9007199254740992 both float to 2**53). Decimal/scientific source
    # tokens are also parsed as exact Rational values above, never through Python's binary float. When
    # BOTH sides are exact rationals, compare them EXACTLY. Irrational/transcendental/symbolic values
    # fall through to the numeric-tolerance / structural branches.
    if isinstance(ep, Rational) and isinstance(eg, Rational):
        result = bool(ep == eg)
        context.memo[memo_key] = result
        return result

    # Canonical constructors settle many exact irrational/symbolic identities cheaply. Accept those
    # before considering approximation; unequal exact expressions must not be rounded together merely
    # because they both happen to have a floating evaluation.
    if ep is not None and eg is not None and ep == eg:
        context.memo[memo_key] = True
        return True

    np_, ng = _as_number(pred), _as_number(gold)
    # A decimal/scientific token opts into tolerance only when that *whole side* is a rational
    # numerical approximation to a non-rational value (the intended ``sqrt(2)`` vs ``1.414...``
    # case). An exact decimal coefficient embedded inside an irrational expression must not taint the
    # whole expression and reopen relative-tolerance acceptance for unequal exact formulas.
    approximation_requested = (
        (_has_explicit_approximation_literal(pred) and isinstance(ep, Rational))
        or (_has_explicit_approximation_literal(gold) and isinstance(eg, Rational))
    )
    if approximation_requested and np_ is not None and ng is not None:
        # Relative tolerance is not an equivalence rule for infinities: both ``abs(finite - inf)``
        # and its right-hand side are ``inf``, and Python considers ``inf <= inf`` true. Only the
        # same signed infinity is equivalent; NaN never is.
        if not (math.isfinite(np_) and math.isfinite(ng)):
            result = np_ == ng and not (math.isnan(np_) or math.isnan(ng))
        else:
            result = abs(np_ - ng) <= atol * max(1.0, abs(ng))
        context.memo[memo_key] = result
        return result

    if ep is not None and eg is not None:
        # SymPy's simplify()/equals() are general-purpose, potentially unbounded algorithms. The
        # constructors already canonicalize ordinary Add/Mul ordering and exact radicals; use only
        # structural SymPy equality here. Harder identities are conservatively declined instead of
        # handing attacker-controlled expressions to an unbounded simplifier.
        result = bool(ep == eg)
        context.memo[memo_key] = result
        return result
    return False
