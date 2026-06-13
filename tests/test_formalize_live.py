"""Live end-to-end formalization-bridge test (opt-in; requires Codex + Lean + Mathlib project).

Skipped unless MATHAGENT_CODEX_TESTS=1 AND MATHAGENT_LEAN_TESTS=1 (and the tools/project exist). Runs
the full loop: informal ledger -> gate -> Codex formalization -> compile (lake env lean) -> Layer-4
audit, and asserts the trivial target is authoritatively verified elementary.
"""
import json
import os

import pytest

from agent.gates import lean_bridge
from agent.gates.toolkit import load_toolkit
from agent.tools.codex_prover import CodexProver, CodexConfig
from agent.tools.formalizer import CodexFormalizer
from agent.orchestrator.formalize_bridge import full_verify

_LIVE = (os.environ.get("MATHAGENT_CODEX_TESTS") == "1"
         and os.environ.get("MATHAGENT_LEAN_TESTS") == "1"
         and CodexProver.available() and lean_bridge.available())

pytestmark = pytest.mark.skipif(not _LIVE, reason="needs MATHAGENT_CODEX_TESTS=1 + MATHAGENT_LEAN_TESTS=1")


def test_full_verify_trivial_target():
    tk = load_toolkit()
    ledger = json.dumps({"problem": "add_zero", "claim": "For all integers n, n + 0 = n.", "steps": [
        {"id": "s1", "claim": "Let n be an integer.", "justification": "given", "depends_on": []},
        {"id": "s2", "claim": "n + 0 = n by additive identity.", "justification": "algebra",
         "depends_on": ["s1"]},
        {"id": "s3", "claim": "For all integers n, n + 0 = n.", "justification": "conclusion",
         "depends_on": ["s2"]}]})
    fm = CodexFormalizer(tk, CodexConfig(reasoning_effort="low", timeout_s=300))
    res = full_verify(ledger, fm, toolkit=tk, timeout_s=540)
    assert res.gate.admitted_deterministically
    assert res.authoritative_elementary, res.summary()
