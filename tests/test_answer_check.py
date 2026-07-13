"""Tests for the final-answer equivalence grader (SymPy-based)."""
import io
import contextlib
import time

import pytest

from agent.tools.answer_check import answers_equivalent


@pytest.mark.parametrize("a,b", [
    ("4", "4"),
    ("1/2", "0.5"),
    (r"\frac{1}{2}", "0.5"),
    (r"\frac{1}{2}", "1/2"),
    ("2\\sqrt{2}", "2*sqrt(2)"),
    ("x+1", "1+x"),
    ("2x", "x*2"),
    (r"\pi", "pi"),
    ("{1, 2, 3}", "{3, 2, 1}"),         # unordered set
    ("(1, 2)", "(1, 2)"),               # ordered tuple
    ("The answer is 7", "7"),           # leading prose stripped
    ("$x^2$", "x^2"),                   # math delimiters + caret power
    ("1,000", "1000"),                  # thousands separator collapses to a plain integer
    ("12,345,678", "12345678"),         # multi-group thousands separators
])
def test_equivalent(a, b):
    assert answers_equivalent(a, b)


@pytest.mark.parametrize("a,b", [
    ("1", "2"),
    ("x+1", "x+2"),
    ("3", "-3"),
    ("{1, 2}", "{1, 3}"),
    ("{1, 2}", "{1, 2, 3}"),            # different cardinality
    ("(1, 2)", "(2, 1)"),               # tuples are ordered
    ("x", "y"),
    ("3,5", "3.5"),                     # bare comma is NOT a 2-element set vs the number 3.5
    ("(1, 2)", "{1, 2}"),              # tuple kind != set kind
    ("[1, 2]", "(1, 2)"),              # brackets are ambiguous (often a closed interval)
])
def test_not_equivalent(a, b):
    assert not answers_equivalent(a, b)


def test_bare_comma_is_not_a_set():
    # Regression: any-comma-no-'=' used to be classified as a SET, so "3,5" became {3,5} and matched
    # the actual set "{3,5}". With explicit-delimiter-only classification, a bare-comma string is
    # not a set.
    from agent.tools.answer_check import _split_collection
    assert _split_collection("3,5") is None
    assert _split_collection("1,000") is None
    assert _split_collection("{3,5}") == ("set", ["3", "5"])
    assert _split_collection("(1,2)") == ("tuple", ["1", "2"])


def test_malformed_and_duplicate_sets_are_handled_conservatively():
    assert not answers_equivalent("{1,,2}", "{1,2}")
    assert answers_equivalent("{1,1,2}", "{2,1}")
    assert answers_equivalent("{1,2/2,2}", "{1,2}")


@pytest.mark.parametrize("collection, scalar", [("{1}", "1"), ("{pi}", "pi")])
def test_singleton_set_is_not_equivalent_to_scalar(collection, scalar):
    assert not answers_equivalent(collection, scalar)
    assert not answers_equivalent(scalar, collection)


@pytest.mark.parametrize("finite", ["0", "1e308", "-1e308"])
@pytest.mark.parametrize("infinite", ["oo", "-oo", "infinity", "-infinity"])
def test_finite_answer_never_matches_infinity_via_relative_tolerance(finite, infinite):
    assert not answers_equivalent(finite, infinite)
    assert not answers_equivalent(infinite, finite)


def test_infinity_requires_same_sign():
    assert answers_equivalent("oo", "infinity")
    assert answers_equivalent("-oo", "-infinity")
    assert not answers_equivalent("oo", "-infinity")


def test_thousands_separator_only_collapses_grouped_numbers():
    # A genuine set written with spaces after commas is untouched by thousands normalization
    # (the separator regex requires comma-immediately-before-three-digits).
    assert answers_equivalent("{1, 2, 3}", "{1, 2, 3}")
    from agent.tools.answer_check import _strip_thousands
    assert _strip_thousands("1,000") == "1000"
    assert _strip_thousands("12,345,678") == "12345678"
    assert _strip_thousands("3,5") == "3,5"            # not a 3-digit group -> untouched
    assert _strip_thousands("1, 2, 3") == "1, 2, 3"    # spaces -> untouched (a real list)


def test_none_is_never_equivalent():
    assert not answers_equivalent(None, "1")
    assert not answers_equivalent("1", None)
    assert not answers_equivalent("", "1")


@pytest.mark.parametrize("payload", [
    "print('PUBLIC_API_EXECUTED')",
    "exec('x = 1')",
    "__import__('os')",
    "open('/etc/passwd')",
    "eval('1 + 1')",
])
def test_untrusted_answer_string_is_not_executed(payload):
    # Regression: parse_expr() uses eval() and used to seed its globals with Python builtins, so a
    # model-supplied answer like "print('...')" executed (side effect) BEFORE being rejected. The
    # locked-down global namespace makes such names unresolvable: no side effect, and not equivalent.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = answers_equivalent(payload, "0")
    assert result is False
    assert "PUBLIC_API_EXECUTED" not in buf.getvalue()
    assert buf.getvalue() == ""               # no output at all -> the call never ran


def test_pm_is_not_equivalent_to_a_single_value():
    # Regression: unknown LaTeX commands were stripped, so \pm 1 collapsed to "1" and wrongly graded
    # equal to 1. \pm 1 means {1, -1}; refuse to grade it equal to a single value.
    assert not answers_equivalent("1", r"\pm 1")
    assert not answers_equivalent(r"\pm 1", "1")
    assert not answers_equivalent(r"\mp 2", "2")        # \mp is likewise not droppable
    # An identical \pm answer still matches via the cleaned-string fast path.
    assert answers_equivalent(r"\pm 1", r"\pm 1")


# --- FIX 2: DoS guard on the untrusted-answer parse (deterministic, no timeout machinery) ---

def test_power_tower_answer_declines_fast_without_oom():
    # "2**(2**40)" would build a ~137 GB integer if evaluated; the Pow-exponent magnitude guard
    # must decline it (return None) in well under a second, never materializing the value.
    from agent.tools.answer_check import _to_expr
    t = time.perf_counter()
    assert _to_expr("2**(2**40)") is None
    assert answers_equivalent("2**(2**40)", "4") is False
    assert time.perf_counter() - t < 1.0


def test_factorial_answer_declines_fast_without_oom():
    # "factorial(100000000)" would hang/OOM if factorial were callable; it is rebound to an inert
    # Symbol and _to_expr declines any surviving banned-callable name -> None, fast.
    from agent.tools.answer_check import _to_expr
    t = time.perf_counter()
    assert _to_expr("factorial(100000000)") is None
    assert answers_equivalent("factorial(100000000)", "4") is False
    assert _to_expr("gamma(10**8)") is None
    assert time.perf_counter() - t < 1.0


def test_normal_answers_still_grade_after_dos_guard():
    # The DoS guard must not disturb ordinary answers: they parse and grade exactly as before.
    from agent.tools.answer_check import _to_expr
    assert _to_expr("2**10") is not None and answers_equivalent("2**10", "1024")
    assert answers_equivalent("1/2", "0.5")
    assert answers_equivalent(r"\frac{1}{2}", "0.5")
    assert _to_expr("sqrt(2)") is not None and answers_equivalent("sqrt(2)", r"\sqrt{2}")


def test_unsupported_latex_command_declines_rather_than_dropping():
    from agent.tools.answer_check import _to_expr
    # \pm survives the known substitutions, so _to_expr declines (returns None) instead of parsing
    # a silently-mangled "1".
    assert _to_expr(r"\pm 1") is None
    # Commands we DO translate still parse fine.
    assert _to_expr(r"\frac{1}{2}") is not None
    assert _to_expr(r"\pi") is not None
    assert _to_expr(r"2\sqrt{2}") is not None


# --- FIX 2 (residual): construction-based bounds over the inert Python AST. Unknown calls are rejected
# by the explicit converter; concrete powers are bounded by cumulative work and estimated result bits.
# All DoS cases must decline FAST (well under a second); legit answers must still grade. ---

@pytest.mark.parametrize("payload", [
    "fibonacci(10**8)",          # NOT on any old name list; eager-eval hang without the inert rebind
    "fibonacci(100000000)",      # bare-integer arg: hangs at PARSE without the inert Function rebind
    "bernoulli(10**8)",
    "catalan(10**8)",
    "harmonic(99999999)",
    "primepi(10**8)",
    "loggamma(10**8)",
])
def test_unbanned_combinatorial_callables_decline_fast(payload):
    # Each names a star-imported SymPy Function that the old enumeration blocklist did NOT cover; the
    # construction bound (big numeric function argument) declines it in well under a second, never
    # materializing a giant integer, never hanging at parse.
    from agent.tools.answer_check import _to_expr
    t = time.perf_counter()
    assert _to_expr(payload) is None
    assert answers_equivalent(payload, "4") is False
    assert time.perf_counter() - t < 1.0, f"{payload!r} did not decline fast"


def test_cumulative_power_work_declines_only_genuinely_large_constructions():
    from agent.tools.answer_check import _to_expr
    # Two independent powers fit the exact-result bit budget and must not be rejected by multiplying
    # their exponent costs.
    assert _to_expr("2**1000 * 3**1000") is not None
    # Enough separate powers exceed the cumulative construction-work/result budget and decline fast.
    expr = "*".join("2**9999" for _ in range(120))
    t = time.perf_counter()
    assert _to_expr(expr) is None
    assert time.perf_counter() - t < 1.0
    # A single power at exactly the same per-Pow exponent is still fine on its own if it fits the budget.
    assert _to_expr("2**9999") is not None


def test_construction_bound_declines_fast_overall():
    # Belt: the whole DoS battery returns in well under a second in aggregate (no per-case timeout
    # machinery in the grader; the bound is purely structural/deterministic).
    from agent.tools.answer_check import _to_expr
    t = time.perf_counter()
    for s in ("fibonacci(10**8)", "catalan(100000000)", "bernoulli(10**8)",
              "*".join("2**9999" for _ in range(120)), "2**(2**40)"):
        assert _to_expr(s) is None
    assert time.perf_counter() - t < 1.0


def test_legit_answers_unaffected_by_construction_bound():
    # The bound must not disturb ordinary final answers: small integers, a reasonable power (2**32),
    # fractions, sqrt, sets, tuples, and a normal equality all grade exactly as before.
    from agent.tools.answer_check import _to_expr
    assert _to_expr("2**32") is not None and answers_equivalent("2**32", "4294967296")
    assert answers_equivalent("2**10", "1024")
    assert answers_equivalent("1/2", "0.5")
    assert answers_equivalent(r"\frac{1}{2}", "0.5")
    assert answers_equivalent("sqrt(2)", r"\sqrt{2}")
    assert answers_equivalent("{1, 2, 3}", "{3, 2, 1}")
    assert answers_equivalent("(1, 2)", "(1, 2)")
    assert answers_equivalent("x+1", "1+x")
    assert not answers_equivalent("1", "2")


# --- FIX 3: the length cap must run BEFORE _latexish_to_sympy, so an oversized untrusted answer is
#     declined before the O(depth*length) \frac/\sqrt expansion loop ever runs on it. ---

def test_oversized_answer_declined_before_latex_transform(monkeypatch):
    import agent.tools.answer_check as ac
    called = {"hit": False}
    orig = ac._latexish_to_sympy

    def _spy(text):
        called["hit"] = True
        return orig(text)

    monkeypatch.setattr(ac, "_latexish_to_sympy", _spy)
    payload = r"\frac{1}{2}" * 400            # ~4400 chars, well over _MAX_ANSWER_LEN (2000)
    assert len(payload) > ac._MAX_ANSWER_LEN
    assert ac._to_expr(payload) is None        # declined at the top of _to_expr ...
    assert called["hit"] is False              # ... BEFORE the latex expansion transform ran


def test_oversized_answer_declines_fast_and_public_api_handles_it():
    from agent.tools.answer_check import _to_expr, _MAX_ANSWER_LEN
    payload = r"\frac{1}{2}" * 400
    assert len(payload) > _MAX_ANSWER_LEN
    t = time.perf_counter()
    assert _to_expr(payload) is None
    assert answers_equivalent(payload, "1") is False
    assert time.perf_counter() - t < 1.0


def test_negative_fractional_power_tower_is_rejected_before_sympy(monkeypatch):
    import agent.tools.answer_check as ac

    called = {"sympy": False}

    def _must_not_construct(_node):
        called["sympy"] = True
        raise AssertionError("unsafe nested exponent reached SymPy construction")

    monkeypatch.setattr(ac, "_ast_to_sympy", _must_not_construct)
    assert ac._to_expr("2**((1/2)**-10000)") is None
    assert called["sympy"] is False


def test_folded_safe_call_exponents_hit_constructor_guard_before_power_materialization():
    import agent.tools.answer_check as ac
    from sympy import I, Integer

    with pytest.raises(ValueError, match="exponent exceeds"):
        ac._guard_sympy_pow(Integer(2), Integer(ac._MAX_POW_EXP + 1))
    assert ac._to_expr(f"2**abs({ac._MAX_POW_EXP + 1})") is None
    assert ac._to_expr(f"2**max({ac._MAX_POW_EXP + 1},1)") is None
    gaussian = Integer(2) ** 1000 + I
    with pytest.raises(ValueError, match="exact numeric power"):
        ac._guard_sympy_pow(gaussian, Integer(ac._MAX_POW_EXP))


def test_public_api_bounds_raw_input_and_collection_nesting(monkeypatch):
    import agent.tools.answer_check as ac

    called = {"clean": False}

    def _must_not_clean(_text):
        called["clean"] = True
        raise AssertionError("oversized input reached normalization")

    monkeypatch.setattr(ac, "_clean", _must_not_clean)
    assert ac.answers_equivalent("x" * (ac._MAX_ANSWER_LEN + 1), "x") is False
    huge_set = "{" + ",".join("1" for _ in range(ac._MAX_ANSWER_LEN)) + "}"
    assert ac.answers_equivalent(huge_set, "{1}") is False
    assert called["clean"] is False

    monkeypatch.undo()
    deep = "(" * (ac._MAX_DELIMITER_DEPTH + 1) + "1," + ")" * (ac._MAX_DELIMITER_DEPTH + 1)
    assert ac.answers_equivalent(deep, "(1,)") is False

    many = "{" + ",".join(str(i) for i in range(ac._MAX_COLLECTION_ITEMS + 1)) + "}"
    reversed_many = "{" + ",".join(
        str(i) for i in reversed(range(ac._MAX_COLLECTION_ITEMS + 1))) + "}"
    assert ac.answers_equivalent(many, reversed_many) is False


# --- FIX 4 (M3/M4): large-integer grading must NOT collapse through float(). Past 2**53 distinct
#     integers share a float, and the relative tolerance opens a wide window for large golds, so the
#     float branch over-accepts. Exact rationals are compared EXACTLY, before the tolerance branch. ---

def test_large_integers_off_by_one_not_equivalent():
    # 9007199254740993 = 2**53 + 1 and 9007199254740992 = 2**53 both float to 2**53 -> the float
    # branch used to grade them equal. Exact rational comparison rejects them.
    assert answers_equivalent("9007199254740993", "9007199254740992") is False


def test_large_integer_gold_relative_window_not_equivalent():
    # 1e18 vs 1e18+5 differ by 5, but the float branch's atol*max(1, |gold|) window would swallow it.
    assert answers_equivalent("1000000000000000000", "1000000000000000005") is False


def test_equal_large_integers_via_different_form_equivalent():
    # Genuinely equal large integers, written in different textual forms, still grade equal (exact).
    assert answers_equivalent("12345678901234567890", "12345678901234567889 + 1") is True


def test_irrational_pair_still_uses_tolerance():
    # One side is genuinely irrational, so the exact-rational branch is inapplicable and numeric
    # tolerance still lets a close finite decimal approximation grade equal.
    assert answers_equivalent("sqrt(2)", "1.4142135624") is True
    # A clearly-wrong decimal for an irrational is still not equivalent.
    assert answers_equivalent("sqrt(2)", "1.5") is False
    # The existing exact-irrational pair is unchanged.
    assert answers_equivalent("2\\sqrt{2}", "2*sqrt(2)") is True


@pytest.mark.parametrize(
    ("candidate", "gold"),
    [
        ("pi*10**20 + 1", "pi*10**20"),
        ("sqrt(2)+10**-10", "sqrt(2)"),
        ("sqrt(2)+1/10000000000", "sqrt(2)"),
        ("pi+1/10000000000", "pi"),
        ("pi*10**20 + 1", "pi*1e20"),
        ("pi*10**20 + 100000000000", "pi*1e20"),
        ("sqrt(2)+1/10000000000", "sqrt(2)+0.0"),
    ],
)
def test_exact_irrational_expressions_never_use_approximation_tolerance(candidate, gold):
    assert answers_equivalent(candidate, gold) is False


def test_finite_decimals_are_exact_rationals():
    # Decimal/scientific source spelling is preserved exactly; ordinary equal values still agree.
    assert answers_equivalent("1/2", "0.5") is True
    assert answers_equivalent("1000", "1,000") is True   # thousands-grouped integer vs plain int
    assert answers_equivalent("0.1 + 0.2", "3/10") is True


@pytest.mark.parametrize(
    ("candidate", "gold"),
    [
        ("1.0000000001e20", "100000000000000000000"),
        ("100000000010000000000.0", "100000000000000000000"),
        ("9007199254740993.0", "9007199254740992"),
        ("0.5000000001", "1/2"),
    ],
)
def test_mixed_decimal_and_rational_never_bypass_exactness(candidate, gold):
    assert answers_equivalent(candidate, gold) is False


def test_extreme_scientific_exponent_is_declined_without_materialization():
    assert answers_equivalent("1e999999", "1") is False


def test_nested_sympy_parser_is_not_reachable_from_an_answer(monkeypatch):
    """Regression: putting parse_expr in a broad globals dict let an answer start a second parser.

    The grader now uses a data-only AST converter, so even SymPy's parser being monkeypatched to a
    conspicuous callable cannot make it reachable.
    """
    import sympy.parsing.sympy_parser as parser
    from agent.tools.answer_check import _to_expr

    called = {"parse_expr": False}

    def _must_not_run(*_args, **_kwargs):
        called["parse_expr"] = True
        raise AssertionError("eval-backed SymPy parser was reached")

    monkeypatch.setattr(parser, "parse_expr", _must_not_run)
    assert _to_expr('parse_expr("__import__(\\\'math\\\').sqrt(49)")') is None
    assert answers_equivalent('parse_expr("1 + 1")', "2") is False
    assert called["parse_expr"] is False


@pytest.mark.parametrize("payload", [
    "sqrt.__globals__['__builtins__']['__import__']('os')",
    "(1).__class__.__mro__",
    "[x for x in (1, 2, 3)]",
    "(lambda: 7)()",
    "Symbol('x')",
    "sympify('1 + 1')",
])
def test_object_graph_and_unknown_callable_syntax_is_rejected(payload):
    from agent.tools.answer_check import _to_expr
    assert _to_expr(payload) is None
    assert answers_equivalent(payload, "7") is False


def test_implicit_multiplication_before_parentheses_remains_supported():
    assert answers_equivalent("x(x+1)", "x*(x+1)")
    assert answers_equivalent("pi(x+1)", "pi*(x+1)")
    # Longer evaluator-looking names remain rejected calls, never symbols times parentheses.
    assert not answers_equivalent("parse_expr(2)", "2*parse_expr")


def test_compact_symbols_and_bare_safe_functions_remain_supported():
    assert answers_equivalent("xy", "x*y")
    assert answers_equivalent("abc", "a*b*c")
    assert answers_equivalent("sqrt 2", "sqrt(2)")
    assert answers_equivalent("sin 2x", "sin(2*x)")
    assert answers_equivalent("sin x^2", "sin(x**2)")
    assert answers_equivalent("sin 2xyz", "sin(2*x*y*z)")
    assert answers_equivalent("sqrt 2xy", "sqrt(2*x*y)")
    assert answers_equivalent("2sin x", "2*sin(x)")
    assert answers_equivalent("sin (x+1)y", "sin(x+1)*y")
    assert answers_equivalent("sin (x+1)^2", "sin(x+1)**2")
    assert answers_equivalent("sqrt (x+1)y", "sqrt(x+1)*y")
    assert answers_equivalent("ln(2)", "log(2)")


def test_short_unknown_call_is_not_reinterpreted_as_multiplication():
    from agent.tools.answer_check import _to_expr

    assert _to_expr("ab(2)") is None


def test_large_concrete_sqrt_is_rejected_before_eager_sympy_call(monkeypatch):
    import agent.tools.answer_check as ac

    called = {"sqrt": False}

    def _forbidden(_value):
        called["sqrt"] = True
        raise AssertionError("oversized radical reached eager SymPy construction")

    monkeypatch.setitem(ac._SAFE_FUNCTIONS, "sqrt", _forbidden)
    assert ac._to_expr("sqrt(2**10000 + 1)") is None
    assert called["sqrt"] is False


def test_large_concrete_fractional_power_is_rejected_before_sympy(monkeypatch):
    import agent.tools.answer_check as ac

    called = {"sympy": False}

    def _forbidden(_node):
        called["sympy"] = True
        raise AssertionError("oversized radical reached SymPy construction")

    monkeypatch.setattr(ac, "_ast_to_sympy", _forbidden)
    assert ac._to_expr("(2**10000 + 1)**(1/2)") is None
    assert called["sympy"] is False


def test_safe_function_arity_and_variadic_work_are_bounded(monkeypatch):
    import agent.tools.answer_check as ac

    called = {"max": False}

    def _forbidden(*_args):
        called["max"] = True
        raise AssertionError("oversized variadic call reached SymPy")

    monkeypatch.setitem(ac._SAFE_FUNCTIONS, "max", _forbidden)
    arguments = ",".join(f"x{i}" for i in range(ac._MAX_FUNCTION_ARGS + 1))
    assert ac._to_expr(f"max({arguments})") is None
    assert called["max"] is False
    assert ac._to_expr("sin(1, 2)") is None


def test_normal_exact_radicals_remain_supported_after_eager_guard():
    assert answers_equivalent("sqrt(2)", r"\sqrt{2}")
    assert answers_equivalent("(2**8)**(1/2)", "16")


@pytest.mark.parametrize(
    "atol",
    [-1, True, "1e-9", float("nan"), float("inf"), 1.0, 1e100],
)
def test_public_answer_checker_rejects_unsafe_tolerance(atol):
    with pytest.raises(ValueError, match="atol"):
        answers_equivalent("1.4", "sqrt(2)", atol=atol)


def test_public_answer_checker_accepts_bounded_tolerance_endpoints():
    assert answers_equivalent("sqrt(2)", "1.4142135623730951", atol=0)
    assert not answers_equivalent("1.4", "sqrt(2)", atol=0.01)
