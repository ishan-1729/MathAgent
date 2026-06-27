"""Tests for the SEARCH-FITNESS vs REPORTING-STATUS separation (P3 / openevolve_stacking_brief §9).

The graded search fitness (the evolutionary combined_score, Elo ratings, etc.) is INTERNAL. The
user-facing status is a CATEGORICAL enum on the certification ladder
``rejected < candidate_incomplete < soft_proven < formalized_not_elementary < authoritative_elementary``;
a numeric search score must NEVER leak into the certification language. These tests pin the mapping and
the precedence so reporting stays categorical regardless of any internal fitness.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

# scripts/ is not a package; load prove.py by path so we can import its status helper directly.
_PROVE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "prove.py"
_spec = importlib.util.spec_from_file_location("prove_cli", _PROVE_PATH)
prove_cli = importlib.util.module_from_spec(_spec)
sys.modules["prove_cli"] = prove_cli
_spec.loader.exec_module(prove_cli)

ReportStatus = prove_cli.ReportStatus
report_status = prove_cli.report_status


# ---- the enum is the categorical certification ladder (no scores) ------------------------------

def test_status_enum_values_are_categorical():
    assert {s.value for s in ReportStatus} == {
        "rejected", "candidate_incomplete", "soft_proven",
        "formalized_not_elementary", "authoritative_elementary",
    }
    # Every member's label is a category string, never a number.
    for s in ReportStatus:
        assert isinstance(s.label, str)
        with pytest.raises(ValueError):
            float(s.label)


# ---- the mapping covers every outcome bucket ---------------------------------------------------

def test_rejected_when_no_proof_and_no_candidate():
    assert report_status(proven=False) is ReportStatus.REJECTED


def test_candidate_incomplete_when_candidate_but_not_proven():
    assert report_status(proven=False, has_candidate=True) is ReportStatus.CANDIDATE_INCOMPLETE


def test_soft_proven_for_a_proven_but_uncertified_result():
    # PROVEN (soft gate) is soft_proven — NOT a certificate.
    assert report_status(proven=True) is ReportStatus.SOFT_PROVEN


def test_formalized_not_elementary_when_audited_but_not_authoritative():
    # Formalized + audited but the audit did NOT certify elementary -> formalized_not_elementary.
    st = report_status(proven=True, formalized=True, authoritative_elementary=False)
    assert st is ReportStatus.FORMALIZED_NOT_ELEMENTARY


def test_authoritative_elementary_is_the_only_certificate():
    st = report_status(proven=True, formalized=True, authoritative_elementary=True)
    assert st is ReportStatus.AUTHORITATIVE_ELEMENTARY


# ---- precedence: authoritative dominates; formalized-not-elementary is never a certificate -----

def test_authoritative_dominates_even_if_other_flags_set():
    # Authoritative wins regardless of the other inputs.
    st = report_status(proven=False, has_candidate=True, formalized=True,
                       authoritative_elementary=True)
    assert st is ReportStatus.AUTHORITATIVE_ELEMENTARY


def test_formalized_not_elementary_never_reported_as_proven_certificate():
    # A formalized-but-not-elementary result must NOT collapse to soft_proven (it carries the audit
    # result) and must NEVER be authoritative.
    st = report_status(proven=True, formalized=True, authoritative_elementary=False)
    assert st is not ReportStatus.SOFT_PROVEN
    assert st is not ReportStatus.AUTHORITATIVE_ELEMENTARY


# ---- a search SCORE never leaks into the status (the whole point of the separation) ------------

def test_status_ignores_any_search_fitness_score():
    # report_status takes NO numeric fitness argument; a high internal score cannot promote the status.
    # A high-fitness-but-unproven candidate is still only 'candidate_incomplete', never 'soft_proven'.
    high_fitness_unproven = report_status(proven=False, has_candidate=True)
    assert high_fitness_unproven is ReportStatus.CANDIDATE_INCOMPLETE
    # And the function signature does not accept a score (keyword-only, fixed set of flags).
    import inspect
    params = set(inspect.signature(report_status).parameters)
    assert params == {"proven", "has_candidate", "formalized", "authoritative_elementary"}
    assert not any("score" in p or "fitness" in p for p in params)
