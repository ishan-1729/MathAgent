"""Tests for the ledger->Lean formalizer and the formalize->compile->audit bridge (offline).

Codex and Lean are stubbed/monkeypatched; the live end-to-end loop is validated by
tests/test_formalize_live.py (opt-in).
"""
import json

import pytest

from agent.tools import formalizer as fz
from agent.gates.lean_audit import LeanAuditResult, LeanVerdict, DependencyReport
from agent.gates.report import Finding, Severity, LAYER_LEAN
from agent.orchestrator import formalize_bridge as fb
from agent.gates.toolkit import load_toolkit

TOOLKIT = load_toolkit()

VALID_LEDGER = json.dumps({"problem": "p", "claim": "For all integers n, n + 0 = n.", "steps": [
    {"id": "s1", "claim": "Let n be an integer.", "justification": "given", "depends_on": []},
    {"id": "s2", "claim": "n + 0 = n.", "justification": "conclusion", "depends_on": ["s1"]}]})

BAD_LEDGER = json.dumps({"problem": "p", "claim": "c", "steps": [
    {"id": "s1", "claim": "x", "justification": "class_field_theory", "depends_on": []},
    {"id": "s2", "claim": "c", "justification": "conclusion", "depends_on": ["s1"]}]})


# ---- Lean extraction / naming ----

def test_extract_lean_fenced():
    raw = "Here:\n```lean\nimport Mathlib\ntheorem ma_target : True := trivial\n```\nDone."
    src = fz.extract_lean(raw)
    assert "theorem ma_target" in src and "import Mathlib" in src


def test_extract_lean_lean4_fence():
    raw = "```lean4\ntheorem foo : 1 = 1 := rfl\n```"
    assert "theorem foo" in fz.extract_lean(raw)


def test_extract_lean_unfenced():
    assert fz.extract_lean("theorem bar : True := trivial").startswith("theorem")


def test_extract_lean_none():
    assert fz.extract_lean("no code here") is None


def test_first_decl_name():
    assert fz.first_decl_name("import Mathlib\ntheorem ma_x (n:Nat): n=n := rfl") == "ma_x"
    assert fz.first_decl_name("lemma helper : True := trivial") == "helper"
    assert fz.first_decl_name("-- nothing") is None


def test_codex_formalizer_parses(monkeypatch):
    monkeypatch.setattr(fz, "_run_codex",
                        lambda p, c: "```lean\ntheorem ma_target (n : Nat) : n + 0 = n := Nat.add_zero n\n```")
    res = fz.CodexFormalizer(TOOLKIT).formalize(VALID_LEDGER)
    assert res.ok and res.theorem_name == "ma_target"
    assert "Nat.add_zero" in res.lean_source


def test_codex_formalizer_no_code(monkeypatch):
    monkeypatch.setattr(fz, "_run_codex", lambda p, c: "I cannot formalize this.")
    assert not fz.CodexFormalizer(TOOLKIT).formalize(VALID_LEDGER).ok


# ---- formalize -> audit orchestration (Lean stubbed) ----

def _passing_audit():
    return LeanAuditResult(verdict=LeanVerdict.PASS, findings=[],
                           report=DependencyReport("ma_target"))


def _rejecting_audit():
    return LeanAuditResult(
        verdict=LeanVerdict.REJECT,
        findings=[Finding(LAYER_LEAN, Severity.REJECT, "denylisted_dependency", "uses ClassGroup")],
        report=DependencyReport("ma_target"))


def test_formalize_and_audit_pass(monkeypatch):
    monkeypatch.setattr("agent.gates.lean_bridge.audit_lean_source", lambda *a, **k: _passing_audit())
    fr = fz.ScriptedFormalizer("theorem ma_target : True := trivial")
    res = fb.formalize_and_audit(VALID_LEDGER, fr, toolkit=TOOLKIT)
    assert res.formalized and res.compiled and res.elementary_verified


def test_formalize_and_audit_reject(monkeypatch):
    monkeypatch.setattr("agent.gates.lean_bridge.audit_lean_source", lambda *a, **k: _rejecting_audit())
    fr = fz.ScriptedFormalizer("theorem ma_target : True := by exact?")
    res = fb.formalize_and_audit(VALID_LEDGER, fr, toolkit=TOOLKIT)
    assert res.compiled and not res.elementary_verified


def test_formalize_and_audit_compile_failure(monkeypatch):
    from agent.gates.lean_bridge import LeanBridgeError

    def boom(*a, **k):
        raise LeanBridgeError("proof failed to compile")
    monkeypatch.setattr("agent.gates.lean_bridge.audit_lean_source", boom)
    res = fb.formalize_and_audit(VALID_LEDGER, fz.ScriptedFormalizer("theorem ma_target := bad"),
                                 toolkit=TOOLKIT)
    assert res.formalized and not res.compiled and "compile" in res.error


def test_formalize_failure_short_circuits():
    res = fb.formalize_and_audit(VALID_LEDGER, fz.ScriptedFormalizer("", ok=False), toolkit=TOOLKIT)
    assert not res.formalized and not res.elementary_verified


# ---- full_verify: informal gate + Lean audit ----

def test_full_verify_authoritative(monkeypatch):
    monkeypatch.setattr("agent.gates.lean_bridge.audit_lean_source", lambda *a, **k: _passing_audit())
    fr = fz.ScriptedFormalizer("theorem ma_target : True := trivial")
    res = fb.full_verify(VALID_LEDGER, fr, toolkit=TOOLKIT)
    assert res.gate.admitted_deterministically
    assert res.authoritative_elementary


def test_faithfulness_blocks_authoritative(monkeypatch):
    from agent.orchestrator.faithfulness import ScriptedFaithfulnessChecker
    monkeypatch.setattr("agent.gates.lean_bridge.audit_lean_source", lambda *a, **k: _passing_audit())
    fr = fz.ScriptedFormalizer("theorem ma_target : True := trivial")
    # Audit passes, but the statement is judged UNFAITHFUL -> not authoritative.
    res = fb.formalize_and_audit(VALID_LEDGER, fr, toolkit=TOOLKIT,
                                 informal_claim="For all integers n, n + 0 = n.",
                                 faithfulness_checker=ScriptedFaithfulnessChecker(False, ["wrong domain"]))
    assert res.elementary_verified            # the proof IS elementary...
    assert not res.faithful and not res.authoritative   # ...but the statement is unfaithful
    assert res.faithfulness is not None


def test_faithfulness_pass_is_authoritative(monkeypatch):
    from agent.orchestrator.faithfulness import ScriptedFaithfulnessChecker
    monkeypatch.setattr("agent.gates.lean_bridge.audit_lean_source", lambda *a, **k: _passing_audit())
    fr = fz.ScriptedFormalizer("theorem ma_target : True := trivial")
    res = fb.formalize_and_audit(VALID_LEDGER, fr, toolkit=TOOLKIT,
                                 informal_claim="claim", faithfulness_checker=ScriptedFaithfulnessChecker(True))
    assert res.authoritative


def test_make_terminal_gate(monkeypatch):
    from agent.orchestrator.faithfulness import ScriptedFaithfulnessChecker
    monkeypatch.setattr("agent.gates.lean_bridge.audit_lean_source", lambda *a, **k: _passing_audit())
    gate = fb.make_terminal_gate(fz.ScriptedFormalizer("theorem ma_target : True := trivial"),
                                 toolkit=TOOLKIT, faithfulness_checker=ScriptedFaithfulnessChecker(True))
    result = gate("root goal", "proof bundle text")
    assert result.authoritative


def test_full_verify_skips_lean_when_gate_rejects(monkeypatch):
    called = {"n": 0}

    def spy(*a, **k):
        called["n"] += 1
        return _passing_audit()
    monkeypatch.setattr("agent.gates.lean_bridge.audit_lean_source", spy)
    fr = fz.ScriptedFormalizer("theorem ma_target : True := trivial")
    res = fb.full_verify(BAD_LEDGER, fr, toolkit=TOOLKIT)
    assert res.gate.rejected and res.lean is None
    assert not res.authoritative_elementary
    assert called["n"] == 0  # Lean not invoked on a gate-rejected ledger
