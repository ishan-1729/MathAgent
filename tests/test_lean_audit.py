"""Tests for the Layer-4 Lean dependency/axiom auditor (offline, synthetic + fixture reports).

The content-denylist path needs Mathlib to trigger live, so it is exercised here with synthetic
dependency reports. The axiom-gate and the pass path are also validated live by
tests/test_lean_bridge.py (opt-in) and were confirmed against Lean 4.30.0 during development.
"""
import json
from pathlib import Path

import pytest

from agent.gates.lean_audit import (
    ConstDep, DependencyReport, LeanVerdict, audit_report, audit_json, _name_matches,
    _match_span, _dominating_allow,
)
from agent.gates.report import Severity
from agent.gates.toolkit import Toolkit, Justification, load_toolkit

REPO = Path(__file__).resolve().parents[1]
TOOLKIT = load_toolkit()


def _codes(res, sev=Severity.REJECT):
    return {f.code for f in res.findings if f.severity is sev}


# ---- name matching ----

def test_name_matches_dotted_prefix():
    assert _name_matches("Mathlib.NumberTheory.ClassNumber.foo", "Mathlib.NumberTheory.ClassNumber")
    assert _name_matches("Mathlib.NumberTheory.ClassNumber", "Mathlib.NumberTheory.ClassNumber")
    assert not _name_matches("Mathlib.NumberTheory.ClassNumberX", "Mathlib.NumberTheory.ClassNumber")


def test_name_matches_bare_component():
    assert _name_matches("Mathlib.AlgebraicGeometry.EllipticCurve.j", "EllipticCurve")
    assert _name_matches("NumberField.RingOfIntegers.foo", "NumberField")
    assert not _name_matches("MyEllipticCurveHelper", "EllipticCurve")  # substring, not a component


# ---- axiom integrity ----

def test_clean_proof_passes():
    rep = DependencyReport("thm", axioms=[], constants=[ConstDep("Nat.add"), ConstDep("Nat.rec", "recursor")])
    res = audit_report(rep, TOOLKIT)
    assert res.passed and res.verdict is LeanVerdict.PASS


def test_whitelisted_axioms_pass():
    rep = DependencyReport("thm", axioms=["propext", "Classical.choice", "Quot.sound"],
                           constants=[ConstDep("Nat.add")])
    assert audit_report(rep, TOOLKIT).passed


def test_sorry_axiom_rejected():
    rep = DependencyReport("thm", axioms=["sorryAx"], constants=[ConstDep("Nat.add")])
    res = audit_report(rep, TOOLKIT)
    assert not res.passed
    assert "sorry_axiom" in _codes(res)


def test_unknown_axiom_rejected():
    rep = DependencyReport("thm", axioms=["Mathlib.SomeChoiceAxiom"], constants=[])
    res = audit_report(rep, TOOLKIT)
    assert "axiom_not_whitelisted" in _codes(res)


# ---- content denylist over the closure ----

def test_denylisted_dependency_rejected():
    rep = DependencyReport("thm", axioms=[], constants=[
        ConstDep("Nat.add"),
        ConstDep("Mathlib.NumberTheory.ClassNumber.finite", "theorem"),
    ])
    res = audit_report(rep, TOOLKIT)
    assert not res.passed
    assert "denylisted_dependency" in _codes(res)


def test_elliptic_curve_component_rejected():
    rep = DependencyReport("thm", axioms=[], constants=[
        ConstDep("Mathlib.AlgebraicGeometry.EllipticCurve.Weierstrass.j")])
    assert not audit_report(rep, TOOLKIT).passed


def test_allowlist_wins_over_denylist():
    # A constant matching BOTH the denylist and an allowlist is exempted (INFO, not REJECT).
    tk = Toolkit(
        justifications={"conclusion": Justification("conclusion")},
        lean_denylist_decls=["Heavy"],
        lean_infrastructure_allowlist=[],
        lean_elementary_by_fiat=["Heavy.ok"],
        lean_axiom_whitelist=["propext", "Classical.choice", "Quot.sound"],
    )
    rep = DependencyReport("thm", axioms=[], constants=[
        ConstDep("Heavy.ok.lemma"),   # deny "Heavy" (component) but allow "Heavy.ok" (prefix)
        ConstDep("Heavy.bad.lemma"),  # deny only
    ])
    res = audit_report(rep, tk)
    assert not res.passed
    assert "denylisted_dependency" in _codes(res)
    assert "denylist_exempted" in {f.code for f in res.findings if f.severity is Severity.INFO}
    # exactly one reject (Heavy.bad), not Heavy.ok
    assert len(res.rejects()) == 1


# ---- allowlist precedence (L2 fix d): an unrelated infra component must NOT exempt content ----

def test_match_span_dotted_is_prefix_anchored():
    assert _match_span("Mathlib.NumberTheory.ClassNumber.foo", "Mathlib.NumberTheory.ClassNumber") == (0, 3)
    assert _match_span("X.Mathlib.NumberTheory.ClassNumber", "Mathlib.NumberTheory.ClassNumber") is None


def test_match_span_bare_component_records_position():
    assert _match_span("Mathlib.NumberTheory.ClassNumber.Decidable.bar", "Decidable") == (3, 4)
    assert _match_span("Decidable.foo", "Decidable") == (0, 1)


def test_dominating_allow_requires_prefix_anchored_coverage():
    # Stray infra component deep in the name does NOT dominate the denylist prefix span.
    name = "Mathlib.NumberTheory.ClassNumber.Decidable.bar"
    assert _dominating_allow(name, (0, 3), ["Decidable"]) is None
    # A prefix-anchored allow that covers the deny span does dominate.
    assert _dominating_allow("Heavy.ok.lemma", (0, 1), ["Heavy.ok"]) == "Heavy.ok"


def test_denylisted_with_unrelated_infra_component_still_rejected():
    """A name that is denylisted CONTENT and happens to contain a bare infra component
    (Decidable/SizeOf/instHAdd) elsewhere must STILL be rejected — the allowlist must not exempt it."""
    tk = Toolkit(
        justifications={"conclusion": Justification("conclusion")},
        lean_denylist_decls=["Mathlib.NumberTheory.ClassNumber"],
        lean_infrastructure_allowlist=["Decidable", "SizeOf", "instHAdd"],
        lean_elementary_by_fiat=[],
        lean_axiom_whitelist=["propext", "Classical.choice", "Quot.sound"],
    )
    for bad in (
        "Mathlib.NumberTheory.ClassNumber.Decidable.bar",
        "Mathlib.NumberTheory.ClassNumber.instSizeOfFoo",  # not a component but ensure no leak
        "Mathlib.NumberTheory.ClassNumber.foo.Decidable",
    ):
        rep = DependencyReport("thm", axioms=[], constants=[ConstDep(bad)])
        res = audit_report(rep, tk)
        assert not res.passed, bad
        assert "denylisted_dependency" in _codes(res), bad


def test_real_denylist_with_stray_decidable_component_rejected():
    # Same attack against the SHIPPED denylist/allowlist (Decidable IS on the infra allowlist).
    rep = DependencyReport("thm", axioms=[], constants=[
        ConstDep("Mathlib.NumberTheory.ClassNumber.Decidable.finite")])
    res = audit_report(rep, TOOLKIT)
    assert not res.passed
    assert "denylisted_dependency" in _codes(res)


# ---- theorem cross-check (L2 fix b) ----

def test_theorem_name_mismatch_rejected():
    rep = DependencyReport("actual_thm", axioms=[], constants=[ConstDep("Nat.add")])
    res = audit_report(rep, TOOLKIT, theorem_name="requested_thm")
    assert not res.passed
    assert "theorem_mismatch" in _codes(res)


def test_theorem_name_match_passes():
    rep = DependencyReport("thm", axioms=[], constants=[ConstDep("Nat.add")])
    assert audit_report(rep, TOOLKIT, theorem_name="thm").passed


def test_theorem_cross_check_via_audit_json():
    payload = json.dumps({"theorem": "wrong", "axioms": [], "constants": []})
    res = audit_json(payload, TOOLKIT, theorem_name="right")
    assert not res.passed
    assert "theorem_mismatch" in _codes(res)


def test_theorem_name_omitted_keeps_old_behavior():
    # No theorem_name => no cross-check (back-compat for existing callers / fixtures).
    rep = DependencyReport("anything", axioms=[], constants=[ConstDep("Nat.add")])
    assert audit_report(rep, TOOLKIT).passed


# ---- parsing ----

def test_from_dict_and_json():
    d = {"theorem": "t", "axioms": ["propext"],
         "constants": ["BareName", {"name": "X.y", "kind": "definition"}]}
    rep = DependencyReport.from_dict(d)
    assert rep.constants[0].name == "BareName" and rep.constants[0].kind == "theorem"
    assert rep.constants[1].kind == "definition"
    rep2 = DependencyReport.from_json(json.dumps(d))
    assert rep2.theorem == "t"


def test_audits_real_add_zero_fixture():
    fixture = REPO / "agent/gates/lean/examples/add_zero_report.json"
    res = audit_json(fixture.read_text(encoding="utf-8"), TOOLKIT)
    assert res.passed, [str(f) for f in res.rejects()]
