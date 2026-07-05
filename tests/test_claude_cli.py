"""Tests for the headless CLI drivers' error contract (offline; no subprocess spawned).

`_run_claude`/`_run_codex` resolve `launcher = cfg.launcher or find_*()`. An explicitly-configured
launcher that does not exist on disk (and cannot be resolved on PATH) must surface the module's TYPED
error (ClaudeError / CodexError), NOT a raw FileNotFoundError from subprocess. These tests pin that
contract and, via a subprocess.run sentinel, assert no process is ever spawned for the missing path.
"""
import os
import subprocess

import pytest

from agent.tools.claude_cli import ClaudeConfig, ClaudeError, _run_claude
from agent.tools.codex_prover import CodexConfig, CodexError, _run_codex


def _no_subprocess(monkeypatch, module):
    """Make subprocess.run in `module` explode if called — the typed error must fire first."""
    def _boom(*a, **k):
        raise AssertionError("subprocess.run must not be called for a missing launcher")
    monkeypatch.setattr(module.subprocess, "run", _boom)


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


def test_run_claude_timeout_raises_typed_error(tmp_path):
    cfg = ClaudeConfig(launcher=_sleeper_launcher(tmp_path), timeout_s=0.5)
    with pytest.raises(ClaudeError, match="timed out"):
        _run_claude("hi", cfg)


def test_run_codex_timeout_raises_typed_error(tmp_path):
    cfg = CodexConfig(launcher=_sleeper_launcher(tmp_path), timeout_s=0.5)
    with pytest.raises(CodexError, match="timed out"):
        _run_codex("hi", cfg)
