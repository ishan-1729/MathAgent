"""Ledger -> Lean formalizer (the informal->formal bridge, LEAP/Aristotle-style).

Takes a gate-passed elementary step-ledger and asks Codex (GPT-5.5-xHigh) to produce a Lean 4 theorem
with a complete, compiling, sorry-free proof. The result is then compiled + audited by Layer 4
(`agent/orchestrator/formalize_bridge.py`), closing the loop: informal proof -> Lean -> dependency
audit -> authoritative elementary verdict.

Autoformalization is the known wall (the proof may not compile; a statement may be unfaithful). The
bridge reports those outcomes honestly; this module just generates and parses the Lean source.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from agent.gates.toolkit import Toolkit
from agent.tools.claude_cli import ClaudeConfig, _run_claude
from agent.tools.codex_prover import CodexConfig, _run_codex

DEFAULT_THEOREM_NAME = "ma_target"

_LEAN_FENCE = re.compile(r"```(?:lean4?|lean)?\s*(.*?)```", re.DOTALL)
_DECL_RE = re.compile(r"\b(?:theorem|lemma)\s+([A-Za-z_][A-Za-z0-9_'.]*)")


@dataclass
class FormalizationResult:
    ok: bool                       # a Lean code block with a theorem/lemma was produced
    lean_source: str = ""
    theorem_name: str = DEFAULT_THEOREM_NAME
    notes: list[str] = field(default_factory=list)
    raw: str = ""


def extract_lean(raw: str) -> Optional[str]:
    """Pull the Lean source out of a fenced block, or treat the whole text as Lean if it looks like it."""
    m = _LEAN_FENCE.search(raw)
    if m:
        return m.group(1).strip()
    if "theorem " in raw or "lemma " in raw:
        return raw.strip()
    return None


def first_decl_name(lean_src: str) -> Optional[str]:
    m = _DECL_RE.search(lean_src)
    return m.group(1) if m else None


# --------------------------------------------------------------------------------------------------
# P4: AND-node sorry-sketch composition (LEAP §2.2/§2.5). A decomposition sketch proves the PARENT
# goal assuming the CHILD goals as named hypotheses h0, h1, ... The emitted body is SORRY-FREE (the
# children are HYPOTHESES, not `sorry` placeholders), so the result is audit-clean and reuses
# `audit_lean_source` verbatim — the audit then certifies the COMPOSITION is Lean-valid and the body's
# own axioms/deps are elementary, deferring the children themselves to their own per-node verification.
# Shared by CodexFormalizer/ClaudeFormalizer so both impls emit the same shape from the same _RULES.
# --------------------------------------------------------------------------------------------------

def _sketch_prompt(rules: str, parent_goal: str, sketch_text: str, child_goals: list[str],
                   prior_source: Optional[str] = None, errors: Optional[list[str]] = None,
                   lemmas: Optional[list[str]] = None) -> str:
    """Build the prompt asking for a SORRY-FREE Lean theorem proving the parent ASSUMING the children
    as hypotheses h0, h1, ... (the LEAP composition check). `rules` is the impl's elementary `_RULES`."""
    hyps = "\n".join(f"- h{i} : {cg}" for i, cg in enumerate(child_goals)) or "- (no child lemmas)"
    if prior_source:
        err = "\n".join(f"- {e}" for e in (errors or [])[:8])
        lem = ("\nReal Mathlib declarations you may use (retrieved — these EXIST, prefer them over "
               "guessed names):\n" + "\n".join(f"- {x}" for x in lemmas[:15]) if lemmas else "")
        return (
            "Your previous Lean 4 DECOMPOSITION SKETCH failed to compile. Fix it.\n" + rules +
            "\nThe theorem must prove the PARENT goal ASSUMING the child lemmas as hypotheses "
            "`(h0 : <child_0>) (h1 : <child_1>) ...`. The proof BODY must be COMPLETE and SORRY-FREE — "
            "the children are HYPOTHESES you may use, NEVER `sorry` placeholders.\n"
            f"\nPREVIOUS ATTEMPT:\n```lean\n{prior_source}\n```\n\n"
            f"LEAN COMPILER ERRORS:\n{err}\n{lem}\n\n"
            f"PARENT GOAL (the conclusion to prove):\n{parent_goal}\n\n"
            f"CHILD LEMMAS (the hypotheses h0, h1, ... you may assume):\n{hyps}\n\n"
            f"INFORMAL DECOMPOSITION SKETCH (how the parent reduces to the children):\n{sketch_text}\n\n"
            "Return the CORRECTED, complete, compiling theorem. Output ONLY one fenced ```lean block.\n"
        )
    return (
        "You are formalizing a DECOMPOSITION SKETCH into a Lean 4 COMPOSITION theorem.\n"
        f"Produce a Lean 4 theorem named `{DEFAULT_THEOREM_NAME}` that proves the PARENT goal ASSUMING "
        "the child lemmas as named hypotheses `(h0 : <child_0>) (h1 : <child_1>) ...`.\n"
        "The proof BODY must be COMPLETE and SORRY-FREE: the children are HYPOTHESES you may use, NEVER "
        "`sorry` placeholders. This checks ONLY that the parent follows from the children (the LEAP "
        "composition step); the children are proved separately.\n"
        + rules +
        "\nOutput ONLY one fenced ```lean code block (imports + the theorem). No prose.\n\n"
        f"PARENT GOAL (the conclusion to prove):\n{parent_goal}\n\n"
        f"CHILD LEMMAS (the hypotheses h0, h1, ... you may assume):\n{hyps}\n\n"
        f"INFORMAL DECOMPOSITION SKETCH (how the parent reduces to the children):\n{sketch_text}\n"
    )


def _parse_sketch_result(raw: str) -> FormalizationResult:
    """Parse a sketch-formalization CLI response into a FormalizationResult (same contract as
    formalize(): a fenced ```lean block carrying a theorem/lemma)."""
    src = extract_lean(raw)
    if not src:
        return FormalizationResult(ok=False, raw=raw, notes=["no Lean code block found"])
    name = first_decl_name(src) or DEFAULT_THEOREM_NAME
    return FormalizationResult(ok=True, lean_source=src, theorem_name=name, raw=raw)


class CodexFormalizer:
    def __init__(self, toolkit: Toolkit, cfg: Optional[CodexConfig] = None):
        self.toolkit = toolkit
        self.cfg = cfg or CodexConfig()

    _RULES = (
        "Use ELEMENTARY methods only — the proof is machine-audited and will be REJECTED if it "
        "transitively depends on class groups, Dedekind domains, number fields, elliptic curves, "
        "modular forms, cyclotomic theory, Mihailescu/Catalan, or Baker's theorem.\n"
        "Prefer core/Std and elementary Mathlib lemmas (`Nat`/`Int` arithmetic, `Nat.gcd`, `ZMod`, "
        "`Int.ModEq`, `omega`, `decide`, `interval_cases`, `Nat.Prime`). If you need Mathlib, put "
        "`import Mathlib` as the FIRST line. Keep the statement faithful to the informal claim.\n"
        "TACTIC DISCIPLINE (critical — most failures come from misusing `decide`): a QUANTIFIED goal "
        "over ℤ or ℕ ranges over an INFINITE domain and is NOT decidable, so `decide`/`Decidable` "
        "synthesis on the whole goal FAILS. For a claim modulo m, REDUCE to the finite ring `ZMod m`: "
        "prove the finite core (e.g. `∀ x : ZMod m, P x` or `∀ x y : ZMod m, …`) by `decide` (it IS "
        "decidable there), then bridge ℤ↔`ZMod m` with `ZMod.intCast_zmod_eq_zero_iff_dvd`, "
        "`Int.cast`, `push_cast`, and `exact_mod_cast`. Use `omega` for linear integer/nat arithmetic "
        "and goals with `∣`/`%`/`<`, and `interval_cases` for bounded ranges. Reserve `decide` for "
        "genuinely FINITE, decidable subgoals ONLY. For infinite descent / minimal-counterexample, use "
        "strong induction or `Nat.find`/well-founded recursion on a NONNEGATIVE integer measure.\n"
        "No `sorry`, no `admit`, no new `axiom`s, no `unsafe`/`native_decide`.\n"
    )

    def _fresh_prompt(self, ledger_text: str) -> str:
        return (
            "You are formalizing an ALREADY-VERIFIED elementary number-theory proof into Lean 4.\n"
            f"Produce a Lean 4 theorem named `{DEFAULT_THEOREM_NAME}` with a COMPLETE, compiling proof.\n"
            + self._RULES +
            "\nOutput ONLY one fenced ```lean code block (imports + the theorem). No prose.\n\n"
            f"INFORMAL STEP-LEDGER (a gate-passed elementary proof):\n{ledger_text}\n"
        )

    def _repair_prompt(self, ledger_text: str, prior_source: str,
                       errors: list[str], lemmas: Optional[list[str]]) -> str:
        err = "\n".join(f"- {e}" for e in (errors or [])[:8])
        lem = ("\nReal Mathlib declarations you may use (retrieved — these EXIST, prefer them over "
               "guessed names):\n" + "\n".join(f"- {x}" for x in lemmas[:15]) if lemmas else "")
        return (
            "Your previous Lean 4 formalization FAILED to compile. Fix it.\n" + self._RULES +
            f"\nPREVIOUS ATTEMPT:\n```lean\n{prior_source}\n```\n\n"
            f"LEAN COMPILER ERRORS:\n{err}\n{lem}\n\n"
            "Return the CORRECTED, complete, compiling theorem. Keep the name and the (faithful) "
            f"statement; only fix what's broken. Output ONLY one fenced ```lean code block.\n\n"
            f"INFORMAL STEP-LEDGER (the proof to formalize):\n{ledger_text}\n"
        )

    def formalize(self, ledger_text: str, prior_source: Optional[str] = None,
                  errors: Optional[list[str]] = None,
                  lemmas: Optional[list[str]] = None) -> FormalizationResult:
        if prior_source:
            prompt = self._repair_prompt(ledger_text, prior_source, errors or [], lemmas)
        else:
            prompt = self._fresh_prompt(ledger_text)
        raw = _run_codex(prompt, self.cfg)
        src = extract_lean(raw)
        if not src:
            return FormalizationResult(ok=False, raw=raw, notes=["no Lean code block found"])
        name = first_decl_name(src) or DEFAULT_THEOREM_NAME
        return FormalizationResult(ok=True, lean_source=src, theorem_name=name, raw=raw)

    # ---- P4: AND-node sorry-sketch compilation (LEAP §2.2/§2.5 composition) ----------------------
    def _sketch_prompt(self, parent_goal: str, sketch_text: str, child_goals: list[str],
                       prior_source: Optional[str] = None, errors: Optional[list[str]] = None,
                       lemmas: Optional[list[str]] = None) -> str:
        return _sketch_prompt(self._RULES, parent_goal, sketch_text, child_goals,
                              prior_source, errors, lemmas)

    def formalize_sketch(self, parent_goal: str, sketch_text: str, child_goals: list[str],
                         prior_source: Optional[str] = None, errors: Optional[list[str]] = None,
                         lemmas: Optional[list[str]] = None) -> FormalizationResult:
        """Formalize a DECOMPOSITION sketch as the LEAP composition check (MODE A): emit a SORRY-FREE
        Lean theorem that proves `parent_goal` ASSUMING the `child_goals` as named hypotheses
        ``(h0 : <child_0>) (h1 : <child_1>) ...``. The children are HYPOTHESES, never ``sorry`` — so the
        emitted body is audit-clean and ``audit_lean_source`` applies VERBATIM. Mirrors ``formalize()``
        (same CLI, ``_RULES``, parsing, repair contract); ``formalize()`` semantics are UNCHANGED."""
        prompt = self._sketch_prompt(parent_goal, sketch_text, child_goals,
                                     prior_source, errors, lemmas)
        raw = _run_codex(prompt, self.cfg)
        return _parse_sketch_result(raw)


class ClaudeFormalizer:
    """Opus-backed twin of :class:`CodexFormalizer` — same prompts/_RULES/parsing/repair contract and
    same :class:`FormalizationResult`, but generates via the headless Claude CLI (``--model opus``)
    instead of Codex. A drop-in for the same ``Formalizer`` Protocol used by ``formalize_and_audit`` /
    ``make_terminal_gate``."""

    def __init__(self, toolkit: Toolkit, cfg: Optional[ClaudeConfig] = None):
        self.toolkit = toolkit
        self.cfg = cfg or ClaudeConfig(model="opus")

    # Identical rule block to CodexFormalizer: the elementary/faithfulness contract is the same
    # regardless of which model generates the Lean.
    _RULES = CodexFormalizer._RULES

    def _fresh_prompt(self, ledger_text: str) -> str:
        return (
            "You are formalizing an ALREADY-VERIFIED elementary number-theory proof into Lean 4.\n"
            f"Produce a Lean 4 theorem named `{DEFAULT_THEOREM_NAME}` with a COMPLETE, compiling proof.\n"
            + self._RULES +
            "\nOutput ONLY one fenced ```lean code block (imports + the theorem). No prose.\n\n"
            f"INFORMAL STEP-LEDGER (a gate-passed elementary proof):\n{ledger_text}\n"
        )

    def _repair_prompt(self, ledger_text: str, prior_source: str,
                       errors: list[str], lemmas: Optional[list[str]]) -> str:
        err = "\n".join(f"- {e}" for e in (errors or [])[:8])
        lem = ("\nReal Mathlib declarations you may use (retrieved — these EXIST, prefer them over "
               "guessed names):\n" + "\n".join(f"- {x}" for x in lemmas[:15]) if lemmas else "")
        return (
            "Your previous Lean 4 formalization FAILED to compile. Fix it.\n" + self._RULES +
            f"\nPREVIOUS ATTEMPT:\n```lean\n{prior_source}\n```\n\n"
            f"LEAN COMPILER ERRORS:\n{err}\n{lem}\n\n"
            "Return the CORRECTED, complete, compiling theorem. Keep the name and the (faithful) "
            f"statement; only fix what's broken. Output ONLY one fenced ```lean code block.\n\n"
            f"INFORMAL STEP-LEDGER (the proof to formalize):\n{ledger_text}\n"
        )

    def formalize(self, ledger_text: str, prior_source: Optional[str] = None,
                  errors: Optional[list[str]] = None,
                  lemmas: Optional[list[str]] = None) -> FormalizationResult:
        if prior_source:
            prompt = self._repair_prompt(ledger_text, prior_source, errors or [], lemmas)
        else:
            prompt = self._fresh_prompt(ledger_text)
        raw = _run_claude(prompt, self.cfg)
        src = extract_lean(raw)
        if not src:
            return FormalizationResult(ok=False, raw=raw, notes=["no Lean code block found"])
        name = first_decl_name(src) or DEFAULT_THEOREM_NAME
        return FormalizationResult(ok=True, lean_source=src, theorem_name=name, raw=raw)

    # ---- P4: AND-node sorry-sketch compilation (mirror of CodexFormalizer.formalize_sketch) -------
    def _sketch_prompt(self, parent_goal: str, sketch_text: str, child_goals: list[str],
                       prior_source: Optional[str] = None, errors: Optional[list[str]] = None,
                       lemmas: Optional[list[str]] = None) -> str:
        return _sketch_prompt(self._RULES, parent_goal, sketch_text, child_goals,
                              prior_source, errors, lemmas)

    def formalize_sketch(self, parent_goal: str, sketch_text: str, child_goals: list[str],
                         prior_source: Optional[str] = None, errors: Optional[list[str]] = None,
                         lemmas: Optional[list[str]] = None) -> FormalizationResult:
        """Opus twin of ``CodexFormalizer.formalize_sketch``: emit a SORRY-FREE Lean theorem proving
        ``parent_goal`` ASSUMING the ``child_goals`` as named hypotheses (the LEAP §2.2/§2.5 composition
        check). Same prompt/_RULES/parsing/repair contract; generates via the Claude CLI."""
        prompt = self._sketch_prompt(parent_goal, sketch_text, child_goals,
                                     prior_source, errors, lemmas)
        raw = _run_claude(prompt, self.cfg)
        return _parse_sketch_result(raw)


class ScriptedFormalizer:
    """Returns a fixed Lean source, or a SEQUENCE (one per formalize call) to drive a repair loop."""

    def __init__(self, lean_source: str | list[str], theorem_name: str = DEFAULT_THEOREM_NAME,
                 ok: bool = True):
        self._sources = [lean_source] if isinstance(lean_source, str) else list(lean_source)
        self.theorem_name = theorem_name
        self.ok = ok
        self.calls = 0
        self.repair_calls = 0

    def formalize(self, ledger_text: str, prior_source: Optional[str] = None,
                  errors: Optional[list[str]] = None,
                  lemmas: Optional[list[str]] = None) -> FormalizationResult:
        if prior_source:
            self.repair_calls += 1
        idx = min(self.calls, len(self._sources) - 1)
        self.calls += 1
        return FormalizationResult(ok=self.ok, lean_source=self._sources[idx],
                                   theorem_name=self.theorem_name)

    def formalize_sketch(self, parent_goal: str, sketch_text: str, child_goals: list[str],
                         prior_source: Optional[str] = None, errors: Optional[list[str]] = None,
                         lemmas: Optional[list[str]] = None) -> FormalizationResult:
        """Sketch-formalization twin: returns the same canned source(s), advancing the same counters so
        a scripted repair loop can be driven (mirrors formalize())."""
        if prior_source:
            self.repair_calls += 1
        idx = min(self.calls, len(self._sources) - 1)
        self.calls += 1
        return FormalizationResult(ok=self.ok, lean_source=self._sources[idx],
                                   theorem_name=self.theorem_name)
