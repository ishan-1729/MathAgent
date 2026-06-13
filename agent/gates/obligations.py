"""Validate the discharged proof-obligations attached to ledger steps.

A method tag alone ("descent", "case_split") carries none of the load-bearing content; this module
checks the *obligations* that make those tags meaningful, and re-runs the finite ones numerically
rather than trusting prose (PLAN.md Section 5, Layer 1/3):

  - case_cover       : the enumerated residues must cover a complete residue system (numeric, REJECT)
  - descent          : must assert strict decrease + staying in domain; optional numeric spot-check
  - split_coprimality: must reference a prior gcd/coprimality step (structural, REJECT)

Presence of a required obligation is enforced upstream in ledger.validate_structure; here we check
its *content*.
"""
from __future__ import annotations

from agent.gates.ledger import Ledger
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
    except numeric.NumericError as e:
        return [Finding(LAYER_NUMERIC, Severity.REJECT, "case_cover_error", str(e), step_id)]
    if not res.ok:
        return [Finding(
            LAYER_NUMERIC, Severity.REJECT, "case_cover_incomplete",
            f"case split mod {res.modulus} misses residues {res.missing}", step_id,
        )]
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
    # sample box, confirm next < measure everywhere in the box.
    me, ne = d.get("measure_expr"), d.get("next_expr")
    variables, bounds = d.get("variables"), d.get("sample_bounds")
    if me and ne and variables and bounds:
        try:
            box = {v: tuple(bounds[v]) for v in variables}
            # next - measure must be < 0 (i.e. has NO solution >= 0) at every box point.
            viol = numeric.find_integer_solutions(
                f"({ne}) - ({me})", variables, box
            )
            # find_integer_solutions finds == 0; we also need > 0. Check directly:
            ge_zero = _points_where_nonneg(f"({ne}) - ({me})", variables, box)
            if ge_zero:
                findings.append(Finding(
                    LAYER_NUMERIC, Severity.REJECT, "descent_no_decrease",
                    f"measure does not strictly decrease at e.g. {ge_zero[0]}", step_id,
                ))
            _ = viol  # presence of equality points alone is fine; only >= 0 violates strictness
        except numeric.NumericError as e:
            findings.append(Finding(LAYER_NUMERIC, Severity.REJECT, "descent_expr_error", str(e), step_id))
    return findings


def _points_where_nonneg(expression: str, variables: list[str], bounds: dict) -> list[dict]:
    """Box points where expression >= 0 (i.e. strict-decrease violations). Exact integer eval."""
    import sympy
    expr, ordered = numeric._parse(expression, variables)  # reuse validated parser
    f = sympy.lambdify(ordered, expr, modules=[{}])
    out = []
    import itertools
    ranges = [range(bounds[v][0], bounds[v][1] + 1) for v in variables]
    for point in itertools.product(*ranges):
        if f(*point) >= 0:
            out.append({v: int(x) for v, x in zip(variables, point)})
            if len(out) >= 8:
                break
    return out


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
