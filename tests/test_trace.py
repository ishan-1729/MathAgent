"""Tests for the run trace (JSONL events + run-record rendering)."""
import json

from agent.orchestrator.trace import RunTrace


def test_emit_increments_seq():
    tr = RunTrace("r")
    tr.emit("a", x=1)
    tr.emit("b", y=2)
    assert [e.seq for e in tr.events] == [0, 1]
    assert tr.events[0].kind == "a" and tr.events[0].data == {"x": 1}


def test_injectable_clock_is_deterministic():
    ticks = iter([10.0, 20.0, 30.0])
    tr = RunTrace("r", clock=lambda: next(ticks))
    tr.emit("a")
    tr.emit("b")
    assert [e.t for e in tr.events] == [10.0, 20.0]


def test_to_jsonl_parses_back():
    tr = RunTrace("run42")
    tr.emit("prove", attempt=1)
    tr.emit("final", state="proven")
    rows = [json.loads(line) for line in tr.to_jsonl().splitlines()]
    assert rows[0]["run_id"] == "run42"
    assert rows[0]["kind"] == "prove" and rows[0]["attempt"] == 1
    assert rows[1]["kind"] == "final" and rows[1]["state"] == "proven"


def test_write_jsonl(tmp_path):
    tr = RunTrace("r")
    tr.emit("x", v=1)
    p = tmp_path / "trace.jsonl"
    tr.write_jsonl(p)
    content = p.read_text(encoding="utf-8").strip()
    assert json.loads(content)["v"] == 1


def test_by_kind_filter():
    tr = RunTrace("r")
    tr.emit("gate"); tr.emit("gate"); tr.emit("final")
    assert len(tr.by_kind("gate")) == 2
    assert len(tr.by_kind("final")) == 1


def test_render_run_record_has_headings():
    tr = RunTrace("r")
    tr.emit("prove"); tr.emit("final", state="proven")
    md = tr.render_run_record({"problem": "squares_mod4", "proof_status": "proven"})
    for heading in ["# Run Record", "## Run ID", "## Problem", "## Proof status", "## Trace"]:
        assert heading in md
    assert "squares_mod4" in md


def test_render_summary_reflects_actual_event_kinds_not_flatdriver_line():
    # FIX 5: the trace summary must derive from the ACTUAL event stream. The production DagDriver's
    # vocabulary (prove_node/decompose/terminal_gate/...) differs from the retired FlatDriver's
    # prove/gate/judge/final, so a rendered DagDriver run must reflect ITS kinds + total — never a fixed
    # line whose 'gate' count is structurally always 0.
    tr = RunTrace("dag-run")
    tr.emit("prove_node"); tr.emit("prove_node"); tr.emit("decompose"); tr.emit("terminal_gate")
    md = tr.render_run_record({})
    assert "4 events" in md
    assert "prove_node=2" in md
    assert "decompose=1" in md
    assert "terminal_gate=1" in md
    # the retired hardcoded FlatDriver line (with its structurally-zero 'gate') is gone.
    assert "gate=0" not in md
    assert "judge=0" not in md


def test_render_summary_handles_empty_trace():
    tr = RunTrace("empty")
    assert "0 events" in tr.render_run_record({})
