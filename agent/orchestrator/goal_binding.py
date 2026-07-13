"""Cycle-neutral, soundness-critical binding between a requested goal and a proof ledger."""
from __future__ import annotations

from agent.gates.ledger import Ledger, LedgerError, parse_ledger
from agent.orchestrator.dag import goal_hash


def goal_binding_mismatches(ledger: Ledger | str, goal: str) -> list[str]:
    """Return soundness-bearing ledger fields that do not bind to ``goal``.

    ``problem`` is deliberately absent: the public schema and prover contract define it as a dataset
    identifier, not a theorem statement. Authority is carried by the stated ``claim`` and the unique
    operative ``conclusion`` step.
    """
    if isinstance(ledger, str):
        try:
            parsed = parse_ledger(ledger)
        except LedgerError:
            return ["ledger"]
    elif isinstance(ledger, Ledger):
        parsed = ledger
    else:
        return ["ledger"]

    wanted = goal_hash(goal)
    mismatches: list[str] = []
    if goal_hash(parsed.claim) != wanted:
        mismatches.append("claim")
    conclusions = [step for step in parsed.steps if step.justification == "conclusion"]
    if len(conclusions) != 1 or goal_hash(conclusions[0].claim) != wanted:
        mismatches.append("conclusion")
    return mismatches


def proves_goal(ledger: Ledger | str, goal: str) -> bool:
    """Return whether ``ledger``'s stated and operative conclusions both prove ``goal``.

    The top-level claim alone is not authoritative: a malformed/adversarial certificate can name the
    requested goal in metadata while its terminal conclusion proves another statement.  Requiring one
    terminal conclusion and binding both fields keeps Flat, Ralph, DAG promotion, and the independent
    verifier on exactly one implementation.
    """
    return not goal_binding_mismatches(ledger, goal)
