"""Offline tests for numeric statement triage (agent/tools/statement_triage.py).

Zero live LLM: the ONE ``_run_claude`` call is monkeypatched to a scripted stub returning canned
falsification-spec JSON, so the deterministic checker path, the honest-epistemics fields, and the
fail-closed discarding are all exercised fully offline.

The invariants under test:
  * The LLM only PROPOSES inert JSON; the exact-integer checker (agent/tools/numeric.py) DECIDES.
  * A valid falsifying spec for a genuinely false statement -> refuted_modulo_translation True with the
    concrete counterexample recorded.
  * Garbage / unparseable candidates -> discarded (no signal, no crash).
  * A spec that parses but confirms nothing -> no signal.
  * NEVER a field called ``refuted`` and NEVER a "confirmed true" signal.
"""
from __future__ import annotations

import json

import pytest

from agent.tools import statement_triage as st
from agent.tools.statement_triage import (
    TriageResult,
    propose_falsification_specs,
    triage_statement,
)


def _stub(monkeypatch, response: str):
    """Replace the single LLM call with a canned response (no live CLI)."""
    monkeypatch.setattr(st, "_run_claude", lambda prompt, cfg: response)


# The canonical false-statement fixture: "every integer is even". A counterexample is any odd n; the
# spec confirms n=1 is the unique box-solution of `n - 1 == 0`, an odd integer contradicting the claim.
_ODD_SPEC = {
    "kind": "solution_set",
    "expression": "n - 1",
    "variables": ["n"],
    "bounds": {"n": [0, 3]},
    "claimed": [{"n": 1}],
}


# --------------------------------------------------------------------------------------------------
# (a) A valid falsifying spec for a genuinely false statement -> refuted_modulo_translation True.
# --------------------------------------------------------------------------------------------------
def test_valid_falsifying_spec_refutes_modulo_translation(monkeypatch):
    _stub(monkeypatch, "```json\n" + json.dumps([_ODD_SPEC]) + "\n```")
    res = triage_statement("Every integer n satisfies: n is even.", k=3)
    assert isinstance(res, TriageResult)
    assert res.refuted_modulo_translation is True
    assert res.candidate_counterexample == {"n": 1}
    assert res.spec is not None
    assert json.loads(res.spec)["expression"] == "n - 1"
    assert res.candidates_tried == 1
    # HONEST EPISTEMICS: no "refuted" field, no "confirmed true" — only the modulo-translation signal.
    assert not hasattr(res, "refuted")


def test_honest_field_names_only():
    # The dataclass must expose the epistemically-honest names, never a bare `refuted`.
    fields = set(TriageResult.__dataclass_fields__)
    assert "refuted_modulo_translation" in fields
    assert "candidate_counterexample" in fields
    assert "refuted" not in fields
    assert "counterexample" not in fields  # it's a *candidate*, pending translation confirmation


# --------------------------------------------------------------------------------------------------
# (b) Garbage / unparseable candidates -> discarded, no signal, no crash.
# --------------------------------------------------------------------------------------------------
def test_garbage_response_discarded_no_signal(monkeypatch):
    _stub(monkeypatch, "sorry, I cannot find a counterexample here. {not: valid json,,,}")
    res = triage_statement("Some statement.", k=3)
    assert res.refuted_modulo_translation is False
    assert res.candidate_counterexample is None
    assert res.spec is None


def test_empty_array_means_no_candidate(monkeypatch):
    _stub(monkeypatch, "```json\n[]\n```")
    res = triage_statement("A true statement with no small counterexample.", k=3)
    assert res.refuted_modulo_translation is False
    assert res.candidates_tried == 0


def test_partially_garbage_still_finds_the_good_spec(monkeypatch):
    # A valid spec buried among prose and a broken fenced block: the good one is still recovered.
    resp = (
        "Here is my best attempt.\n"
        "```json\n{not json at all\n```\n"
        "```json\n" + json.dumps([_ODD_SPEC]) + "\n```"
    )
    _stub(monkeypatch, resp)
    res = triage_statement("Every integer is even.", k=3)
    assert res.refuted_modulo_translation is True
    assert res.candidate_counterexample == {"n": 1}


def test_cli_error_yields_no_signal(monkeypatch):
    from agent.tools.claude_cli import ClaudeError

    def _boom(prompt, cfg):
        raise ClaudeError("claude CLI not found on PATH")

    monkeypatch.setattr(st, "_run_claude", _boom)
    res = triage_statement("Anything.", k=3)
    assert res.refuted_modulo_translation is False
    assert res.candidates_tried == 0
    assert res.spec is None


# --------------------------------------------------------------------------------------------------
# (c) A spec that parses+validates but confirms NOTHING -> no signal.
# --------------------------------------------------------------------------------------------------
def test_spec_that_confirms_nothing_yields_no_signal(monkeypatch):
    # This spec is a WELL-FORMED solution_set spec, but its `claimed` set is WRONG: it claims n=2 is a
    # solution of `n - 1 == 0`, which it is not (spurious), AND misses the real solution n=1. The
    # checker returns ok=False -> no counterexample confirmed -> no triage signal (fail-closed).
    bad = {
        "kind": "solution_set",
        "expression": "n - 1",
        "variables": ["n"],
        "bounds": {"n": [0, 3]},
        "claimed": [{"n": 2}],   # wrong: n=2 is not a root; n=1 (the real root) is missing
    }
    _stub(monkeypatch, json.dumps([bad]))
    res = triage_statement("Every integer is even.", k=3)
    assert res.refuted_modulo_translation is False
    assert res.candidate_counterexample is None
    assert res.candidates_tried == 1


def test_empty_solution_set_is_not_a_counterexample(monkeypatch):
    # A confirmed-but-EMPTY solution set exhibits no witness -> not a counterexample (no signal).
    empty = {
        "kind": "solution_set",
        "expression": "n - 100",   # no root in [0,3]
        "variables": ["n"],
        "bounds": {"n": [0, 3]},
        "claimed": [],             # correctly empty -> checker ok=True, but there is nothing to exhibit
    }
    _stub(monkeypatch, json.dumps([empty]))
    res = triage_statement("Every n in [0,3] equals 100.", k=3)
    assert res.refuted_modulo_translation is False
    assert res.candidate_counterexample is None


def test_non_solution_set_kind_is_ignored(monkeypatch):
    # A residue_cover spec is a proof helper, not a refutation witness; triage ignores it (no signal).
    cover = {"kind": "residue_cover", "modulus": 2, "residues": [0, 1]}
    _stub(monkeypatch, json.dumps([cover]))
    res = triage_statement("Whatever.", k=3)
    assert res.refuted_modulo_translation is False


# --------------------------------------------------------------------------------------------------
# SAFETY: a spec attempting to smuggle code is refused by the no-eval checker -> no signal, no exec.
# --------------------------------------------------------------------------------------------------
def test_malicious_expression_is_refused_no_signal(monkeypatch):
    evil = {
        "kind": "solution_set",
        "expression": "__import__('os').system('echo pwned')",  # rejected at the AST level
        "variables": ["n"],
        "bounds": {"n": [0, 3]},
        "claimed": [{"n": 1}],
    }
    _stub(monkeypatch, json.dumps([evil]))
    res = triage_statement("Every integer is even.", k=3)
    assert res.refuted_modulo_translation is False
    assert res.candidate_counterexample is None


# --------------------------------------------------------------------------------------------------
# propose_falsification_specs: the parser layer directly.
# --------------------------------------------------------------------------------------------------
def test_propose_tolerates_bare_array(monkeypatch):
    _stub(monkeypatch, json.dumps([_ODD_SPEC, _ODD_SPEC]))
    specs = propose_falsification_specs("stmt", k=3)
    assert len(specs) == 2
    assert all(json.loads(s)["kind"] == "solution_set" for s in specs)


def test_propose_caps_at_k(monkeypatch):
    _stub(monkeypatch, json.dumps([_ODD_SPEC] * 10))
    specs = propose_falsification_specs("stmt", k=2)
    assert len(specs) == 2


def test_propose_k_zero_makes_no_call(monkeypatch):
    called = {"n": 0}

    def _spy(prompt, cfg):
        called["n"] += 1
        return json.dumps([_ODD_SPEC])

    monkeypatch.setattr(st, "_run_claude", _spy)
    assert propose_falsification_specs("stmt", k=0) == []
    assert called["n"] == 0  # no LLM call at all when k<=0


def test_propose_single_object_not_wrapped_in_array(monkeypatch):
    # A model that emits ONE spec object (not an array) is still accepted.
    _stub(monkeypatch, "```json\n" + json.dumps(_ODD_SPEC) + "\n```")
    specs = propose_falsification_specs("stmt", k=3)
    assert len(specs) == 1
    assert json.loads(specs[0])["expression"] == "n - 1"


# --------------------------------------------------------------------------------------------------
# The multi-candidate loop: the FIRST confirming spec wins; earlier non-confirming ones are noted.
# --------------------------------------------------------------------------------------------------
def test_first_confirming_candidate_wins(monkeypatch):
    bad = {"kind": "solution_set", "expression": "n - 1", "variables": ["n"],
           "bounds": {"n": [0, 3]}, "claimed": [{"n": 2}]}   # non-confirming
    _stub(monkeypatch, json.dumps([bad, _ODD_SPEC]))
    res = triage_statement("Every integer is even.", k=3)
    assert res.refuted_modulo_translation is True
    assert res.candidate_counterexample == {"n": 1}
    assert res.candidates_tried == 2
    assert len(res.notes) == 1  # the first (non-confirming) candidate left a discard note
