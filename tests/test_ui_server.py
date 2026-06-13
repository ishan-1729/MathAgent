"""Tests for the UI backend: argv mapping (pure) + SSE streaming plumbing (stub subprocess, no Codex)."""
import sys

import pytest

from ui.server import build_argv, stream_command, ROOT


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


def test_certify_in_dag_mode_uses_terminal_gate():
    argv = build_argv(P(goal="g", certify="1"))
    assert "--terminal-gate" in argv and "--formalize" not in argv
    assert "--server" in argv and "--faithfulness" in argv
    assert "--repair" in argv


def test_certify_in_direct_mode_uses_formalize():
    argv = build_argv(P(goal="g", direct="1", certify="1"))
    assert "--direct" in argv and "--formalize" in argv and "--terminal-gate" not in argv


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
