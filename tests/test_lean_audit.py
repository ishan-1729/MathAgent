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


# ---- native_decide / compiler-trust axiom denylist (explicit, intent-carrying ban) ----

@pytest.mark.parametrize("ax", ["Lean.ofReduceBool", "ofReduceBool", "Lean.trustCompiler"])
def test_native_decide_axiom_denylisted(ax):
    """The per-computation native_decide / compiler-trust axioms are REJECTED via the explicit
    denylist (distinct code `axiom_denylisted`), not merely via the whitelist."""
    rep = DependencyReport("thm", axioms=[ax], constants=[ConstDep("Nat.add")])
    res = audit_report(rep, TOOLKIT)
    assert not res.passed
    assert "axiom_denylisted" in _codes(res)


def test_axiom_denylist_survives_whitelist_widening():
    """A denylisted axiom stays rejected even if the whitelist is widened to include it — the
    denylist is checked BEFORE the whitelist, so widening the floor cannot readmit native_decide."""
    tk = Toolkit(
        justifications={"conclusion": Justification("conclusion")},
        lean_axiom_whitelist=["propext", "Classical.choice", "Quot.sound", "Lean.ofReduceBool"],
        lean_axiom_denylist=["Lean.ofReduceBool"],
    )
    rep = DependencyReport("thm", axioms=["Lean.ofReduceBool"], constants=[ConstDep("Nat.add")])
    res = audit_report(rep, tk)
    assert not res.passed
    assert "axiom_denylisted" in _codes(res)


def test_missing_axiom_denylist_key_fails_closed_to_whitelist():
    """A missing `lean_axiom_denylist` (empty list) must not open the floor: the whitelist still
    guards, so an off-whitelist axiom is still rejected (as axiom_not_whitelisted)."""
    tk = Toolkit(
        justifications={"conclusion": Justification("conclusion")},
        lean_axiom_whitelist=["propext", "Classical.choice", "Quot.sound"],
        lean_axiom_denylist=[],
    )
    rep = DependencyReport("thm", axioms=["Lean.ofReduceBool"], constants=[ConstDep("Nat.add")])
    res = audit_report(rep, tk)
    assert not res.passed
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


# ---- THREAD 4: non-elementary algebraic shortcuts beyond Z[i]/Z[√d] (probed live, then
#      pinned offline). Each constant below is a REAL member of the named family's dependency
#      closure observed against Mathlib via a warm LeanServer; the audit must REJECT it. ----

# (family label, a real constant pulled into that shortcut's proof-term closure)
_THREAD4_DENY_FAMILIES = [
    ("generic UFD",            "UniqueFactorizationMonoid"),
    ("generic PID",            "IsPrincipalIdealRing"),
    ("adjoin root",            "AdjoinRoot.root"),
    ("Algebra.adjoin",         "Algebra.adjoin"),
    ("cyclotomic poly",        "Polynomial.cyclotomic"),
    ("primitive root",         "IsPrimitiveRoot"),
    ("roots of unity (lower)", "rootsOfUnity"),
    ("roots of unity (upper)", "RootsOfUnity"),
    ("p-adic field ℚ_p",       "Padic.normedField"),
    ("p-adic ring ℤ_p",        "PadicInt.instCommRing"),
    ("multivariate poly",      "MvPolynomial.X"),
]


@pytest.mark.parametrize("label,const", _THREAD4_DENY_FAMILIES)
def test_thread4_nonelementary_shortcut_rejected(label, const):
    """A synthetic report whose closure contains a constant from each newly-denylisted
    family is REJECTED by the SHIPPED denylist (offline; no Lean needed). Each fails against
    the pre-change denylist (those families were a live GAP)."""
    rep = DependencyReport("thm", axioms=[], constants=[
        ConstDep("Nat.add"), ConstDep(const, "definition")])
    res = audit_report(rep, TOOLKIT)
    assert not res.passed, f"{label}: {const!r} should be rejected"
    assert "denylisted_dependency" in _codes(res), label


def test_thread4_padic_completion_denied_but_valuation_allowed():
    """The p-adic COMPLETIONS (ℚ_p/ℤ_p) are denied, but the elementary integer/rational
    p-adic VALUATION functions (v_p = exponent in the factorization) must NOT be collateral-
    rejected: bare `padicValNat`/`padicValInt`/`padicValRat` are not the `Padic`/`PadicInt`
    components, so a report using only them still PASSES."""
    denied = DependencyReport("thm", axioms=[], constants=[ConstDep("PadicInt.instCommRing")])
    assert not audit_report(denied, TOOLKIT).passed

    valuation_only = DependencyReport("thm", axioms=[], constants=[
        ConstDep("padicValNat"), ConstDep("padicValInt"), ConstDep("padicValRat"),
        ConstDep("Nat.add")])
    res = audit_report(valuation_only, TOOLKIT)
    assert res.passed, [str(f) for f in res.rejects()]


def test_thread4_additions_do_not_overreject_elementary():
    """A synthetic ELEMENTARY closure (Nat/Int arithmetic, gcd/Coprime/Prime/factorization,
    Legendre/Jacobi, single-variable Polynomial, ZMod field, well-founded recursion) STILL
    PASSES under the shipped denylist after the THREAD-4 additions. Guards against the new
    entries collateral-rejecting elementary number theory or the by-fiat allowlist.

    `Polynomial.X` confirms denylisting `Polynomial.cyclotomic` (dotted) leaves bare
    single-variable `Polynomial` alone; `Algebra.id` confirms `Algebra.adjoin` (dotted) does
    not touch bare `Algebra` plumbing."""
    rep = DependencyReport("thm", axioms=["propext", "Classical.choice", "Quot.sound"], constants=[
        ConstDep("Nat.add"), ConstDep("Nat.mul"), ConstDep("Int.add"),
        ConstDep("Nat.gcd"), ConstDep("Nat.Coprime"), ConstDep("Nat.Prime"),
        ConstDep("Nat.factorization"), ConstDep("legendreSym"), ConstDep("jacobiSym"),
        ConstDep("Int.ModEq"), ConstDep("padicValNat"),
        ConstDep("Polynomial.X"),          # single-var poly is NOT denylisted
        ConstDep("Algebra.id"),            # bare Algebra plumbing is NOT denylisted
        ConstDep("WellFounded.fix"), ConstDep("Nat.rec", "recursor"),
        ConstDep("Decidable"),
    ])
    res = audit_report(rep, TOOLKIT)
    assert res.passed, [str(f) for f in res.rejects()]


# ---- R-DENY: six LIVE denylist gaps (pass-to-fractions / quotient-by-ideal / bare NumberField).
#      Reproduced live against a warm LeanServer + Mathlib (each construction below COMPILED and was
#      ADMITTED — audit.passed=True — under the pre-R-DENY denylist), then pinned offline. Each
#      constant is a REAL member of the named construction's proof-term dependency closure observed
#      against Mathlib; the audit must now REJECT it. Every case fails against the pre-R-DENY denylist
#      (those families were a live GAP), so none is vacuous. ----

# (family label, a real constant pulled into that shortcut's proof-term closure)
_RDENY_FAMILIES = [
    ("fractional ideal (class-group/Dedekind)", "FractionalIdeal"),
    ("localization (pass-to-fractions)",        "Localization"),
    ("OreLocalization (Localization impl)",     "Localization.mk"),     # 'Localization' component
    ("field of fractions object",               "FractionRing"),
    ("pass-to-fractions typeclass",             "IsFractionRing"),
    ("bare NumberField typeclass",              "NumberField"),
    ("bare NumberField (closure proof)",        "NumberField.to_charZero"),
    ("ideal quotient ring",                     "Ideal.Quotient.mk"),
    ("ideal quotient (ring instance proof)",    "Ideal.Quotient.ring._proof_10"),
    ("ideal-quotient typeclass (the ⧸ op)",     "HasQuotient.Quotient"),
    ("ideal span (generated ideal)",            "Ideal.span"),
]


@pytest.mark.parametrize("label,const", _RDENY_FAMILIES)
def test_rdeny_nonelementary_construction_rejected(label, const):
    """A synthetic report whose closure contains a constant from each newly-denylisted R-DENY
    family is REJECTED by the SHIPPED denylist (offline; no Lean needed)."""
    rep = DependencyReport("thm", axioms=[], constants=[
        ConstDep("Nat.add"), ConstDep(const, "definition")])
    res = audit_report(rep, TOOLKIT)
    assert not res.passed, f"{label}: {const!r} should be rejected"
    assert "denylisted_dependency" in _codes(res), label


def test_rdeny_quotient_family_split_each_token_needed():
    """The three quotient gaps split across the tokens, so all of Ideal.Quotient / HasQuotient /
    Ideal.span are needed (observed live):
      - 'Q[X] ⧸ Ideal.span {X^2+1}'  hits HasQuotient + Ideal.span but NOT Ideal.Quotient;
      - 'IsDomain (R ⧸ P)'           hits Ideal.Quotient + HasQuotient but NOT Ideal.span.
    A denylist missing any one of the three would admit one of the two shapes."""
    # Q[X]/(X^2+1): the un-named-quotient path that re-opens AdjoinRoot. No `Ideal.Quotient.*`.
    qx = DependencyReport("thm", axioms=[], constants=[
        ConstDep("Nat.add"), ConstDep("HasQuotient.Quotient", "definition"),
        ConstDep("Ideal.span", "definition")])
    assert not audit_report(qx, TOOLKIT).passed
    # quotient-by-prime-is-a-domain: no `Ideal.span` in the closure.
    dom = DependencyReport("thm", axioms=[], constants=[
        ConstDep("Nat.add"), ConstDep("Ideal.Quotient.mk", "definition"),
        ConstDep("HasQuotient.Quotient", "definition")])
    assert not audit_report(dom, TOOLKIT).passed


def test_rdeny_isfractionring_distinct_from_localization():
    """A bare `IsFractionRing` pass-to-fractions typeclass does NOT always force `Localization`
    into the closure (probed live: 'IsFractionRing Int Rat' closure has IsFractionRing but NOT
    Localization), so IsFractionRing is a distinct token that must reject ON ITS OWN."""
    rep = DependencyReport("thm", axioms=[], constants=[
        ConstDep("Nat.add"), ConstDep("IsFractionRing", "inductive")])
    res = audit_report(rep, TOOLKIT)
    assert not res.passed
    assert "denylisted_dependency" in _codes(res)


def test_rdeny_additions_do_not_overreject_zmod_intcast_elementary():
    """THE OVER-REJECTION GUARD. A synthetic ELEMENTARY closure mirroring the CERTIFIED
    ZMod/intCast reach — the `3 ∣ a^2 + b^2 ⇒ 3∣a ∧ 3∣b` shape that uses
    `ZMod.intCast_zmod_eq_zero_iff_dvd` + `decide` over a finite ZMod core — STILL PASSES under
    the shipped denylist after the R-DENY additions.

    Constants below are REAL members of that certified proof's live closure (the cast bridge +
    Int/Nat arithmetic; after `decide` reduces the finite ZMod core the surviving closure is plain
    NatCast/Int/Nat). This is the dominating constraint: the ZMod/intCast bridge must NOT
    transitively touch Ideal.Quotient / HasQuotient / Ideal.span / Localization / FractionRing /
    IsFractionRing / FractionalIdeal / NumberField — and it does not."""
    rep = DependencyReport("thm", axioms=["propext", "Classical.choice", "Quot.sound"], constants=[
        ConstDep("Nat.cast"), ConstDep("NatCast.natCast"), ConstDep("instNatCastInt"),
        ConstDep("Int.add"), ConstDep("Int.mul"), ConstDep("Int.instDvd"),
        ConstDep("Int.mul_comm"), ConstDep("Int.pow"), ConstDep("Nat.add"),
        ConstDep("Nat.rec", "recursor"), ConstDep("Decidable"), ConstDep("DecidableEq"),
        # the elementary toolkit's other certified shapes' anchors, for breadth:
        ConstDep("Nat.gcd"), ConstDep("Nat.Coprime"), ConstDep("Nat.Prime"),
        ConstDep("Nat.find"),
    ])
    res = audit_report(rep, TOOLKIT)
    assert res.passed, [str(f) for f in res.rejects()]


# ---- ANALYSIS/TRIG scope (live-audited 2026-07-03): the imo_1963_p5 negative control certified
#      authoritative_elementary because the denylist had NO real-analysis/trig coverage — Real.cos in the
#      closure matched nothing. v1's fragment T is INTEGER elementary NT; real-analytic/trig machinery is
#      out-of-domain and must REJECT. Deliberate boundary: Complex.exp stays ALLOWED (roots-of-unity
#      filter, PLAN §2.1). ----

# (family label, a real constant that would appear in that construct's proof-term closure)
_ANALYSIS_DENY_FAMILIES = [
    ("real cosine",         "Real.cos"),
    ("real sine",           "Real.sin"),
    ("real tangent",        "Real.tan"),
    ("real pi",             "Real.pi"),
    ("real arccos",         "Real.arccos"),
    ("real exp",            "Real.exp"),
    ("real log",            "Real.log"),
    ("complex cosine",      "Complex.cos"),
    ("complex sine",        "Complex.sin"),
    ("trig namespace decl", "Mathlib.Analysis.SpecialFunctions.Trigonometric.Basic.cos_sq_pi_div_two"),
]


@pytest.mark.parametrize("label,const", _ANALYSIS_DENY_FAMILIES)
def test_analysis_trig_shortcut_rejected(label, const):
    """A synthetic report whose closure contains a real-analysis/trig constant (the imo_1963_p5
    negative-control shape) is REJECTED by the SHIPPED denylist. Each fails against the pre-change
    denylist (analysis/trig was a live GAP that certified the trig identity)."""
    rep = DependencyReport("thm", axioms=[], constants=[
        ConstDep("Nat.add"), ConstDep(const, "definition")])
    res = audit_report(rep, TOOLKIT)
    assert not res.passed, f"{label}: {const!r} should be rejected"
    assert "denylisted_dependency" in _codes(res), label


def test_analysis_namespaced_trig_decl_rejected():
    """A `Mathlib.Analysis.SpecialFunctions.Trigonometric.*` namespaced declaration is rejected via the
    dotted namespace-prefix denylist entry (not just the bare `Real.cos`/`Real.sin` components)."""
    rep = DependencyReport("thm", axioms=[], constants=[
        ConstDep("Mathlib.Analysis.SpecialFunctions.Trigonometric.Basic.Real.cos_pi_div_seven",
                 "theorem")])
    res = audit_report(rep, TOOLKIT)
    assert not res.passed
    assert "denylisted_dependency" in _codes(res)


def test_analysis_boundary_complex_exp_still_passes():
    """THE DELIBERATE BOUNDARY: `Complex.exp` is NOT banned (the roots-of-unity filter, PLAN §2.1,
    goes through ζ = exp(2πi/n)). A closure containing ONLY Complex.exp plus infrastructure STILL
    PASSES — banning the trig functions must not collaterally ban Complex.exp (they are dotted, so
    `Complex.cos`/`Complex.sin` do not touch `Complex.exp`)."""
    rep = DependencyReport("thm", axioms=["propext", "Classical.choice", "Quot.sound"], constants=[
        ConstDep("Complex.exp", "definition"),
        ConstDep("Nat.add"), ConstDep("Int.add"),
        ConstDep("WellFounded.fix"), ConstDep("Nat.rec", "recursor"),
        ConstDep("Decidable"),
    ])
    res = audit_report(rep, TOOLKIT)
    assert res.passed, [str(f) for f in res.rejects()]


def test_analysis_additions_do_not_touch_whitelisted_axioms():
    """The three whitelisted axioms still pass after the analysis/trig additions (no axiom-floor
    regression)."""
    rep = DependencyReport("thm", axioms=["propext", "Classical.choice", "Quot.sound"],
                           constants=[ConstDep("Nat.add")])
    assert audit_report(rep, TOOLKIT).passed


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
