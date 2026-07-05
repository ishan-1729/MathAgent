"""Claude CLI driver — the headless Sonnet/Opus text-generation backend for the evolve bridge.

Mirrors :mod:`agent.tools.codex_prover._run_codex` in spirit: a small, audited shell-out to a local
CLI that takes the prompt on **stdin**, runs in a **throwaway cwd**, with a subprocess **timeout** and
``utf-8``/``errors=replace`` decoding (Windows-safe). The only difference is the launcher and flags.

VERIFIED invocation (prompt on stdin, throwaway cwd, no tools):

    echo "PROMPT" | claude -p --model sonnet --output-format text --allowedTools ""

``--model sonnet`` drives the FAST/breadth model; ``--model opus`` the STRONGER/depth model
(AlphaEvolve's two-model ensemble). ``--allowedTools ""`` disables all tools so this is pure text
generation — the CLI never reads/writes files or shells out further.

SAFETY: this module only *generates text*. It never executes model output. The evolve bridge feeds
the generated text back as a candidate JSON ledger which is scored by the deterministic gate; nothing
here (or there) ever ``exec``/``eval``/``import``s model output.

:class:`ClaudeConfig` is a plain dataclass (no closures, no open handles) so it is picklable and can
be shipped to OpenEvolve worker processes inside a model-factory.
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class ClaudeError(RuntimeError):
    pass


# `model` reaches the cmd.exe command line (--model <model>); cmd.exe re-parses its arguments, so a
# value like `x" & calc & rem "` would execute commands (BatBadBut). REJECT anything outside this
# conservative shell-safe set rather than trying to escape it.
_SAFE_ARG_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill a launched process AND its descendants (stdlib only; no psutil).

    On Windows the launcher is a cmd.exe shim whose claude/node grandchild outlives a plain
    ``proc.kill()`` — use ``taskkill /T`` to walk the tree. On POSIX the child was started in its own
    session (``start_new_session=True``) so ``killpg`` reaches the whole group. Then reap briefly.
    """
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                proc.kill()
    except Exception:
        pass
    try:
        proc.communicate(timeout=5)
    except Exception:
        pass


def find_claude() -> Optional[str]:
    """Locate the ``claude`` CLI launcher (prefer the Windows ``.cmd`` shim), or ``None``."""
    for name in ("claude.cmd", "claude.exe", "claude"):
        p = shutil.which(name)
        if p:
            return p
    guess = Path.home() / ".local" / "nodejs" / "claude.cmd"
    return str(guess) if guess.exists() else None


@dataclass
class ClaudeConfig:
    """Picklable config for the headless Claude CLI.

    ``model`` selects the ensemble role: ``"sonnet"`` (breadth) or ``"opus"`` (depth). It is overridden
    per-ensemble-member by the bridge. Plain fields only — keep it pickle-safe (no closures/handles).
    """

    model: str = "sonnet"
    timeout_s: int = 600
    launcher: Optional[str] = None

    def __post_init__(self):
        # `model` reaches the cmd.exe command line — reject shell metacharacters (fail closed) rather
        # than sanitizing. Reachable from prove.py --model and ui/server.py /run?model=.
        # fullmatch, not match: `$` would tolerate a trailing newline in a security gate.
        if not (isinstance(self.model, str) and _SAFE_ARG_RE.fullmatch(self.model)):
            raise ClaudeError(
                f"invalid ClaudeConfig.model={self.model!r}: must match {_SAFE_ARG_RE.pattern}"
            )


def _run_claude(prompt: str, cfg: ClaudeConfig) -> str:
    """Run the headless Claude CLI on ``prompt`` and return its text output.

    Prompt goes on **stdin**; the process runs in a **throwaway temp cwd** with **all tools disabled**
    (``--allowedTools ""``) and a subprocess **timeout**. Decoding is ``utf-8``/``errors=replace`` so
    Unicode in math prompts never trips cp1252 on Windows. Raises :class:`ClaudeError` on a missing
    launcher, non-zero exit, empty output, or timeout.
    """
    launcher = cfg.launcher or find_claude()
    if not launcher:
        raise ClaudeError("claude CLI not found on PATH")
    # An explicitly-configured launcher that does not exist must surface the TYPED error, not a raw
    # FileNotFoundError from subprocess. (A .cmd/.bat is run via cmd.exe so the guard below need not
    # cover the bare-name-on-PATH case, which find_claude/shutil.which already resolve.)
    if not Path(launcher).exists() and shutil.which(launcher) is None:
        raise ClaudeError(f"claude CLI launcher not found: {launcher!r}")

    workdir = tempfile.mkdtemp(prefix="claude_cwd_")
    flags = [
        "-p",
        "--model", cfg.model,
        "--output-format", "text",
        "--allowedTools", "",  # disable ALL tools: pure text generation, no file/shell access
    ]
    if launcher.lower().endswith((".cmd", ".bat")):
        argv = [os.environ.get("COMSPEC", "cmd.exe"), "/c", launcher, *flags]
    else:
        argv = [launcher, *flags]

    # subprocess.run(timeout=...) only kills the cmd.exe shim; the real claude/node grandchild leaks.
    # Use Popen + communicate(timeout) and, on timeout, kill the WHOLE process tree before raising.
    proc = subprocess.Popen(
        argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        encoding="utf-8", errors="replace",  # prompts contain Unicode (->, math); avoid cp1252
        cwd=workdir,
        start_new_session=(os.name != "nt"),  # POSIX: own process group so we can killpg the tree
    )
    try:
        try:
            stdout, stderr = proc.communicate(input=prompt, timeout=cfg.timeout_s)
        except subprocess.TimeoutExpired as e:
            _kill_tree(proc)
            raise ClaudeError(f"claude -p timed out after {cfg.timeout_s}s") from e
        if proc.returncode != 0:
            tail = (stderr or stdout or "").strip()[-600:]
            raise ClaudeError(f"claude -p failed (exit {proc.returncode}): {tail}")
        msg = (stdout or "").strip()
        if not msg:
            raise ClaudeError("claude returned empty output")
        return msg
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


__all__ = ["ClaudeConfig", "ClaudeError", "find_claude", "_run_claude"]
