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
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from agent.gates.lean_audit import LeanAuditResult, audit_json
from agent.gates.toolkit import Toolkit

_AUDIT_LEAN = Path(__file__).resolve().parent / "lean" / "Audit.lean"
_SENTINEL = "MATHAGENT_AUDIT_JSON"
# The bare sentinel token. A trusted report is emitted as `MATHAGENT_AUDIT_JSON <nonce> {json}`,
# where <nonce> is a fresh per-run secret the bridge injects into the `#audit` command and that an
# untrusted proof body cannot predict. We additionally count EVERY occurrence of the bare sentinel:
# more than one means the proof tried to forge a report (e.g. via `#eval IO.println "..."`), so we
# reject. The report JSON is one line (Json.compress).
_SENTINEL_RE = re.compile(re.escape(_SENTINEL))
# Lean diagnostic format: `file:line:col: error: ...` — distinguishes a real compile error from a
# (recoverable) warning like "declaration uses 'sorry'" (which the axiom gate catches separately).
_ERROR_RE = re.compile(r":\s*error:")
# Commands a proof body must not contain. Two threat classes:
#   1. Output-emitting / reduction / arbitrary-elaboration commands let untrusted source print its
#      own forged sentinel (or otherwise smuggle output into the captured channel): #eval/#print/
#      #reduce/#check, run_cmd/run_elab (run arbitrary CommandElabM/TermElabM code, incl. logInfo),
#      logInfo, logWarning, logError (any log* that reaches the message channel), IO.println/print,
#      dbg_trace. The emit family is matched with OPTIONAL dotted qualification (`Lean.logInfo`,
#      `_root_.IO.println`) — a bare-name-only match was bypassable via the namespace-qualified form.
#   2. Syntax/elaborator (re)definition lets untrusted source SHADOW the trusted `#audit` elaborator
#      so the appended `#audit <thm> "<nonce>"` runs attacker code that emits ONE clean nonce-bearing
#      sentinel (defeating both the >1-sentinel count and the nonce check). Ban elab/elab_rules/
#      macro/macro_rules/syntax/notation/infix*/prefix/postfix so the proof cannot redefine `#audit`.
#      These keyword tokens stay bare-name-only ON PURPOSE: allowing a dotted prefix there would
#      false-positive on legitimate decl components (e.g. `List.prefix_append` hits `prefix`).
# An elementary, sorry-free proof of a theorem never needs any of these, so we fail CLOSED on them.
_FORBIDDEN_PROOF_RE = re.compile(
    r"(?<![A-Za-z0-9_.])("
    r"#eval|#print|#reduce|#check"
    r"|run_cmd|run_elab"
    r"|(?:[A-Za-z_][A-Za-z0-9_]*\.)*"
    r"(?:logInfoAt|logInfo|logWarningAt|logWarning|logErrorAt|logError|logAt"
    r"|IO\.println|IO\.print|dbg_trace)"
    r"|elab_rules|elab"
    r"|macro_rules|macro"
    r"|syntax|notation"
    r"|infixl|infixr|infix|prefix|postfix"
    r")\b"
)


def make_nonce() -> str:
    """A fresh, unguessable token stamped into the audit sentinel for one run."""
    return secrets.token_hex(16)


def _audit_re(nonce: Optional[str]) -> re.Pattern:
    """Regex capturing the report JSON after the sentinel (and the run nonce, if provided)."""
    if nonce:
        return re.compile(re.escape(_SENTINEL) + r"\s+" + re.escape(nonce) + r"\s+(\{.*\})")
    return re.compile(re.escape(_SENTINEL) + r"\s+(\{.*\})")


def extract_report_json(text: str, nonce: Optional[str] = None) -> Optional[str]:
    """Return the trusted report JSON, or None if absent/forged.

    Fails CLOSED: if the bare sentinel appears more than once anywhere in `text`, a proof body
    forged an extra report line, so we reject (return None). When a `nonce` is given, only a line
    carrying that exact nonce is accepted.
    """
    if len(_SENTINEL_RE.findall(text)) > 1:
        return None  # forged second sentinel — reject
    matches = _audit_re(nonce).findall(text)
    if len(matches) != 1:
        return None
    return matches[0]


def _reject_if_forbidden(proof_src: str) -> None:
    """Raise if the untrusted proof body could forge a report or shadow the trusted `#audit`.

    Three layers, all fail CLOSED:
      1. No output/reduction/arbitrary-elaboration command (#eval/run_cmd/logInfo/IO.println/...,
         qualified or not) — can't print a forged sentinel.
      2. No syntax/elaborator (re)definition (elab/macro/syntax/notation/...) — can't SHADOW the
         appended `#audit <thm> "<nonce>"` so attacker code emits one clean nonce-bearing sentinel.
      3. No literal `#audit` command token and no literal sentinel string — defense in depth so the
         body cannot reference the trusted command name or embed the sentinel even if a redefinition
         keyword were ever missed.
    """
    m = _FORBIDDEN_PROOF_RE.search(proof_src)
    if m:
        raise LeanBridgeError(
            f"proof_src contains forbidden command {m.group(1)!r}: output/reduction/elaborator "
            "commands are not allowed in an audited proof (sentinel-injection guard)"
        )
    if "#audit" in proof_src:
        raise LeanBridgeError(
            "proof_src references the trusted '#audit' command: it may not name or redefine the "
            "audit command (sentinel-injection guard)"
        )
    if _SENTINEL in proof_src:
        raise LeanBridgeError(
            f"proof_src contains the audit sentinel {_SENTINEL!r}: it may not embed the report "
            "sentinel string (sentinel-injection guard)"
        )


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


def _audit_command(theorem_name: str, nonce: Optional[str]) -> str:
    """The `#audit` invocation. With a nonce, the extractor stamps it into the emitted sentinel so
    the bridge can distinguish a trusted report from one a proof body forged."""
    if nonce:
        return f'#audit {theorem_name} "{nonce}"'
    return f"#audit {theorem_name}"


def _assemble_source(proof_src: str, theorem_name: str, nonce: Optional[str] = None) -> str:
    """Combine the extractor + proof into one file with all imports hoisted to the top (deduped).

    The untrusted proof body is sanitized first (no output/reduction commands), and the appended
    `#audit` line — emitted AFTER the body so a clean body cannot suppress it — carries the run nonce.
    """
    _reject_if_forbidden(proof_src)
    ext_imports, ext_body = _split_imports(_extractor_src())
    pf_imports, pf_body = _split_imports(proof_src)
    seen: set[str] = set()
    imports: list[str] = []
    for imp in ext_imports + pf_imports:
        if imp not in seen:
            seen.add(imp)
            imports.append(imp)
    return ("\n".join(imports) + "\n\n" + ext_body.strip() + "\n\n"
            + pf_body.strip() + f"\n\n{_audit_command(theorem_name, nonce)}\n")


def run_extractor(proof_src: str, theorem_name: str, timeout_s: int = 300,
                  project_dir: Optional[str | Path] = None, server: Optional[object] = None) -> str:
    """Compile `proof_src` and return the raw dependency-report JSON for `theorem_name`.

    - `server` (a LeanServer): reuse a persistent Mathlib-loaded process (fast). Preferred when set.
    - `project_dir` (a lake project): run `lake env lean` so `import Mathlib` resolves.
    - otherwise a bare `lean` runs core-only proofs.
    """
    if server is not None:
        return server.audit(proof_src, theorem_name, timeout_s=timeout_s)

    nonce = make_nonce()
    source = _assemble_source(proof_src, theorem_name, nonce)
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
        report = extract_report_json(combined, nonce)
        # ERROR diagnostics => broken even if Lean error-recovered a declaration and emitted a report.
        if report is not None and not _ERROR_RE.search(combined):
            return report
        # No sentinel, or errors present => the proof did not compile; surface diagnostics.
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
    return audit_json(report_json, toolkit, theorem_name=theorem_name)
