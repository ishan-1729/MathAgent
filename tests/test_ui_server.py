"""Tests for the UI backend: argv mapping, request security, and process-tree cancellation."""
import http.client
import io
import json
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from types import SimpleNamespace

import pytest

import ui.server as ui_server
from ui.server import build_argv, stream_command, ROOT


def _frontend_script() -> str:
    html = ui_server.INDEX.read_text(encoding="utf-8")
    return html.split("<script>", 1)[1].split("</script>", 1)[0]


def _node_or_skip() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable; skipping dependency-free browser-script regression")
    return node


def P(**kw):
    """A parse_qs-style param dict (each value wrapped in a list)."""
    return {k: [str(v)] for k, v in kw.items()}


def test_minimal_argv_puts_goal_after_separator():
    argv = build_argv(P(goal="prove n+0=n"))
    assert str(argv[2]).endswith("prove.py")
    assert "--model" in argv and "gpt-5.5" in argv
    assert "--effort" in argv and "xhigh" in argv
    assert argv[-2:] == ["--", "prove n+0=n"]      # goal isolated after `--`


def test_goal_starting_with_dash_is_not_a_flag():
    argv = build_argv(P(goal="-x is negative"))
    assert argv[-1] == "-x is negative"
    assert argv.index("--") == len(argv) - 2        # nothing parses it as an option


def test_empty_goal_raises():
    with pytest.raises(ValueError):
        build_argv(P(goal="   "))


@pytest.mark.parametrize("params", [
    P(goal="g" * (ui_server.MAX_GOAL_CHARS + 1)),
    P(goal="valid", model="m" * (ui_server.MAX_MODEL_CHARS + 1)),
    P(goal="contains\x00nul"),
])
def test_argv_text_is_bounded_and_nul_free(params):
    with pytest.raises(ValueError):
        build_argv(params)


def test_certify_in_dag_mode_uses_terminal_gate():
    argv = build_argv(P(goal="g", certify="1"))
    assert "--terminal-gate" in argv and "--formalize" not in argv
    assert "--server" in argv and "--faithfulness" in argv
    assert "--repair" in argv


def test_certify_in_direct_mode_uses_formalize():
    argv = build_argv(P(goal="g", direct="1", certify="1"))
    assert "--direct" in argv and "--formalize" in argv and "--terminal-gate" not in argv


def test_certification_cannot_disable_mandatory_faithfulness():
    argv = build_argv(P(goal="g", certify="1", faithfulness="0"))
    assert "--faithfulness" in argv
    assert "--no-faithfulness" not in argv


def test_no_certify_omits_formalization_flags():
    argv = build_argv(P(goal="g"))
    for f in ("--terminal-gate", "--formalize", "--server", "--faithfulness", "--repair"):
        assert f not in argv


def test_search_and_retrieval_flags():
    argv = build_argv(P(goal="g", refine="1", population="3", judges="2",
                        retrieval="1", neural="1", rerank="1"))
    assert "--refine" in argv
    assert "--population" in argv and argv[argv.index("--population") + 1] == "3"
    assert argv[argv.index("--judges") + 1] == "2"
    assert {"--retrieval", "--neural", "--rerank"} <= set(argv)


def test_population_zero_is_omitted():
    assert "--population" not in build_argv(P(goal="g", population="0"))


def test_numeric_clamping():
    argv = build_argv(P(goal="g", budget="99999", timeout="1", max_depth="-5"))
    assert argv[argv.index("--budget") + 1] == "300"      # clamped to max
    assert argv[argv.index("--timeout") + 1] == "30"      # clamped to min
    assert argv[argv.index("--max-depth") + 1] == "0"     # clamped to min


def test_stream_command_emits_sse_and_done():
    argv = [sys.executable, "-u", "-c", "print('alpha'); print('beta')"]
    out = b"".join(stream_command(argv, ROOT)).decode("utf-8")
    assert "data: alpha" in out
    assert "data: beta" in out
    assert "event: done" in out and "exit 0" in out


def test_stream_command_reports_nonzero_exit():
    argv = [sys.executable, "-u", "-c", "import sys; sys.exit(3)"]
    out = b"".join(stream_command(argv, ROOT)).decode("utf-8")
    assert "exit 3" in out


def test_stream_command_cleanup_failure_does_not_replace_done_event(monkeypatch):
    control = ui_server.RunControl()
    monkeypatch.setattr(
        control, "cancel", lambda: (_ for _ in ()).throw(RuntimeError("cleanup failed")),
    )
    argv = [sys.executable, "-u", "-c", "print('ok')"]
    out = b"".join(stream_command(argv, ROOT, control)).decode("utf-8")
    assert "data: ok" in out
    assert "event: done" in out and "exit 0" in out


def test_stream_command_reports_launch_failure_as_finite_sse(monkeypatch):
    control = ui_server.RunControl()
    monkeypatch.setattr(ui_server, "_popen",
                        lambda *_a, **_kw: (_ for _ in ()).throw(OSError("launch failed")))
    out = b"".join(stream_command([sys.executable, "-u", "missing"], ROOT, control)).decode()
    assert "launch failed (OSError)" in out
    assert "event: done" in out and "data: error" in out
    assert control.cancelled.is_set()


def test_stream_command_chunks_oversized_line():
    size = ui_server._MAX_OUTPUT_CHUNK * 2 + 17
    argv = [sys.executable, "-u", "-c", f"import sys; sys.stdout.write('x'*{size})"]
    out = b"".join(stream_command(argv, ROOT)).decode("utf-8")
    # Banner + at least three bounded output chunks; no unbounded readline allocation.
    assert out.count("data: ") >= 4
    assert "event: done" in out


def test_same_origin_authorization_rejects_cross_site_even_with_token(monkeypatch):
    monkeypatch.setattr(ui_server, "PORT", 8765)
    good = {"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765",
            "Sec-Fetch-Site": "same-origin", "X-MathAgent-CSRF": ui_server._CSRF_TOKEN}
    assert ui_server._request_authorized(good) is True
    assert ui_server._request_authorized({**good, "Origin": "https://attacker.example"}) is False
    assert ui_server._request_authorized({**good, "Sec-Fetch-Site": "cross-site"}) is False
    assert ui_server._request_authorized({**good, "X-MathAgent-CSRF": "wrong"}) is False


@pytest.fixture
def local_ui_server(monkeypatch):
    srv = ui_server.MathAgentHTTPServer((ui_server.HOST, 0), ui_server.Handler)
    monkeypatch.setattr(ui_server, "PORT", srv.server_port)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv.server_port
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def _post_headers(port: int, *, origin: str | None = None) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Origin": origin or f"http://127.0.0.1:{port}",
        "Sec-Fetch-Site": "same-origin",
        "X-MathAgent-CSRF": ui_server._CSRF_TOKEN,
    }


def test_http_server_threads_and_connections_are_bounded():
    assert ui_server.MathAgentHTTPServer.daemon_threads is True
    assert ui_server.MathAgentHTTPServer.block_on_close is False
    assert 0 < ui_server.Handler.timeout < float("inf")


def test_partial_request_body_times_out_without_dispatch(local_ui_server, monkeypatch):
    monkeypatch.setattr(ui_server.Handler, "timeout", 0.1)
    run_id = str(uuid.uuid4())
    prefix = json.dumps({"run_id": run_id}).encode()[:1]
    request = (
        f"POST /selftest HTTP/1.1\r\nHost: 127.0.0.1:{local_ui_server}\r\n"
        f"Origin: http://127.0.0.1:{local_ui_server}\r\n"
        "Sec-Fetch-Site: same-origin\r\n"
        f"X-MathAgent-CSRF: {ui_server._CSRF_TOKEN}\r\n"
        "Content-Type: application/json\r\nContent-Length: 100\r\n\r\n"
    ).encode() + prefix

    with socket.create_connection(("127.0.0.1", local_ui_server), timeout=1) as client:
        client.settimeout(1)
        client.sendall(request)
        try:
            closed = client.recv(1) == b""
        except ConnectionResetError:
            closed = True
    assert closed
    assert run_id not in ui_server._ACTIVE_RUNS


def test_run_initiation_is_not_available_via_get(local_ui_server):
    conn = http.client.HTTPConnection("127.0.0.1", local_ui_server, timeout=5)
    conn.request("GET", "/run")
    response = conn.getresponse()
    assert response.status == 405 and response.getheader("Allow") == "POST"
    response.read()
    conn.close()


def test_index_injects_uncached_token_and_denies_embedding(local_ui_server):
    conn = http.client.HTTPConnection("127.0.0.1", local_ui_server, timeout=5)
    conn.request("GET", "/")
    response = conn.getresponse()
    body = response.read().decode("utf-8")
    conn.close()
    assert response.status == 200
    assert ui_server._CSRF_TOKEN in body and "__MATHAGENT_CSRF_TOKEN__" not in body
    assert response.getheader("Cache-Control") == "no-store"
    assert response.getheader("X-Frame-Options") == "DENY"
    assert "frame-ancestors 'none'" in response.getheader("Content-Security-Policy")
    assert response.getheader("Referrer-Policy") == "no-referrer"
    assert "camera=()" in response.getheader("Permissions-Policy")
    assert response.getheader("Access-Control-Allow-Origin") is None


def test_index_inline_javascript_is_syntactically_valid():
    result = subprocess.run(
        [_node_or_skip(), "--check", "-"], input=_frontend_script(), text=True,
        capture_output=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_frontend_categorical_status_waits_for_authoritative_done_event():
    # Exercise the actual inline script in Node's standard-library VM with the smallest DOM surface
    # it uses. This pins the SSE parser/state reducer without adding jsdom or another frontend stack.
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const assert = require("assert").strict;
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    id, textContent: "", className: "badge", style: {display: "none"}, checked: false,
    value: "", disabled: false, scrollTop: 0, scrollHeight: 0,
    addEventListener() {}, appendChild() {}, focus() {}, click() {},
  });
  return elements.get(id);
}
const document = {
  getElementById: element,
  createElement: id => element("created-" + id + "-" + elements.size),
};
const context = vm.createContext({
  document, console, URLSearchParams, TextDecoder, Uint8Array,
  crypto: {randomUUID: () => "00000000-0000-4000-8000-000000000000"},
});
vm.runInContext(fs.readFileSync(0, "utf8"), context, {filename: "ui/index.inline.js"});
context.assert = assert;
vm.runInContext(`
function beginTestRun() {
  $("b-auth").style.display="none"; $("b-auth").textContent=""; $("b-auth").className="badge";
  const candidate={finished:false, reportStatus:null, authorityClaim:null, controller:{abort(){}}};
  currentRun=candidate; setState("running", "run"); return candidate;
}
function send(run, block) { handleEvent(block, run); }

let testRun=beginTestRun();
send(testRun, "data: result: PROVEN");
assert.equal($("b-state").textContent, "running");
send(testRun, "data: status: authoritative_elementary");
assert.equal($("b-state").textContent, "running", "category must remain pending before done");
send(testRun, "event: done\\ndata: exit 7");
assert.equal($("b-state").textContent, "failed (exit 7)");
assert.equal($("b-state").className, "badge off");

testRun=beginTestRun();
send(testRun, "data: authoritative_elementary: True");
send(testRun, "data: status: authoritative_elementary");
assert.equal($("b-auth").style.display, "none", "authority must remain pending before done");
assert.notEqual($("b-auth").textContent, "certified-elementary: yes");
send(testRun, "event: done\\ndata: cancelled");
assert.equal($("b-state").textContent, "cancelled (incomplete)");
assert.equal($("b-state").className, "badge off");
assert.equal($("b-auth").textContent, "certification incomplete");
assert.equal($("b-auth").className, "badge off");

testRun=beginTestRun();
send(testRun, "data: authoritative_elementary: True");
send(testRun, "data: status: authoritative_elementary");
send(testRun, "event: done\\ndata: exit 0");
assert.equal($("b-state").textContent, "authoritative elementary");
assert.equal($("b-auth").textContent, "certified-elementary: yes");
assert.equal($("b-auth").className, "badge on");

testRun=beginTestRun();
send(testRun, "data: status: soft_proven");
send(testRun, "event: done\\ndata: error");
assert.equal($("b-state").textContent, "failed");
assert.equal($("b-state").className, "badge off");

testRun=beginTestRun();
send(testRun, "data: status: soft_proven");
assert.equal($("b-state").textContent, "running");
send(testRun, "event: done\\ndata: exit 0");
assert.equal($("b-state").textContent, "soft proven");
assert.equal($("b-state").className, "badge on");

testRun=beginTestRun();
send(testRun, "data: status: authoritative_elementary trailing-junk");
send(testRun, "event: done\\ndata: exit 0");
assert.equal($("b-state").textContent, "complete");
assert.equal($("b-state").className, "badge");
`, context);
"""
    result = subprocess.run(
        [_node_or_skip(), "-e", harness], input=_frontend_script(), text=True,
        capture_output=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_cross_origin_post_cannot_start_even_selftest(local_ui_server):
    body = json.dumps({"run_id": str(uuid.uuid4())})
    conn = http.client.HTTPConnection("127.0.0.1", local_ui_server, timeout=5)
    conn.request("POST", "/selftest", body=body,
                 headers=_post_headers(local_ui_server, origin="https://attacker.example"))
    response = conn.getresponse()
    assert response.status == 403
    response.read()
    conn.close()


def test_authorized_post_streams_and_cleans_registry(local_ui_server):
    run_id = str(uuid.uuid4())
    body = json.dumps({"run_id": run_id})
    conn = http.client.HTTPConnection("127.0.0.1", local_ui_server, timeout=10)
    conn.request("POST", "/selftest", body=body, headers=_post_headers(local_ui_server))
    response = conn.getresponse()
    payload = response.read().decode("utf-8")
    conn.close()
    assert response.status == 200
    assert "data: selftest line 0" in payload and "event: done" in payload
    assert run_id not in ui_server._ACTIVE_RUNS


def test_uppercase_run_id_is_canonicalized_for_lowercase_stop(local_ui_server):
    uppercase_id = str(uuid.uuid4()).upper()
    while uppercase_id == uppercase_id.lower():  # avoid the vanishing all-digit UUID edge case
        uppercase_id = str(uuid.uuid4()).upper()
    canonical_id = uppercase_id.lower()

    def is_active(run_id):
        with ui_server._ACTIVE_RUNS_LOCK:
            return run_id in ui_server._ACTIVE_RUNS

    run_conn = http.client.HTTPConnection("127.0.0.1", local_ui_server, timeout=10)
    run_conn.request(
        "POST", "/selftest", body=json.dumps({"run_id": uppercase_id}),
        headers=_post_headers(local_ui_server),
    )
    run_response = run_conn.getresponse()

    deadline = time.monotonic() + 2
    while not is_active(canonical_id) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert is_active(canonical_id)
    assert not is_active(uppercase_id)

    stop_conn = http.client.HTTPConnection("127.0.0.1", local_ui_server, timeout=5)
    stop_conn.request(
        "POST", "/stop", body=json.dumps({"run_id": canonical_id}),
        headers=_post_headers(local_ui_server),
    )
    stop_response = stop_conn.getresponse()
    stop_payload = json.loads(stop_response.read())
    stop_conn.close()
    assert stop_response.status == 200 and stop_payload == {"stopped": True}

    run_response.read()
    run_conn.close()
    deadline = time.monotonic() + 2
    while is_active(canonical_id) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not is_active(canonical_id)


def test_active_run_registry_is_capacity_bounded():
    reserved = []
    try:
        for _ in range(ui_server.MAX_ACTIVE_RUNS):
            run_id = str(uuid.uuid4())
            control = ui_server._reserve_run(run_id)
            assert control is not None
            reserved.append((run_id, control))
        assert ui_server._reserve_run(str(uuid.uuid4())) is None
    finally:
        for run_id, control in reserved:
            ui_server._release_run(run_id, control)


def test_serve_stream_releases_registry_and_suppresses_cancel_failure(monkeypatch):
    class _StubHandler:
        _serve_stream = ui_server.Handler._serve_stream

        def __init__(self):
            self.wfile = io.BytesIO()

        def _sse_headers(self):
            pass

    run_id = str(uuid.uuid4())
    control = ui_server.RunControl()
    with ui_server._ACTIVE_RUNS_LOCK:
        ui_server._ACTIVE_RUNS[run_id] = control
    monkeypatch.setattr(
        control, "cancel", lambda: (_ for _ in ()).throw(RuntimeError("terminate failed")),
    )
    try:
        _StubHandler()._serve_stream(
            lambda _params: (_ for _ in ()).throw(ValueError("bad request")),
            {}, run_id, control,
        )
        assert run_id not in ui_server._ACTIVE_RUNS
    finally:
        with ui_server._ACTIVE_RUNS_LOCK:
            ui_server._ACTIVE_RUNS.pop(run_id, None)


def test_cancel_all_runs_continues_after_one_control_raises(monkeypatch):
    calls = []

    class _Control:
        def __init__(self, name, raises=False):
            self.name = name
            self.raises = raises

        def cancel(self):
            calls.append(self.name)
            if self.raises:
                raise RuntimeError("broken termination handle")

    monkeypatch.setattr(ui_server, "_ACTIVE_RUNS", {
        "first": _Control("first", raises=True),
        "second": _Control("second"),
        "third": _Control("third"),
    })
    ui_server._cancel_all_runs()
    assert calls == ["first", "second", "third"]


def test_run_control_cancel_invokes_tree_termination(monkeypatch):
    proc = object()
    calls = []
    monkeypatch.setattr(ui_server, "_terminate_process_tree",
                        lambda p, **kwargs: calls.append((p, kwargs.get("job"))))
    control = ui_server.RunControl()
    control.bind(proc)
    control.cancel()
    assert control.cancelled.is_set()
    assert calls == [(proc, None)]


def test_silent_disconnected_stream_heartbeats_then_cleans_process(monkeypatch):
    calls = []

    def _kill(proc, **kwargs):
        calls.append(proc.pid)
        if kwargs.get("job") is not None:
            kwargs["job"].close()
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    monkeypatch.setattr(ui_server, "_terminate_process_tree", _kill)
    control = ui_server.RunControl()
    gen = stream_command([sys.executable, "-u", "-c", "import time; time.sleep(60)"], ROOT, control)
    assert b"[ui] running:" in next(gen)
    assert next(gen) == b": keepalive\n\n"       # silent child still probes the socket every 0.5s
    gen.close()                                    # handler does this on BrokenPipe/disconnect
    assert calls and control.cancelled.is_set()
    assert control.proc is not None and control.proc.poll() is not None


def test_process_that_closes_output_still_heartbeats_until_cancel(monkeypatch):
    def _kill(proc, **kwargs):
        if kwargs.get("job") is not None:
            kwargs["job"].close()
        elif proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=5)
        except ui_server.subprocess.TimeoutExpired:
            proc.kill()

    monkeypatch.setattr(ui_server, "_terminate_process_tree", _kill)
    control = ui_server.RunControl()
    code = "import sys,time; sys.stdout.close(); sys.stderr.close(); time.sleep(60)"
    gen = stream_command([sys.executable, "-u", "-c", code], ROOT, control)
    next(gen)                                        # command banner
    assert next(gen) == b": keepalive\n\n"         # EOF alone must not enter blocking proc.wait()
    assert control.proc is not None and control.proc.poll() is None
    gen.close()
    assert control.cancelled.is_set() and control.proc.poll() is not None


def test_windows_tree_termination_uses_taskkill_descendant_flag(monkeypatch):
    monkeypatch.setattr(ui_server, "os", SimpleNamespace(name="nt"))

    class _Proc:
        pid = 4321
        returncode = None

        def poll(self):
            return None

        def wait(self, timeout):
            self.returncode = 0
            return 0

    seen = []
    monkeypatch.setattr(ui_server.subprocess, "run", lambda argv, **kwargs: seen.append((argv, kwargs)))
    ui_server._terminate_process_tree(_Proc())
    assert seen[0][0] == ["taskkill.exe", "/PID", "4321", "/T", "/F"]
    assert seen[0][1]["check"] is False


@pytest.mark.parametrize("tree_terminated", [True, False])
def test_windows_job_close_failure_uses_pid_fallback_only_for_live_tree(
        monkeypatch, tree_terminated):
    monkeypatch.setattr(ui_server, "os", SimpleNamespace(name="nt"))

    class _Proc:
        pid = 7654

        def __init__(self):
            self.waits = 0
            self.kills = 0

        def poll(self):
            return None

        def wait(self, timeout):
            self.waits += 1
            if not tree_terminated and self.waits == 1:
                raise ui_server.subprocess.TimeoutExpired("ui", timeout)
            return 0

        def kill(self):
            self.kills += 1

    class _Job:
        def close(self):
            error = RuntimeError("job close failed")
            error.tree_terminated = tree_terminated
            raise error

    seen = []
    monkeypatch.setattr(
        ui_server.subprocess, "run", lambda argv, **kwargs: seen.append((argv, kwargs)),
    )
    proc = _Proc()
    ui_server._terminate_process_tree(proc, job=_Job())

    assert bool(seen) is (not tree_terminated)
    if tree_terminated:
        assert proc.waits == 1 and proc.kills == 0
    else:
        assert seen[0][0] == ["taskkill.exe", "/PID", "7654", "/T", "/F"]
        assert proc.waits == 2 and proc.kills == 1


@pytest.mark.parametrize("tree_terminated", [True, False])
def test_windows_assignment_failure_cleanup_preserves_primary_error(
        monkeypatch, tree_terminated):
    monkeypatch.setattr(ui_server, "os", SimpleNamespace(name="nt", environ={}))

    class _Proc:
        pid = 8765

        def poll(self):
            return None

        def wait(self, timeout):
            return 0

        def kill(self):
            raise AssertionError("wait succeeded; root kill should not be needed")

    proc = _Proc()
    monkeypatch.setattr(ui_server.subprocess, "Popen", lambda *_args, **_kwargs: proc)

    class _Job:
        def __init__(self, _proc):
            pass

        def close(self):
            error = RuntimeError("secondary close failure")
            error.tree_terminated = tree_terminated
            raise error

    monkeypatch.setattr(ui_server, "_WindowsKillJob", _Job)
    monkeypatch.setattr(
        ui_server, "_resume_suspended_windows_process",
        lambda _proc: (_ for _ in ()).throw(RuntimeError("primary resume failure")),
    )
    seen = []
    monkeypatch.setattr(
        ui_server.subprocess, "run", lambda argv, **kwargs: seen.append((argv, kwargs)),
    )

    with pytest.raises(RuntimeError, match="primary resume failure"):
        ui_server._popen(["python", "worker.py"], ROOT)
    assert bool(seen) is (not tree_terminated)


def test_windows_job_kills_child_after_root_has_already_exited():
    if ui_server.os.name != "nt":
        pytest.skip("Windows Job Object behavior")

    import ctypes
    from ctypes import wintypes

    child_code = "import time; time.sleep(60)"
    # Spawn immediately: CREATE_SUSPENDED guarantees the root joins its Job Object before any of this
    # Python code runs, pinning the launch-race closure as well as cleanup after leader exit.
    parent_code = (
        "import subprocess,sys; "
        "p=subprocess.Popen([sys.executable,'-c',sys.argv[1]]); "
        "print('CHILD_PID='+str(p.pid),flush=True)"
    )
    output = b"".join(stream_command(
        [sys.executable, "-u", "-c", parent_code, child_code], ROOT)).decode("utf-8")
    child_line = next(line for line in output.splitlines() if line.startswith("data: CHILD_PID="))
    child_pid = int(child_line.removeprefix("data: CHILD_PID="))

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    handle = kernel32.OpenProcess(0x1000, False, child_pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return                                             # process is already gone
    try:
        code = wintypes.DWORD()
        assert kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        assert code.value != 259                           # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def test_chatty_descendant_cannot_keep_stream_alive_after_root_exit():
    child_code = (
        "import time; end=time.monotonic()+3; "
        "exec(\"while time.monotonic() < end:\\n print('orphan-output', flush=True)\")"
    )
    parent_code = (
        "import subprocess,sys; subprocess.Popen([sys.executable,'-u','-c',sys.argv[1]]); "
        "print('root-done',flush=True)"
    )
    started = time.monotonic()
    output = b"".join(stream_command(
        [sys.executable, "-u", "-c", parent_code, child_code], ROOT)).decode("utf-8")
    assert time.monotonic() - started < 2.0       # bounded final drain, not child's 3-second lifetime
    assert "data: root-done" in output


def test_stop_post_cancels_registered_run(local_ui_server):
    run_id = str(uuid.uuid4())
    control = ui_server.RunControl()
    with ui_server._ACTIVE_RUNS_LOCK:
        ui_server._ACTIVE_RUNS[run_id] = control
    try:
        conn = http.client.HTTPConnection("127.0.0.1", local_ui_server, timeout=5)
        conn.request("POST", "/stop", body=json.dumps({"run_id": run_id}),
                     headers=_post_headers(local_ui_server))
        response = conn.getresponse()
        payload = json.loads(response.read())
        conn.close()
        assert response.status == 200 and payload == {"stopped": True}
        assert control.cancelled.is_set()
    finally:
        with ui_server._ACTIVE_RUNS_LOCK:
            ui_server._ACTIVE_RUNS.pop(run_id, None)


def test_stop_post_returns_success_when_platform_cancel_fails(local_ui_server, monkeypatch):
    run_id = str(uuid.uuid4())
    control = ui_server.RunControl()
    monkeypatch.setattr(
        control, "cancel", lambda: (_ for _ in ()).throw(RuntimeError("terminate failed")),
    )
    with ui_server._ACTIVE_RUNS_LOCK:
        ui_server._ACTIVE_RUNS[run_id] = control
    try:
        conn = http.client.HTTPConnection("127.0.0.1", local_ui_server, timeout=5)
        conn.request("POST", "/stop", body=json.dumps({"run_id": run_id}),
                     headers=_post_headers(local_ui_server))
        response = conn.getresponse()
        payload = json.loads(response.read())
        conn.close()
        assert response.status == 200 and payload == {"stopped": True}
    finally:
        with ui_server._ACTIVE_RUNS_LOCK:
            ui_server._ACTIVE_RUNS.pop(run_id, None)
