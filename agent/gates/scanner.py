"""Layer 1b: soft prose denylist scanner + elastic-justification router.

This is explicitly NOT a deterministic gate (a euphemism scan cannot be sound). Every finding here
is severity REVIEW: it routes a proof to adversarial LLM review (Layer 2), it never rejects on its
own. Two jobs:

  1. Flag denylist phrases in ledger prose (e.g. "class group", "elliptic curve"), respecting an
     allow-context list of overloaded NT words.
  2. Route steps using "elastic" justifications (bounding, factorization) to mandatory review, since
     those tags can hide heavy steps (PLAN.md Section 5, Layer 1).
"""
from __future__ import annotations

from agent.gates.ledger import Ledger
from agent.gates.report import Finding, Severity, LAYER_SCAN
from agent.gates.toolkit import Toolkit

# Justifications whose content is broad enough to hide a non-elementary or unsound step.
# `descent`/`vieta_jumping` are included because PLAN.md flags them as the central NT method and the
# prime smuggling site: their decrease/domain obligations are partly self-asserted, so any use is
# routed to adversarial review even when it passes the deterministic obligation checks.
ELASTIC_JUSTIFICATIONS = {"bounding", "factorization", "squeeze", "descent", "vieta_jumping"}


def _texts(ledger: Ledger):
    yield None, ledger.claim
    for s in ledger.steps:
        yield s.id, s.claim
        if s.method_ref:
            yield s.id, s.method_ref


def scan_prose(ledger: Ledger, toolkit: Toolkit) -> list[Finding]:
    findings: list[Finding] = []
    terms = toolkit.prose_terms
    # NOTE: toolkit.allow_context_terms ("order", "unit", "norm", ...) are documented in denylist.yaml
    # as overloaded words the scanner must NOT flag on their own — but that suppression is enforced by
    # CONSTRUCTION, not by a runtime check: every prose_term is a MULTI-WORD phrase ("class group",
    # "elliptic curve", ...) disjoint from the single-word allow vocabulary, so a bare allow word can
    # never equal or be contained in a prose_term and thus never produces a hit to suppress. (The prior
    # `if any(term in ctx or ctx == term for ctx in allow)` suppression clause compared a multi-word
    # phrase against short allow words, so it could never fire and never referenced the scanned prose;
    # it was dead and is removed.)

    for step_id, text in _texts(ledger):
        low = text.lower()
        for term in terms:
            if term in low:
                findings.append(Finding(
                    LAYER_SCAN, Severity.REVIEW, "denylist_term",
                    f"prose mentions denylisted term {term!r}; route to adversarial review",
                    step_id,
                ))

    for s in ledger.steps:
        if s.justification in ELASTIC_JUSTIFICATIONS:
            findings.append(Finding(
                LAYER_SCAN, Severity.REVIEW, "elastic_justification",
                f"justification {s.justification!r} is elastic; mandatory adversarial review",
                s.id,
            ))
    return findings
