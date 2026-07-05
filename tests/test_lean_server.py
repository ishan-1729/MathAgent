"""Offline tests for the persistent Lean server's parsing/discovery (live REPL test is opt-in)."""
import queue

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
        srv.audit("proof", "thm")
    assert started["n"] == 1
    assert not srv._q.empty()  # the stale line was NEVER consumed by audit (start intercepted first)


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
