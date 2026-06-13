"""Shared result types for the gate layers (kept separate to avoid import cycles)."""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional


class Severity(enum.Enum):
    """How a finding affects the verdict.

    REJECT  - deterministic, authoritative failure (the proof is structurally/numerically wrong
              or names a banned method in a checkable way). The proof cannot be admitted.
    REVIEW  - a soft flag routed to adversarial LLM review (Layer 2). NOT authoritative on its own
              (e.g. a prose denylist keyword hit, or use of an "elastic" justification).
    INFO    - advisory note (e.g. an orphan step); does not block.
    """

    REJECT = "reject"
    REVIEW = "review"
    INFO = "info"


# Layer identifiers (see PLAN.md Section 5).
LAYER_STRUCTURE = "1a-structure"   # deterministic typed-ledger validation
LAYER_SCAN = "1b-scan"             # soft prose denylist router
LAYER_OBLIGATION = "1a-obligation"  # deterministic obligation presence/shape
LAYER_NUMERIC = "3-numeric"        # deterministic numeric grounding


@dataclass(frozen=True)
class Finding:
    layer: str
    severity: Severity
    code: str
    message: str
    step_id: Optional[str] = None

    def __str__(self) -> str:
        loc = f" [{self.step_id}]" if self.step_id else ""
        return f"{self.severity.value.upper()} {self.layer} {self.code}{loc}: {self.message}"
