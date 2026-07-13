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
    {"id": "s2", "claim": "For all integers n, n + 0 = n.",
     "justification": "conclusion", "depends_on": ["s1"]}]})

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
                           report=DependencyReport("ma_target"), provenance_verified=True)


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


def test_formalize_and_audit_marks_post_preflight_unavailability(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise fb.lean_bridge.LeanUnavailable("Lean disappeared after preflight")

    monkeypatch.setattr(fb.lean_bridge, "audit_lean_source", unavailable)
    res = fb.formalize_and_audit(
        VALID_LEDGER, fz.ScriptedFormalizer("theorem ma_target : True := trivial"),
        toolkit=TOOLKIT,
    )
    assert res.compiled is False
    assert res.lean_unavailable is True
    assert res.lean_could_not_formalize is False


def test_formalize_failure_short_circuits():
    res = fb.formalize_and_audit(VALID_LEDGER, fz.ScriptedFormalizer("", ok=False), toolkit=TOOLKIT)
    assert not res.formalized and not res.elementary_verified


# ---- full_verify: informal gate + Lean audit ----

def test_repair_loop_recovers(monkeypatch):
    from agent.gates.lean_bridge import LeanBridgeError
    from agent.tools.retrieval import ScriptedRetriever
    calls = {"n": 0}

    def audit(source, name, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise LeanBridgeError("error: unknown identifier 'Nat.add_zer'")
        return _passing_audit()

    monkeypatch.setattr("agent.gates.lean_bridge.audit_lean_source", audit)
    formalizer = fz.ScriptedFormalizer(["BAD lean", "GOOD lean"])
    retriever = ScriptedRetriever(["Nat.add_zero : n + 0 = n"])
    res = fb.formalize_and_audit(VALID_LEDGER, formalizer, toolkit=TOOLKIT,
                                 retriever=retriever, repair_iters=2)
    assert res.compiled and res.elementary_verified
    assert res.attempts == 2                       # failed once, repaired, succeeded
    assert formalizer.repair_calls == 1            # one repair invocation
    assert "unknown identifier" in retriever.calls[0][1]  # the Lean error reached the retriever


def test_verification_model_calls_are_reported_separately(monkeypatch):
    from agent.gates.lean_bridge import LeanBridgeError
    from agent.orchestrator.faithfulness import ScriptedFaithfulnessChecker

    calls = {"n": 0}

    def audit(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise LeanBridgeError("repair me")
        return _passing_audit()

    monkeypatch.setattr("agent.gates.lean_bridge.audit_lean_source", audit)
    formalizer = fz.ScriptedFormalizer(["bad", "good"], certification_trusted=True)
    formalizer.model_call_cost = 1
    checker = ScriptedFaithfulnessChecker(True, certification_trusted=True)
    checker.model_call_cost = 4
    result = fb.formalize_and_audit(
        VALID_LEDGER, formalizer, toolkit=TOOLKIT, repair_iters=1,
        faithfulness_checker=checker,
    )
    assert result.authoritative
    assert result.model_calls == 6  # two formalizer invocations + four panel lenses
    assert "model_calls=6" in result.summary()


@pytest.mark.parametrize("repair_iters", [-1, 1001, True, 1.5])
def test_repair_iterations_are_bounded(repair_iters):
    with pytest.raises(ValueError, match="repair_iters"):
        fb.formalize_and_audit(
            VALID_LEDGER, fz.ScriptedFormalizer("theorem ma_target : True := trivial"),
            toolkit=TOOLKIT, repair_iters=repair_iters,
        )


def test_repair_on_audit_reject(monkeypatch):
    # A proof can COMPILE but be rejected by the audit (sorry / non-elementary); the loop should
    # repair on that too, not only on hard compile errors.
    calls = {"n": 0}

    def audit(source, name, **k):
        calls["n"] += 1
        return _rejecting_audit() if calls["n"] == 1 else _passing_audit()

    monkeypatch.setattr("agent.gates.lean_bridge.audit_lean_source", audit)
    formalizer = fz.ScriptedFormalizer(["compiles-but-sorry", "elementary-and-complete"])
    res = fb.formalize_and_audit(VALID_LEDGER, formalizer, toolkit=TOOLKIT, repair_iters=2)
    assert res.elementary_verified and res.attempts == 2 and formalizer.repair_calls == 1


def test_repair_loop_exhausts(monkeypatch):
    from agent.gates.lean_bridge import LeanBridgeError
    monkeypatch.setattr("agent.gates.lean_bridge.audit_lean_source",
                        lambda *a, **k: (_ for _ in ()).throw(LeanBridgeError("compile error")))
    res = fb.formalize_and_audit(VALID_LEDGER, fz.ScriptedFormalizer(["always bad"]),
                                 toolkit=TOOLKIT, repair_iters=2)
    assert res.formalized and not res.compiled
    assert res.attempts == 3                       # initial + 2 repairs
    assert "compile error" in res.error


def test_full_verify_authoritative(monkeypatch):
    # Faithfulness FAILS CLOSED: authoritative requires a faithfulness panel that actually passed.
    from agent.orchestrator.faithfulness import ScriptedFaithfulnessChecker
    monkeypatch.setattr("agent.gates.lean_bridge.audit_lean_source", lambda *a, **k: _passing_audit())
    fr = fz.ScriptedFormalizer("theorem ma_target : True := trivial",
                               certification_trusted=True)
    res = fb.full_verify(VALID_LEDGER, fr, toolkit=TOOLKIT,
                         faithfulness_checker=ScriptedFaithfulnessChecker(
                             True, certification_trusted=True))
    assert res.gate.admitted_deterministically
    assert res.authoritative_elementary


def test_full_verify_not_authoritative_without_faithfulness(monkeypatch):
    # Regression: compiled + audited but NO faithfulness checker => NOT authoritative (fail closed).
    # Asserted via formalize_and_audit so it is independent of the informal-gate outcome.
    monkeypatch.setattr("agent.gates.lean_bridge.audit_lean_source", lambda *a, **k: _passing_audit())
    fr = fz.ScriptedFormalizer("theorem ma_target : True := trivial")
    res = fb.formalize_and_audit(VALID_LEDGER, fr, toolkit=TOOLKIT)
    assert res.elementary_verified      # the proof IS audited elementary...
    assert not res.faithful             # ...but faithfulness never ran...
    assert not res.authoritative        # ...so it is NOT authoritative


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
    fr = fz.ScriptedFormalizer("theorem ma_target : True := trivial",
                               certification_trusted=True)
    res = fb.formalize_and_audit(VALID_LEDGER, fr, toolkit=TOOLKIT,
                                 informal_claim="claim",
                                 faithfulness_checker=ScriptedFaithfulnessChecker(
                                     True, certification_trusted=True))
    assert res.authoritative


def test_no_faithfulness_checker_blocks_authoritative(monkeypatch):
    # Fail closed at the unit level: compiled + audit-passed but NO faithfulness checker => the
    # statement was never verified, so the result must NOT be authoritative.
    monkeypatch.setattr("agent.gates.lean_bridge.audit_lean_source", lambda *a, **k: _passing_audit())
    fr = fz.ScriptedFormalizer("theorem ma_target : True := trivial")
    res = fb.formalize_and_audit(VALID_LEDGER, fr, toolkit=TOOLKIT)  # no faithfulness_checker
    assert res.compiled and res.elementary_verified
    assert res.faithfulness is None
    assert not res.faithful and not res.authoritative


def test_make_terminal_gate(monkeypatch):
    from agent.orchestrator.faithfulness import ScriptedFaithfulnessChecker
    monkeypatch.setattr("agent.gates.lean_bridge.audit_lean_source", lambda *a, **k: _passing_audit())
    gate = fb.make_terminal_gate(
        fz.ScriptedFormalizer("theorem ma_target : True := trivial",
                              certification_trusted=True),
        toolkit=TOOLKIT,
        faithfulness_checker=ScriptedFaithfulnessChecker(True, certification_trusted=True))
    result = gate("root goal", "proof bundle text")
    assert result.authoritative


def test_make_terminal_gate_without_faithfulness_not_authoritative(monkeypatch):
    # A terminal gate built WITHOUT a faithfulness checker can never certify authoritative (fail closed).
    monkeypatch.setattr("agent.gates.lean_bridge.audit_lean_source", lambda *a, **k: _passing_audit())
    gate = fb.make_terminal_gate(fz.ScriptedFormalizer("theorem ma_target : True := trivial"),
                                 toolkit=TOOLKIT)
    result = gate("root goal", "proof bundle text")
    assert result.elementary_verified and not result.authoritative


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
