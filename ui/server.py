"""Local web UI for the MathAgent harness.

Spawns `scripts/prove.py` with the levers chosen in the browser and streams its output back live over
Server-Sent Events (SSE). The harness itself is reused verbatim — the UI controls map 1:1 onto the
CLI flags, so there is no second implementation to keep in sync.

Safety: this is a LOCAL dev tool. It binds to 127.0.0.1 only, and the subprocess argv is built as a
LIST (never a shell string), with the typed problem passed after a `--` separator, so a typed problem
cannot inject shell commands or argparse flags. It does run the real harness (Codex + Lean), so each
run makes live model/compiler calls.

Run:  python ui/server.py   →   open http://127.0.0.1:8765
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
PROVE = ROOT / "scripts" / "prove.py"
INDEX = Path(__file__).resolve().parent / "index.html"
HOST, PORT = "127.0.0.1", 8765


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


def build_argv(p: dict) -> list[str]:
    """Map UI params (a parse_qs dict: key -> [values]) to a prove.py argv. Pure and unit-tested.

    The goal is appended after `--` so a problem starting with '-' can't be read as a flag."""
    goal = _str(p, "goal", "")
    if not goal:
        raise ValueError("empty problem")

    effort = _str(p, "effort", "xhigh")
    if effort not in ("low", "medium", "high", "xhigh"):
        effort = "xhigh"

    argv: list[str] = [sys.executable, "-u", str(PROVE),
                       "--model", _str(p, "model", "gpt-5.5"), "--effort", effort]

    direct = _flag(p, "direct")
    if direct:
        argv.append("--direct")

    # The headline "formalized proof?" checkbox: run the full formalize -> Layer-4 audit pipeline.
    if _flag(p, "certify"):
        argv.append("--formalize" if direct else "--terminal-gate")
        if _flag(p, "server", default=True):
            argv.append("--server")
        if _flag(p, "faithfulness", default=True):
            argv.append("--faithfulness")
        argv += ["--repair", str(_int(p, "repair", 3, 0, 8))]

    # Search / revision levers.
    if _flag(p, "refine"):
        argv.append("--refine")
    pop = _int(p, "population", 0, 0, 8)
    if pop:
        argv += ["--population", str(pop)]
    argv += ["--judges", str(_int(p, "judges", 1, 1, 5))]

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


def stream_command(argv: list[str], cwd: Path):
    """Yield SSE byte-chunks of a subprocess's combined output, then a `done` event. Closing the
    generator (client disconnect) terminates the subprocess so we never leak a running harness."""
    yield _sse("[ui] running: " + shlex.join(argv[2:]))   # show the flags (skip the python -u prefix)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(argv, cwd=str(cwd), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, bufsize=1, text=True,
                            encoding="utf-8", errors="replace", env=env)
    try:
        for line in proc.stdout:
            yield _sse(line.rstrip("\n"))
        proc.wait()
        yield f"event: done\ndata: exit {proc.returncode}\n\n".encode("utf-8")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def _selftest_argv() -> list[str]:
    """A harmless subprocess that proves the SSE plumbing works without invoking Codex/Lean."""
    code = "import time\nfor i in range(4):\n    print('selftest line', i, flush=True)\n    time.sleep(0.3)"
    return [sys.executable, "-u", "-c", code]


# ------------------------------------------------------------------ HTTP handler -------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass  # quiet

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            return self._serve_index()
        if parsed.path == "/run":
            return self._serve_stream(build_argv, parse_qs(parsed.query))
        if parsed.path == "/selftest":
            return self._serve_stream(lambda _p: _selftest_argv(), {})
        self.send_error(404)

    def _serve_index(self):
        try:
            body = INDEX.read_bytes()
        except OSError:
            return self.send_error(500, "index.html missing")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
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

    def _serve_stream(self, argv_builder, params):
        try:
            argv = argv_builder(params)
        except ValueError as e:
            self._sse_headers()
            self.wfile.write(_sse(f"[ui] {e}"))
            self.wfile.write(b"event: done\ndata: error\n\n")
            return
        self._sse_headers()
        gen = stream_command(argv, ROOT)
        try:
            for chunk in gen:
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            gen.close()   # disconnect → terminate the subprocess


def main() -> int:
    if not PROVE.exists():
        print(f"ERROR: {PROVE} not found", file=sys.stderr)
        return 1
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"MathAgent UI on http://{HOST}:{PORT}  (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
