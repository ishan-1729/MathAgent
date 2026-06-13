"""Tests for the final-answer equivalence grader (SymPy-based)."""
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
])
def test_not_equivalent(a, b):
    assert not answers_equivalent(a, b)


def test_none_is_never_equivalent():
    assert not answers_equivalent(None, "1")
    assert not answers_equivalent("1", None)
    assert not answers_equivalent("", "1")
