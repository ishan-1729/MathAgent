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


class CodexFormalizer:
    def __init__(self, toolkit: Toolkit, cfg: Optional[CodexConfig] = None):
        self.toolkit = toolkit
        self.cfg = cfg or CodexConfig()

    def _prompt(self, ledger_text: str) -> str:
        return (
            "You are formalizing an ALREADY-VERIFIED elementary number-theory proof into Lean 4.\n"
            f"Produce a Lean 4 theorem named `{DEFAULT_THEOREM_NAME}` with a COMPLETE, compiling proof: "
            "no `sorry`, no `admit`, no new `axiom`s, no `unsafe`/`native_decide`.\n"
            "Use ELEMENTARY methods only — the proof is machine-audited and will be REJECTED if it "
            "transitively depends on class groups, Dedekind domains, number fields, elliptic curves, "
            "modular forms, cyclotomic theory, Mihailescu/Catalan, or Baker's theorem.\n"
            "Prefer core/Std and elementary Mathlib lemmas (`Nat`/`Int` arithmetic, `Nat.gcd`, `ZMod`, "
            "`Int.ModEq`, `omega`, `decide`, `interval_cases`, `Nat.Prime`). If you need Mathlib, put "
            "`import Mathlib` as the FIRST line. Keep the statement faithful to the informal claim.\n\n"
            "Output ONLY one fenced ```lean code block (imports + the theorem). No prose.\n\n"
            f"INFORMAL STEP-LEDGER (a gate-passed elementary proof):\n{ledger_text}\n"
        )

    def formalize(self, ledger_text: str) -> FormalizationResult:
        raw = _run_codex(self._prompt(ledger_text), self.cfg)
        src = extract_lean(raw)
        if not src:
            return FormalizationResult(ok=False, raw=raw, notes=["no Lean code block found"])
        name = first_decl_name(src) or DEFAULT_THEOREM_NAME
        return FormalizationResult(ok=True, lean_source=src, theorem_name=name, raw=raw)


class ScriptedFormalizer:
    """Returns a fixed Lean source (for tests/offline demos)."""

    def __init__(self, lean_source: str, theorem_name: str = DEFAULT_THEOREM_NAME, ok: bool = True):
        self.lean_source = lean_source
        self.theorem_name = theorem_name
        self.ok = ok

    def formalize(self, ledger_text: str) -> FormalizationResult:
        return FormalizationResult(ok=self.ok, lean_source=self.lean_source,
                                   theorem_name=self.theorem_name)
