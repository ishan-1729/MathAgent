"""Regression tests for the FAIL-CLOSED faithfulness gate (audited defect L1-faithfulness).

The defect: `FormalizeAuditResult.faithful` FAILED OPEN — it returned True when no faithfulness
panel had run (`faithfulness is None`), so a compiling + dependency-audited proof of the WRONG
statement was silently treated as `authoritative`. The fix makes it FAIL CLOSED: `authoritative`
is True only when a faithfulness panel actually ran AND passed.
"""
import json

from agent.gates.lean_audit import LeanAuditResult, LeanVerdict, DependencyReport
from agent.orchestrator import formalize_bridge as fb
from agent.orchestrator.faithfulness import (FaithfulnessVerdict, SingleVerdict,
                                             PanelFaithfulnessChecker, ScriptedFaithfulnessChecker)
from agent.tools import formalizer as fz
from agent.gates.toolkit import load_toolkit

TOOLKIT = load_toolkit()

VALID_LEDGER = json.dumps({"problem": "p", "claim": "For all integers n, n + 0 = n.", "steps": [
    {"id": "s1", "claim": "Let n be an integer.", "justification": "given", "depends_on": []},
    {"id": "s2", "claim": "n + 0 = n.", "justification": "conclusion", "depends_on": ["s1"]}]})


def _passing_audit():
    return LeanAuditResult(verdict=LeanVerdict.PASS, findings=[],
                           report=DependencyReport("ma_target"), provenance_verified=True)


# ---- property-level fail-closed semantics (the heart of the defect) ----

def test_faithful_property_fails_closed_when_unchecked():
    # No faithfulness verdict attached => faithful is False (NOT silently True).
    res = fb.FormalizeAuditResult(formalized=True, compiled=True, audit=_passing_audit())
    assert res.elementary_verified         # audited elementary...
    assert res.faithfulness is None
    assert not res.faithful                 # ...but faithfulness never ran...
    assert not res.authoritative            # ...so it is NOT authoritative (fail closed)


def test_faithful_property_true_only_when_panel_passed():
    passed = FaithfulnessVerdict(faithful=True, votes=[SingleVerdict("back_translation", True)])
    res = fb.FormalizeAuditResult(formalized=True, compiled=True, audit=_passing_audit(),
                                  faithfulness=passed, certification_trusted=True)
    assert res.faithful and res.authoritative


def test_content_pass_without_verified_toolchain_receipt_never_becomes_authoritative():
    legacy = LeanAuditResult(
        verdict=LeanVerdict.PASS, findings=[], report=DependencyReport(
            "ma_target", toolchain="caller-supplied"))
    passed = FaithfulnessVerdict(
        faithful=True, votes=[SingleVerdict("back_translation", True)])
    res = fb.FormalizeAuditResult(
        formalized=True, compiled=True, audit=legacy,
        faithfulness=passed, certification_trusted=True)
    assert legacy.passed is True               # content audit remains usable offline
    assert legacy.authoritative is False       # but there is no bridge-derived receipt
    assert res.elementary_verified is False
    assert res.authoritative is False


def test_authoritative_false_when_panel_failed():
    failed = FaithfulnessVerdict(faithful=False,
                                 votes=[SingleVerdict("strength", False, ["weaker statement"])],
                                 issues=["weaker statement"])
    res = fb.FormalizeAuditResult(formalized=True, compiled=True, audit=_passing_audit(),
                                  faithfulness=failed)
    assert res.elementary_verified and not res.faithful and not res.authoritative


def test_authority_properties_require_literal_true_booleans():
    """Malformed duck-typed checker fields must never become certificate authority by truthiness."""
    from types import SimpleNamespace

    malformed_audit = SimpleNamespace(passed="false")
    malformed_faith = FaithfulnessVerdict(faithful="false")
    res = fb.FormalizeAuditResult(
        formalized=True,
        compiled=True,
        audit=malformed_audit,
        faithfulness=malformed_faith,
        certification_trusted=True,
    )
    assert not res.elementary_verified
    assert not res.faithful
    assert not res.authoritative


# ---- end-to-end via formalize_and_audit (Lean stubbed) ----

def test_formalize_and_audit_no_checker_is_not_authoritative(monkeypatch):
    monkeypatch.setattr("agent.gates.lean_bridge.audit_lean_source", lambda *a, **k: _passing_audit())
    fr = fz.ScriptedFormalizer("theorem ma_target : True := trivial")
    res = fb.formalize_and_audit(VALID_LEDGER, fr, toolkit=TOOLKIT)
    assert res.compiled and res.elementary_verified
    assert not res.authoritative           # no panel => never authoritative


def test_formalize_and_audit_with_passing_checker_is_authoritative(monkeypatch):
    monkeypatch.setattr("agent.gates.lean_bridge.audit_lean_source", lambda *a, **k: _passing_audit())
    fr = fz.ScriptedFormalizer("theorem ma_target : True := trivial",
                               certification_trusted=True)
    res = fb.formalize_and_audit(VALID_LEDGER, fr, toolkit=TOOLKIT,
                                 faithfulness_checker=ScriptedFaithfulnessChecker(
                                     True, certification_trusted=True))
    assert res.authoritative


def test_scripted_certificate_components_are_untrusted_by_default(monkeypatch):
    """A scripted ``True`` proof and scripted passing judge must never mint production authority."""
    monkeypatch.setattr("agent.gates.lean_bridge.audit_lean_source", lambda *a, **k: _passing_audit())
    res = fb.formalize_and_audit(
        VALID_LEDGER,
        fz.ScriptedFormalizer("theorem ma_target : True := trivial"),
        toolkit=TOOLKIT,
        faithfulness_checker=ScriptedFaithfulnessChecker(True),
    )
    assert res.elementary_verified and res.faithful
    assert not res.certification_trusted
    assert not res.authoritative


def test_arbitrary_passing_panel_cannot_mint_authority(monkeypatch):
    """A generic callable is not a production trust root, even when every synthetic vote passes."""
    monkeypatch.setattr("agent.gates.lean_bridge.audit_lean_source", lambda *a, **k: _passing_audit())

    def always_pass(_claim, _source, _name, lens):
        return SingleVerdict(lens=lens, faithful=True)

    panel = PanelFaithfulnessChecker(always_pass)
    res = fb.formalize_and_audit(
        VALID_LEDGER,
        fz.ScriptedFormalizer(
            "theorem ma_target : True := trivial", certification_trusted=True),
        toolkit=TOOLKIT,
        faithfulness_checker=panel,
    )
    assert res.elementary_verified and res.faithful
    assert not panel.certification_trusted
    assert not res.certification_trusted
    assert not res.authoritative


# ---- FullVerifyResult / terminal-gate wiring inherits the fail-closed property ----

def test_full_verify_no_checker_not_authoritative_elementary(monkeypatch):
    # No faithfulness checker => never authoritative_elementary, regardless of the informal-gate outcome.
    monkeypatch.setattr("agent.gates.lean_bridge.audit_lean_source", lambda *a, **k: _passing_audit())
    fr = fz.ScriptedFormalizer("theorem ma_target : True := trivial")
    res = fb.full_verify(VALID_LEDGER, fr, toolkit=TOOLKIT)
    assert not res.authoritative_elementary
    if res.lean is not None:                       # if the informal gate admitted and Lean ran...
        assert res.lean.elementary_verified and not res.lean.faithful   # audited but unfaithful (closed)


def test_terminal_gate_no_checker_not_authoritative(monkeypatch):
    monkeypatch.setattr("agent.gates.lean_bridge.audit_lean_source", lambda *a, **k: _passing_audit())
    gate = fb.make_terminal_gate(fz.ScriptedFormalizer("theorem ma_target : True := trivial"),
                                 toolkit=TOOLKIT)
    result = gate("For all integers n, n + 0 = n.", "proof bundle")
    assert result.elementary_verified and not result.authoritative


# ---- a NEEDS_REVIEW informal gate must NOT yield authoritative_elementary (audited defect: round 2) ----

def _authoritative_lean():
    passed = FaithfulnessVerdict(faithful=True, votes=[SingleVerdict("back_translation", True)])
    return fb.FormalizeAuditResult(formalized=True, compiled=True, audit=_passing_audit(),
                                   faithfulness=passed, certification_trusted=True)


def test_needs_review_gate_is_not_authoritative_elementary():
    from agent.gates.gate import GateReport, Verdict
    # An informal gate that only NEEDS_REVIEW (e.g. an unreviewed elastic justification) must NOT be
    # authoritative even with a fully-passing Lean audit + faithfulness — only PASSED_DETERMINISTIC does.
    review = fb.FullVerifyResult(gate=GateReport(verdict=Verdict.NEEDS_REVIEW), lean=_authoritative_lean())
    assert not review.authoritative_elementary
    passed = fb.FullVerifyResult(gate=GateReport(verdict=Verdict.PASSED_DETERMINISTIC),
                                 lean=_authoritative_lean())
    assert passed.authoritative_elementary
