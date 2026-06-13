"""Live tests for the Lean Layer-4 bridge (compile -> extract -> audit).

Opt-in: skipped unless `lean` is installed AND env var MATHAGENT_LEAN_TESTS=1 is set, so the default
suite needs no Lean toolchain. Validated against Lean 4.30.0 during development:
  - a core elementary proof passes the audit;
  - a `sorry` proof is rejected by the axiom gate (sorryAx).
"""
import os

import pytest

from agent.gates import lean_bridge

_LIVE = os.environ.get("MATHAGENT_LEAN_TESTS") == "1" and lean_bridge.available()
pytestmark = pytest.mark.skipif(
    not _LIVE, reason="set MATHAGENT_LEAN_TESTS=1 and install lean for live Lean-audit tests")


def test_live_elementary_proof_passes():
    res = lean_bridge.audit_lean_source(
        "theorem ma_add_zero (n : Nat) : n + 0 = n := Nat.add_zero n",
        "ma_add_zero", timeout_s=300)
    assert res.passed, [str(f) for f in res.rejects()]


def test_live_sorry_is_rejected():
    res = lean_bridge.audit_lean_source(
        "theorem ma_sorry : (2 : Nat) = 2 := by sorry", "ma_sorry", timeout_s=300)
    assert not res.passed
    assert "sorry_axiom" in {f.code for f in res.rejects()}
