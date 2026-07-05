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

import os
import re
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agent.orchestrator.dag_driver import ReviewVerdict
# Shared CLI-output JSON parsers (lifted into agent/tools/_cli_json.py so the Codex and Claude CLI
# backends share ONE implementation). Re-exported below for backwards compatibility — callers and
# tests that import these names from `codex_prover` keep working unchanged.
from agent.tools._cli_json import (  # noqa: F401  (re-exported)
    _FENCED_JSON_RE,
    _CLOSERS,
    _last_balanced,
    _extract_json,
    _extract_json_object,
    _json_array,
    children_from_sketch,
)
from agent.orchestrator.population import Candidate
from agent.orchestrator.faithfulness import SingleVerdict, PanelFaithfulnessChecker
from agent.orchestrator.tournament import RevisionController

_ROLE_DIR = Path(__file__).resolve().parents[1] / "roles"


class CodexError(RuntimeError):
    pass


# Fields that flow onto the cmd.exe command line (model=..., -s <sandbox>, ...). cmd.exe re-parses
# its arguments, so a value like `x" & calc & rem "` would execute commands (BatBadBut). We REJECT
# anything outside this conservative shell-safe set rather than trying to escape it.
_SAFE_ARG_RE = re.compile(r"^[A-Za-z0-9._-]+$")


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

    def __post_init__(self):
        # model/reasoning_effort/sandbox reach the cmd.exe command line — reject shell metacharacters
        # (fail closed) instead of sanitizing. Reachable from prove.py --model and ui/server.py.
        for field in ("model", "reasoning_effort", "sandbox"):
            value = getattr(self, field)
            # fullmatch, not match: `$` would tolerate a trailing newline in a security gate.
            if not (isinstance(value, str) and _SAFE_ARG_RE.fullmatch(value)):
                raise CodexError(
                    f"invalid CodexConfig.{field}={value!r}: must match {_SAFE_ARG_RE.pattern}"
                )


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill a launched process AND its descendants (stdlib only; no psutil).

    On Windows the launcher is a cmd.exe shim whose codex/node grandchild outlives a plain
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


def _run_codex(prompt: str, cfg: CodexConfig) -> str:
    launcher = cfg.launcher or find_codex()
    if not launcher:
        raise CodexError("codex CLI not found on PATH")
    # An explicitly-configured launcher that does not exist must surface the TYPED error, not a raw
    # FileNotFoundError from subprocess (bare names on PATH are already resolved by find_codex/which).
    if not Path(launcher).exists() and shutil.which(launcher) is None:
        raise CodexError(f"codex CLI launcher not found: {launcher!r}")

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

    # subprocess.run(timeout=...) only kills the cmd.exe shim; the real codex/node grandchild leaks.
    # Use Popen + communicate(timeout) and, on timeout, kill the WHOLE process tree before raising.
    proc = subprocess.Popen(
        argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        encoding="utf-8", errors="replace",  # prompts contain Unicode (->, math); avoid cp1252
        cwd=workdir,
        start_new_session=(os.name != "nt"),  # POSIX: own process group so we can killpg the tree
    )
    try:
        try:
            _stdout, stderr = proc.communicate(input=prompt, timeout=cfg.timeout_s)
        except subprocess.TimeoutExpired as e:
            _kill_tree(proc)
            raise CodexError(f"codex exec timed out after {cfg.timeout_s}s") from e
        if proc.returncode != 0:
            tail = (stderr or _stdout or "").strip()[-600:]
            raise CodexError(f"codex exec failed (exit {proc.returncode}): {tail}")
        msg = Path(out_path).read_text(encoding="utf-8", errors="replace").strip()
        if not msg:
            raise CodexError("codex returned an empty final message")
        return msg
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
            "GOAL-BINDING (required): the ledger's top-level `claim` AND the terminal `conclusion` "
            "step's `claim` must restate the requested goal STRING VERBATIM — copy the goal exactly as "
            "written above, with no paraphrase, no re-notation, no added or dropped qualifiers. A "
            "ledger whose claim/conclusion is a reworded equivalent of the goal is REJECTED.\n"
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
        obj = _extract_json_object(raw)
        if obj is None:
            return ReviewVerdict(useful=False, elementary=False,
                                 notes=["reviewer output was not parseable JSON"])
        return ReviewVerdict(
            # Strict `is True`: JSON `true` -> Python True, but a model emitting the STRING "true"/
            # "false" (bool("false") == True) must fail closed. Any non-True value => False.
            useful=(obj.get("useful") is True),
            elementary=(obj.get("elementary") is True),
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
        obj = _extract_json_object(raw)
        if obj is None:
            return 0
        w = str(obj.get("winner", "tie")).strip().lower()
        return 1 if w == "a" else (-1 if w == "b" else 0)


_LENS_FOCUS = {
    "back_translation": "Translate the Lean statement back into precise English, then check it says "
                        "EXACTLY the informal claim — no more, no less.",
    "quantifiers_domain": "Scrutinize quantifiers (∀/∃ and their order), variable domains/types "
                          "(ℕ vs ℤ vs ℝ, positivity, ranges), and edge values (0, 1, negatives).",
    "vacuity": "Is the statement vacuously true, are its hypotheses unsatisfiable, or is it a "
               "trivially-true restatement that does not assert the informal content?",
    "strength": "Is the Lean statement strictly WEAKER (only a special case) or STRONGER/UNRELATED "
                "than the informal claim? Either is unfaithful.",
}


class _CodexFaithJudge:
    def __init__(self, cfg: CodexConfig):
        self.cfg = cfg

    def __call__(self, claim: str, lean_source: str, name: str, lens: str) -> SingleVerdict:
        focus = _LENS_FOCUS.get(lens, "Check the Lean statement faithfully captures the claim.")
        prompt = (
            "You ADVERSARIALLY check whether a Lean 4 statement faithfully formalizes an informal "
            "number-theory claim. Your job is to FIND a discrepancy; default to faithful=false if at "
            "all unsure.\n"
            f"LENS [{lens}]: {focus}\n\n"
            f"INFORMAL CLAIM:\n{claim}\n\n"
            f"LEAN SOURCE (the statement under check is theorem/lemma `{name}`):\n{lean_source}\n\n"
            'Output ONLY a JSON object: {"faithful": <bool>, "issues": [<short strings>]}'
        )
        raw = _run_codex(prompt, self.cfg)
        obj = _extract_json_object(raw)
        if obj is None:
            return SingleVerdict(lens=lens, faithful=False, issues=["unparseable judge output"])
        return SingleVerdict(lens=lens, faithful=(obj.get("faithful") is True),
                             issues=list(obj.get("issues", []) or []))


class CodexFaithfulnessChecker:
    """Adversarial faithfulness panel backed by Codex (one judge per diverse lens)."""

    def __init__(self, cfg: Optional[CodexConfig] = None, lenses: Optional[list[str]] = None,
                 max_unfaithful: int = 0):
        self.cfg = cfg or CodexConfig()
        self._panel = PanelFaithfulnessChecker(_CodexFaithJudge(self.cfg), lenses=lenses,
                                               max_unfaithful=max_unfaithful)

    def check(self, informal_claim: str, lean_source: str, theorem_name: str):
        return self._panel.check(informal_claim, lean_source, theorem_name)


# --------------------------------------------------------------------------------------------------
# Autoreason incumbent-tournament roles, backed by Codex (the revision controller's critic / author /
# synthesizer / judge). These are GENERIC over a "candidate" string — a proof ledger (DagDriver
# refiner) OR a free-form solution (benchmark harness) — so the same tournament drives both. See
# agent/orchestrator/tournament.py.
# --------------------------------------------------------------------------------------------------

_NON_ELEM = ("class groups, elliptic curves, modular forms, Baker's theorem, Mihăilescu/Catalan, "
             "p-adic machinery beyond v_p, algebraic number fields, analytic number theory")


class CodexCritic:
    """Adversarial failure-analysis of a candidate (problems only, no fixes)."""

    def __init__(self, cfg: Optional[CodexConfig] = None):
        self.cfg = cfg or CodexConfig()

    def critique(self, goal: str, incumbent: str) -> list[str]:
        prompt = (
            "You are an adversarial CRITIC of a candidate solution/proof to a number-theory problem. "
            "Do failure analysis ONLY — list concrete flaws: logical gaps, computational/arithmetic "
            "errors, unjustified steps, wrong or edge-case-incorrect final answers, and any "
            f"NON-ELEMENTARY method ({_NON_ELEM}). Propose NO fixes.\n\n"
            f"PROBLEM:\n{goal}\n\nCANDIDATE:\n{incumbent}\n\n"
            'Output ONLY a JSON array of short strings (e.g. ["flaw 1","flaw 2"]); use [] if flawless. '
            "Do not read or modify any files."
        )
        return _json_array(_run_codex(prompt, self.cfg))


class CodexAuthor:
    """Revise a candidate to address a critique, preserving its output format."""

    def __init__(self, cfg: Optional[CodexConfig] = None):
        self.cfg = cfg or CodexConfig()

    def revise(self, goal: str, incumbent: str, critique: list[str]) -> str:
        issues = "\n".join(f"- {c}" for c in critique) or "(no specific issues; improve rigor/clarity)"
        prompt = (
            "You REVISE a candidate solution/proof to a number-theory problem to fix the listed issues, "
            "using ONLY elementary methods (the IMO toolkit: divisibility, congruences, gcd/coprimality, "
            "descent, bounding, parity, induction). Keep the SAME output format as the candidate; if it "
            "ends with a line 'FINAL ANSWER: <answer>', your revision MUST also end with exactly that "
            "line.\n\n"
            f"PROBLEM:\n{goal}\n\nCURRENT CANDIDATE:\n{incumbent}\n\nISSUES TO FIX:\n{issues}\n\n"
            "Output ONLY the revised candidate. Do not read or modify any files."
        )
        return _run_codex(prompt, self.cfg)


class CodexSynthesizer:
    """Merge two candidates into a single stronger one (blind to which is the incumbent)."""

    def __init__(self, cfg: Optional[CodexConfig] = None):
        self.cfg = cfg or CodexConfig()

    def synthesize(self, goal: str, a: str, b: str) -> str:
        prompt = (
            "You MERGE two candidate solutions/proofs to a number-theory problem into a single, stronger "
            "one — combining their correct insights, discarding errors, using ONLY elementary methods. "
            "Keep the SAME output format; if either ends with a line 'FINAL ANSWER: <answer>', end with "
            "that line.\n\n"
            f"PROBLEM:\n{goal}\n\n=== CANDIDATE 1 ===\n{a}\n\n=== CANDIDATE 2 ===\n{b}\n\n"
            "Output ONLY the merged solution. Do not read or modify any files."
        )
        return _run_codex(prompt, self.cfg)


class CodexSolutionComparator:
    """Pairwise judge for the incumbent tournament: which candidate solution/proof is better?"""

    def __init__(self, cfg: Optional[CodexConfig] = None):
        self.cfg = cfg or CodexConfig()

    def compare(self, a: Candidate, b: Candidate) -> int:
        prompt = (
            "You judge two candidate solutions/proofs to the SAME number-theory problem. Pick the one "
            "more likely to be CORRECT, that is more rigorous, and that is fully ELEMENTARY (penalize "
            f"any reliance on heavy machinery: {_NON_ELEM}).\n\n"
            f"PROBLEM:\n{a.goal}\n\n=== CANDIDATE A ===\n{a.content}\n\n=== CANDIDATE B ===\n{b.content}\n\n"
            'Output ONLY a JSON object: {"winner": "A" | "B" | "tie"}. Do not read or modify any files.'
        )
        raw = _run_codex(prompt, self.cfg)
        obj = _extract_json_object(raw)
        if obj is None:
            return 0
        w = str(obj.get("winner", "tie")).strip().lower()
        return 1 if w == "a" else (-1 if w == "b" else 0)


def make_codex_refiner(cfg: Optional[CodexConfig] = None, *, n_judges: int = 1,
                       budget=None, trace=None, max_passes: int = 2, k_stop: int = 2,
                       margin: int = 1, seed: int = 0) -> RevisionController:
    """A fully Codex-backed Autoreason incumbent tournament (critic + author + synthesizer + an
    n_judges-strong Codex judge panel). PUCT + Bradley-Terry run on the judges' win matrix inside the
    controller; the caller supplies the admissibility gate (e.g. the elementary gate) to refine()."""
    cfg = cfg or CodexConfig()
    judges = [CodexSolutionComparator(cfg) for _ in range(max(1, n_judges))]
    return RevisionController(CodexCritic(cfg), CodexAuthor(cfg), CodexSynthesizer(cfg), judges,
                              max_passes=max_passes, k_stop=k_stop, margin=margin,
                              budget=budget, trace=trace, seed=seed)
