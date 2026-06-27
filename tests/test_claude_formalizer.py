"""Tests for the Opus-backed ClaudeFormalizer (offline, deterministic).

Mirrors the Codex formalizer stub tests in tests/test_formalizer.py: the Claude CLI
(`_run_claude`) is monkeypatched to return a fixed fenced ```lean block, and we assert
`formalize()` parses it into a FormalizationResult. The live CLI is never invoked here.
"""
import json

from agent.tools import formalizer as fz
from agent.tools.claude_cli import ClaudeConfig
from agent.gates.toolkit import load_toolkit

TOOLKIT = load_toolkit()

VALID_LEDGER = json.dumps({"problem": "p", "claim": "For all integers n, n + 0 = n.", "steps": [
    {"id": "s1", "claim": "Let n be an integer.", "justification": "given", "depends_on": []},
    {"id": "s2", "claim": "n + 0 = n.", "justification": "conclusion", "depends_on": ["s1"]}]})


def test_claude_formalizer_parses(monkeypatch):
    monkeypatch.setattr(fz, "_run_claude",
                        lambda p, c: "```lean\ntheorem ma_target (n : Nat) : n + 0 = n := Nat.add_zero n\n```")
    res = fz.ClaudeFormalizer(TOOLKIT).formalize(VALID_LEDGER)
    assert res.ok and res.theorem_name == "ma_target"
    assert "Nat.add_zero" in res.lean_source


def test_claude_formalizer_no_code(monkeypatch):
    monkeypatch.setattr(fz, "_run_claude", lambda p, c: "I cannot formalize this.")
    assert not fz.ClaudeFormalizer(TOOLKIT).formalize(VALID_LEDGER).ok


def test_claude_formalizer_uses_opus_by_default(monkeypatch):
    seen = {}

    def fake_run(prompt, cfg):
        seen["model"] = cfg.model
        return "```lean\ntheorem ma_target : True := trivial\n```"

    monkeypatch.setattr(fz, "_run_claude", fake_run)
    res = fz.ClaudeFormalizer(TOOLKIT).formalize(VALID_LEDGER)
    assert res.ok and seen["model"] == "opus"


def test_claude_formalizer_repair_path(monkeypatch):
    # When prior_source is given the repair prompt is used; the parsed result is unchanged in shape.
    seen = {}

    def fake_run(prompt, cfg):
        seen["prompt"] = prompt
        return "```lean4\ntheorem ma_target (n : Nat) : n + 0 = n := Nat.add_zero n\n```"

    monkeypatch.setattr(fz, "_run_claude", fake_run)
    res = fz.ClaudeFormalizer(TOOLKIT).formalize(
        VALID_LEDGER, prior_source="theorem ma_target := bad",
        errors=["unknown identifier 'bad'"], lemmas=["Nat.add_zero : n + 0 = n"])
    assert res.ok and res.theorem_name == "ma_target"
    assert "FAILED to compile" in seen["prompt"]
    assert "unknown identifier 'bad'" in seen["prompt"]
    assert "Nat.add_zero : n + 0 = n" in seen["prompt"]


def test_claude_formalizer_honors_config(monkeypatch):
    seen = {}

    def fake_run(prompt, cfg):
        seen["timeout"] = cfg.timeout_s
        return "```lean\ntheorem ma_target : True := trivial\n```"

    monkeypatch.setattr(fz, "_run_claude", fake_run)
    fz.ClaudeFormalizer(TOOLKIT, cfg=ClaudeConfig(model="opus", timeout_s=42)).formalize(VALID_LEDGER)
    assert seen["timeout"] == 42
