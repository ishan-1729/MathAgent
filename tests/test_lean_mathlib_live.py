"""Live Mathlib-backed Layer-4 tests (opt-in; require the scaffolded Mathlib project).

Skipped unless MATHAGENT_LEAN_TESTS=1 AND the Mathlib lake project exists (formal/lean/mathagent_formal
with `lake exe cache get` done). Validated against Lean 4.30.0 + Mathlib (v4.30.0 rev):
  - an elementary `import Mathlib` proof passes the dependency audit;
  - a proof that references a denylisted declaration (IsDedekindDomain) is rejected.
These each load all of Mathlib (~30-60s), hence opt-in.
"""
import os

import pytest

from agent.gates import lean_bridge

_PROJ = lean_bridge.find_mathlib_project()
_LIVE = os.environ.get("MATHAGENT_LEAN_TESTS") == "1" and lean_bridge.available() and _PROJ is not None
pytestmark = pytest.mark.skipif(
    not _LIVE, reason="set MATHAGENT_LEAN_TESTS=1 and scaffold the Mathlib project for live Mathlib tests")


def test_elementary_mathlib_proof_passes():
    res = lean_bridge.audit_lean_source(
        "import Mathlib\ntheorem ma_target (n : Int) : n + 0 = n := by simp",
        "ma_target", project_dir=_PROJ, timeout_s=480)
    assert res.passed, [str(f) for f in res.rejects()]


def test_denylisted_dependency_rejected():
    res = lean_bridge.audit_lean_source(
        "import Mathlib\ntheorem ma_dedekind : IsDedekindDomain Int -> True := fun _ => trivial",
        "ma_dedekind", project_dir=_PROJ, timeout_s=480)
    assert not res.passed
    assert "denylisted_dependency" in {f.code for f in res.rejects()}
