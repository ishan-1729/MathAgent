"""Offline tests for the persistent Lean server's parsing/discovery (live REPL test is opt-in)."""
import json
import queue
import threading
import time

import pytest

from agent.gates import lean_server
from agent.gates.lean_bridge import LeanBridgeError, make_nonce
from agent.gates.lean_server import LeanServer, report_from_response, response_errors


def test_report_from_response_extracts_json():
    resp = {"messages": [
        {"severity": "info", "data": "some preamble"},
        {"severity": "info", "data": 'MATHAGENT_AUDIT_JSON {"theorem":"t","axioms":[],"constants":[]}'},
    ]}
    assert report_from_response(resp) == '{"theorem":"t","axioms":[],"constants":[]}'


def test_report_from_response_none_on_errors():
    resp = {"messages": [{"severity": "error", "data": "unknown identifier 'foo'"}]}
    assert report_from_response(resp) is None


def test_response_errors():
    assert "unknown" in response_errors({"messages": [{"severity": "error", "data": "unknown id"}]})
    assert "sorries" in response_errors({"sorries": [{"goal": "x"}]})
    assert response_errors({"messages": []}) == "no audit output"


def test_available_is_bool():
    assert isinstance(LeanServer.available(), bool)


# ---- sentinel-injection / forgery guards on the server response path (L2 fix a) ----

def test_report_from_response_nonce_enforced():
    nonce = make_nonce()
    good = {"messages": [{"severity": "info",
                          "data": f'MATHAGENT_AUDIT_JSON {nonce} {{"theorem":"t","axioms":[],"constants":[]}}'}]}
    assert report_from_response(good, nonce) == '{"theorem":"t","axioms":[],"constants":[]}'
    # A bare sentinel without the nonce is not accepted when a nonce is in force.
    bare = {"messages": [{"severity": "info",
                          "data": 'MATHAGENT_AUDIT_JSON {"theorem":"t","axioms":[],"constants":[]}'}]}
    assert report_from_response(bare, nonce) is None


def test_report_from_response_forged_second_sentinel_across_messages_rejected():
    # The forged report sits in one message, the real one (with nonce) in another: combined-scan
    # must still detect the duplicate bare sentinel and reject.
    nonce = make_nonce()
    resp = {"messages": [
        {"severity": "info", "data": 'MATHAGENT_AUDIT_JSON {"theorem":"t","axioms":[],"constants":[]}'},
        {"severity": "info",
         "data": f'MATHAGENT_AUDIT_JSON {nonce} {{"theorem":"t","axioms":["sorryAx"],"constants":[]}}'},
    ]}
    assert report_from_response(resp, nonce) is None


# ---- server robustness: dead/mocked proc -> LeanBridgeError + close() (L2 fix c) ----

class _DeadStdin:
    def write(self, _data):
        raise BrokenPipeError("REPL stdin is closed")

    def flush(self):
        raise BrokenPipeError("REPL stdin is closed")

    def close(self):
        pass


class _FakeProc:
    """A minimal subprocess.Popen stand-in for offline robustness tests."""

    def __init__(self, stdin=None):
        self.stdin = stdin
        self.stdout = None
        self.stderr = None
        self.terminated = False
        self.killed = False
        self.waited = False

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.waited = True
        return 0


def test_send_on_dead_proc_raises_and_closes():
    srv = LeanServer.__new__(LeanServer)  # bypass discovery in __init__
    proc = _FakeProc(stdin=_DeadStdin())
    srv.proc = proc
    srv.base_env = 0
    with pytest.raises(LeanBridgeError):
        srv._send({"cmd": "anything"})
    # _send tore down the dead server.
    assert srv.proc is None
    assert proc.terminated and proc.waited


def test_send_when_not_running_raises():
    srv = LeanServer.__new__(LeanServer)
    srv.proc = None
    with pytest.raises(LeanBridgeError):
        srv._send({"cmd": "x"})


def test_read_response_eof_closes_server():
    srv = LeanServer.__new__(LeanServer)
    srv.proc = _FakeProc()
    srv.base_env = 0
    srv._q = queue.Queue()
    srv._stderr = ["boom\n"]
    srv._q.put(None)  # EOF sentinel from the (dead) stdout pump
    with pytest.raises(LeanBridgeError):
        srv._read_response(timeout_s=1.0)
    assert srv.proc is None  # EOF tore down the server


def test_read_response_timeout_drains_queue_and_tears_down():
    srv = LeanServer.__new__(LeanServer)
    proc = _FakeProc()
    srv.proc = proc
    srv.base_env = 0
    srv._q = queue.Queue()
    srv._stderr = []
    srv._q.put("late partial output that never forms valid JSON\n")
    with pytest.raises(LeanBridgeError):
        srv._read_response(timeout_s=0.05)
    # The stale line was drained so the next command will not read it.
    assert srv._q.empty()
    # FIX 3: the still-running REPL is torn down on timeout so its late reply cannot desync every
    # subsequent audit (which would silently lose Layer-4 certification). audit() lazily restarts it.
    assert srv.proc is None and srv.base_env is None
    assert proc.terminated and proc.waited


def test_stdout_pump_returns_small_flushed_protocol_line_without_waiting_for_chunk():
    class _LineStream:
        def __init__(self):
            self.lines = iter(['{"env":1}\n', ""])

        def readline(self, size=-1):
            assert 0 < size <= lean_server._MAX_REPL_READ_CHUNK
            return next(self.lines)

        def read(self, _size=-1):
            raise AssertionError("fixed-size text read would block on a small flushed reply")

    proc = _FakeProc()
    proc.stdout = _LineStream()
    out = queue.Queue(maxsize=lean_server._MAX_REPL_QUEUE_CHUNKS)
    overflow = threading.Event()
    srv = LeanServer.__new__(LeanServer)

    srv._pump_stdout(proc, out, overflow)

    assert out.get_nowait() == '{"env":1}\n'
    assert out.get_nowait() is None
    assert not overflow.is_set()


def test_stdout_overflow_and_consumer_close_serialize_job_teardown(monkeypatch):
    class _LineStream:
        def __init__(self):
            self.closed = False

        def readline(self, _size=-1):
            return "overflow\n"

        def close(self):
            self.closed = True

    class FakePopen(_FakeProc):
        pid = 4321

        def wait(self, timeout=None):
            self.waited = True
            return 0

    class _Job:
        def __init__(self):
            self.closes = 0

        def close(self):
            self.closes += 1

    srv = LeanServer.__new__(LeanServer)
    srv._io_lock = threading.RLock()
    proc, job = FakePopen(stdin=_DeadStdin()), _Job()
    proc.stdout = _LineStream()
    srv.proc, srv._job, srv.base_env = proc, job, 1
    out = queue.Queue(maxsize=1)
    out.put_nowait("already full")
    overflow = threading.Event()
    taskkills = []
    monkeypatch.setattr(lean_server.os, "name", "nt")
    monkeypatch.setattr(lean_server.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(
        lean_server.subprocess, "run",
        lambda argv, **kwargs: taskkills.append((argv, kwargs)))

    # Force the pump to encounter queue.Full while the response consumer owns the protocol lock.
    # Consumer teardown must win; after the lock is released the pump observes the detached proc and
    # must not close the Job handle a second time.
    with srv._transaction_lock():
        pump = threading.Thread(target=srv._pump_stdout, args=(proc, out, overflow))
        pump.start()
        assert overflow.wait(timeout=1)
        srv.close()
    pump.join(timeout=1)

    assert not pump.is_alive()
    assert job.closes == 1 and not taskkills
    assert srv.proc is None and proc.waited


def test_audit_restarts_after_timeout_teardown_instead_of_reading_stale():
    # After a timeout tore the server down (proc is None), the NEXT audit() must go through start()
    # (a fresh REPL) rather than reading whatever stale output is still queued from the timed-out cmd.
    srv = LeanServer.__new__(LeanServer)
    srv.proc = None                       # torn-down state left by a prior timeout
    srv._q = queue.Queue()
    srv._q.put("STALE reply from the timed-out command\n")   # would desync a naive re-read

    started = {"n": 0}

    def _fake_start():
        started["n"] += 1
        raise LeanBridgeError("start() called — restarting rather than reading the stale queue")

    srv.start = _fake_start  # type: ignore[method-assign]
    # audit() sees proc is None -> RESTARTS via start() (not a silent re-read of the stale queue).
    with pytest.raises(LeanBridgeError):
        srv.audit("theorem thm : True := trivial", "thm")
    assert started["n"] == 1
    assert not srv._q.empty()  # the stale line was NEVER consumed by audit (start intercepted first)


def test_audit_uses_nonce_scoped_local_theorem_and_restamps_public_name(monkeypatch):
    nonce = "fixednonce"
    internal = f"MathAgentGenerated_{nonce}.ma_target"
    srv = LeanServer.__new__(LeanServer)
    srv.proc = object()  # already-started marker; the fake command below owns the response
    srv.base_env = 9
    srv._provenance = lean_server._ProjectProvenance(
        manifest="sha256:" + "a" * 64,
        expected_toolchain="leanprover/lean4:v4.30.0",
    )
    sent = {}
    monkeypatch.setattr(lean_server, "make_nonce", lambda: nonce)

    def _command(cmd, timeout_s, env=None):
        sent.update(cmd=cmd, timeout_s=timeout_s, env=env)
        report = json.dumps({
            "theorem": internal,
            "toolchain": "leanprover/lean4:v4.30.0",
            "axioms": [],
            "constants": [{"name": internal, "kind": "theorem", "module": ""}],
        }, separators=(",", ":"))
        return {"messages": [{
            "severity": "info",
            "data": f"MATHAGENT_AUDIT_JSON {nonce} {report}",
        }]}

    srv._command = _command
    report = json.loads(srv.audit("theorem ma_target : True := trivial", "ma_target"))

    assert report["theorem"] == "ma_target"
    assert report["manifest"] == "sha256:" + "a" * 64
    assert report["provenance"] == "mathagent-derived-v1"
    assert f"namespace MathAgentGenerated_{nonce}" in sent["cmd"]
    assert f"#audit {internal}" in sent["cmd"]
    assert sent["env"] == 9


def test_live_server_without_startup_provenance_cannot_certify(monkeypatch):
    nonce = "fixednonce"
    internal = f"MathAgentGenerated_{nonce}.ma_target"
    srv = LeanServer.__new__(LeanServer)
    srv.proc = object()
    srv.base_env = 9
    srv._provenance = None
    monkeypatch.setattr(lean_server, "make_nonce", lambda: nonce)

    def _command(_cmd, _timeout_s, env=None):
        assert env == 9
        report = json.dumps({
            "theorem": internal,
            "toolchain": "leanprover/lean4:v4.30.0",
            "axioms": [],
            "constants": [{"name": internal, "kind": "theorem", "module": ""}],
        }, separators=(",", ":"))
        return {"messages": [{
            "severity": "info",
            "data": f"MATHAGENT_AUDIT_JSON {nonce} {report}",
        }]}

    srv._command = _command
    with pytest.raises(LeanBridgeError, match="no verified toolchain/manifest identity"):
        srv.audit("theorem ma_target : True := trivial", "ma_target")


def test_concurrent_audits_are_serialized_as_complete_protocol_transactions(monkeypatch):
    nonce = "fixednonce"
    internal = f"MathAgentGenerated_{nonce}.ma_target"
    srv = LeanServer.__new__(LeanServer)
    srv.proc = object()
    srv.base_env = 9
    srv._provenance = lean_server._ProjectProvenance(
        manifest="sha256:" + "a" * 64,
        expected_toolchain="leanprover/lean4:v4.30.0",
    )
    srv._io_lock = threading.RLock()
    monkeypatch.setattr(lean_server, "make_nonce", lambda: nonce)
    active = 0
    max_active = 0
    state_lock = threading.Lock()

    def _command(_cmd, _timeout_s, env=None):
        nonlocal active, max_active
        assert env == 9
        with state_lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with state_lock:
            active -= 1
        report = json.dumps({
            "theorem": internal,
            "toolchain": "leanprover/lean4:v4.30.0",
            "axioms": [],
            "constants": [{"name": internal, "kind": "theorem", "module": ""}],
        }, separators=(",", ":"))
        return {"messages": [{
            "severity": "info",
            "data": f"MATHAGENT_AUDIT_JSON {nonce} {report}",
        }]}

    srv._command = _command
    errors = []

    def run():
        try:
            srv.audit("theorem ma_target : True := trivial", "ma_target")
        except Exception as exc:  # pragma: no cover - assertion below preserves diagnostics
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    assert max_active == 1


def test_close_waits_and_kills_and_closes_streams():
    srv = LeanServer.__new__(LeanServer)

    class _Stream:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    class _Hung(_FakeProc):
        def wait(self, timeout=None):
            raise Exception("did not exit in time")

    out, err = _Stream(), _Stream()
    proc = _Hung(stdin=_Stream())
    proc.stdout, proc.stderr = out, err
    srv.proc = proc
    srv.base_env = 5
    srv.close()
    assert srv.proc is None and srv.base_env is None
    assert proc.terminated and proc.killed       # hung wait -> kill fallback
    assert out.closed and err.closed             # pipes released

    # close() is idempotent / safe with no process.
    srv.close()


@pytest.mark.parametrize(
    ("tree_terminated", "expect_taskkill"), ((True, False), (False, True)))
def test_close_job_failure_uses_stale_pid_safe_fallback(
        monkeypatch, tree_terminated, expect_taskkill):
    srv = LeanServer.__new__(LeanServer)

    class FakePopen(_FakeProc):
        pid = 4321

        def wait(self, timeout=None):
            self.waited = True
            return 0

    class RaisingJob:
        def close(self):
            error = OSError("CloseHandle failed")
            error.tree_terminated = tree_terminated
            raise error

    proc = FakePopen(stdin=_DeadStdin())
    srv.proc = proc
    srv._job = RaisingJob()
    srv.base_env = {}
    taskkills = []
    monkeypatch.setattr(lean_server.os, "name", "nt")
    monkeypatch.setattr(lean_server.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(
        lean_server.subprocess, "run",
        lambda argv, **kwargs: taskkills.append((argv, kwargs)))

    srv.close()

    assert bool(taskkills) is expect_taskkill
    if taskkills:
        argv, kwargs = taskkills[0]
        assert argv == ["taskkill.exe", "/PID", "4321", "/T", "/F"]
        assert 0 < kwargs["timeout"] <= 2
    assert proc.waited
    assert srv.proc is None and srv._job is None


def test_start_rejects_project_import_shadow_before_popen(tmp_path, monkeypatch):
    (tmp_path / "Mathlib.lean").write_text(
        "initialize payload : Unit <- pure ()\n", encoding="utf-8")
    srv = LeanServer(project_dir=str(tmp_path), lake="lake", init_timeout_s=1)
    # This test targets the import-shadow guard, so supply the already-discovered REPL capability and
    # avoid failing earlier on the constructor's fail-closed path validation.
    srv.repl = str(tmp_path / "repl")

    def _must_not_spawn(*_args, **_kwargs):
        raise AssertionError("shadowed project reached Popen")

    monkeypatch.setattr(lean_server.subprocess, "Popen", _must_not_spawn)
    with pytest.raises(LeanBridgeError, match="shadows trusted import"):
        srv.start()


def test_start_is_idempotent_for_an_initialized_live_server(monkeypatch):
    class _Live(_FakeProc):
        def poll(self):
            return None

    srv = LeanServer.__new__(LeanServer)
    proc = _Live()
    srv.proc = proc
    srv.base_env = 7
    monkeypatch.setattr(
        srv, "available", lambda *_args: (_ for _ in ()).throw(
            AssertionError("idempotent start performed discovery")))

    assert srv.start() is srv
    assert srv.proc is proc and srv.base_env == 7
