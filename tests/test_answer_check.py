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
    ("[1, 2]", "(1, 2)"),               # bracket and paren tuples coincide
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


# --- FIX 2 (residual): construction-based materialization bound over the evaluate=False parse tree.
# The old enumeration blocklist was fundamentally incomplete against `from sympy import *` (fibonacci,
# bernoulli, catalan, harmonic, primepi, prime, ... were all live+unbanned) and a product-of-powers each
# under the per-Pow cap still materialized a huge integer. The bound caps materialization by TREE
# STRUCTURE (Pow exponents + function-argument magnitude), name-agnostically, so no callable can starve
# it. All DoS cases must decline FAST (well under a second); legit answers must still grade. ---

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


def test_product_of_powers_under_per_pow_cap_declines_fast():
    # ~20 powers, each exponent (9999) UNDER the per-Pow cap (10**4), but the PRODUCT of (|exp|+1)
    # blows past the materialization budget (10**6) -> the product-of-powers channel the old per-Pow-only
    # check missed. Must decline fast, without materializing the (astronomically large) product.
    from agent.tools.answer_check import _to_expr
    expr = "*".join(f"{2 + i}**9999" for i in range(20))
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
              "*".join("2**9999" for _ in range(30)), "2**(2**40)"):
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
