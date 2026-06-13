"""Tests for the Layer-1b soft prose scanner + elastic-justification router."""
from agent.gates.ledger import parse_ledger
from agent.gates.scanner import scan_prose, ELASTIC_JUSTIFICATIONS
from agent.gates.report import Severity


def _ledger(steps, claim="A theorem."):
    return parse_ledger({"problem": "p", "claim": claim, "steps": steps})


def test_denylist_term_flags_review(toolkit):
    led = _ledger([
        {"id": "s1", "claim": "Use the class group of the ring of integers.",
         "justification": "algebra", "depends_on": []},
        {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]},
    ])
    findings = scan_prose(led, toolkit)
    assert any(f.code == "denylist_term" and f.severity is Severity.REVIEW for f in findings)
    # Soft: never a REJECT.
    assert all(f.severity is not Severity.REJECT for f in findings)


def test_denylist_in_top_claim(toolkit):
    led = _ledger(
        [{"id": "s1", "claim": "done", "justification": "conclusion", "depends_on": []}],
        claim="By the theory of elliptic curves, ...",
    )
    findings = scan_prose(led, toolkit)
    assert any(f.code == "denylist_term" for f in findings)


def test_elastic_justification_routes_to_review(toolkit):
    led = _ledger([
        {"id": "s1", "claim": "bound it", "justification": "bounding", "depends_on": [],
         "obligations": {"bounding": {"inequality": "n^2 < N", "strict": True}}},
        {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]},
    ])
    findings = scan_prose(led, toolkit)
    elastic = [f for f in findings if f.code == "elastic_justification"]
    assert elastic and elastic[0].severity is Severity.REVIEW
    assert "bounding" in ELASTIC_JUSTIFICATIONS


def test_clean_ledger_no_flags(toolkit, example_ledger_dict):
    led = parse_ledger(example_ledger_dict)
    findings = scan_prose(led, toolkit)
    # squares_mod4 uses given/case_split/congruence/conclusion -> no elastic, no denylist.
    assert findings == []


def test_overloaded_word_order_not_flagged(toolkit):
    led = _ledger([
        {"id": "s1", "claim": "Let d be the order of a modulo p.",
         "justification": "orders", "depends_on": []},
        {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]},
    ])
    assert scan_prose(led, toolkit) == []
