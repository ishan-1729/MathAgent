"""Tests for the Codex (GPT-5.5-xHigh) focused-prover integration.

Offline tests cover the pure logic (sketch->children extraction, prompt construction, availability).
The LIVE test actually shells out to Codex and is skipped unless BOTH:
  - the codex CLI is on PATH, and
  - the env var MATHAGENT_CODEX_TESTS=1 is set
so the default suite stays fast and offline.
"""
import json
import os

import pytest

from agent.gates.toolkit import load_toolkit
from agent.tools import codex_prover as cp

TOOLKIT = load_toolkit()


# ---- offline: sketch -> children extraction ----

def test_children_from_sketch_extracts_lemma_claims():
    sketch = json.dumps({"problem": "p", "claim": "G", "steps": [
        {"id": "L0", "claim": "lemma A", "justification": "lemma", "depends_on": []},
        {"id": "L1", "claim": "lemma B", "justification": "lemma", "depends_on": []},
        {"id": "s", "claim": "setup", "justification": "given", "depends_on": []},
        {"id": "c", "claim": "G", "justification": "conclusion", "depends_on": ["L0", "L1", "s"]}]})
    assert cp.children_from_sketch(sketch) == ["lemma A", "lemma B"]


def test_children_from_sketch_dedupes():
    sketch = json.dumps({"problem": "p", "claim": "G", "steps": [
        {"id": "L0", "claim": "same lemma", "justification": "lemma", "depends_on": []},
        {"id": "L1", "claim": "same   lemma", "justification": "lemma", "depends_on": []},
        {"id": "c", "claim": "G", "justification": "conclusion", "depends_on": ["L0", "L1"]}]})
    # normalized-equal claims collapse to one child
    assert cp.children_from_sketch(sketch) == ["same lemma"]


def test_children_from_sketch_unparseable():
    assert cp.children_from_sketch("not a ledger") == []


def test_prover_prompt_contains_goal_and_toolkit(monkeypatch):
    captured = {}

    def fake_run(prompt, cfg):
        captured["prompt"] = prompt
        return '```json\n{"problem":"p","claim":"G","steps":[]}\n```'

    monkeypatch.setattr(cp, "_run_codex", fake_run)
    cp.CodexProver(TOOLKIT).prove("MY_GOAL_X", feedback=["fix the descent measure"])
    assert "MY_GOAL_X" in captured["prompt"]
    assert "descent" in captured["prompt"]              # toolkit keys are listed
    assert "fix the descent measure" in captured["prompt"]  # feedback is plumbed in


def test_reviewer_parses_verdict(monkeypatch):
    monkeypatch.setattr(cp, "_run_codex",
                        lambda p, c: 'sure: {"useful": true, "elementary": false, "notes": ["uses ANT"]}')
    v = cp.CodexReviewer(TOOLKIT).review("G", "sketch", ["A"])
    assert v.useful is True and v.elementary is False and "uses ANT" in v.notes


def test_reviewer_handles_garbage(monkeypatch):
    monkeypatch.setattr(cp, "_run_codex", lambda p, c: "no json here")
    v = cp.CodexReviewer(TOOLKIT).review("G", "sketch", ["A"])
    assert not v.ok


def test_config_defaults_to_gpt55_xhigh():
    cfg = cp.CodexConfig()
    assert cfg.model == "gpt-5.5" and cfg.reasoning_effort == "xhigh"


# ---- live (opt-in) ----

_LIVE = os.environ.get("MATHAGENT_CODEX_TESTS") == "1" and cp.CodexProver.available()


@pytest.mark.skipif(not _LIVE, reason="set MATHAGENT_CODEX_TESTS=1 and have codex on PATH for live test")
def test_live_direct_prove_trivial():
    from agent.orchestrator import RalphLoop, Budget, RunTrace
    cfg = cp.CodexConfig(reasoning_effort="low", timeout_s=300)
    prover = cp.CodexProver(TOOLKIT, cfg)
    res = RalphLoop(prover, toolkit=TOOLKIT, budget=Budget(max_llm_calls=3),
                    trace=RunTrace("live")).run("For all integers n, n + 0 = n.")
    assert res.success
