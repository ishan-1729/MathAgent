"""Tests for the composed deterministic gate."""
from agent.gates.gate import evaluate, Verdict
from agent.gates.report import Severity
from tests.conftest import minimal_ledger


def test_example_passes_deterministically(toolkit, example_ledger_dict):
    rep = evaluate(example_ledger_dict, toolkit)
    assert rep.verdict is Verdict.PASSED_DETERMINISTIC, rep.summary()
    assert rep.admitted_deterministically
    assert not rep.rejected
    assert rep.ledger is not None


def test_malformed_is_rejected(toolkit):
    rep = evaluate("this is not json", toolkit)
    assert rep.verdict is Verdict.REJECTED
    assert rep.parse_error
    assert any(f.code == "parse_error" for f in rep.rejects())


def test_bad_justification_rejected(toolkit):
    d = minimal_ledger()
    d["steps"][0]["justification"] = "elliptic_curve_descent"
    rep = evaluate(d, toolkit)
    assert rep.rejected
    assert "bad_justification" in {f.code for f in rep.rejects()}


def test_incomplete_case_cover_rejected(toolkit):
    d = {
        "problem": "p", "claim": "c",
        "steps": [
            {"id": "s1", "claim": "split mod 4", "justification": "case_split", "depends_on": [],
             "obligations": {"case_cover": {"modulus": 4, "residues": [0, 1, 2]}}},
            {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]},
        ],
    }
    rep = evaluate(d, toolkit)
    assert rep.rejected
    assert "case_cover_incomplete" in {f.code for f in rep.rejects()}


def test_denylist_term_needs_review_not_rejected(toolkit):
    d = minimal_ledger()
    d["steps"][0]["claim"] = "Pass to the class group of the number field."
    rep = evaluate(d, toolkit)
    # Soft: routed to review, but the deterministic gate does not reject on prose alone.
    assert rep.verdict is Verdict.NEEDS_REVIEW
    assert not rep.rejected
    assert any(f.code == "denylist_term" for f in rep.reviews())


def test_elastic_justification_needs_review(toolkit):
    d = {
        "problem": "p", "claim": "c",
        "steps": [
            {"id": "s1", "claim": "bound it", "justification": "bounding", "depends_on": [],
             "obligations": {"bounding": {"inequality": "n < n+1", "strict": True}}},
            {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]},
        ],
    }
    rep = evaluate(d, toolkit)
    assert rep.verdict is Verdict.NEEDS_REVIEW


def test_report_helpers(toolkit, example_ledger_dict):
    rep = evaluate(example_ledger_dict, toolkit)
    assert isinstance(rep.summary(), str)
    assert rep.rejects() == []
