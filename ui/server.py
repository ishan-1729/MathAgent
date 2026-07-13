"""Local web UI for the MathAgent harness.

Spawns `scripts/prove.py` with the levers chosen in the browser and streams its output back live over
Server-Sent Events (SSE). The harness itself is reused verbatim — the UI controls map 1:1 onto the
CLI flags, so there is no second implementation to keep in sync.

Safety: this is a LOCAL dev tool. It binds to 127.0.0.1 only; run/stop are token-protected same-origin
POSTs (not ambient GETs); and the subprocess argv is a LIST (never a shell string), with the typed
problem passed after a `--` separator. Runs use isolated process groups so Stop, disconnect, and server
shutdown terminate the complete descendant tree. It does run the real harness (Codex + Lean), so each
authorized run makes live model/compiler calls.

Run:  python ui/server.py   →   open http://127.0.0.1:8765
"""
from __future__ import annotations

import hmac
import json
import os
import queue
import secrets
import shlex
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.gates.windows_job import (  # noqa: E402 - ROOT makes direct `python ui/server.py` work
    WindowsMemoryJob as _WindowsKillJob,
    resume_suspended_windows_process as _resume_suspended_windows_process,
)

PROVE = ROOT / "scripts" / "prove.py"
INDEX = Path(__file__).resolve().parent / "index.html"
HOST = "127.0.0.1"
PORT = int(os.environ.get("MATHAGENT_UI_PORT", "8765"))
MAX_REQUEST_BYTES = 128 * 1024
MAX_ACTIVE_RUNS = 4
MAX_GOAL_CHARS = 12_000
MAX_MODEL_CHARS = 128
REQUEST_IO_TIMEOUT_S = 10.0
_FINAL_OUTPUT_GRACE_S = 0.5
_MAX_OUTPUT_CHUNK = 64 * 1024
_CSRF_TOKEN = secrets.token_urlsafe(32)
_ACTIVE_RUNS: dict[str, "RunControl"] = {}
_ACTIVE_RUNS_LOCK = threading.Lock()


# ------------------------------------------------------------------ param helpers ------------------

def _str(p: dict, key: str, default: str) -> str:
    return (p.get(key, [default]) or [default])[0].strip() or default


def _flag(p: dict, key: str, default: bool = False) -> bool:
    raw = p.get(key)
    if not raw or not (raw[0] or "").strip():
        return default
    return raw[0].strip().lower() in ("1", "true", "on", "yes")


def _int(p: dict, key: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(_str(p, key, str(default)))
    except ValueError:
        v = default
    return max(lo, min(hi, v))


def _argv_text(p: dict, key: str, default: str, max_chars: int) -> str:
    value = _str(p, key, default)
    if "\x00" in value or len(value) > max_chars:
        raise ValueError(f"{key} must contain at most {max_chars} characters and no NUL bytes")
    return value


def build_argv(p: dict) -> list[str]:
    """Map UI params (a parse_qs dict: key -> [values]) to a prove.py argv. Pure and unit-tested.

    The goal is appended after `--` so a problem starting with '-' can't be read as a flag."""
    goal = _argv_text(p, "goal", "", MAX_GOAL_CHARS)
    if not goal:
        raise ValueError("empty problem")

    effort = _str(p, "effort", "xhigh")
    if effort not in ("low", "medium", "high", "xhigh"):
        effort = "xhigh"

    model = _argv_text(p, "model", "gpt-5.5", MAX_MODEL_CHARS)
    argv: list[str] = [sys.executable, "-u", str(PROVE),
                       "--model", model, "--effort", effort]

    direct = _flag(p, "direct")
    if direct:
        argv.append("--direct")

    # The headline "formalized proof?" checkbox: run the full formalize -> Layer-4 audit pipeline.
    # Faithfulness FAILS CLOSED and is mandatory for certification.  The CLI rejects an unchecked
    # authoritative run, so the UI always requests the panel instead of exposing an inert/invalid
    # "certify without faithfulness" combination.
    if _flag(p, "certify"):
        argv.append("--formalize" if direct else "--terminal-gate")
        if _flag(p, "server", default=True):
            argv.append("--server")
        argv.append("--faithfulness")
        argv += ["--repair", str(_int(p, "repair", 3, 0, 8))]

    # Search / revision levers.
    if _flag(p, "refine"):
        argv.append("--refine")
    pop = _int(p, "population", 0, 0, 8)
    if pop:
        argv += ["--population", str(pop)]
    argv += ["--judges", str(_int(p, "judges", 1, 1, 5))]
    # OpenEvolve proof-sketch search: K iterations (0 = off). The supervised CLI fails closed with an
    # install hint if the explicitly requested optional backend is unavailable.
    evolve = _int(p, "evolve", 0, 0, 200)
    if evolve:
        argv += ["--evolve", str(evolve)]

    # Retrieval levers.
    if _flag(p, "retrieval"):
        argv.append("--retrieval")
    if _flag(p, "neural"):
        argv.append("--neural")
    if _flag(p, "rerank"):
        argv.append("--rerank")

    # Numeric harness params.
    argv += ["--max-depth", str(_int(p, "max_depth", 3, 0, 8))]
    argv += ["--max-decomp", str(_int(p, "max_decomp", 2, 1, 6))]
    argv += ["--episodes", str(_int(p, "episodes", 3, 1, 8))]
    argv += ["--budget", str(_int(p, "budget", 40, 1, 300))]
    argv += ["--max-replan", str(_int(p, "max_replan", 2, 0, 8))]
    argv += ["--timeout", str(_int(p, "timeout", 1500, 30, 3600))]

    argv += ["--", goal]
    return argv


# ------------------------------------------------------------------ SSE streaming ------------------

def _sse(text: str) -> bytes:
    """Frame one logical message as an SSE `data:` event (one `data:` line per text line)."""
    body = "".join(f"data: {line}\n" for line in text.split("\n"))
    return (body + "\n").encode("utf-8")


def _popen(argv: list[str], cwd: Path) -> tuple[subprocess.Popen, _WindowsKillJob | None]:
    """Start one isolated process group so cancellation can terminate the complete descendant tree."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    kwargs: dict = {}
    if os.name == "nt":
        # CREATE_SUSPENDED closes the launch race: no model/compiler code can spawn a child before
        # the root is assigned to the kill-on-close Job Object below.
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | 0x00000004
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        argv, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1, text=True,
        encoding="utf-8", errors="replace", env=env, **kwargs,
    )
    job = None
    if os.name == "nt":
        try:
            # Descendants inherit this kill-on-close job.  Fail closed if containment cannot be
            # established: running an uncontained model/compiler process would make Stop unreliable.
            job = _WindowsKillJob(proc)
            _resume_suspended_windows_process(proc)
        except Exception:
            # Preserve the launch/containment error. Cleanup is best-effort and must not replace it
            # with a secondary Job/handle failure; _terminate_process_tree supplies the bounded
            # taskkill + root fallback when the Job did not already terminate its tree.
            _terminate_process_tree(proc, job=job, grace_s=5.0)
            raise
    return proc, job


def _terminate_process_tree(proc: subprocess.Popen, *, job: _WindowsKillJob | None = None,
                            grace_s: float = 2.0) -> None:
    """Terminate `proc` and descendants without invoking a shell or targeting an unverified PID."""
    if os.name == "nt":
        job_failed = False
        if job is not None:
            # Closing a KILL_ON_JOB_CLOSE job terminates associated descendants even if the original
            # group leader already exited, which taskkill-by-PID cannot guarantee.  A close failure
            # records whether TerminateJobObject already succeeded: in that case a PID fallback risks
            # targeting a reassigned root PID and is both unnecessary and unsafe.
            try:
                job.close()
            except Exception as exc:  # cleanup must never mask the caller's primary failure
                job_failed = getattr(exc, "tree_terminated", False) is not True
        if job is None or job_failed:
            # Assignment can fail before a Job exists, and Job termination itself can fail. taskkill
            # /T is the bounded Windows fallback for the complete descendant tree.
            try:
                subprocess.run(
                    ["taskkill.exe", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                    timeout=max(grace_s, 0.1),
                )
            except Exception:
                pass
    else:
        # Always address the process group, even if the leader has already exited: descendants may
        # still be alive under the same PGID.  start_new_session=True makes proc.pid the group id.
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError:
            if proc.poll() is not None:
                return
            proc.terminate()
        # Wait for the GROUP to disappear, not merely the root process. A root that exits before an
        # ignoring descendant must not make cleanup return early.
        deadline = time.monotonic() + grace_s
        while time.monotonic() < deadline:
            proc.poll()  # reap an exited leader so it does not keep an otherwise-empty PGID visible
            try:
                os.killpg(proc.pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        except OSError:
            if proc.poll() is None:
                proc.kill()
        try:
            proc.wait(timeout=grace_s)
        except subprocess.TimeoutExpired:
            pass
        return

    try:
        proc.wait(timeout=grace_s)
        return
    except Exception:
        pass
    try:
        if proc.poll() is None:
            proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=grace_s)
    except Exception:
        pass


class RunControl:
    """Thread-safe cancellation handle shared by the stream request and `/stop` request."""

    def __init__(self):
        self.cancelled = threading.Event()
        self._lock = threading.Lock()
        self._terminate_lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._job: _WindowsKillJob | None = None

    @property
    def proc(self) -> subprocess.Popen | None:
        with self._lock:
            return self._proc

    def bind(self, proc: subprocess.Popen, job: _WindowsKillJob | None = None) -> None:
        with self._lock:
            self._proc = proc
            self._job = job
            cancelled = self.cancelled.is_set()
        if cancelled:
            self._terminate(proc, job)

    def _terminate(self, proc: subprocess.Popen, job: _WindowsKillJob | None) -> None:
        # Stop, disconnect, and normal stream cleanup can race.  Serialize the destructive operation
        # so two handler threads never run taskkill/killpg concurrently against the same live handle.
        with self._terminate_lock:
            _terminate_process_tree(proc, job=job)

    def cancel(self) -> None:
        self.cancelled.set()
        with self._lock:
            proc, job = self._proc, self._job
        if proc is not None:
            self._terminate(proc, job)


def _cancel_quietly(control: RunControl) -> None:
    """Best-effort cancellation that never replaces a transport/launch outcome."""
    try:
        control.cancel()
    except Exception:
        pass


def stream_command(argv: list[str], cwd: Path, control: RunControl | None = None):
    """Yield subprocess output as SSE and cancel the full tree when stopped or disconnected.

    Stdout is read on a daemon thread so this generator can emit heartbeats while a child is silent.
    A dead browser connection is therefore detected promptly by the handler's next write, which closes
    this generator and reaches the process-tree cleanup in `finally`.
    """
    control = control or RunControl()
    yield _sse("[ui] running: " + shlex.join(argv[2:]))   # show the flags (skip the python -u prefix)
    try:
        proc, job = _popen(argv, cwd)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        _cancel_quietly(control)
        yield _sse(f"[ui] launch failed ({type(exc).__name__})")
        yield b"event: done\ndata: error\n\n"
        return
    control.bind(proc, job)
    try:
        # Bound buffered output so a very chatty local child cannot grow the UI server without limit.
        # Backpressure is cancellation-aware, so a disconnected stream does not strand the reader.
        lines: queue.Queue[str] = queue.Queue(maxsize=1024)
    except BaseException:
        _cancel_quietly(control)
        raise

    def _read_stdout() -> None:
        assert proc.stdout is not None
        while True:
            # Size-bounded readline also chunks a pathological no-newline stream, preventing one local
            # child line from bypassing the queue bound and allocating arbitrarily large text.
            line = proc.stdout.readline(_MAX_OUTPUT_CHUNK + 1)
            if not line:
                break
            while not control.cancelled.is_set():
                try:
                    lines.put(line, timeout=0.5)
                    break
                except queue.Full:
                    continue

    try:
        threading.Thread(
            target=_read_stdout, name=f"mathagent-ui-output-{proc.pid}", daemon=True).start()
    except BaseException:
        _cancel_quietly(control)
        raise
    try:
        root_exit_deadline: float | None = None
        while True:
            now = time.monotonic()
            if proc.poll() is not None:
                if root_exit_deadline is None:
                    # Drain only a short final window. A descendant may inherit stdout and keep writing
                    # forever; root completion must still reach Job/PGID cleanup deterministically.
                    root_exit_deadline = now + _FINAL_OUTPUT_GRACE_S
                elif now >= root_exit_deadline:
                    break
            timeout = 0.5
            if root_exit_deadline is not None:
                timeout = max(0.001, min(timeout, root_exit_deadline - now))
            try:
                item = lines.get(timeout=timeout)
            except queue.Empty:
                # Root exit ends this run even if a descendant inherited and still holds the stdout
                # pipe open. Breaking reaches Job/PGID cleanup, which kills that descendant. The
                # half-second empty-queue wait lets the reader enqueue the root's final flushed lines.
                if proc.poll() is not None:
                    break
                # SSE comments are ignored by the UI but force a socket write, detecting a browser/tab
                # disconnect even when the child is silent OR closed stdout while continuing to run.
                yield b": keepalive\n\n"
                continue
            yield _sse(item.rstrip("\r\n"))
        proc.wait()
        status = "cancelled" if control.cancelled.is_set() else f"exit {proc.returncode}"
        yield f"event: done\ndata: {status}\n\n".encode("utf-8")
    finally:
        _cancel_quietly(control)


def _canonical_run_id(value) -> str | None:
    """Return one canonical UUID spelling, or ``None`` for malformed/non-canonical input."""
    try:
        text = str(value)
        canonical = str(uuid.UUID(text))
        return canonical if canonical == text.lower() else None
    except (ValueError, TypeError, AttributeError):
        return None


def _request_authorized(headers) -> bool:
    """Require same-origin POST plus an unguessable token embedded only in the served UI."""
    host = (headers.get("Host") or "").lower()
    allowed_hosts = {f"{HOST}:{PORT}", f"localhost:{PORT}"}
    if host not in allowed_hosts:
        return False
    if (headers.get("Origin") or "").lower() != f"http://{host}":
        return False
    if (headers.get("Sec-Fetch-Site") or "same-origin").lower() != "same-origin":
        return False
    supplied = headers.get("X-MathAgent-CSRF") or ""
    return hmac.compare_digest(supplied, _CSRF_TOKEN)


def _reserve_run(run_id: str) -> RunControl | None:
    with _ACTIVE_RUNS_LOCK:
        if run_id in _ACTIVE_RUNS or len(_ACTIVE_RUNS) >= MAX_ACTIVE_RUNS:
            return None
        control = RunControl()
        _ACTIVE_RUNS[run_id] = control
        return control


def _release_run(run_id: str, control: RunControl) -> None:
    with _ACTIVE_RUNS_LOCK:
        if _ACTIVE_RUNS.get(run_id) is control:
            _ACTIVE_RUNS.pop(run_id, None)


def _cancel_all_runs() -> None:
    with _ACTIVE_RUNS_LOCK:
        controls = list(_ACTIVE_RUNS.values())
    for control in controls:
        try:
            control.cancel()
        except Exception:
            # Shutdown is best-effort across independent process trees. One broken termination handle
            # must not prevent every later run from receiving its cancellation signal.
            continue


def _selftest_argv() -> list[str]:
    """A harmless subprocess that proves the SSE plumbing works without invoking Codex/Lean."""
    code = "import time\nfor i in range(4):\n    print('selftest line', i, flush=True)\n    time.sleep(0.3)"
    return [sys.executable, "-u", "-c", code]


# ------------------------------------------------------------------ HTTP handler -------------------

class MathAgentHTTPServer(ThreadingHTTPServer):
    """Thread-per-connection server whose clients can never delay process shutdown indefinitely."""

    daemon_threads = True
    block_on_close = False


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    # StreamRequestHandler applies this to the accepted socket before parsing the request line.  It
    # therefore covers slow/incomplete headers and bodies as well as blocked response writes.  The
    # stdlib handler catches TimeoutError, closes the connection, and does not dispatch the request.
    timeout = REQUEST_IO_TIMEOUT_S

    def log_message(self, *_args):
        pass  # quiet

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            return self._serve_index()
        if parsed.path in ("/run", "/selftest", "/stop"):
            self.send_response(405)
            self.send_header("Allow", "POST")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path not in ("/run", "/selftest", "/stop"):
            return self.send_error(404)
        if not _request_authorized(self.headers):
            return self.send_error(403, "same-origin authorization required")
        try:
            body = self._read_json()
        except ValueError as e:
            return self.send_error(400, str(e))
        run_id = _canonical_run_id(body.pop("run_id", None))
        if run_id is None:
            return self.send_error(400, "invalid run_id")
        if parsed.path == "/stop":
            return self._stop_run(run_id)
        control = _reserve_run(run_id)
        if control is None:
            return self.send_error(409, "run_id is already active or run capacity is full")
        params = {str(k): [str(v)] for k, v in body.items()}
        builder = build_argv if parsed.path == "/run" else (lambda _p: _selftest_argv())
        return self._serve_stream(builder, params, run_id, control)

    def _read_json(self) -> dict:
        if (self.headers.get_content_type() or "").lower() != "application/json":
            raise ValueError("Content-Type must be application/json")
        try:
            n = int(self.headers.get("Content-Length") or "")
        except ValueError as e:
            raise ValueError("invalid Content-Length") from e
        if n < 2 or n > MAX_REQUEST_BYTES:
            raise ValueError(f"request body must be 2..{MAX_REQUEST_BYTES} bytes")
        try:
            body = json.loads(self.rfile.read(n))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError("invalid JSON body") from e
        if not isinstance(body, dict):
            raise ValueError("JSON body must be an object")
        if any(isinstance(v, (dict, list)) or v is None for v in body.values()):
            raise ValueError("JSON parameter values must be scalar")
        return body

    def _stop_run(self, run_id: str):
        with _ACTIVE_RUNS_LOCK:
            control = _ACTIVE_RUNS.get(run_id)
        if control is not None:
            # Stop is an idempotent control request. Platform cleanup can fail independently of the
            # HTTP response; stream teardown still releases the registry reservation.
            try:
                control.cancel()
            except Exception:
                pass
        payload = json.dumps({"stopped": control is not None}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _serve_index(self):
        try:
            body = INDEX.read_bytes().replace(b"__MATHAGENT_CSRF_TOKEN__", _CSRF_TOKEN.encode("ascii"))
        except OSError:
            return self.send_error(500, "index.html missing")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; "
                         "style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; "
                         "frame-ancestors 'none'; form-action 'none'")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.end_headers()
        self.wfile.write(body)

    def _sse_headers(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")          # finite stream → close when done (no hang)
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True

    def _serve_stream(self, argv_builder, params, run_id: str, control: RunControl):
        try:
            try:
                argv = argv_builder(params)
            except ValueError as e:
                self._sse_headers()
                self.wfile.write(_sse(f"[ui] {e}"))
                self.wfile.write(b"event: done\ndata: error\n\n")
                return
            self._sse_headers()
            gen = stream_command(argv, ROOT, control)
            try:
                for chunk in gen:
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                gen.close()   # heartbeat detects disconnect -> terminate the complete process tree
        finally:
            try:
                _cancel_quietly(control)
            finally:
                # Registry capacity is a bookkeeping invariant, independent of process-tree cleanup.
                # A failing platform termination call must not leak this run's reservation forever.
                _release_run(run_id, control)


def main() -> int:
    if not PROVE.exists():
        print(f"ERROR: {PROVE} not found", file=sys.stderr)
        return 1
    srv = MathAgentHTTPServer((HOST, PORT), Handler)
    print(f"MathAgent UI on http://{HOST}:{PORT}  (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        _cancel_all_runs()
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
