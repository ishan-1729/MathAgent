"""Build and install the wheel, then exercise runtime paths from outside the source checkout."""
from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def _run(argv, *, cwd: Path, env=None) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=cwd, env=env, text=True, capture_output=True, timeout=180,
                          check=True)


def test_installed_wheel_has_runtime_assets_and_console_entrypoint(tmp_path):
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    _run([sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "--no-build-isolation",
          "--wheel-dir", str(wheel_dir)], cwd=REPO)
    wheels = list(wheel_dir.glob("mathagent-*.whl"))
    assert len(wheels) == 1

    with zipfile.ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
        required = {
            "agent/roles/prover.md",
            "agent/roles/critic_judge.md",
            "agent/gates/lean/Audit.lean",
            "profiles/default.yaml",
            "profiles/authoritative.yaml",
            "scripts/prove.py",
            "formal/lean/mathagent_formal/lakefile.toml",
            "formal/lean/mathagent_formal/lake-manifest.json",
            "formal/lean/mathagent_formal/lean-toolchain",
            "formal/lean/mathagent_formal/MathagentFormal.lean",
            "formal/lean/mathagent_formal/MathagentFormal/Basic.lean",
        }
        assert required <= names
        assert not any("/.lake/" in name for name in names)
        assert "profiles/ablation/no-h0.yaml" not in names
        entry_points = next(name for name in names if name.endswith(".dist-info/entry_points.txt"))
        assert "mathagent-prove = scripts.prove:main" in archive.read(entry_points).decode("utf-8")

    # Install the wheel into an isolated target directory while retaining the test interpreter's
    # already-installed dependencies.  A nested ``venv --system-site-packages`` does not inherit the
    # *current venv's* packages (only the base interpreter's), so it falsely fails on PyYAML/SymPy in
    # normal CI virtualenvs.  ``--target`` isolates MathAgent itself without requiring network access.
    install_root = tmp_path / "installed"
    _run([sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(install_root),
          str(wheels[0])], cwd=tmp_path)
    bindir = install_root / ("Scripts" if os.name == "nt" else "bin")

    # Run outside the checkout with PYTHONPATH removed: every resolved asset must come from the actual
    # wheel install, not an accidentally importable source-tree sibling.
    env = os.environ.copy()
    env["PYTHONPATH"] = str(install_root)
    env["MATHAGENT_INSTALL_ROOT"] = str(install_root)
    env["MATHAGENT_CACHE_DIR"] = str(tmp_path / "runtime-cache")
    probe = r"""
import os
from pathlib import Path
import agent
import scripts
from agent.gates import lean_bridge
from agent.tools import claude_roles, codex_prover
import scripts.prove
from scripts import _benchmark_artifacts

site_agent = Path(agent.__file__).resolve().parent
assert site_agent.is_relative_to(Path(os.environ['MATHAGENT_INSTALL_ROOT']).resolve())
assert scripts.__file__ is not None
assert Path(scripts.__file__).resolve().parent.is_relative_to(
    Path(os.environ['MATHAGENT_INSTALL_ROOT']).resolve())
assert codex_prover._role('prover.md').strip()
assert claude_roles._role('critic_judge.md').strip()
assert lean_bridge._AUDIT_LEAN.is_file()
project = lean_bridge.find_mathlib_project()
assert project is not None and (Path(project) / 'MathagentFormal' / 'Basic.lean').is_file()
assert Path(project).is_relative_to(Path(os.environ['MATHAGENT_CACHE_DIR']))
assert 'site-packages' not in str(Path(project)).lower()
revision = _benchmark_artifacts.code_revision(Path(agent.__file__).resolve().parents[1])
assert revision.startswith('dist:mathagent@') and '+payload.sha256:' in revision
assert not revision.endswith('unknown+unverified')
"""
    _run([sys.executable, "-c", probe], cwd=tmp_path, env=env)

    cli = bindir / ("mathagent-prove.exe" if os.name == "nt" else "mathagent-prove")
    result = _run([str(cli), "--help"], cwd=tmp_path, env=env)
    assert "usage: mathagent-prove" in result.stdout and "--profile" in result.stdout
    preset = _run([str(cli), "--dump-profile", "--profile", "profiles/default.yaml"],
                  cwd=tmp_path, env=env)
    assert "name: default" in preset.stdout
