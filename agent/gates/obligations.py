"""Validate the discharged proof-obligations attached to ledger steps.

A method tag alone ("descent", "case_split") carries none of the load-bearing content; this module
checks the *obligations* that make those tags meaningful, and re-runs the finite ones numerically
rather than trusting prose (PLAN.md Section 5, Layer 1/3):

  - case_cover       : the enumerated residues must cover a complete residue system (numeric, REJECT)
  - descent          : must assert strict decrease + staying in domain; optional numeric spot-check
  - split_coprimality: must reference a prior gcd/coprimality step that is an *ancestor* (REJECT)

Presence of a required obligation is enforced upstream in ledger.validate_structure; here we check
its *content*. Every numeric call is wrapped so a malformed-but-schema-valid obligation becomes a
deterministic REJECT, never an uncaught exception.
"""
from __future__ import annotations

from agent.gates.ledger import Ledger, reachable_from
from agent.gates.report import (
    Finding,
    Severity,
    LAYER_NUMERIC,
    LAYER_OBLIGATION,
)
from agent.gates.toolkit import Toolkit
from agent.tools import numeric


def _check_case_cover(step_id: str, ob: dict) -> list[Finding]:
    cc = ob.get("case_cover")
    if not cc:
        return []
    try:
        res = numeric.verify_residue_cover(cc["modulus"], cc["residues"])
    except (numeric.NumericError, KeyError, TypeError) as e:
        return [Finding(LAYER_NUMERIC, Severity.REJECT, "case_cover_error", str(e), step_id)]
    if res.modulus < 2:
        return [Finding(LAYER_NUMERIC, Severity.REJECT, "case_cover_vacuous",
                        f"case split modulus {res.modulus} is vacuous (need >= 2)", step_id)]
    if not res.ok:
        return [Finding(LAYER_NUMERIC, Severity.REJECT, "case_cover_incomplete",
                        f"case split mod {res.modulus} misses residues {res.missing}", step_id)]
    return []


def _check_descent(step_id: str, ob: dict) -> list[Finding]:
    d = ob.get("descent")
    if not d:
        return []
    findings: list[Finding] = []
    if d.get("strictly_decreases") is not True:
        findings.append(Finding(LAYER_OBLIGATION, Severity.REJECT, "descent_not_decreasing",
                                "descent obligation does not assert strictly_decreases=true", step_id))
    if d.get("stays_in_domain") is not True:
        findings.append(Finding(LAYER_OBLIGATION, Severity.REJECT, "descent_leaves_domain",
                                "descent obligation does not assert stays_in_domain=true", step_id))

    # Optional numeric spot-check: if the prover supplied concrete measure/next expressions and a
    # sample box, confirm next < measure everywhere in the box (i.e. next - measure is never >= 0).
    me, ne = d.get("measure_expr"), d.get("next_expr")
    variables, sample_bounds = d.get("variables"), d.get("sample_bounds")
    if me and ne and variables and sample_bounds:
        # Coerce list-bounds (JSON arrays) to tuples; missing entries -> deterministic REJECT.
        missing = [v for v in variables if v not in sample_bounds]
        if missing:
            findings.append(Finding(LAYER_NUMERIC, Severity.REJECT, "descent_expr_error",
                                    f"sample_bounds missing variables {missing}", step_id))
            return findings
        box = {v: tuple(sample_bounds[v]) for v in variables}
        try:
            ge_zero = numeric.find_points_where_nonneg(f"({ne}) - ({me})", variables, box)
        except (numeric.NumericError, TypeError, ValueError) as e:
            findings.append(Finding(LAYER_NUMERIC, Severity.REJECT, "descent_expr_error", str(e), step_id))
            return findings
        if ge_zero:
            findings.append(Finding(LAYER_NUMERIC, Severity.REJECT, "descent_no_decrease",
                                    f"measure does not strictly decrease at e.g. {ge_zero[0]}", step_id))
    return findings


def _check_split_coprimality(ledger: Ledger, step_id: str, ob: dict) -> list[Finding]:
    sc = ob.get("split_coprimality")
    if not sc:
        return []
    ref = sc.get("coprimality_from")
    target = ledger.by_id(ref) if ref else None
    if target is None:
        return [Finding(LAYER_OBLIGATION, Severity.REJECT, "split_coprimality_dangling",
                        f"split_coprimality references unknown step {ref!r}", step_id)]
    if target.justification != "gcd_coprimality":
        return [Finding(LAYER_OBLIGATION, Severity.REJECT, "split_coprimality_wrong_kind",
                        f"split relies on {ref!r} but that step is {target.justification!r}, "
                        f"not 'gcd_coprimality'", step_id)]
    # The cited coprimality must actually be a premise of this step (a transitive ancestor),
    # otherwise the split relies on coprimality that was never established for this product.
    ancestors = reachable_from(ledger, [step_id]) - {step_id}
    if ref not in ancestors:
        return [Finding(LAYER_OBLIGATION, Severity.REJECT, "split_coprimality_unrelated",
                        f"split cites {ref!r}, which is not an ancestor (premise) of this step", step_id)]
    return []


def check_obligations(ledger: Ledger, toolkit: Toolkit) -> list[Finding]:
    findings: list[Finding] = []
    for s in ledger.steps:
        if not s.obligations:
            continue
        findings += _check_case_cover(s.id, s.obligations)
        findings += _check_descent(s.id, s.obligations)
        findings += _check_split_coprimality(ledger, s.id, s.obligations)
    return findings
