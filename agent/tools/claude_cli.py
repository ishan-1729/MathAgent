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
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class ClaudeError(RuntimeError):
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

    try:
        proc = subprocess.run(
            argv, input=prompt, capture_output=True,
            encoding="utf-8", errors="replace",  # prompts contain Unicode (->, math); avoid cp1252
            timeout=cfg.timeout_s, cwd=workdir,
        )
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip()[-600:]
            raise ClaudeError(f"claude -p failed (exit {proc.returncode}): {tail}")
        msg = (proc.stdout or "").strip()
        if not msg:
            raise ClaudeError("claude returned empty output")
        return msg
    except subprocess.TimeoutExpired as e:
        raise ClaudeError(f"claude -p timed out after {cfg.timeout_s}s") from e
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


__all__ = ["ClaudeConfig", "ClaudeError", "find_claude", "_run_claude"]
