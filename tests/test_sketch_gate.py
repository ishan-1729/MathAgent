"""Tests for the P4 AND-node sorry-sketch composition gate (LEAP §2.2/§2.5).

All offline + deterministic: the formalizer's `formalize_sketch` is a stub (no model/CLI), and Lean is
either unavailable (the unavailable-detection path) or stubbed via `audit_lean_source` monkeypatching
(the compiled+audited path). The live CLI and a real Lean toolchain are never invoked here.

This covers:
  * `formalizer.formalize_sketch` (CodexFormalizer / ClaudeFormalizer / ScriptedFormalizer): the
    children-as-hypotheses prompt shape + sorry-free contract.
  * `formalize_bridge.make_sketch_gate`: unavailable detection without Lean, and a compiled+audited
    composition reporting `elementary_verified`.
"""
import json

import pytest

from agent.tools import formalizer as fz
from agent.tools.formalizer import (
    CodexFormalizer, ClaudeFormalizer, ScriptedFormalizer, DEFAULT_THEOREM_NAME,
)
from agent.gates import lean_bridge
from agent.gates.lean_audit import LeanAuditResult, LeanVerdict
from agent.gates.report import Finding, Severity, LAYER_LEAN
from agent.gates.toolkit import load_toolkit
from agent.orchestrator import formalize_bridge as fb
from agent.orchestrator.formalize_bridge import make_sketch_gate

TOOLKIT = load_toolkit()

PARENT = "For all integers n, P(n)."
CHILDREN = ["For all integers n, Q(n).", "For all integers n, Q(n) -> P(n)."]
SKETCH = json.dumps({"problem": "p", "claim": PARENT, "steps": [
    {"id": "L0", "claim": CHILDREN[0], "justification": "lemma", "depends_on": []},
    {"id": "L1", "claim": CHILDREN[1], "justification": "lemma", "depends_on": []},
    {"id": "c", "claim": PARENT, "justification": "conclusion", "depends_on": ["L0", "L1"]}]})


# ---- formalize_sketch: the children-as-hypotheses prompt + parsing (Claude impl) ----

def test_claude_formalize_sketch_emits_hypotheses_prompt(monkeypatch):
    seen = {}

    def fake_run(prompt, cfg):
        seen["prompt"] = prompt
        return "```lean\ntheorem ma_target (h0 : True) (h1 : True) : True := trivial\n```"

    monkeypatch.setattr(fz, "_run_claude", fake_run)
    res = ClaudeFormalizer(TOOLKIT).formalize_sketch(PARENT, SKETCH, CHILDREN)
    assert res.ok and res.theorem_name == "ma_target"
    # The prompt asks for a SORRY-FREE composition with the children as named hypotheses h0, h1.
    assert "SORRY-FREE" in seen["prompt"]
    assert "h0 : " + CHILDREN[0] in seen["prompt"]
    assert "h1 : " + CHILDREN[1] in seen["prompt"]
    assert PARENT in seen["prompt"]


def test_claude_formalize_sketch_repair_prompt(monkeypatch):
    seen = {}

    def fake_run(prompt, cfg):
        seen["prompt"] = prompt
        return "```lean\ntheorem ma_target (h0 : True) : True := trivial\n```"

    monkeypatch.setattr(fz, "_run_claude", fake_run)
    res = ClaudeFormalizer(TOOLKIT).formalize_sketch(
        PARENT, SKETCH, CHILDREN[:1],
        prior_source="theorem ma_target := bad", errors=["unknown identifier 'bad'"],
        lemmas=["Nat.add_zero : n + 0 = n"])
    assert res.ok
    assert "failed to compile" in seen["prompt"]
    assert "unknown identifier 'bad'" in seen["prompt"]
    assert "Nat.add_zero : n + 0 = n" in seen["prompt"]


def test_claude_formalize_sketch_no_code(monkeypatch):
    monkeypatch.setattr(fz, "_run_claude", lambda p, c: "I cannot formalize this.")
    assert not ClaudeFormalizer(TOOLKIT).formalize_sketch(PARENT, SKETCH, CHILDREN).ok


# ---- formalize_sketch: the Codex impl mirrors the same shape; formalize() is unchanged ----

def test_codex_formalize_sketch_mirrors_and_formalize_unchanged(monkeypatch):
    seen = {}

    def fake_run(prompt, cfg):
        seen.setdefault("prompts", []).append(prompt)
        return "```lean\ntheorem ma_target (h0 : True) (h1 : True) : True := trivial\n```"

    monkeypatch.setattr(fz, "_run_codex", fake_run)
    f = CodexFormalizer(TOOLKIT)
    sk = f.formalize_sketch(PARENT, SKETCH, CHILDREN)
    assert sk.ok and sk.theorem_name == "ma_target"
    assert "h0 : " + CHILDREN[0] in seen["prompts"][-1]
    # The plain formalize() path still works and uses the ALREADY-VERIFIED single-proof prompt
    # (not the composition prompt) -> its existing semantics are untouched.
    ledger = json.dumps({"problem": "p", "claim": PARENT, "steps": [
        {"id": "s1", "claim": "x", "justification": "given", "depends_on": []},
        {"id": "s2", "claim": PARENT, "justification": "conclusion", "depends_on": ["s1"]}]})
    fr = f.formalize(ledger)
    assert fr.ok
    assert "ALREADY-VERIFIED" in seen["prompts"][-1]
    assert "ASSUMING" not in seen["prompts"][-1]   # the composition wording is not in plain formalize


def test_scripted_formalizer_formalize_sketch():
    f = ScriptedFormalizer("theorem ma_target (h0 : True) : True := trivial")
    res = f.formalize_sketch(PARENT, SKETCH, CHILDREN[:1])
    assert res.ok and res.theorem_name == DEFAULT_THEOREM_NAME
    # A prior_source advances the repair counter, mirroring formalize().
    res2 = f.formalize_sketch(PARENT, SKETCH, CHILDREN[:1], prior_source="x", errors=["e"])
    assert res2.ok and f.repair_calls == 1


# ---- make_sketch_gate: unavailable detection without Lean (offline-safe) ----

def test_make_sketch_gate_reports_unavailable_when_no_lean():
    """With no Lean toolchain reachable (no server, no project_dir, `lean` absent), make_sketch_gate
    returns lean_unavailable WITHOUT attempting a compile — the offline-safe fail-closed path. (Skipped
    if Lean is installed, since then the gate would try to compile.)"""
    class _NoFormalizer:
        def formalize_sketch(self, parent_goal, sketch_text, child_goals, **kw):
            raise AssertionError("formalize_sketch must not be called when Lean is unavailable")

    if lean_bridge.available():
        pytest.skip("Lean is installed in this environment; the unavailable path is not exercised")
    gate = make_sketch_gate(_NoFormalizer(), toolkit=TOOLKIT)
    verdict = gate(PARENT, SKETCH, CHILDREN)
    assert verdict.lean_unavailable is True
    assert verdict.compiled is False and verdict.elementary_verified is False


# ---- make_sketch_gate: a compiled+audited composition reports elementary_verified (Lean stubbed) ----

class _StubServer:
    """A minimal 'server' so make_sketch_gate's `_lean_available` returns True without a real Lean."""
    def available(self):
        return True


def test_make_sketch_gate_compiled_audited_is_elementary_verified(monkeypatch):
    """A sketch that formalizes (stub formalizer) and whose audit PASSES (stub audit) yields a verdict
    with `.elementary_verified` True: the composition compiled and the body's deps/axioms are
    elementary. faithfulness is None (deferred to the root), so `.authoritative` stays False."""
    formalizer = ScriptedFormalizer("theorem ma_target (h0 : True) (h1 : True) : True := trivial")

    def fake_audit(source, name, **kw):
        return LeanAuditResult(verdict=LeanVerdict.PASS, findings=[])

    monkeypatch.setattr(fb.lean_bridge, "audit_lean_source", fake_audit)
    gate = make_sketch_gate(formalizer, toolkit=TOOLKIT, server=_StubServer())
    verdict = gate(PARENT, SKETCH, CHILDREN)
    assert verdict.formalized is True and verdict.compiled is True
    assert verdict.elementary_verified is True
    assert verdict.authoritative is False           # faithfulness deferred to the root terminal gate
    assert verdict.faithfulness is None


def test_make_sketch_gate_audit_rejected_is_not_elementary_verified(monkeypatch):
    """A sketch that compiles but whose audit REJECTS (non-elementary body dep) is compiled but NOT
    elementary_verified -> the driver would reject the composition (composition not Lean-elementary)."""
    formalizer = ScriptedFormalizer("theorem ma_target (h0 : True) : True := sorry")
    rej = Finding(LAYER_LEAN, Severity.REJECT, "sorry_axiom",
                  "proof uses non-whitelisted axiom 'sorryAx'")

    def fake_audit(source, name, **kw):
        return LeanAuditResult(verdict=LeanVerdict.REJECT, findings=[rej])

    monkeypatch.setattr(fb.lean_bridge, "audit_lean_source", fake_audit)
    gate = make_sketch_gate(formalizer, toolkit=TOOLKIT, server=_StubServer())
    verdict = gate(PARENT, SKETCH, CHILDREN[:1])
    assert verdict.compiled is True
    assert verdict.elementary_verified is False
    assert verdict.lean_compiled_but_rejected is True


def test_make_sketch_gate_unparseable_formalization_not_formalized(monkeypatch):
    """A formalizer that produces no Lean -> formalized=False, never compiled/elementary."""
    formalizer = ScriptedFormalizer("not lean at all", ok=False)
    # audit must never be reached when nothing formalized.
    monkeypatch.setattr(fb.lean_bridge, "audit_lean_source",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("audit must not run")))
    gate = make_sketch_gate(formalizer, toolkit=TOOLKIT, server=_StubServer())
    verdict = gate(PARENT, SKETCH, CHILDREN)
    assert verdict.formalized is False
    assert verdict.compiled is False and verdict.elementary_verified is False
