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


def test_parse_prefers_last_fenced_block_over_decoy(example_ledger_dict):
    # FIX 4: a DECOY json fence before the real ledger must not win; the LAST parseable fence does.
    import json
    decoy = '```json\n{"note": "ignore me"}\n```'
    real = "```json\n" + json.dumps(example_ledger_dict) + "\n```"
    led = parse_ledger(decoy + "\nsome prose in between\n" + real)
    assert led.problem == "squares_mod4"
    assert len(led.steps) == 5


def test_parse_single_fenced_block_still_works(example_ledger_dict):
    # A single valid fence is unchanged by the last-block selection.
    import json
    led = parse_ledger("```json\n" + json.dumps(example_ledger_dict) + "\n```")
    assert led.problem == "squares_mod4"


def test_parse_no_fenced_block_still_errors():
    # No fence and no parseable JSON -> the same LedgerError as before (error path preserved).
    with pytest.raises(LedgerError, match="could not parse ledger JSON"):
        parse_ledger("no json here at all")


def test_parse_schema_violation_missing_steps():
    with pytest.raises(LedgerError, match="schema"):
        parse_ledger({"problem": "p", "claim": "c"})


def test_parse_schema_violation_bad_id():
    bad = minimal_ledger()
    bad["steps"][0]["id"] = "1bad"  # must start with a letter
    with pytest.raises(LedgerError):
        parse_ledger(bad)


def test_parse_rejects_newline_suffixed_id_despite_regex_dollar_semantics():
    bad = minimal_ledger()
    bad["steps"][0]["id"] = "s1\n"
    with pytest.raises(LedgerError, match="invalid step id"):
        parse_ledger(bad)


def test_parse_rejects_cyclic_programmatic_container_before_schema_walk():
    bad = minimal_ledger()
    bad["cycle"] = bad
    with pytest.raises(LedgerError, match="acyclic JSON tree"):
        parse_ledger(bad)


def test_parse_enforces_aggregate_container_bound(monkeypatch):
    import agent.gates.ledger as ledger_mod
    monkeypatch.setattr(ledger_mod, "_MAX_LEDGER_CONTAINER_ITEMS", 5)
    with pytest.raises(LedgerError, match="containers exceed"):
        parse_ledger(minimal_ledger())


def test_parse_preflights_oversized_steps_before_jsonschema(monkeypatch):
    import agent.gates.ledger as ledger_mod

    class _ForbiddenValidator:
        def iter_errors(self, _obj):
            raise AssertionError("oversized steps reached jsonschema error amplification")

    monkeypatch.setattr(ledger_mod, "_VALIDATOR", _ForbiddenValidator())
    bad = minimal_ledger()
    bad["steps"] = [{} for _ in range(ledger_mod._MAX_LEDGER_STEPS + 1)]
    with pytest.raises(LedgerError, match="steps.*exceeds"):
        parse_ledger(bad)


def test_parse_rejects_excessive_programmatic_nesting_before_schema():
    import agent.gates.ledger as ledger_mod

    bad = minimal_ledger()
    nested = []
    cursor = nested
    for _ in range(ledger_mod._MAX_LEDGER_NESTING + 1):
        child = []
        cursor.append(child)
        cursor = child
    bad["unexpected"] = nested
    with pytest.raises(LedgerError, match="nesting exceeds"):
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
            {"id": "s3", "claim": "c", "justification": "conclusion", "depends_on": ["s1"]},
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


# ---- goal<->claim binding: the conclusion must conclude the ledger's stated goal ----

def test_goal_claim_mismatch_rejected(toolkit):
    # The conclusion restates an intermediate lemma ("n is even") instead of the stated goal,
    # so the proof concludes a DIFFERENT statement than the one claimed -> REJECT.
    d = {
        "problem": "p", "claim": "n^2 is even",
        "steps": [
            {"id": "s1", "claim": "n is even", "justification": "algebra", "depends_on": []},
            {"id": "s2", "claim": "n is even", "justification": "conclusion", "depends_on": ["s1"]},
        ],
    }
    led = parse_ledger(d)
    assert "goal_claim_mismatch" in _codes(validate_structure(led, toolkit), Severity.REJECT)


def test_arbitrary_goal_claim_mismatch_is_also_rejected(toolkit):
    # A mismatch is invalid even when the terminal text does not duplicate a body step. The external
    # goal binder is defense in depth, not a substitute for this ledger-local invariant.
    d = {
        "problem": "p", "claim": "n^2 is even",
        "steps": [
            {"id": "s1", "claim": "n is an integer", "justification": "given", "depends_on": []},
            {"id": "s2", "claim": "an unrelated statement", "justification": "conclusion",
             "depends_on": ["s1"]},
        ],
    }
    led = parse_ledger(d)
    assert "goal_claim_mismatch" in _codes(validate_structure(led, toolkit), Severity.REJECT)


def test_conclusion_matching_goal_binds_despite_connective(toolkit):
    # A conclusion that restates the goal with a leading "Hence" + trailing period still binds
    # (notation/connective folding), so no mismatch fires.
    d = {
        "problem": "p", "claim": "n^2 is congruent to 0 or 1 mod 4",
        "steps": [
            {"id": "s1", "claim": "split on parity", "justification": "given", "depends_on": []},
            {"id": "s2", "claim": "Hence n^2 is congruent to 0 or 1 mod 4.",
             "justification": "conclusion", "depends_on": ["s1"]},
        ],
    }
    led = parse_ledger(d)
    assert "goal_claim_mismatch" not in _codes(validate_structure(led, toolkit), Severity.REJECT)
