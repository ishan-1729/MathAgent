"""Tests for the SEARCH-FITNESS vs REPORTING-STATUS separation (P3 / openevolve_stacking_brief §9).

The graded search fitness (the evolutionary combined_score, Elo ratings, etc.) is INTERNAL. The
user-facing status is a CATEGORICAL enum on the certification ladder
``rejected < candidate_incomplete < soft_proven < audited_not_certified < authoritative_elementary``;
a numeric search score must NEVER leak into the certification language. These tests pin the mapping and
the precedence so reporting stays categorical regardless of any internal fitness.
"""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.orchestrator.dag import ProofDAG
from agent.orchestrator.reporting import (
    result_audit_record, result_has_candidate, result_proof_context, result_role_provenance,
)

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
        "audited_not_certified", "authoritative_elementary",
    }
    # Every member's label is a category string, never a number.
    for s in ReportStatus:
        assert isinstance(s.label, str)
        with pytest.raises(ValueError):
            float(s.label)
    assert (ReportStatus.FORMALIZED_NOT_ELEMENTARY
            is ReportStatus.AUDITED_NOT_CERTIFIED)


# ---- the mapping covers every outcome bucket ---------------------------------------------------

def test_rejected_when_no_proof_and_no_candidate():
    assert report_status(proven=False) is ReportStatus.REJECTED


def test_candidate_incomplete_when_candidate_but_not_proven():
    assert report_status(proven=False, has_candidate=True) is ReportStatus.CANDIDATE_INCOMPLETE


def test_soft_proven_for_a_proven_but_uncertified_result():
    # PROVEN (soft gate) is soft_proven — NOT a certificate.
    assert report_status(proven=True) is ReportStatus.SOFT_PROVEN


def test_audited_not_certified_when_audited_but_not_authoritative():
    # Lean compiled and the audit completed, but the full certificate did not pass.
    st = report_status(proven=True, audited=True, authoritative_elementary=False)
    assert st is ReportStatus.AUDITED_NOT_CERTIFIED


def test_legacy_formalized_keyword_maps_to_the_audited_bucket():
    assert report_status(proven=True, formalized=True) is ReportStatus.AUDITED_NOT_CERTIFIED
    assert report_status(proven=True, formalized="yes") is ReportStatus.SOFT_PROVEN


def test_authoritative_elementary_is_the_only_certificate():
    st = report_status(proven=True, audited=True, authoritative_elementary=True)
    assert st is ReportStatus.AUTHORITATIVE_ELEMENTARY


# ---- logical prerequisites: incoherent flags fail closed ----------------------------------------

def test_authoritative_flag_cannot_certify_an_unproven_result():
    st = report_status(proven=False, has_candidate=True, audited=True,
                       authoritative_elementary=True)
    assert st is ReportStatus.CANDIDATE_INCOMPLETE


def test_audit_flag_cannot_promote_an_unproven_result():
    assert report_status(proven=False, audited=True) is ReportStatus.REJECTED


def test_status_requires_literal_true_facts():
    assert report_status(
        proven="yes", audited=True, authoritative_elementary=True,
    ) is ReportStatus.REJECTED
    assert report_status(proven=False, has_candidate="yes") is ReportStatus.REJECTED


def test_audited_not_certified_never_reported_as_proven_certificate():
    # An audited-but-not-elementary result must NOT collapse to soft_proven (it carries the audit
    # result) and must NEVER be authoritative.
    st = report_status(proven=True, audited=True, authoritative_elementary=False)
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
    assert params == {
        "proven", "has_candidate", "audited", "authoritative_elementary", "formalized",
    }
    assert not any("score" in p or "fitness" in p for p in params)


# ---- retained-candidate detection is current-run scoped and type-strict -------------------------

def test_result_has_candidate_ignores_unrelated_nodes_retained_in_reused_dag():
    dag = ProofDAG()
    old = dag.get_or_create("old goal A")
    old.proof = '{"claim": "old goal A"}'
    dag.get_or_create("current goal B")

    result = SimpleNamespace(goal="current goal B", candidate=None, ledger=None, dag=dag)
    assert result_has_candidate(result) is False


def test_result_has_candidate_accepts_only_current_root_reachable_proof():
    dag = ProofDAG()
    root = dag.get_or_create("current goal")
    child = dag.get_or_create("current child")
    root.children = [child.key]
    child.proof = '{"claim": "current child"}'

    result = SimpleNamespace(goal="current goal", candidate=None, ledger=None, dag=dag)
    assert result_has_candidate(result) is True


@pytest.mark.parametrize("malformed", [False, 1, [], {}, object(), "   "])
def test_result_has_candidate_rejects_malformed_or_empty_explicit_payloads(malformed):
    result = SimpleNamespace(candidate=malformed, ledger=None, dag=None)
    assert result_has_candidate(result) is False


def test_result_execution_provenance_is_json_safe_and_defensively_copied():
    from agent.gates.lean_audit import (
        ConstDep, DependencyReport, LeanAuditResult, LeanVerdict,
    )

    roles = {
        "prover": {
            "role": "prover", "provider": "codex", "model": "gpt-5.5",
            "effort": "high", "timeout_s": 60, "fallback_selected": True,
        },
    }
    audit = LeanAuditResult(
        verdict=LeanVerdict.PASS,
        report=DependencyReport(
            theorem="T", axioms=["propext"],
            constants=[ConstDep("Nat.add_comm", module="Mathlib")],
            toolchain="leanprover/lean4:v4.30.0",
            manifest="sha256:" + "a" * 64,
            provenance="mathagent-derived-v1",
        ),
        provenance_verified=True,
    )
    result = SimpleNamespace(
        resolved_roles=roles,
        terminal=SimpleNamespace(audit=audit),
        dag=SimpleNamespace(context="c" * 64),
    )

    copied = result_role_provenance(result)
    record = result_audit_record(result)
    roles["prover"]["provider"] = "tampered"
    assert copied["prover"]["provider"] == "codex"
    assert result_proof_context(result) == "c" * 64
    assert record is not None
    assert record["passed"] is True
    assert record["provenance_verified"] is True
    assert record["authoritative"] is True
    assert record["report"]["toolchain"].startswith("leanprover/lean4")
    assert record["report"]["manifest"] == "sha256:" + "a" * 64
    assert record["report"]["provenance"] == "mathagent-derived-v1"
    assert record["report"]["constants"][0]["name"] == "Nat.add_comm"


@pytest.mark.parametrize("provenance_flag,authority_flag", [
    ("true", "true"),  # malformed truthy values
    (True, True),       # exact booleans still need a durable receipt
])
def test_audit_record_never_promotes_unsupported_provenance_flags(
        provenance_flag, authority_flag):
    audit = SimpleNamespace(
        verdict=SimpleNamespace(value="pass"),
        passed=True,
        provenance_verified=provenance_flag,
        authoritative=authority_flag,
        findings=[],
        report=None,
    )
    record = result_audit_record(SimpleNamespace(terminal=SimpleNamespace(audit=audit)))
    assert record is not None
    assert record["passed"] is True
    assert record["provenance_verified"] is False
    assert record["authoritative"] is False


def test_result_proof_context_rejects_truncated_or_malformed_digests():
    assert result_proof_context(SimpleNamespace(dag=SimpleNamespace(context="a" * 12))) is None
    assert result_proof_context(SimpleNamespace(dag=SimpleNamespace(context=True))) is None
