"""Validate the discharged proof-obligations attached to ledger steps.

A method tag alone ("descent", "case_split") carries none of the load-bearing content; this module
checks the *obligations* that make those tags meaningful, and re-runs the finite ones numerically
rather than trusting prose (PLAN.md Section 5, Layer 1/3):

  - case_cover       : the enumerated residues must cover a complete residue system (numeric, REJECT)
  - descent          : must assert strict decrease + staying in domain; when measure_expr/next_expr
                       are supplied the exact-integer strict-decrease check runs BY DEFAULT (a
                       non-decrease / domain-leak / malformed expr is a REJECT). A descent with NO
                       checkable measure is NOT silently passed on self-assertion — it is routed to
                       NEEDS_REVIEW (a REVIEW finding) so an unverified descent is flagged.
  - bounding         : the declared inequality must be well-formed and its strict flag consistent with
                       the operator; where both sides are concrete integers the relation is verified
                       via the restricted no-eval evaluator (a malformed or violated bound is a REJECT)
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
from agent.tools.numeric import NumericError


def _check_case_cover(step_id: str, ob: dict) -> list[Finding]:
    cc = ob.get("case_cover")
    if not cc:
        return []
    try:
        res = numeric.verify_residue_cover(cc["modulus"], cc["residues"])
    except Exception as e:  # noqa: BLE001 - any malformed-but-schema-valid obligation must become a
        # deterministic REJECT, never an uncaught exception (KeyError, TypeError, NumericError, ...).
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

    # Numeric strict-decrease check (run BY DEFAULT, not opt-in): the moment the prover supplies the
    # concrete measure/next expressions, we confirm next < measure everywhere in the sample box
    # (i.e. next - measure is never >= 0). A checkable descent that omits the box variables it needs
    # is a malformed obligation, NOT a free pass -> deterministic REJECT (descent_expr_error).
    me, ne = d.get("measure_expr"), d.get("next_expr")
    variables, sample_bounds = d.get("variables"), d.get("sample_bounds")
    if me or ne:
        # If one expression is given the other must be too; a half-specified measure cannot be checked.
        if not (me and ne):
            findings.append(Finding(LAYER_NUMERIC, Severity.REJECT, "descent_expr_error",
                                    "descent supplies only one of measure_expr/next_expr; both are "
                                    "required to check the strict decrease", step_id))
            return findings
        if not variables:
            findings.append(Finding(LAYER_NUMERIC, Severity.REJECT, "descent_expr_error",
                                    "descent supplies measure_expr/next_expr but no variables to "
                                    "evaluate them over", step_id))
            return findings
        if not sample_bounds:
            findings.append(Finding(LAYER_NUMERIC, Severity.REJECT, "descent_expr_error",
                                    "descent supplies measure_expr/next_expr but no sample_bounds box "
                                    "to check the strict decrease over", step_id))
            return findings
        # Coerce list-bounds (JSON arrays) to tuples; missing entries -> deterministic REJECT.
        missing = [v for v in variables if v not in sample_bounds]
        if missing:
            findings.append(Finding(LAYER_NUMERIC, Severity.REJECT, "descent_expr_error",
                                    f"sample_bounds missing variables {missing}", step_id))
            return findings
        box = {v: tuple(sample_bounds[v]) for v in variables}
        try:
            ge_zero = numeric.find_points_where_nonneg(f"({ne}) - ({me})", variables, box)
        except Exception as e:  # noqa: BLE001 - prover-controlled measure/next exprs must never crash
            # the gate; any failure (NumericError, TypeError, ValueError, ...) is a deterministic REJECT.
            findings.append(Finding(LAYER_NUMERIC, Severity.REJECT, "descent_expr_error", str(e), step_id))
            return findings
        if ge_zero:
            findings.append(Finding(LAYER_NUMERIC, Severity.REJECT, "descent_no_decrease",
                                    f"measure does not strictly decrease at e.g. {ge_zero[0]}", step_id))
        return findings

    # No checkable measure was supplied. The strict-decrease is then only SELF-ASSERTED (the booleans
    # above). A self-assertion is NOT a deterministic proof of descent, so rather than silently
    # PASSING it we route it to NEEDS_REVIEW: an unverified descent is flagged for adversarial review,
    # never admitted as deterministically proven. (If the self-asserted booleans were false we already
    # emitted a REJECT above; this REVIEW only fires when nothing else rejected the descent.)
    if not findings:
        findings.append(Finding(LAYER_OBLIGATION, Severity.REVIEW, "descent_unverified_measure",
                                "descent asserts strict decrease but supplies no checkable "
                                "measure_expr/next_expr; routed to review (not deterministically "
                                "proven)", step_id))
    return findings


def _check_bounding(step_id: str, ob: dict) -> list[Finding]:
    """Content checker for the `bounding` obligation.

    The obligation declares ``inequality`` (a relation string like "n < n+1") and ``strict`` (whether
    the relation is strict). We validate that:
      * the inequality is WELL-FORMED — exactly one comparison operator, non-empty parseable sides;
      * the declared ``strict`` flag MATCHES the operator's strictness ('<'/'>' strict, '<='/'>=' not);
      * where BOTH sides are CONCRETE integer expressions (no free variables), the relation actually
        HOLDS — verified by numeric.py's restricted no-eval evaluator (never eval).
    A malformed inequality, a strict/operator mismatch, or a concrete bound that is VIOLATED is a
    deterministic REJECT (presence alone is not enough). A symbolic bound (free variables on either
    side) cannot be numerically decided here; its surface form is still validated, and it remains a
    REVIEW-routed elastic justification upstream (scanner.py) rather than a silent deterministic pass.
    """
    b = ob.get("bounding")
    if not b:
        return []
    findings: list[Finding] = []
    inequality = b.get("inequality")
    strict = b.get("strict")
    if not isinstance(inequality, str) or not inequality.strip():
        return [Finding(LAYER_OBLIGATION, Severity.REJECT, "bounding_malformed",
                        "bounding obligation has no inequality string", step_id)]
    try:
        lhs, rhs, op, op_strict = numeric.split_inequality(inequality)
    except NumericError as e:
        return [Finding(LAYER_OBLIGATION, Severity.REJECT, "bounding_malformed",
                        f"bounding inequality is malformed: {e}", step_id)]
    # The declared `strict` flag must agree with the operator's strictness (a "<" bound declared
    # strict=false, or a "<=" declared strict=true, is an inconsistent obligation).
    if isinstance(strict, bool) and strict != op_strict:
        findings.append(Finding(LAYER_OBLIGATION, Severity.REJECT, "bounding_strictness_mismatch",
                                f"bounding declares strict={strict} but operator in {inequality!r} is "
                                f"{'strict' if op_strict else 'non-strict'}", step_id))
    # Concrete numeric check: if both sides reduce to closed integers, the relation must actually hold
    # (verified by the restricted no-eval evaluator). A symbolic bound returns None -> not decidable
    # here (shape validated, content stays a REVIEW-routed elastic justification upstream).
    try:
        holds = numeric.concrete_inequality_holds(lhs, rhs, op)
    except Exception as e:  # noqa: BLE001 - any failure on the (prover-controlled) sides is a malformed
        # bound, surfaced as a deterministic REJECT, never an uncaught crash and never a silent pass.
        return findings + [Finding(LAYER_NUMERIC, Severity.REJECT, "bounding_malformed",
                                   f"bounding inequality side is malformed: {e}", step_id)]
    if holds is False:
        findings.append(Finding(LAYER_NUMERIC, Severity.REJECT, "bounding_violated",
                                f"bounding inequality {inequality!r} is numerically FALSE", step_id))
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
        findings += _check_bounding(s.id, s.obligations)
        findings += _check_split_coprimality(ledger, s.id, s.obligations)
    return findings
