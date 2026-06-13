"""Tests for ledger parsing (Layer 1a entry) and deterministic structural validation."""
import pytest

from agent.gates.ledger import parse_ledger, validate_structure, LedgerError, Ledger
from agent.gates.report import Severity
from tests.conftest import minimal_ledger, clone


def _codes(findings, severity=None):
    return {f.code for f in findings if severity is None or f.severity is severity}


# ---- parsing ----

def test_parse_from_dict(example_ledger_dict):
    led = parse_ledger(example_ledger_dict)
    assert isinstance(led, Ledger)
    assert led.problem == "squares_mod4"
    assert len(led.steps) == 5
    assert led.by_id("s2").obligations["case_cover"]["modulus"] == 2


def test_parse_from_json_string(example_ledger_dict):
    import json
    led = parse_ledger(json.dumps(example_ledger_dict))
    assert led.problem == "squares_mod4"


def test_parse_from_fenced_block(example_ledger_dict):
    import json
    text = "Here is my proof:\n```json\n" + json.dumps(example_ledger_dict) + "\n```\nDone."
    led = parse_ledger(text)
    assert len(led.steps) == 5


def test_parse_garbage_raises():
    with pytest.raises(LedgerError):
        parse_ledger("not a proof at all")


def test_parse_schema_violation_missing_steps():
    with pytest.raises(LedgerError, match="schema"):
        parse_ledger({"problem": "p", "claim": "c"})


def test_parse_schema_violation_bad_id():
    bad = minimal_ledger()
    bad["steps"][0]["id"] = "1bad"  # must start with a letter
    with pytest.raises(LedgerError):
        parse_ledger(bad)


def test_nfc_normalization():
    d = minimal_ledger()
    # 'e' + combining acute accent should normalize to precomposed 'é'.
    d["steps"][0]["claim"] = "café"
    led = parse_ledger(d)
    assert led.steps[0].claim == "café"


# ---- structural validation (valid case) ----

def test_valid_ledger_no_rejects(toolkit, example_ledger_dict):
    led = parse_ledger(example_ledger_dict)
    findings = validate_structure(led, toolkit)
    assert not _codes(findings, Severity.REJECT), [str(f) for f in findings]


def test_minimal_ledger_valid(toolkit):
    led = parse_ledger(minimal_ledger())
    assert not _codes(validate_structure(led, toolkit), Severity.REJECT)


# ---- structural validation (failures) ----

def test_duplicate_id(toolkit):
    d = minimal_ledger()
    d["steps"][1]["id"] = "s1"
    led = parse_ledger(d)
    assert "dup_id" in _codes(validate_structure(led, toolkit), Severity.REJECT)


def test_bad_justification(toolkit):
    d = minimal_ledger()
    d["steps"][0]["justification"] = "class_field_theory"
    led = parse_ledger(d)
    assert "bad_justification" in _codes(validate_structure(led, toolkit), Severity.REJECT)


def test_dangling_dependency(toolkit):
    d = minimal_ledger()
    d["steps"][1]["depends_on"] = ["does_not_exist"]
    led = parse_ledger(d)
    assert "dangling_dep" in _codes(validate_structure(led, toolkit), Severity.REJECT)


def test_cycle_detected(toolkit):
    d = {
        "problem": "p", "claim": "c",
        "steps": [
            {"id": "a", "claim": "x", "justification": "algebra", "depends_on": ["b"]},
            {"id": "b", "claim": "y", "justification": "algebra", "depends_on": ["a"]},
            {"id": "c", "claim": "z", "justification": "conclusion", "depends_on": ["a"]},
        ],
    }
    led = parse_ledger(d)
    assert "cycle" in _codes(validate_structure(led, toolkit), Severity.REJECT)


def test_no_conclusion(toolkit):
    d = minimal_ledger()
    d["steps"][1]["justification"] = "algebra"
    led = parse_ledger(d)
    assert "no_conclusion" in _codes(validate_structure(led, toolkit), Severity.REJECT)


def test_multiple_conclusions(toolkit):
    d = minimal_ledger()
    d["steps"][0]["justification"] = "conclusion"
    led = parse_ledger(d)
    assert "multi_conclusion" in _codes(validate_structure(led, toolkit), Severity.REJECT)


def test_orphan_step_is_info_not_reject(toolkit):
    d = {
        "problem": "p", "claim": "c",
        "steps": [
            {"id": "s1", "claim": "used", "justification": "given", "depends_on": []},
            {"id": "s2", "claim": "orphan", "justification": "algebra", "depends_on": []},
            {"id": "s3", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]},
        ],
    }
    led = parse_ledger(d)
    findings = validate_structure(led, toolkit)
    assert "orphan_step" in _codes(findings, Severity.INFO)
    assert not _codes(findings, Severity.REJECT)


def test_missing_required_obligation_case_split(toolkit):
    d = {
        "problem": "p", "claim": "c",
        "steps": [
            {"id": "s1", "claim": "split", "justification": "case_split", "depends_on": []},
            {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]},
        ],
    }
    led = parse_ledger(d)
    assert "missing_obligation" in _codes(validate_structure(led, toolkit), Severity.REJECT)


def test_missing_required_obligation_descent(toolkit):
    d = {
        "problem": "p", "claim": "c",
        "steps": [
            {"id": "s1", "claim": "descend", "justification": "descent", "depends_on": []},
            {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]},
        ],
    }
    led = parse_ledger(d)
    assert "missing_obligation" in _codes(validate_structure(led, toolkit), Severity.REJECT)
