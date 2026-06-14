"""Tests for the final-answer equivalence grader (SymPy-based)."""
import io
import contextlib

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


def test_unsupported_latex_command_declines_rather_than_dropping():
    from agent.tools.answer_check import _to_expr
    # \pm survives the known substitutions, so _to_expr declines (returns None) instead of parsing
    # a silently-mangled "1".
    assert _to_expr(r"\pm 1") is None
    # Commands we DO translate still parse fine.
    assert _to_expr(r"\frac{1}{2}") is not None
    assert _to_expr(r"\pi") is not None
    assert _to_expr(r"2\sqrt{2}") is not None
