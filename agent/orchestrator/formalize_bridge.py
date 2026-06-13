"""Close the loop: informal step-ledger -> Lean -> compile -> Layer-4 audit -> verdict.

`formalize_and_audit` formalizes a gate-passed ledger (via a `Formalizer`, e.g. Codex), compiles it,
and runs the authoritative Lean dependency/axiom audit on the resulting proof term.

`full_verify` runs the whole stack: the fast informal gate (Layers 0-3) AND the Lean Layer-4 audit,
and reports `authoritative_elementary` = (informal gate admitted) AND (Lean proof compiled and passed
the dependency audit). This is the only place where "elementary" is *enforced*, not merely pressured.

Autoformalization is the wall: a proof may fail to compile or a statement may be unfaithful. Those
outcomes are reported honestly (compiled=False / a recorded error), never silently passed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from agent.gates import lean_bridge
from agent.gates.gate import GateReport, evaluate
from agent.gates.lean_audit import LeanAuditResult
from agent.gates.toolkit import Toolkit, load_toolkit
from agent.tools.formalizer import FormalizationResult


@runtime_checkable
class Formalizer(Protocol):
    def formalize(self, ledger_text: str) -> FormalizationResult:
        ...


@dataclass
class FormalizeAuditResult:
    formalized: bool
    compiled: bool = False
    theorem_name: str = ""
    lean_source: Optional[str] = None
    audit: Optional[LeanAuditResult] = None
    error: Optional[str] = None
    notes: list[str] = field(default_factory=list)

    @property
    def elementary_verified(self) -> bool:
        """True only if the proof compiled AND passed the Lean dependency/axiom audit."""
        return bool(self.compiled and self.audit is not None and self.audit.passed)

    def summary(self) -> str:
        if not self.formalized:
            return "formalize: failed (no Lean produced)"
        if not self.compiled:
            return f"formalize: ok, compile/audit: failed ({self.error})"
        return f"formalize: ok, compiled, {self.audit.summary()}"


def formalize_and_audit(ledger_text: str, formalizer: Formalizer,
                        toolkit: Optional[Toolkit] = None,
                        project_dir: Optional[str | Path] = None,
                        timeout_s: int = 600) -> FormalizeAuditResult:
    toolkit = toolkit or load_toolkit()
    # Default to the repo's Mathlib project so formalized `import Mathlib` proofs resolve; a core-only
    # proof still compiles fine under `lake env lean` (Mathlib on the path but unused).
    if project_dir is None:
        project_dir = lean_bridge.find_mathlib_project()
    fr = formalizer.formalize(ledger_text)
    if not fr.ok:
        return FormalizeAuditResult(formalized=False, notes=fr.notes)

    try:
        audit = lean_bridge.audit_lean_source(
            fr.lean_source, fr.theorem_name, toolkit=toolkit,
            project_dir=project_dir, timeout_s=timeout_s)
    except (lean_bridge.LeanBridgeError, lean_bridge.LeanUnavailable) as e:
        return FormalizeAuditResult(formalized=True, compiled=False,
                                    theorem_name=fr.theorem_name, lean_source=fr.lean_source,
                                    error=str(e))
    return FormalizeAuditResult(formalized=True, compiled=True,
                                theorem_name=fr.theorem_name, lean_source=fr.lean_source,
                                audit=audit)


@dataclass
class FullVerifyResult:
    gate: GateReport
    lean: Optional[FormalizeAuditResult] = None

    @property
    def authoritative_elementary(self) -> bool:
        return bool(self.gate.admitted_deterministically
                    and self.lean is not None and self.lean.elementary_verified)

    def summary(self) -> str:
        g = self.gate.summary()
        l = self.lean.summary() if self.lean else "lean: skipped (informal gate rejected)"
        return f"[informal] {g} | [lean] {l} | authoritative_elementary={self.authoritative_elementary}"


def full_verify(ledger_text: str, formalizer: Formalizer,
                toolkit: Optional[Toolkit] = None,
                project_dir: Optional[str | Path] = None,
                timeout_s: int = 600) -> FullVerifyResult:
    toolkit = toolkit or load_toolkit()
    gate = evaluate(ledger_text, toolkit)
    if gate.rejected:
        return FullVerifyResult(gate=gate, lean=None)
    lean = formalize_and_audit(ledger_text, formalizer, toolkit=toolkit,
                               project_dir=project_dir, timeout_s=timeout_s)
    return FullVerifyResult(gate=gate, lean=lean)
