"""Persistent Lean server — keeps Mathlib + the `#audit` command loaded across audits.

Per-audit `lake env lean` reloads all of Mathlib (~40-60s each). This wraps the community Lean REPL
(leanprover-community/repl, built into the Mathlib project) so Mathlib is loaded ONCE; each subsequent
audit reuses that environment and returns in well under a second of Lean time.

Protocol: write a JSON command + blank line, read a JSON response. The first command imports Lean +
Mathlib and defines `#audit` (slow, once) yielding a base environment id; each audit sends the proof +
`#audit <thm>` against that env and reads the report from the response messages (the extractor uses
`logInfo`, so its output arrives in the REPL message channel, not raw stdout).

Guarded: if the REPL exe / lake / project is missing, `available()` is False and `start()` raises
LeanUnavailable, so callers fall back to per-call `lake env lean`.
"""
from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from agent.gates.lean_bridge import (
    LeanBridgeError, LeanUnavailable, find_lake, find_mathlib_project,
    _split_imports, _extractor_src, extract_report_json,
)


def report_from_response(resp: dict) -> Optional[str]:
    """Extract the audit report JSON from a REPL response's messages, or None if absent."""
    for msg in resp.get("messages", []):
        rep = extract_report_json(str(msg.get("data", "")))
        if rep is not None:
            return rep
    return None


def response_errors(resp: dict) -> str:
    errs = [str(m.get("data", "")) for m in resp.get("messages", []) if m.get("severity") == "error"]
    sorries = resp.get("sorries", [])
    return "; ".join(errs)[:1000] or (f"{len(sorries)} sorries" if sorries else "no audit output")


class LeanServer:
    def __init__(self, project_dir: Optional[str] = None, lake: Optional[str] = None,
                 init_timeout_s: int = 900):
        self.project_dir = project_dir or find_mathlib_project()
        self.lake = lake or find_lake()
        self.repl = self.find_repl(self.project_dir) if self.project_dir else None
        self.init_timeout_s = init_timeout_s
        self.proc: Optional[subprocess.Popen] = None
        self.base_env: Optional[int] = None
        self._q: "queue.Queue[Optional[str]]" = queue.Queue()
        self._stderr: list[str] = []

    # --- discovery ---
    @staticmethod
    def find_repl(project_dir: str) -> Optional[str]:
        base = Path(project_dir) / ".lake" / "packages" / "repl" / ".lake" / "build" / "bin"
        for name in ("repl.exe", "repl"):
            if (base / name).exists():
                return str(base / name)
        return None

    @classmethod
    def available(cls, project_dir: Optional[str] = None) -> bool:
        pd = project_dir or find_mathlib_project()
        return bool(pd and find_lake() and cls.find_repl(pd))

    # --- lifecycle ---
    def start(self) -> "LeanServer":
        if not self.available(self.project_dir):
            raise LeanUnavailable("Lean REPL not built (run `lake build repl` in the Mathlib project)")
        self.proc = subprocess.Popen(
            [self.lake, "env", self.repl], cwd=self.project_dir,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()
        resp = self._command(self._init_cmd(), self.init_timeout_s)  # loads Mathlib (slow, once)
        self.base_env = int(resp.get("env", 0))
        return self

    def _init_cmd(self) -> str:
        ext_imports, ext_body = _split_imports(_extractor_src())
        imports: list[str] = []
        for imp in ext_imports + ["import Mathlib"]:
            if imp not in imports:
                imports.append(imp)
        return "\n".join(imports) + "\n\n" + ext_body.strip()

    def _pump_stdout(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            self._q.put(line)
        self._q.put(None)  # EOF sentinel

    def _pump_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        for line in self.proc.stderr:
            self._stderr.append(line)

    def _send(self, obj: dict) -> None:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write(json.dumps(obj) + "\n\n")
        self.proc.stdin.flush()

    def _read_response(self, timeout_s: float) -> dict:
        buf = ""
        end = time.monotonic() + timeout_s
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise LeanBridgeError(f"lean-server response timed out after {timeout_s}s")
            try:
                line = self._q.get(timeout=remaining)
            except queue.Empty:
                raise LeanBridgeError(f"lean-server response timed out after {timeout_s}s")
            if line is None:
                raise LeanBridgeError("lean-server process exited: " + "".join(self._stderr[-20:]))
            buf += line
            s = buf.strip()
            if not s:
                continue
            try:
                return json.loads(s)
            except json.JSONDecodeError:
                continue  # response not complete yet

    def _command(self, cmd: str, timeout_s: float, env: Optional[int] = None) -> dict:
        obj: dict = {"cmd": cmd}
        if env is not None:
            obj["env"] = env
        self._send(obj)
        return self._read_response(timeout_s)

    # --- the LeanServer audit interface (matches what lean_bridge.run_extractor expects) ---
    def audit(self, proof_src: str, theorem_name: str, timeout_s: int = 300) -> str:
        if self.proc is None:
            self.start()
        _imports, body = _split_imports(proof_src)  # Mathlib already in the base env; drop re-imports
        cmd = body.strip() + f"\n#audit {theorem_name}"
        resp = self._command(cmd, timeout_s, env=self.base_env)
        # A proof with ERROR diagnostics is broken even if Lean error-recovered a declaration (and
        # thus still emitted a report): treat it as a compile failure so the repair loop engages.
        if any(m.get("severity") == "error" for m in resp.get("messages", [])):
            raise LeanBridgeError(f"lean-server: compile error: {response_errors(resp)}")
        rep = report_from_response(resp)
        if rep is not None:
            return rep
        raise LeanBridgeError(f"lean-server: no audit JSON: {response_errors(resp)}")

    def close(self) -> None:
        if self.proc is not None:
            for closer in (lambda: self.proc.stdin.close(), self.proc.terminate):
                try:
                    closer()
                except Exception:
                    pass
            self.proc = None

    def __enter__(self) -> "LeanServer":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.close()
