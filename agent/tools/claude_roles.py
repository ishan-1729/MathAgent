"""First-class Claude (Opus/Sonnet/Haiku) roles — the in-repo Claude backend for the DAG harness.

Mirrors :mod:`agent.tools.codex_prover` role-for-role, but generates via the headless Claude CLI
(:func:`agent.tools.claude_cli._run_claude`) instead of ``codex exec``. Same ``agent/roles/*.md``
prompts, same shared CLI-JSON parsers (:mod:`agent.tools._cli_json`), and the SAME return types
(``ReviewVerdict``, ``Candidate`` ordering, ``SingleVerdict``/``FaithfulnessVerdict``,
``RevisionController``) so every role plugs into the EXISTING Protocols the DAG/tournament expect.

Each role takes a per-role :class:`ClaudeConfig` (so the registry can pin ``model=spec.model`` —
prover/refiner=opus, decomposer/reviewer/faithfulness=sonnet, comparator/judge=sonnet). The default
model is the codex/maintainer-sensible one for that role.

SAFETY: like the Codex roles, these only *generate text*. Nothing here ``exec``/``eval``/``import``s
model output — every CLI response is parsed by the deterministic ``_cli_json`` helpers and handed to
the deterministic gate downstream. ``ClaudeFormalizer`` already lives in ``formalizer.py``; reuse it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from agent.orchestrator.dag_driver import ReviewVerdict
from agent.orchestrator.population import Candidate
from agent.orchestrator.faithfulness import SingleVerdict, PanelFaithfulnessChecker
from agent.orchestrator.tournament import RevisionController
from agent.tools.claude_cli import ClaudeConfig, _run_claude
from agent.tools._cli_json import (
    _extract_json_object,
    _json_array,
    children_from_sketch,
)

_ROLE_DIR = Path(__file__).resolve().parents[1] / "roles"


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


class ClaudeProver:
    """Focused prover: goal -> step-ledger text (parsed downstream by the gate). Opus by default."""

    def __init__(self, toolkit, cfg: Optional[ClaudeConfig] = None):
        self.toolkit = toolkit
        self.cfg = cfg or ClaudeConfig(model="opus")

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
        return _run_claude(prompt, self.cfg)


class ClaudeDecomposer:
    """Planner: goal -> (sketch ledger citing `lemma` sub-goals, child goals derived from those steps)."""

    def __init__(self, toolkit, cfg: Optional[ClaudeConfig] = None):
        self.toolkit = toolkit
        self.cfg = cfg or ClaudeConfig(model="sonnet")

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
        sketch = _run_claude(prompt, self.cfg)
        # Children are the claims of the `lemma` steps — keeps the sketch and children consistent.
        return sketch, children_from_sketch(sketch)


class ClaudeReviewer:
    """Decomposition reviewer + elementary judge -> ReviewVerdict. Sonnet by default."""

    def __init__(self, toolkit, cfg: Optional[ClaudeConfig] = None):
        self.toolkit = toolkit
        self.cfg = cfg or ClaudeConfig(model="sonnet")

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
        raw = _run_claude(prompt, self.cfg)
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


class ClaudeComparator:
    """Pairwise judge for the population/Elo search: which candidate decomposition is more promising?

    Mirrors :class:`agent.tools.codex_prover.CodexComparator`; Sonnet by default (cheap pairwise calls)."""

    def __init__(self, cfg: Optional[ClaudeConfig] = None):
        self.cfg = cfg or ClaudeConfig(model="sonnet")

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
        raw = _run_claude(prompt, self.cfg)
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


class _ClaudeFaithJudge:
    def __init__(self, cfg: ClaudeConfig):
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
        raw = _run_claude(prompt, self.cfg)
        obj = _extract_json_object(raw)
        if obj is None:
            return SingleVerdict(lens=lens, faithful=False, issues=["unparseable judge output"])
        return SingleVerdict(lens=lens, faithful=(obj.get("faithful") is True),
                             issues=list(obj.get("issues", []) or []))


class ClaudeFaithfulnessChecker:
    """Adversarial faithfulness panel backed by Claude (one judge per diverse lens). Sonnet by default."""

    def __init__(self, cfg: Optional[ClaudeConfig] = None, lenses: Optional[list[str]] = None,
                 max_unfaithful: int = 0):
        self.cfg = cfg or ClaudeConfig(model="sonnet")
        self._panel = PanelFaithfulnessChecker(_ClaudeFaithJudge(self.cfg), lenses=lenses,
                                               max_unfaithful=max_unfaithful)

    def check(self, informal_claim: str, lean_source: str, theorem_name: str):
        return self._panel.check(informal_claim, lean_source, theorem_name)


# --------------------------------------------------------------------------------------------------
# Autoreason incumbent-tournament roles, backed by Claude (critic / author / synthesizer / judge).
# GENERIC over a "candidate" string — a proof ledger (DagDriver refiner) OR a free-form solution
# (benchmark harness). Mirrors the Codex tournament roles in agent/tools/codex_prover.py.
# --------------------------------------------------------------------------------------------------

_NON_ELEM = ("class groups, elliptic curves, modular forms, Baker's theorem, Mihăilescu/Catalan, "
             "p-adic machinery beyond v_p, algebraic number fields, analytic number theory")


class ClaudeCritic:
    """Adversarial failure-analysis of a candidate (problems only, no fixes). Opus by default."""

    def __init__(self, cfg: Optional[ClaudeConfig] = None):
        self.cfg = cfg or ClaudeConfig(model="opus")

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
        return _json_array(_run_claude(prompt, self.cfg))


class ClaudeAuthor:
    """Revise a candidate to address a critique, preserving its output format. Opus by default."""

    def __init__(self, cfg: Optional[ClaudeConfig] = None):
        self.cfg = cfg or ClaudeConfig(model="opus")

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
        return _run_claude(prompt, self.cfg)


class ClaudeSynthesizer:
    """Merge two candidates into a single stronger one (blind to which is the incumbent). Opus by default."""

    def __init__(self, cfg: Optional[ClaudeConfig] = None):
        self.cfg = cfg or ClaudeConfig(model="opus")

    def synthesize(self, goal: str, a: str, b: str) -> str:
        prompt = (
            "You MERGE two candidate solutions/proofs to a number-theory problem into a single, stronger "
            "one — combining their correct insights, discarding errors, using ONLY elementary methods. "
            "Keep the SAME output format; if either ends with a line 'FINAL ANSWER: <answer>', end with "
            "that line.\n\n"
            f"PROBLEM:\n{goal}\n\n=== CANDIDATE 1 ===\n{a}\n\n=== CANDIDATE 2 ===\n{b}\n\n"
            "Output ONLY the merged solution. Do not read or modify any files."
        )
        return _run_claude(prompt, self.cfg)


class ClaudeJudge:
    """Pairwise judge for the incumbent tournament: which candidate solution/proof is better?

    Mirrors :class:`agent.tools.codex_prover.CodexSolutionComparator` (the ``judge`` role); Sonnet by
    default. Returns 1 if A is better, -1 if B, 0 for a tie (a :class:`Comparator`)."""

    def __init__(self, cfg: Optional[ClaudeConfig] = None):
        self.cfg = cfg or ClaudeConfig(model="sonnet")

    def compare(self, a: Candidate, b: Candidate) -> int:
        prompt = (
            "You judge two candidate solutions/proofs to the SAME number-theory problem. Pick the one "
            "more likely to be CORRECT, that is more rigorous, and that is fully ELEMENTARY (penalize "
            f"any reliance on heavy machinery: {_NON_ELEM}).\n\n"
            f"PROBLEM:\n{a.goal}\n\n=== CANDIDATE A ===\n{a.content}\n\n=== CANDIDATE B ===\n{b.content}\n\n"
            'Output ONLY a JSON object: {"winner": "A" | "B" | "tie"}. Do not read or modify any files.'
        )
        raw = _run_claude(prompt, self.cfg)
        obj = _extract_json_object(raw)
        if obj is None:
            return 0
        w = str(obj.get("winner", "tie")).strip().lower()
        return 1 if w == "a" else (-1 if w == "b" else 0)


def make_claude_refiner(cfg: Optional[ClaudeConfig] = None, *, n_judges: int = 1,
                        budget=None, trace=None, max_passes: int = 2, k_stop: int = 2,
                        margin: int = 1, seed: int = 0) -> RevisionController:
    """A fully Claude-backed Autoreason incumbent tournament (critic + author + synthesizer + an
    n_judges-strong Claude judge panel). Mirrors
    :func:`agent.tools.codex_prover.make_codex_refiner`. PUCT + Bradley-Terry run on the judges' win
    matrix inside the controller; the caller supplies the admissibility gate (e.g. the elementary
    gate) to refine()."""
    cfg = cfg or ClaudeConfig(model="opus")
    judges = [ClaudeJudge(cfg) for _ in range(max(1, n_judges))]
    return RevisionController(ClaudeCritic(cfg), ClaudeAuthor(cfg), ClaudeSynthesizer(cfg), judges,
                              max_passes=max_passes, k_stop=k_stop, margin=margin,
                              budget=budget, trace=trace, seed=seed)
