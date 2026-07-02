"""Tests for the headless CLI drivers' error contract (offline; no subprocess spawned).

`_run_claude`/`_run_codex` resolve `launcher = cfg.launcher or find_*()`. An explicitly-configured
launcher that does not exist on disk (and cannot be resolved on PATH) must surface the module's TYPED
error (ClaudeError / CodexError), NOT a raw FileNotFoundError from subprocess. These tests pin that
contract and, via a subprocess.run sentinel, assert no process is ever spawned for the missing path.
"""
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
