"""Codex (GPT-5.5-xHigh) as the focused-prover tool — the AlphaProof substitute.

AlphaProof_Nexus calls a strong RL-trained prover (AlphaProof) on subgoals. We don't have that model,
but we do have the Codex CLI (GPT-5.5 at xHigh reasoning) locally. This module shells out to
`codex exec` non-interactively and exposes three roles that plug into the DAG harness via the existing
Protocols:

  - CodexProver     : prove a goal -> a step-ledger (the focused prover; AlphaProof's role)
  - CodexDecomposer : propose a blueprint -> a sketch ledger citing `lemma` sub-goals (LEAP's planner)
  - CodexReviewer   : judge a decomposition for "does it simplify?" + "is it elementary?" (LEAP reviewer)

Invocation is read-only, ephemeral, in a throwaway cwd, with the prompt on stdin and the model's final
message captured via `--output-last-message`. Model + reasoning effort are configurable (default
gpt-5.5 / xhigh, matching ~/.codex/config.toml). The deterministic gate remains authoritative; these
are just the generation/soft-review tools.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agent.gates.ledger import parse_ledger, LedgerError
from agent.orchestrator.dag import goal_hash
from agent.orchestrator.dag_driver import ReviewVerdict
from agent.orchestrator.population import Candidate

_ROLE_DIR = Path(__file__).resolve().parents[1] / "roles"


class CodexError(RuntimeError):
    pass


def find_codex() -> Optional[str]:
    """Locate a non-interactive Codex launcher (prefer the Windows .cmd shim)."""
    for name in ("codex.cmd", "codex.exe", "codex"):
        p = shutil.which(name)
        if p:
            return p
    guess = Path.home() / ".local" / "nodejs" / "codex.cmd"
    return str(guess) if guess.exists() else None


@dataclass
class CodexConfig:
    model: str = "gpt-5.5"
    reasoning_effort: str = "xhigh"
    sandbox: str = "read-only"
    timeout_s: int = 1200
    launcher: Optional[str] = None


def _run_codex(prompt: str, cfg: CodexConfig) -> str:
    launcher = cfg.launcher or find_codex()
    if not launcher:
        raise CodexError("codex CLI not found on PATH")

    out_fd, out_path = tempfile.mkstemp(suffix=".txt", prefix="codex_out_")
    os.close(out_fd)
    workdir = tempfile.mkdtemp(prefix="codex_cwd_")
    # TOML override values are passed UNQUOTED: they fail TOML parse and are used as raw literals,
    # which avoids any shell quote-mangling through cmd.exe.
    flags = [
        "exec", "--skip-git-repo-check", "--ephemeral",
        "-s", cfg.sandbox, "--color", "never",
        "-c", f"model={cfg.model}",
        "-c", f"model_reasoning_effort={cfg.reasoning_effort}",
        "-o", out_path,
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
            raise CodexError(f"codex exec failed (exit {proc.returncode}): {tail}")
        msg = Path(out_path).read_text(encoding="utf-8", errors="replace").strip()
        if not msg:
            raise CodexError("codex returned an empty final message")
        return msg
    except subprocess.TimeoutExpired as e:
        raise CodexError(f"codex exec timed out after {cfg.timeout_s}s") from e
    finally:
        for p in (out_path,):
            try:
                os.remove(p)
            except OSError:
                pass
        shutil.rmtree(workdir, ignore_errors=True)


def _role(name: str) -> str:
    f = _ROLE_DIR / name
    return f.read_text(encoding="utf-8") if f.exists() else ""


def _toolkit_keys(toolkit) -> str:
    return ", ".join(sorted(toolkit.allowed_keys()))


def _feedback_block(feedback: Optional[list[str]]) -> str:
    if not feedback:
        return ""
    items = "\n".join(f"- {x}" for x in feedback[:12])
    return f"\n\nThe previous attempt was rejected. Fix exactly these issues and re-emit the WHOLE ledger:\n{items}\n"


class CodexProver:
    """Focused prover: goal -> step-ledger text (parsed downstream by the gate)."""

    def __init__(self, toolkit, cfg: Optional[CodexConfig] = None):
        self.toolkit = toolkit
        self.cfg = cfg or CodexConfig()

    @staticmethod
    def available() -> bool:
        return find_codex() is not None

    def prove(self, goal: str, feedback: Optional[list[str]] = None) -> str:
        prompt = (
            f"{_role('prover.md')}\n\n"
            f"Allowed `justification` keys (use ONLY these): {_toolkit_keys(self.toolkit)}\n\n"
            f"PROVE THIS GOAL:\n{goal}\n"
            f"{_feedback_block(feedback)}\n"
            "Output ONLY one fenced ```json block containing the step-ledger. No prose before or after. "
            "Do not read or modify any files."
        )
        return _run_codex(prompt, self.cfg)


class CodexDecomposer:
    """Planner: goal -> (sketch ledger citing `lemma` sub-goals, child goals derived from those steps)."""

    def __init__(self, toolkit, cfg: Optional[CodexConfig] = None):
        self.toolkit = toolkit
        self.cfg = cfg or CodexConfig()

    def decompose(self, goal: str, feedback: Optional[list[str]] = None) -> tuple[str, list[str]]:
        prompt = (
            f"{_role('prover.md')}\n\n"
            f"Allowed `justification` keys (use ONLY these): {_toolkit_keys(self.toolkit)}\n\n"
            "TASK: do NOT prove the goal directly. Instead DECOMPOSE it into a blueprint.\n"
            "Emit a single fenced ```json step-ledger that proves the GOAL while citing intermediate "
            "sub-lemmas you do NOT prove here: each such sub-lemma is a step with "
            'justification \"lemma\" whose `claim` is the FULL, self-contained statement of the '
            "sub-lemma. The conclusion step must follow from those lemma steps (plus elementary steps). "
            "Each sub-lemma should be strictly easier than the goal.\n\n"
            f"GOAL:\n{goal}\n"
            f"{_feedback_block(feedback)}\n"
            "Output ONLY the fenced ```json ledger. Do not read or modify any files."
        )
        sketch = _run_codex(prompt, self.cfg)
        # Children are the claims of the `lemma` steps — keeps the sketch and children consistent.
        return sketch, children_from_sketch(sketch)


def children_from_sketch(sketch: str) -> list[str]:
    """The deduped claims of a sketch's `lemma` steps (the child goals). [] if it won't parse."""
    try:
        led = parse_ledger(sketch)
    except LedgerError:
        return []
    seen: set[str] = set()
    children: list[str] = []
    for s in led.steps:
        if s.justification == "lemma":
            k = goal_hash(s.claim)
            if k not in seen:
                seen.add(k)
                children.append(s.claim)
    return children


_VERDICT_RE = re.compile(r"\{.*\}", re.DOTALL)


class CodexReviewer:
    """Decomposition reviewer + elementary judge -> ReviewVerdict."""

    def __init__(self, toolkit, cfg: Optional[CodexConfig] = None):
        self.toolkit = toolkit
        self.cfg = cfg or CodexConfig()

    def review(self, goal: str, sketch: str, child_goals: list[str]) -> ReviewVerdict:
        kids = "\n".join(f"- {c}" for c in child_goals)
        prompt = (
            f"{_role('critic_judge.md')}\n\n"
            "You are reviewing a DECOMPOSITION before it is committed. Judge two things:\n"
            "1. useful: do the sub-lemmas actually SIMPLIFY the goal (each strictly easier, and together "
            "they imply the goal)? Reject trivial/circular decompositions that just restate the goal.\n"
            "2. elementary: is every proposed step within the allowed elementary toolkit (no class "
            "groups, elliptic curves, modular forms, Baker, Mihailescu, p-adics beyond v_p)?\n\n"
            f"GOAL:\n{goal}\n\nPROPOSED SUB-LEMMAS:\n{kids}\n\nSKETCH LEDGER:\n{sketch}\n\n"
            'Output ONLY a JSON object: {"useful": <bool>, "elementary": <bool>, "notes": [<strings>]}'
        )
        raw = _run_codex(prompt, self.cfg)
        m = _VERDICT_RE.search(raw)
        if not m:
            return ReviewVerdict(useful=False, elementary=False,
                                 notes=["reviewer output was not parseable JSON"])
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return ReviewVerdict(useful=False, elementary=False,
                                 notes=["reviewer output was not valid JSON"])
        return ReviewVerdict(
            useful=bool(obj.get("useful", False)),
            elementary=bool(obj.get("elementary", False)),
            notes=list(obj.get("notes", []) or []),
        )


class CodexComparator:
    """Pairwise judge for the population/Elo search: which candidate decomposition is more promising?"""

    def __init__(self, cfg: Optional[CodexConfig] = None):
        self.cfg = cfg or CodexConfig()

    def compare(self, a: Candidate, b: Candidate) -> int:
        prompt = (
            "You are ranking two candidate DECOMPOSITIONS of the same number-theory goal. Pick the one "
            "more likely to lead to a correct, fully ELEMENTARY proof: prefer sub-lemmas that are "
            "genuinely easier than the goal, a non-circular split, and standard elementary techniques "
            "(divisibility, congruence, descent, gcd, bounding). Penalize trivial/circular splits and "
            "any reliance on heavy machinery.\n\n"
            f"GOAL:\n{a.goal}\n\n=== CANDIDATE A ===\n{a.content}\n\n=== CANDIDATE B ===\n{b.content}\n\n"
            'Output ONLY a JSON object: {"winner": "A" | "B" | "tie"}'
        )
        raw = _run_codex(prompt, self.cfg)
        m = _VERDICT_RE.search(raw)
        if not m:
            return 0
        try:
            w = str(json.loads(m.group(0)).get("winner", "tie")).strip().lower()
        except json.JSONDecodeError:
            return 0
        return 1 if w == "a" else (-1 if w == "b" else 0)
