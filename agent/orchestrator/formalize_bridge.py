"""Close the loop: informal step-ledger -> Lean -> compile -> Layer-4 audit -> faithfulness -> verdict.

`formalize_and_audit` formalizes a gate-passed ledger (via a `Formalizer`, e.g. Codex), compiles it,
runs the authoritative Lean dependency/axiom audit on the proof term, and (optionally) runs an
ADVERSARIAL statement-faithfulness check so a compiling/audited proof of the WRONG statement is caught.

`full_verify` runs the whole stack: the fast informal gate (Layers 0-3) AND Lean Layer 4 AND
faithfulness, reporting `authoritative_elementary` = informal-gate-admitted AND compiled AND
dependency-audit-passed AND statement-faithful. This is the only place where "elementary" is *enforced*.

`make_terminal_gate` packages this as a callable for use as the DAG driver's terminal gate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Protocol, runtime_checkable

from agent.gates import lean_bridge
from agent.gates.gate import GateReport, evaluate
from agent.gates.ledger import parse_ledger, LedgerError
from agent.gates.lean_audit import LeanAuditResult
from agent.gates.toolkit import Toolkit, load_toolkit
from agent.orchestrator.faithfulness import FaithfulnessVerdict
from agent.tools.formalizer import FormalizationResult


@runtime_checkable
class Formalizer(Protocol):
    def formalize(self, ledger_text: str) -> FormalizationResult:
        ...


@runtime_checkable
class FaithfulnessChecker(Protocol):
    def check(self, informal_claim: str, lean_source: str, theorem_name: str) -> FaithfulnessVerdict:
        ...


@dataclass
class FormalizeAuditResult:
    formalized: bool
    compiled: bool = False
    theorem_name: str = ""
    lean_source: Optional[str] = None
    audit: Optional[LeanAuditResult] = None
    faithfulness: Optional[FaithfulnessVerdict] = None
    error: Optional[str] = None
    notes: list[str] = field(default_factory=list)

    @property
    def elementary_verified(self) -> bool:
        """Compiled AND passed the Lean dependency/axiom audit."""
        return bool(self.compiled and self.audit is not None and self.audit.passed)

    @property
    def faithful(self) -> bool:
        """True if faithfulness was not checked (None) or was checked and passed."""
        return self.faithfulness is None or self.faithfulness.faithful

    @property
    def authoritative(self) -> bool:
        """The proof is authoritatively elementary: audited elementary AND the statement is faithful."""
        return self.elementary_verified and self.faithful

    def summary(self) -> str:
        if not self.formalized:
            return "formalize: failed (no Lean produced)"
        if not self.compiled:
            return f"formalize: ok, compile/audit: failed ({self.error})"
        f = "n/a" if self.faithfulness is None else self.faithfulness.summary()
        return f"formalize: ok, compiled, {self.audit.summary()}, faithfulness[{f}], authoritative={self.authoritative}"


def _claim_of(ledger_text: str) -> str:
    try:
        return parse_ledger(ledger_text).claim
    except LedgerError:
        return ledger_text


def formalize_and_audit(ledger_text: str, formalizer: Formalizer,
                        toolkit: Optional[Toolkit] = None,
                        project_dir: Optional[str | Path] = None,
                        timeout_s: int = 600,
                        informal_claim: Optional[str] = None,
                        faithfulness_checker: Optional[FaithfulnessChecker] = None,
                        server: Optional[object] = None) -> FormalizeAuditResult:
    toolkit = toolkit or load_toolkit()
    if project_dir is None and server is None:
        project_dir = lean_bridge.find_mathlib_project()
    fr = formalizer.formalize(ledger_text)
    if not fr.ok:
        return FormalizeAuditResult(formalized=False, notes=fr.notes)

    try:
        audit = lean_bridge.audit_lean_source(
            fr.lean_source, fr.theorem_name, toolkit=toolkit,
            project_dir=project_dir, timeout_s=timeout_s, server=server)
    except (lean_bridge.LeanBridgeError, lean_bridge.LeanUnavailable) as e:
        return FormalizeAuditResult(formalized=True, compiled=False,
                                    theorem_name=fr.theorem_name, lean_source=fr.lean_source,
                                    error=str(e))

    faith: Optional[FaithfulnessVerdict] = None
    if faithfulness_checker is not None:
        claim = informal_claim if informal_claim is not None else _claim_of(ledger_text)
        faith = faithfulness_checker.check(claim, fr.lean_source, fr.theorem_name)

    return FormalizeAuditResult(formalized=True, compiled=True,
                                theorem_name=fr.theorem_name, lean_source=fr.lean_source,
                                audit=audit, faithfulness=faith)


@dataclass
class FullVerifyResult:
    gate: GateReport
    lean: Optional[FormalizeAuditResult] = None

    @property
    def authoritative_elementary(self) -> bool:
        return bool(self.gate.admitted_deterministically
                    and self.lean is not None and self.lean.authoritative)

    def summary(self) -> str:
        g = self.gate.summary()
        l = self.lean.summary() if self.lean else "lean: skipped (informal gate rejected)"
        return f"[informal] {g} | [lean] {l} | authoritative_elementary={self.authoritative_elementary}"


def full_verify(ledger_text: str, formalizer: Formalizer,
                toolkit: Optional[Toolkit] = None,
                project_dir: Optional[str | Path] = None,
                timeout_s: int = 600,
                faithfulness_checker: Optional[FaithfulnessChecker] = None,
                server: Optional[object] = None) -> FullVerifyResult:
    toolkit = toolkit or load_toolkit()
    gate = evaluate(ledger_text, toolkit)
    if gate.rejected:
        return FullVerifyResult(gate=gate, lean=None)
    lean = formalize_and_audit(ledger_text, formalizer, toolkit=toolkit,
                               project_dir=project_dir, timeout_s=timeout_s,
                               informal_claim=_claim_of(ledger_text),
                               faithfulness_checker=faithfulness_checker, server=server)
    return FullVerifyResult(gate=gate, lean=lean)


def make_terminal_gate(formalizer: Formalizer, toolkit: Optional[Toolkit] = None,
                       faithfulness_checker: Optional[FaithfulnessChecker] = None,
                       project_dir: Optional[str | Path] = None,
                       server: Optional[object] = None,
                       timeout_s: int = 600) -> Callable[[str, str], FormalizeAuditResult]:
    """A terminal gate for DagDriver: (root_goal, proof_text) -> FormalizeAuditResult.

    Formalizes the assembled proof, audits it (Layer 4), and checks the statement faithfully captures
    the root goal. The DagDriver treats the run as authoritatively elementary iff this passes.
    """
    toolkit = toolkit or load_toolkit()

    def gate(root_goal: str, proof_text: str) -> FormalizeAuditResult:
        return formalize_and_audit(proof_text, formalizer, toolkit=toolkit,
                                   project_dir=project_dir, timeout_s=timeout_s,
                                   informal_claim=root_goal,
                                   faithfulness_checker=faithfulness_checker, server=server)

    return gate
