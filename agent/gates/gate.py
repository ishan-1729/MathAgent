"""Compose the deterministic gate layers into a single report.

`evaluate` runs the parse + Layers 1a / 1b / 3 that need no LLM and no Lean. It returns a
GateReport whose verdict is:
  - REJECTED  if any REJECT finding fired (deterministic, authoritative failure), or the ledger
              could not be parsed;
  - NEEDS_REVIEW if it passed all deterministic checks but has REVIEW flags (route to Layer 2);
  - PASSED_DETERMINISTIC if it passed everything here with no review flags.

Note what this is NOT: passing here does NOT certify "elementary" — that is only enforced by the
Lean dependency audit (Layer 4), which is not part of this function. See PLAN.md Section 5.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Optional

from agent.gates.ledger import Ledger, LedgerError, parse_ledger, validate_structure
from agent.gates.obligations import check_obligations
from agent.gates.scanner import scan_prose
from agent.gates.report import Finding, Severity, LAYER_STRUCTURE
from agent.gates.toolkit import Toolkit, load_toolkit

# Re-export so callers can `from agent.gates.gate import Finding`.
__all__ = ["Verdict", "GateReport", "Finding", "evaluate"]


class Verdict(enum.Enum):
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"
    PASSED_DETERMINISTIC = "passed_deterministic"


@dataclass
class GateReport:
    verdict: Verdict
    findings: list[Finding] = field(default_factory=list)
    ledger: Optional[Ledger] = None
    parse_error: Optional[str] = None

    @property
    def rejected(self) -> bool:
        return self.verdict is Verdict.REJECTED

    @property
    def admitted_deterministically(self) -> bool:
        """True iff nothing deterministic rejected it (it may still need Layer-2 review)."""
        return self.verdict in (Verdict.PASSED_DETERMINISTIC, Verdict.NEEDS_REVIEW)

    def rejects(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.REJECT]

    def reviews(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.REVIEW]

    def summary(self) -> str:
        r, v = len(self.rejects()), len(self.reviews())
        return f"{self.verdict.value} (rejects={r}, reviews={v})"


def evaluate(source, toolkit: Optional[Toolkit] = None) -> GateReport:
    """Run the deterministic gate over ledger `source` (a dict or text)."""
    toolkit = toolkit or load_toolkit()

    # Parse (Layer 1a entry): a malformed ledger is a deterministic REJECT.
    try:
        ledger = parse_ledger(source)
    except LedgerError as e:
        return GateReport(
            verdict=Verdict.REJECTED,
            findings=[Finding(LAYER_STRUCTURE, Severity.REJECT, "parse_error", str(e))],
            parse_error=str(e),
        )

    findings: list[Finding] = []
    findings += validate_structure(ledger, toolkit)   # Layer 1a (deterministic)
    findings += check_obligations(ledger, toolkit)    # Layer 1a/3 (deterministic)
    findings += scan_prose(ledger, toolkit)           # Layer 1b (soft)

    if any(f.severity is Severity.REJECT for f in findings):
        verdict = Verdict.REJECTED
    elif any(f.severity is Severity.REVIEW for f in findings):
        verdict = Verdict.NEEDS_REVIEW
    else:
        verdict = Verdict.PASSED_DETERMINISTIC

    return GateReport(verdict=verdict, findings=findings, ledger=ledger)
