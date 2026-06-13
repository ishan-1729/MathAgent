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
