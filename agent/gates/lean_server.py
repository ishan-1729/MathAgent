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

import collections
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from agent.gates.lean_bridge import (
    LeanBridgeError, LeanUnavailable, find_lake, find_mathlib_project,
    _split_imports, _extractor_src, extract_report_json, _validate_audited_source, _lean_env,
    _reject_project_import_shadows, _terminate_lean_tree,
    _audit_command, _internal_theorem_name, _LEAN_MAX_MEMORY_MB, make_nonce,
    _restamp_verified_report, _wrap_generated_body,
    _project_provenance, _ProjectProvenance,
)
from agent.gates.windows_job import WindowsMemoryJob, resume_suspended_windows_process

_MAX_REPL_RESPONSE = 4 * 1024 * 1024
_MAX_REPL_READ_CHUNK = 64 * 1024
_MAX_REPL_QUEUE_CHUNKS = 128
_MAX_REPL_STDERR_CHUNK = 16 * 1024
_POSIX_MEMORY_WRAPPER = (
    "import os,resource,sys;"
    "limit=int(sys.argv[1]);"
    "resource.setrlimit(resource.RLIMIT_AS,(limit,limit));"
    "os.execvpe(sys.argv[2],sys.argv[2:],os.environ)"
)


def report_from_response(resp: dict, nonce: Optional[str] = None) -> Optional[str]:
    """Extract the trusted audit report JSON from a REPL response, or None if absent/forged.

    All message bodies are concatenated and scanned together so the sentinel-forgery guard
    (>1 bare sentinel, or a missing/mismatched nonce) sees the whole response at once — a forged
    line in a different message than the real one cannot slip through. Fails CLOSED.
    """
    combined = "\n".join(str(m.get("data", "")) for m in resp.get("messages", []))
    return extract_report_json(combined, nonce)


def response_errors(resp: dict) -> str:
    errs = [str(m.get("data", "")) for m in resp.get("messages", []) if m.get("severity") == "error"]
    sorries = resp.get("sorries", [])
    return "; ".join(errs)[:1000] or (f"{len(sorries)} sorries" if sorries else "no audit output")


class LeanServer:
    # ``lean_bridge.run_extractor`` accepts alternate server-like objects only across an explicit
    # trust boundary. This implementation nonce-binds, validates, and restamps every report.
    certification_trusted = True

    def __init__(self, project_dir: Optional[str] = None, lake: Optional[str] = None,
                 init_timeout_s: int = 900):
        self.project_dir = project_dir or find_mathlib_project()
        self.lake = lake or find_lake()
        self.repl = self.find_repl(self.project_dir) if self.project_dir else None
        self.init_timeout_s = init_timeout_s
        self.proc: Optional[subprocess.Popen] = None
        self._job: Optional[WindowsMemoryJob] = None
        self._provenance: Optional[_ProjectProvenance] = None
        self.base_env: Optional[int] = None
        self._q: "queue.Queue[Optional[str]]" = queue.Queue(maxsize=_MAX_REPL_QUEUE_CHUNKS)
        self._stdout_overflow = threading.Event()
        # The REPL protocol is strictly request/response. One lock covers lazy start, send, receive,
        # validation, and teardown so concurrent audits cannot interleave commands or consume one
        # another's replies. RLock is required because audit -> start/_command -> close are nested.
        self._io_lock = threading.RLock()
        # Bounded ring buffer: only the last ~20 lines are ever read (for error context), so an
        # unbounded list would grow forever across a long-lived server for no benefit.
        self._stderr: "collections.deque[str]" = collections.deque(maxlen=256)

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
        with self._transaction_lock():
            return self._start_locked()

    def _transaction_lock(self) -> threading.RLock:
        """Return the per-server protocol lock (lazily for lightweight ``__new__`` test doubles)."""
        lock = getattr(self, "_io_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._io_lock = lock
        return lock

    def _start_locked(self) -> "LeanServer":
        if self.proc is not None:
            if self.proc.poll() is None:
                if self.base_env is not None:
                    return self
                raise LeanBridgeError("lean-server is already starting")
            self.close()
        # Honor constructor-supplied discovery paths. Calling the class probe here would re-discover
        # lake globally and could reject an explicitly configured valid launcher (or accept a
        # different one than the process we are about to start).
        if not self.project_dir or not self.lake or not self.repl:
            raise LeanUnavailable("Lean REPL not built (run `lake build repl` in the Mathlib project)")
        _reject_project_import_shadows(self.project_dir)
        provenance = _project_provenance(self.project_dir)
        popen_kwargs: dict = {}
        if os.name == "nt":
            # Suspend before user-space startup so no child can escape the memory/kill Job Object.
            popen_kwargs["creationflags"] = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | 0x00000004)
        else:
            popen_kwargs["start_new_session"] = True
        argv = [self.lake, "env", self.repl]
        if os.name != "nt":
            # Avoid preexec_fn (unsafe in threaded Python).  This fixed internal launcher applies
            # RLIMIT_AS and immediately execs lake; the limit is inherited by the REPL tree before
            # any generated Lean source is sent.
            argv = [
                sys.executable, "-c", _POSIX_MEMORY_WRAPPER,
                str(_LEAN_MAX_MEMORY_MB * 1024 * 1024), *argv,
            ]
        proc = subprocess.Popen(
            argv, cwd=self.project_dir,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            env=_lean_env(),
            **popen_kwargs,
        )
        job: Optional[WindowsMemoryJob] = None
        if os.name == "nt":
            try:
                job = WindowsMemoryJob(proc, _LEAN_MAX_MEMORY_MB)
                resume_suspended_windows_process(proc)
            except Exception as e:
                # Preserve the assignment/resume failure even if Job teardown also fails.
                _terminate_lean_tree(proc, job=job)
                raise LeanBridgeError(f"could not contain Lean REPL process: {e}") from e
        # Each process generation owns its queue.  An old pump reaching EOF after a timeout must not
        # inject a stale EOF/reply into a newly started REPL.
        out_queue: "queue.Queue[Optional[str]]" = queue.Queue(
            maxsize=_MAX_REPL_QUEUE_CHUNKS)
        overflow = threading.Event()
        self.proc = proc
        self._job = job
        self._q = out_queue
        self._stdout_overflow = overflow
        try:
            threading.Thread(
                target=self._pump_stdout, args=(proc, out_queue, overflow), daemon=True).start()
            threading.Thread(target=self._pump_stderr, args=(proc,), daemon=True).start()
            resp = self._command(self._init_cmd(), self.init_timeout_s)  # loads Mathlib (slow, once)
            if any(m.get("severity") == "error" for m in resp.get("messages", [])):
                raise LeanBridgeError(
                    f"lean-server initialization failed: {response_errors(resp)}")
            env_id = resp.get("env")
            if isinstance(env_id, bool) or not isinstance(env_id, int) or env_id < 0:
                raise LeanBridgeError("lean-server initialization returned no valid environment id")
            if _project_provenance(self.project_dir) != provenance:
                raise LeanBridgeError(
                    "Lean project toolchain/manifest changed during server initialization")
            self.base_env = env_id
            self._provenance = provenance
        except Exception:
            self.close()  # a half-started server must not leak the REPL process
            raise
        return self

    def _init_cmd(self) -> str:
        ext_imports, ext_body = _split_imports(_extractor_src())
        imports: list[str] = []
        for imp in ext_imports + ["import Mathlib"]:
            if imp not in imports:
                imports.append(imp)
        return "\n".join(imports) + "\n\n" + ext_body.strip()

    def _pump_stdout(self, proc: subprocess.Popen,
                     out_queue: "queue.Queue[Optional[str]]",
                     overflow: threading.Event) -> None:
        assert proc.stdout
        try:
            # TextIOWrapper.read(N) may wait for all N characters even after the child flushed a
            # short JSON reply.  Bounded readline returns promptly at the protocol newline while a
            # no-newline producer can allocate at most one fixed-size chunk per read.
            try:
                while chunk := proc.stdout.readline(_MAX_REPL_READ_CHUNK):
                    try:
                        out_queue.put_nowait(chunk)
                    except queue.Full:
                        # The producer outran the response consumer.  Reject the whole generation;
                        # dropping a chunk and parsing the remainder could certify the wrong response.
                        overflow.set()
                        # The response consumer normally holds this lock and will observe overflow,
                        # then close the generation itself.  If output arrives without a consumer,
                        # the pump acquires the lock and closes it here.  The identity check prevents
                        # both paths from concurrently closing the same non-thread-safe Job handle.
                        with self._transaction_lock():
                            if self.proc is proc:
                                self._close_locked()
                        return
            except (OSError, ValueError):
                pass  # close() may release the pipe while this daemon reader is blocked in readline
        finally:
            try:
                out_queue.put_nowait(None)  # EOF sentinel for this process generation only
            except queue.Full:
                overflow.set()

    def _pump_stderr(self, proc: subprocess.Popen) -> None:
        assert proc.stderr
        try:
            while chunk := proc.stderr.readline(_MAX_REPL_STDERR_CHUNK):
                self._stderr.append(chunk)
        except (OSError, ValueError):
            pass

    def _send(self, obj: dict) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise LeanBridgeError("lean-server is not running")
        try:
            self.proc.stdin.write(json.dumps(obj) + "\n\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as e:
            # Dead/closed REPL stdin (broken pipe, closed file, killed proc): tear down and surface.
            self.close()
            raise LeanBridgeError(f"lean-server write failed (dead REPL?): {e}") from e

    def _drain_queue(self) -> None:
        """Discard any buffered/late stdout so a timed-out command cannot desync the next read."""
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                return

    def _read_response(self, timeout_s: float) -> dict:
        buf = ""
        end = time.monotonic() + timeout_s
        while True:
            overflow = getattr(self, "_stdout_overflow", None)
            if overflow is not None and overflow.is_set():
                self.close()
                raise LeanBridgeError("lean-server stdout queue exceeded its bounded capacity")
            remaining = end - time.monotonic()
            if remaining <= 0:
                # Timeout: drain buffered output AND tear down the still-running REPL. Draining alone is
                # NOT enough — the live REPL would emit its late reply AFTER the drain and desync every
                # subsequent read (silently losing Layer-4 certification on later audits). close() kills
                # it; audit() lazily restarts a closed server (mirrors the EOF branch below).
                self._drain_queue()
                self.close()
                raise LeanBridgeError(f"lean-server response timed out after {timeout_s}s")
            try:
                line = self._q.get(timeout=remaining)
            except queue.Empty:
                self._drain_queue()
                self.close()
                raise LeanBridgeError(f"lean-server response timed out after {timeout_s}s")
            if line is None:
                # EOF: the REPL exited. Tear down so the server is not reused in a dead state.
                self.close()
                raise LeanBridgeError("lean-server process exited: " + "".join(list(self._stderr)[-20:]))
            if overflow is not None and overflow.is_set():
                self.close()
                raise LeanBridgeError("lean-server stdout queue exceeded its bounded capacity")
            buf += line
            if len(buf) > _MAX_REPL_RESPONSE:
                self.close()
                raise LeanBridgeError(
                    f"lean-server response exceeded {_MAX_REPL_RESPONSE} characters")
            s = buf.strip()
            if not s:
                continue
            try:
                return json.loads(s)
            except json.JSONDecodeError:
                continue  # response not complete yet

    def _command(self, cmd: str, timeout_s: float, env: Optional[int] = None) -> dict:
        with self._transaction_lock():
            obj: dict = {"cmd": cmd}
            if env is not None:
                obj["env"] = env
            self._send(obj)
            return self._read_response(timeout_s)

    # --- the LeanServer audit interface (matches what lean_bridge.run_extractor expects) ---
    def audit(self, proof_src: str, theorem_name: str, timeout_s: int = 300) -> str:
        with self._transaction_lock():
            return self._audit_locked(proof_src, theorem_name, timeout_s)

    def _audit_locked(self, proof_src: str, theorem_name: str, timeout_s: int) -> str:
        # Validate BEFORE starting or writing to the long-lived process: rejected model output is
        # inert data and must never reach Lean's elaborator.
        _validate_audited_source(proof_src, theorem_name)
        if self.proc is None:
            self.start()
        _imports, body = _split_imports(proof_src)  # Mathlib already in the base env; drop re-imports
        nonce = make_nonce()  # binds the trusted report to THIS run; a forged sentinel cannot match
        internal_name = _internal_theorem_name(theorem_name, nonce)
        cmd = (_wrap_generated_body(body, nonce) + "\n"
               + _audit_command(internal_name, nonce))
        resp = self._command(cmd, timeout_s, env=self.base_env)
        # A proof with ERROR diagnostics is broken even if Lean error-recovered a declaration (and
        # thus still emitted a report): treat it as a compile failure so the repair loop engages.
        if any(m.get("severity") == "error" for m in resp.get("messages", [])):
            raise LeanBridgeError(f"lean-server: compile error: {response_errors(resp)}")
        rep = report_from_response(resp, nonce)
        if rep is None:
            raise LeanBridgeError(f"lean-server: no audit JSON: {response_errors(resp)}")
        # Theorem cross-check: the report must be ABOUT the theorem we asked to audit (defends
        # against a stale/forged report for a different declaration even if the nonce leaked).
        try:
            stamped = json.loads(rep).get("theorem")
        except json.JSONDecodeError as e:
            raise LeanBridgeError(f"lean-server: malformed audit JSON: {e}") from e
        if stamped != internal_name:
            raise LeanBridgeError(
                f"lean-server: audit report is for {stamped!r}, expected {internal_name!r}")
        provenance = getattr(self, "_provenance", None)
        if provenance is None:
            raise LeanBridgeError(
                "lean-server has no verified toolchain/manifest identity for this process")
        return _restamp_verified_report(
            rep, internal_name, theorem_name, provenance=provenance)

    def close(self) -> None:
        with self._transaction_lock():
            self._close_locked()

    def _close_locked(self) -> None:
        proc, self.proc = self.proc, None
        job, self._job = getattr(self, "_job", None), None
        self.base_env = None
        self._provenance = None
        if proc is None:
            if job is not None:
                try:
                    job.close()
                except Exception:
                    pass
            return
        # Close stdin first (signals EOF), then terminate the entire process group/tree.  Tests and
        # third-party stand-ins may not be real Popen objects, so retain the conservative fallback.
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except Exception:
            pass
        if isinstance(proc, subprocess.Popen):
            _terminate_lean_tree(proc, job=job)
        else:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        # Release the stdout/stderr pipes so the pump threads see EOF and no fds leak.
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass

    def __enter__(self) -> "LeanServer":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self) -> None:
        # Last-resort containment for an exception between startup and the caller's ownership
        # handoff. Normal code uses explicit close()/context management; finalization must never raise.
        try:
            self.close()
        except Exception:
            pass
