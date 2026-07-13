"""Claude CLI driver — the headless Sonnet/Opus text-generation backend for the evolve bridge.

Mirrors :mod:`agent.tools.codex_prover._run_codex` in spirit: a small, audited shell-out to a local
CLI that takes the prompt on **stdin**, runs in a **throwaway cwd**, with a subprocess **timeout** and
``utf-8``/``errors=replace`` decoding (Windows-safe). The only difference is the launcher and flags.

VERIFIED invocation (prompt on stdin, throwaway cwd, no tools or customizations):

    echo "PROMPT" | claude -p --model sonnet --output-format text --tools "" --safe-mode ...

``--model sonnet`` drives the FAST/breadth model; ``--model opus`` the STRONGER/depth model
(AlphaEvolve's two-model ensemble). ``--tools ""`` removes all built-in tools so this is pure text
generation — the CLI never reads/writes files or shells out further.

SAFETY: this module only *generates text*. It never executes model output. The evolve bridge feeds
the generated text back as a candidate JSON ledger which is scored by the deterministic gate; nothing
here (or there) ever ``exec``/``eval``/``import``s model output.

:class:`ClaudeConfig` is a plain dataclass (no closures, no open handles) so it is picklable and can
be shipped to OpenEvolve worker processes inside a model-factory.
"""
from __future__ import annotations

import math
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agent.tools._cli_process import prepare_cli_launcher, run_bounded_cli


class ClaudeError(RuntimeError):
    pass


# `model` reaches the cmd.exe command line (--model <model>); cmd.exe re-parses its arguments, so a
# value like `x" & calc & rem "` would execute commands (BatBadBut). REJECT anything outside this
# conservative shell-safe set rather than trying to escape it.
_SAFE_ARG_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_CMD_LAUNCHER_META_RE = re.compile(r'[\r\n\x00"&|<>^%!()]')
_LAUNCHER_CONTROL_RE = re.compile(r"[\r\n\x00]")
_MAX_CLI_OUTPUT_BYTES = 4 * 1024 * 1024


def _validate_launcher(launcher: str) -> None:
    """Reject batch-launcher paths that ``cmd.exe /c`` could parse as commands."""
    if not isinstance(launcher, str) or not launcher or _LAUNCHER_CONTROL_RE.search(launcher):
        raise ClaudeError("Claude CLI launcher must be non-empty text without control characters")
    if launcher.lower().endswith((".cmd", ".bat")) and _CMD_LAUNCHER_META_RE.search(launcher):
        raise ClaudeError("unsafe shell metacharacter in Claude CLI launcher path")


def _read_bounded(path: Path, *, stream: str) -> str:
    """Decode one file-backed CLI stream without admitting arbitrarily large model output."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ClaudeError(f"could not read Claude CLI {stream}: {exc}") from exc
    if size > _MAX_CLI_OUTPUT_BYTES:
        raise ClaudeError(
            f"Claude CLI {stream} exceeded {_MAX_CLI_OUTPUT_BYTES} bytes"
        )
    try:
        return path.read_bytes().decode("utf-8", errors="replace")
    except OSError as exc:
        raise ClaudeError(f"could not read Claude CLI {stream}: {exc}") from exc


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
        if (isinstance(self.timeout_s, bool) or not isinstance(self.timeout_s, (int, float))
                or not math.isfinite(self.timeout_s) or self.timeout_s <= 0):
            raise ClaudeError("ClaudeConfig.timeout_s must be a positive finite number")
        if self.launcher is not None:
            _validate_launcher(self.launcher)


def _run_claude(prompt: str, cfg: ClaudeConfig) -> str:
    """Run the headless Claude CLI on ``prompt`` and return its text output.

    Prompt goes on **stdin**; the process runs in a **throwaway temp cwd** with **all tools removed**
    (``--tools ""``), customizations/MCP/Chrome disabled, and a subprocess **timeout**. Decoding is
    ``utf-8``/``errors=replace`` so
    Unicode in math prompts never trips cp1252 on Windows. Raises :class:`ClaudeError` on a missing
    launcher, non-zero exit, empty output, or timeout.
    """
    launcher = cfg.launcher or find_claude()
    if not launcher:
        raise ClaudeError("claude CLI not found on PATH")
    _validate_launcher(launcher)
    # An explicitly-configured launcher that does not exist must surface the TYPED error, not a raw
    # FileNotFoundError from subprocess. (A .cmd/.bat is run via cmd.exe so the guard below need not
    # cover the bare-name-on-PATH case, which find_claude/shutil.which already resolve.)
    try:
        launcher_exists = Path(launcher).exists() or shutil.which(launcher) is not None
    except (OSError, ValueError) as exc:
        raise ClaudeError(f"could not resolve Claude CLI launcher: {exc}") from exc
    if not launcher_exists:
        raise ClaudeError(f"claude CLI launcher not found: {launcher!r}")

    flags = [
        "-p",
        "--model", cfg.model,
        "--output-format", "text",
        # --allowedTools is only a permission allowlist. --tools "" removes the built-in tools from
        # model context entirely; the remaining flags remove every customization/tool-discovery path
        # while preserving normal account authentication (unlike --bare).
        "--tools", "",
        "--safe-mode",
        "--no-chrome",
        "--strict-mcp-config",
        "--no-session-persistence",
    ]
    argv_prefix, temp_root = prepare_cli_launcher(
        launcher, error_type=ClaudeError, label="Claude CLI"
    )
    argv = [*argv_prefix, *flags]

    try:
        with tempfile.TemporaryDirectory(prefix="claude_cwd_", dir=temp_root) as workdir:
            stdout_path = Path(workdir) / "stdout.txt"
            stderr_path = Path(workdir) / "stderr.txt"
            # The shared transport contains the whole process tree and applies pipe backpressure, so
            # a burst cannot overshoot the on-disk stream cap between polling intervals.
            returncode = run_bounded_cli(
                argv,
                prompt=prompt,
                cwd=workdir,
                timeout_s=cfg.timeout_s,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                watched_paths={"stdout": stdout_path, "stderr": stderr_path},
                max_bytes=_MAX_CLI_OUTPUT_BYTES,
                error_type=ClaudeError,
                label="Claude CLI",
            )
            stdout = _read_bounded(stdout_path, stream="stdout")
            stderr = _read_bounded(stderr_path, stream="stderr")
            if returncode != 0:
                tail = (stderr or stdout or "").strip()[-600:]
                raise ClaudeError(f"claude -p failed (exit {returncode}): {tail}")
            msg = (stdout or "").strip()
            if not msg:
                raise ClaudeError("claude returned empty output")
            return msg
    except ClaudeError:
        raise
    except OSError as exc:
        # Temporary prompt/output files can contain sensitive model context. Cleanup failures are
        # surfaced as typed transport failures instead of silently leaking the workspace.
        raise ClaudeError(f"Claude CLI temporary workspace failed: {exc}") from exc


__all__ = ["ClaudeConfig", "ClaudeError", "find_claude", "_run_claude"]
