"""Tests for the headless CLI drivers' error contract (offline; no subprocess spawned).

`_run_claude`/`_run_codex` resolve `launcher = cfg.launcher or find_*()`. An explicitly-configured
launcher that does not exist on disk (and cannot be resolved on PATH) must surface the module's TYPED
error (ClaudeError / CodexError), NOT a raw FileNotFoundError from subprocess. These tests pin that
contract and, via a subprocess.run sentinel, assert no process is ever spawned for the missing path.
"""
import os
from pathlib import Path
import sys
import time

import pytest

from agent.gates.windows_job import WindowsMemoryJob
from agent.tools._cli_process import run_bounded_cli
from agent.tools.claude_cli import ClaudeConfig, ClaudeError, _run_claude
from agent.tools.codex_prover import CodexConfig, CodexError, _run_codex


def _no_subprocess(monkeypatch, module):
    """Make the shared launcher explode if called — the typed error must fire first."""
    def _boom(*a, **k):
        raise AssertionError("process transport must not run for a missing launcher")
    monkeypatch.setattr(module, "run_bounded_cli", _boom)


def test_run_claude_nonexistent_launcher_raises_claude_error(monkeypatch):
    import agent.tools.claude_cli as cc
    _no_subprocess(monkeypatch, cc)
    cfg = ClaudeConfig(launcher="C:/nonexistent/claude_xyz.exe")
    with pytest.raises(ClaudeError):
        _run_claude("hi", cfg)


def test_run_codex_nonexistent_launcher_raises_codex_error(monkeypatch):
    import agent.tools.codex_prover as cp
    _no_subprocess(monkeypatch, cp)
    cfg = CodexConfig(launcher="C:/nonexistent/codex_xyz.exe")
    with pytest.raises(CodexError):
        _run_codex("hi", cfg)


# --- FIX 2: BatBadBut command-injection — config fields on the cmd.exe line must be rejected -------

def test_default_configs_still_construct():
    assert CodexConfig().model == "gpt-5.5"
    assert CodexConfig().reasoning_effort == "xhigh"
    assert CodexConfig().sandbox == "read-only"
    assert ClaudeConfig().model == "sonnet"


def test_valid_names_pass():
    c = CodexConfig(model="gpt-5.5", reasoning_effort="xhigh", sandbox="read-only")
    assert c.model == "gpt-5.5"
    assert ClaudeConfig(model="opus").model == "opus"


@pytest.mark.parametrize("bad", ['x" & calc & rem "', "a b", "a|b", "a&b", "a'b", "$(x)", ""])
def test_codex_config_rejects_injection_in_model(bad):
    with pytest.raises(CodexError):
        CodexConfig(model=bad)


def test_codex_config_rejects_injection_in_effort_and_sandbox():
    with pytest.raises(CodexError):
        CodexConfig(reasoning_effort='x" & calc & rem "')
    with pytest.raises(CodexError):
        CodexConfig(sandbox="danger & rm")


def test_codex_config_rejects_inert_or_unsafe_controls():
    with pytest.raises(CodexError, match="reasoning_effort"):
        CodexConfig(reasoning_effort="ultra")
    with pytest.raises(CodexError, match="read-only"):
        CodexConfig(sandbox="workspace-write")


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True])
def test_cli_configs_require_positive_finite_timeout(timeout):
    with pytest.raises(CodexError, match="timeout"):
        CodexConfig(timeout_s=timeout)
    with pytest.raises(ClaudeError, match="timeout"):
        ClaudeConfig(timeout_s=timeout)


@pytest.mark.parametrize("driver", ["claude", "codex"])
@pytest.mark.parametrize("launcher", [123, "", "bad\x00launcher", "bad\nlauncher"])
def test_cli_configs_reject_malformed_launchers_with_typed_error(driver, launcher):
    error = ClaudeError if driver == "claude" else CodexError
    config = ClaudeConfig if driver == "claude" else CodexConfig
    with pytest.raises(error, match="launcher"):
        config(launcher=launcher)


@pytest.mark.parametrize("bad", ['x" & calc & rem "', "a b", "a|b", "a&b"])
def test_claude_config_rejects_injection_in_model(bad):
    with pytest.raises(ClaudeError):
        ClaudeConfig(model=bad)


# --- FIX 3: timeout must kill the process tree and raise the TYPED error ---------------------------

def _sleeper_launcher(tmp_path):
    """A launcher that hangs longer than a sub-second timeout (Windows .cmd / POSIX .sh)."""
    if os.name == "nt":
        p = tmp_path / "sleeper.cmd"
        p.write_text("@echo off\r\nping -n 5 127.0.0.1 >nul\r\n", encoding="ascii")
        return str(p)
    p = tmp_path / "sleeper.sh"
    p.write_text("#!/bin/sh\nsleep 5\n", encoding="ascii")
    os.chmod(p, 0o755)
    return str(p)


def _noisy_launcher(tmp_path):
    """A successful launcher whose stdout is deliberately larger than the monkeypatched cap."""
    if os.name == "nt":
        p = tmp_path / "noisy.cmd"
        p.write_text("@echo off\r\nfor /L %%i in (1,1,200) do @echo x\r\n", encoding="ascii")
        return str(p)
    p = tmp_path / "noisy.sh"
    p.write_text("#!/bin/sh\ni=0; while [ $i -lt 200 ]; do echo x; i=$((i+1)); done\n",
                 encoding="ascii")
    os.chmod(p, 0o755)
    return str(p)


def _result_launcher(tmp_path, *, output="ok"):
    """A launcher that exits immediately with an optional stdout result."""
    if os.name == "nt":
        p = tmp_path / f"result_{output or 'empty'}.cmd"
        body = f"@echo {output}\r\n" if output else "@exit /b 0\r\n"
        p.write_text(body, encoding="ascii")
        return str(p)
    p = tmp_path / f"result_{output or 'empty'}.sh"
    body = f"#!/bin/sh\nprintf '{output}\\n'\n" if output else "#!/bin/sh\nexit 0\n"
    p.write_text(body, encoding="ascii")
    os.chmod(p, 0o755)
    return str(p)


def test_run_claude_timeout_raises_typed_error(tmp_path):
    cfg = ClaudeConfig(launcher=_sleeper_launcher(tmp_path), timeout_s=0.5)
    with pytest.raises(ClaudeError, match="timed out"):
        _run_claude("hi", cfg)


def test_run_codex_timeout_raises_typed_error(tmp_path):
    cfg = CodexConfig(launcher=_sleeper_launcher(tmp_path), timeout_s=0.5)
    with pytest.raises(CodexError, match="timed out"):
        _run_codex("hi", cfg)


def _captures_successful_cli(captured):
    def _fake(argv, **kwargs):
        captured["argv"] = list(argv)
        Path(kwargs["stdout_path"]).write_text("ok\n", encoding="utf-8")
        Path(kwargs["stderr_path"]).write_text("", encoding="utf-8")
        return 0
    return _fake


def _has_adjacent(argv, *values):
    width = len(values)
    return any(argv[i:i + width] == list(values) for i in range(len(argv) - width + 1))


def test_wsl_batch_launcher_gets_windows_path_and_visible_temp_root(monkeypatch, tmp_path):
    import agent.tools._cli_process as transport

    launcher = tmp_path / "mnt" / "c" / "Users" / "Ada" / ".local" / "claude.cmd"
    user_temp = tmp_path / "mnt" / "c" / "Users" / "Ada" / "AppData" / "Local" / "Temp"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("@exit /b 0\n", encoding="ascii")
    user_temp.mkdir(parents=True)
    monkeypatch.setattr(transport.os, "name", "posix")
    real_path = transport.Path

    def fake_path(value):
        path = real_path(value)
        if path == real_path("/mnt"):
            return tmp_path / "mnt"
        return path

    monkeypatch.setattr(transport, "Path", fake_path)
    argv, temp_root = transport.prepare_cli_launcher(
        str(launcher).replace(str(tmp_path), ""), error_type=ClaudeError, label="Claude CLI"
    )
    assert argv[-1] == r"C:\Users\Ada\.local\claude.cmd"
    assert temp_root == str(user_temp)


def test_codex_invocation_removes_shell_tools_and_ignores_customizations(monkeypatch, tmp_path):
    import agent.tools.codex_prover as cp

    captured = {}
    monkeypatch.setattr(cp, "run_bounded_cli", _captures_successful_cli(captured))
    prompt = "confidential theorem text"
    assert _run_codex(prompt, CodexConfig(launcher=_result_launcher(tmp_path))) == "ok"

    argv = captured["argv"]
    assert _has_adjacent(argv, "--disable", "shell_tool")
    assert _has_adjacent(argv, "--disable", "unified_exec")
    assert _has_adjacent(argv, "-s", "read-only")
    assert {"--ignore-user-config", "--ignore-rules", "--strict-config"} <= set(argv)
    assert prompt not in argv  # the prompt remains on stdin, never the process command line


def test_claude_invocation_removes_tools_mcp_chrome_and_persistence(monkeypatch, tmp_path):
    import agent.tools.claude_cli as cc

    captured = {}
    monkeypatch.setattr(cc, "run_bounded_cli", _captures_successful_cli(captured))
    prompt = "confidential theorem text"
    assert _run_claude(prompt, ClaudeConfig(launcher=_result_launcher(tmp_path))) == "ok"

    argv = captured["argv"]
    assert _has_adjacent(argv, "--tools", "")
    assert "--allowedTools" not in argv
    assert {"--safe-mode", "--no-chrome", "--strict-mcp-config",
            "--no-session-persistence"} <= set(argv)
    assert prompt not in argv


def test_run_claude_rejects_oversize_output(monkeypatch, tmp_path):
    import agent.tools.claude_cli as cc
    monkeypatch.setattr(cc, "_MAX_CLI_OUTPUT_BYTES", 64)
    with pytest.raises(ClaudeError, match="stdout exceeded 64 bytes"):
        _run_claude("hi", ClaudeConfig(launcher=_noisy_launcher(tmp_path)))


def test_run_codex_rejects_oversize_output(monkeypatch, tmp_path):
    import agent.tools.codex_prover as cp
    monkeypatch.setattr(cp, "_MAX_CLI_OUTPUT_BYTES", 64)
    with pytest.raises(CodexError, match="stdout exceeded 64 bytes"):
        _run_codex("hi", CodexConfig(launcher=_noisy_launcher(tmp_path)))


def test_transport_pipe_backpressure_prevents_burst_overshoot(tmp_path):
    """A single fast write may not overshoot a tiny capture limit on disk.

    The prior stat-polling implementation rejected this producer eventually but first admitted the
    entire 8 MiB regular file despite a 64-byte limit.
    """
    stdout = tmp_path / "stdout"
    stderr = tmp_path / "stderr"
    argv = [sys.executable, "-c", "import os; os.write(1, b'x' * (8 * 1024 * 1024))"]

    with pytest.raises(ClaudeError, match="stdout exceeded 64 bytes"):
        run_bounded_cli(
            argv,
            prompt="π → 你好",
            cwd=str(tmp_path),
            timeout_s=5,
            stdout_path=stdout,
            stderr_path=stderr,
            watched_paths={"stdout": stdout, "stderr": stderr},
            max_bytes=64,
            error_type=ClaudeError,
            label="test CLI",
        )

    assert stdout.stat().st_size == 64
    assert stderr.stat().st_size <= 64


def test_transport_delivers_prompt_as_exact_utf8_bytes(tmp_path):
    stdout = tmp_path / "stdout"
    stderr = tmp_path / "stderr"
    prompt = "π → 你好\nsecond line"
    argv = [sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"]

    assert run_bounded_cli(
        argv,
        prompt=prompt,
        cwd=str(tmp_path),
        timeout_s=5,
        stdout_path=stdout,
        stderr_path=stderr,
        watched_paths={"stdout": stdout, "stderr": stderr},
        max_bytes=1024,
        error_type=ClaudeError,
        label="test CLI",
    ) == 0
    assert stdout.read_bytes() == prompt.encode("utf-8")


def test_transport_rejects_invalid_unicode_prompt_as_typed_error(tmp_path):
    with pytest.raises(ClaudeError, match="UTF-8"):
        run_bounded_cli(
            [sys.executable, "-c", "pass"],
            prompt="bad surrogate: \ud800",
            cwd=str(tmp_path),
            timeout_s=5,
            stdout_path=tmp_path / "stdout",
            stderr_path=tmp_path / "stderr",
            watched_paths={},
            max_bytes=1024,
            error_type=ClaudeError,
            label="test CLI",
        )


def test_transport_reader_start_failure_is_typed_and_kills_root(monkeypatch, tmp_path):
    import agent.tools._cli_process as transport

    spawned = []
    real_popen = transport.subprocess.Popen

    def recording_popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        spawned.append(proc)
        return proc

    class CannotStartReader:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("synthetic thread exhaustion")

    monkeypatch.setattr(transport.subprocess, "Popen", recording_popen)
    monkeypatch.setattr(transport.threading, "Thread", CannotStartReader)
    stdout = tmp_path / "stdout"
    stderr = tmp_path / "stderr"
    with pytest.raises(ClaudeError, match="transport failed"):
        run_bounded_cli(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            prompt="hi",
            cwd=str(tmp_path),
            timeout_s=5,
            stdout_path=stdout,
            stderr_path=stderr,
            watched_paths={"stdout": stdout, "stderr": stderr},
            max_bytes=1024,
            error_type=ClaudeError,
            label="test CLI",
        )
    assert spawned and spawned[0].poll() is not None


def test_windows_job_close_reports_handle_failure_and_retains_for_retry(monkeypatch):
    """A terminated tree does not excuse silently leaking its Windows kernel handle."""
    import ctypes

    class FakeKernel32:
        close_succeeds = False

        @staticmethod
        def TerminateJobObject(handle, exit_code):
            return 1

        def CloseHandle(self, handle):
            return int(self.close_succeeds)

    monkeypatch.setattr(ctypes, "get_last_error", lambda: 6, raising=False)
    monkeypatch.setattr(
        ctypes, "WinError", lambda code: OSError(code, "invalid handle"), raising=False)
    job = WindowsMemoryJob.__new__(WindowsMemoryJob)
    job._kernel32 = FakeKernel32()
    job._handle = 123

    with pytest.raises(OSError, match="invalid handle") as raised:
        job.close()
    assert raised.value.tree_terminated is True
    assert job._handle == 123

    job._kernel32.close_succeeds = True
    job.close()
    assert job._handle is None


@pytest.mark.parametrize("driver", ["claude", "codex"])
@pytest.mark.parametrize("output", ["ok", ""])
def test_cli_temporary_workspace_is_removed_on_success_and_error(
        monkeypatch, tmp_path, driver, output):
    if driver == "claude":
        import agent.tools.claude_cli as module
        call, config, error, prefix = _run_claude, ClaudeConfig, ClaudeError, "claude_cwd_"
    else:
        import agent.tools.codex_prover as module
        call, config, error, prefix = _run_codex, CodexConfig, CodexError, "codex_cwd_"
    monkeypatch.setattr(module.tempfile, "tempdir", str(tmp_path))
    launcher = _result_launcher(tmp_path, output=output)

    if output:
        assert call("hi", config(launcher=launcher)) == output
    else:
        with pytest.raises(error, match="empty"):
            call("hi", config(launcher=launcher))

    assert not list(tmp_path.glob(f"{prefix}*"))


@pytest.mark.parametrize("driver", ["claude", "codex"])
def test_cli_cleanup_failure_remains_typed(monkeypatch, tmp_path, driver):
    if driver == "claude":
        import agent.tools.claude_cli as module
        call, config, error = _run_claude, ClaudeConfig, ClaudeError
    else:
        import agent.tools.codex_prover as module
        call, config, error = _run_codex, CodexConfig, CodexError
    workdir = tmp_path / "forced-workdir"
    workdir.mkdir()

    class CleanupFails:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return str(workdir)

        def __exit__(self, exc_type, exc, traceback):
            raise OSError("synthetic cleanup denial")

    monkeypatch.setattr(module.tempfile, "TemporaryDirectory", CleanupFails)
    with pytest.raises(error, match="temporary workspace"):
        call("hi", config(launcher=_result_launcher(tmp_path)))


@pytest.mark.parametrize("driver", ["claude", "codex"])
def test_batch_launcher_metacharacters_are_rejected(driver, tmp_path):
    launcher = tmp_path / "unsafe&launcher.cmd"
    launcher.write_text("@exit /b 0\r\n", encoding="ascii")
    if driver == "claude":
        with pytest.raises(ClaudeError, match="launcher"):
            _run_claude("hi", ClaudeConfig(launcher=str(launcher)))
    else:
        with pytest.raises(CodexError, match="launcher"):
            _run_codex("hi", CodexConfig(launcher=str(launcher)))


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group regression")
@pytest.mark.parametrize("driver", ["claude", "codex"])
def test_successful_root_cannot_leave_background_descendant(driver, tmp_path):
    """Cleanup targets containment, not only a still-running launcher PID."""
    import shlex

    pid_path = tmp_path / f"{driver}.pid"
    launcher = tmp_path / f"{driver}_orphan.sh"
    launcher.write_text(
        "#!/bin/sh\n"
        "out=''\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  if [ \"$1\" = '-o' ]; then shift; out=$1; fi\n"
        "  shift\n"
        "done\n"
        "sleep 30 &\n"
        f"echo $! > {shlex.quote(str(pid_path))}\n"
        "if [ -n \"$out\" ]; then printf 'ok\\n' > \"$out\"; else printf 'ok\\n'; fi\n",
        encoding="ascii",
    )
    os.chmod(launcher, 0o755)

    if driver == "claude":
        assert _run_claude("hi", ClaudeConfig(launcher=str(launcher))) == "ok"
    else:
        assert _run_codex("hi", CodexConfig(launcher=str(launcher))) == "ok"

    child_pid = int(pid_path.read_text(encoding="ascii"))
    proc_stat = Path(f"/proc/{child_pid}/stat")
    deadline = time.monotonic() + 2
    while proc_stat.exists() and time.monotonic() < deadline:
        # A killed child can briefly remain a zombie until its reaper runs; it is no longer live.
        if proc_stat.read_text(encoding="ascii").split()[2] == "Z":
            break
        time.sleep(0.02)
    else:
        if proc_stat.exists():
            pytest.fail(f"background descendant {child_pid} survived CLI cleanup")


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group regression")
def test_timeout_kills_launcher_descendants_not_only_root(tmp_path):
    """A timeout must kill a child that inherited the launcher's process group."""
    import shlex

    pid_path = tmp_path / "timeout-child.pid"
    launcher = tmp_path / "timeout_tree.sh"
    launcher.write_text(
        "#!/bin/sh\n"
        "sleep 30 &\n"
        f"echo $! > {shlex.quote(str(pid_path))}\n"
        "sleep 30\n",
        encoding="ascii",
    )
    os.chmod(launcher, 0o755)

    with pytest.raises(ClaudeError, match="timed out"):
        _run_claude("hi", ClaudeConfig(launcher=str(launcher), timeout_s=0.2))

    child_pid = int(pid_path.read_text(encoding="ascii"))
    proc_stat = Path(f"/proc/{child_pid}/stat")
    deadline = time.monotonic() + 2
    while proc_stat.exists() and time.monotonic() < deadline:
        if proc_stat.read_text(encoding="ascii").split()[2] == "Z":
            break
        time.sleep(0.02)
    else:
        if proc_stat.exists():
            pytest.fail(f"timeout descendant {child_pid} survived process-group cleanup")
