"""Bridge: compile a Lean proof, extract its dependency report, and run the Layer-4 audit.

Given Lean source defining a sorry-free theorem, this prepends the `#audit` extractor
(`agent/gates/lean/Audit.lean`), appends `#audit <theorem>`, runs `lean <file>`, parses the emitted
`MATHAGENT_AUDIT_JSON` line, and hands it to `agent.gates.lean_audit.audit_report`.

Self-contained (`import Lean` only), so core-only proofs need no Mathlib build. For Mathlib proofs a
lake project with a Mathlib dependency is required (later); the extractor + auditor are unchanged.

Guarded: if `lean` is not installed, `available()` is False and `audit_lean_source` raises
LeanUnavailable, so the rest of the harness runs without Lean.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from agent.gates.lean_audit import LeanAuditResult, audit_json
from agent.gates.toolkit import Toolkit

_AUDIT_LEAN = Path(__file__).resolve().parent / "lean" / "Audit.lean"
_SENTINEL = "MATHAGENT_AUDIT_JSON "


class LeanUnavailable(RuntimeError):
    pass


class LeanBridgeError(RuntimeError):
    pass


def find_lean() -> Optional[str]:
    p = shutil.which("lean")
    if p:
        return p
    guess = Path.home() / ".elan" / "bin" / ("lean.exe" if os.name == "nt" else "lean")
    return str(guess) if guess.exists() else None


def available() -> bool:
    return find_lean() is not None


def _extractor_src() -> str:
    return _AUDIT_LEAN.read_text(encoding="utf-8")


def run_extractor(proof_src: str, theorem_name: str, timeout_s: int = 300) -> str:
    """Compile `proof_src` and return the raw dependency-report JSON for `theorem_name`."""
    lean = find_lean()
    if not lean:
        raise LeanUnavailable("lean not found on PATH or ~/.elan/bin")

    source = f"{_extractor_src()}\n\n{proof_src}\n\n#audit {theorem_name}\n"
    workdir = tempfile.mkdtemp(prefix="lean_audit_")
    lean_file = Path(workdir) / "Target.lean"
    lean_file.write_text(source, encoding="utf-8")
    try:
        proc = subprocess.run(
            [lean, str(lean_file)],
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=timeout_s, cwd=workdir,
            env={**os.environ, "MATHAGENT_TOOLCHAIN": os.environ.get("MATHAGENT_TOOLCHAIN", "")},
        )
        combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
        for line in combined.splitlines():
            if line.startswith(_SENTINEL):
                return line[len(_SENTINEL):].strip()
        # No sentinel => the proof did not compile (or the decl was absent); surface diagnostics.
        tail = combined.strip()[-1200:]
        raise LeanBridgeError(f"no audit JSON emitted (proof failed to compile?):\n{tail}")
    except subprocess.TimeoutExpired as e:
        raise LeanBridgeError(f"lean timed out after {timeout_s}s") from e
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def audit_lean_source(proof_src: str, theorem_name: str,
                      toolkit: Optional[Toolkit] = None, timeout_s: int = 300) -> LeanAuditResult:
    """Compile + extract + audit a Lean proof. Raises LeanUnavailable/LeanBridgeError on setup issues;
    a compiling-but-non-elementary proof returns a REJECT result (not an exception)."""
    report_json = run_extractor(proof_src, theorem_name, timeout_s=timeout_s)
    return audit_json(report_json, toolkit)
