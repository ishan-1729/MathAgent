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
_SENTINEL = "MATHAGENT_AUDIT_JSON"
# The report JSON is one line (Json.compress); capture it after the sentinel from either `lean`
# stdout diagnostics or a REPL message.
_AUDIT_RE = re.compile(r"MATHAGENT_AUDIT_JSON\s+(\{.*\})")


def extract_report_json(text: str) -> Optional[str]:
    m = _AUDIT_RE.search(text)
    return m.group(1) if m else None


class LeanUnavailable(RuntimeError):
    pass


class LeanBridgeError(RuntimeError):
    pass


def _find_tool(name: str) -> Optional[str]:
    p = shutil.which(name)
    if p:
        return p
    exe = f"{name}.exe" if os.name == "nt" else name
    guess = Path.home() / ".elan" / "bin" / exe
    return str(guess) if guess.exists() else None


def find_lean() -> Optional[str]:
    return _find_tool("lean")


def find_lake() -> Optional[str]:
    return _find_tool("lake")


def available() -> bool:
    return find_lean() is not None


def find_mathlib_project() -> Optional[str]:
    """The repo's Mathlib lake project, if scaffolded (so `import Mathlib` proofs can be audited)."""
    proj = Path(__file__).resolve().parents[2] / "formal" / "lean" / "mathagent_formal"
    return str(proj) if (proj / "lakefile.toml").exists() else None


def _extractor_src() -> str:
    return _AUDIT_LEAN.read_text(encoding="utf-8")


_IMPORT_RE = re.compile(r"^\s*import\s+\S+.*$", re.MULTILINE)


def _split_imports(src: str) -> tuple[list[str], str]:
    """Return (import lines, body-without-imports). Lean requires all imports at the file top."""
    imports = [m.group(0).strip() for m in _IMPORT_RE.finditer(src)]
    body = _IMPORT_RE.sub("", src)
    return imports, body


def _assemble_source(proof_src: str, theorem_name: str) -> str:
    """Combine the extractor + proof into one file with all imports hoisted to the top (deduped)."""
    ext_imports, ext_body = _split_imports(_extractor_src())
    pf_imports, pf_body = _split_imports(proof_src)
    seen: set[str] = set()
    imports: list[str] = []
    for imp in ext_imports + pf_imports:
        if imp not in seen:
            seen.add(imp)
            imports.append(imp)
    return ("\n".join(imports) + "\n\n" + ext_body.strip() + "\n\n"
            + pf_body.strip() + f"\n\n#audit {theorem_name}\n")


def run_extractor(proof_src: str, theorem_name: str, timeout_s: int = 300,
                  project_dir: Optional[str | Path] = None, server: Optional[object] = None) -> str:
    """Compile `proof_src` and return the raw dependency-report JSON for `theorem_name`.

    - `server` (a LeanServer): reuse a persistent Mathlib-loaded process (fast). Preferred when set.
    - `project_dir` (a lake project): run `lake env lean` so `import Mathlib` resolves.
    - otherwise a bare `lean` runs core-only proofs.
    """
    if server is not None:
        return server.audit(proof_src, theorem_name, timeout_s=timeout_s)

    source = _assemble_source(proof_src, theorem_name)
    workdir = tempfile.mkdtemp(prefix="lean_audit_")
    lean_file = Path(workdir) / "Target.lean"
    lean_file.write_text(source, encoding="utf-8")

    if project_dir is not None:
        lake = find_lake()
        if not lake:
            raise LeanUnavailable("lake not found on PATH or ~/.elan/bin")
        argv = [lake, "env", "lean", str(lean_file)]
        cwd = str(project_dir)
    else:
        lean = find_lean()
        if not lean:
            raise LeanUnavailable("lean not found on PATH or ~/.elan/bin")
        argv = [lean, str(lean_file)]
        cwd = workdir

    try:
        proc = subprocess.run(
            argv,
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=timeout_s, cwd=cwd,
            env={**os.environ, "MATHAGENT_TOOLCHAIN": os.environ.get("MATHAGENT_TOOLCHAIN", "")},
        )
        combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
        report = extract_report_json(combined)
        if report is not None:
            return report
        # No sentinel => the proof did not compile (or the decl was absent); surface diagnostics.
        tail = combined.strip()[-1200:]
        raise LeanBridgeError(f"no audit JSON emitted (proof failed to compile?):\n{tail}")
    except subprocess.TimeoutExpired as e:
        raise LeanBridgeError(f"lean timed out after {timeout_s}s") from e
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def audit_lean_source(proof_src: str, theorem_name: str,
                      toolkit: Optional[Toolkit] = None, timeout_s: int = 300,
                      project_dir: Optional[str | Path] = None,
                      server: Optional[object] = None) -> LeanAuditResult:
    """Compile + extract + audit a Lean proof. Raises LeanUnavailable/LeanBridgeError on setup issues;
    a compiling-but-non-elementary proof returns a REJECT result (not an exception). Pass `project_dir`
    (a Mathlib lake project) to audit `import Mathlib` proofs, or `server` (a LeanServer) to reuse a
    persistent Mathlib-loaded process."""
    report_json = run_extractor(proof_src, theorem_name, timeout_s=timeout_s,
                                project_dir=project_dir, server=server)
    return audit_json(report_json, toolkit)
