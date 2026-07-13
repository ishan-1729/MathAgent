"""Offline tests for scripts/ablate.py (W6 ablation harness).

No CLI/model/Lean is invoked: the harness builds+runs through an INJECTED stub builder (and, where it
exercises the real builder, only the SCRIPTED provider). We pin:

  * ablate over 2 stub profiles writes exactly 2 diffable result rows with the full schema;
  * each row carries the profile_hash/name/elementarity/proven/reporting_status/budget/wall fields;
  * a profile that raises is captured as a row with ``error`` set (one bad profile never aborts the
    sweep);
  * the reporting_status is the CATEGORICAL ladder value (soft_proven / rejected / ...), never a score;
  * CSV output has a header + one row per profile;
  * collect_profiles assembles a set from a directory and from a base+sweep;
  * a JSONL re-run of the same profiles is byte-identical except for wall_s (reproducible/diffable).
"""
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.gates.lean_audit import (
    ConstDep, DependencyReport, LeanAuditResult, LeanVerdict, DERIVED_PROVENANCE_SCHEMA,
)
from agent.orchestrator.run_profile import (
    ElementarityLevel,
    ProviderKey,
    RoleSpec,
    RolesProfile,
    RunProfile,
    StageProfile,
)

# scripts/ is not a package; load ablate.py by path.
_REPO = Path(__file__).resolve().parents[1]
_ABLATE_PATH = _REPO / "scripts" / "ablate.py"
_spec = importlib.util.spec_from_file_location("ablate_cli", _ABLATE_PATH)
ablate_cli = importlib.util.module_from_spec(_spec)
sys.modules["ablate_cli"] = ablate_cli
_spec.loader.exec_module(ablate_cli)


# --------------------------------------------------------------------------------------------------
# Stub build_and_run results (no driver constructed): just enough surface for run_profile_row.
# --------------------------------------------------------------------------------------------------
class _StubBudget:
    def __init__(self, spent):
        self.calls_spent = spent


class _StubNode:
    class _State:
        name = "PROVEN"
    state = _State()


class _StubDag:
    def __init__(self, n_nodes=1):
        self.nodes = {f"n{i}": _StubNode() for i in range(n_nodes)}


class _StubResult:
    def __init__(self, proven, *, authoritative=False, terminal=None, candidate=None,
                 spent=4, nodes=1, policy_digest=None, resolved_roles=None):
        self.proven = proven
        self._authoritative = authoritative
        self.terminal = terminal
        self.candidate = candidate
        self.budget = _StubBudget(spent)
        self.dag = _StubDag(nodes)
        self.policy_digest = policy_digest
        self.resolved_roles = resolved_roles or {}

    @property
    def authoritative_elementary(self):
        return self._authoritative


def _receipt_terminal(verdict=LeanVerdict.PASS):
    audit = LeanAuditResult(
        verdict=verdict,
        report=DependencyReport(
            theorem="ma_target", axioms=["propext"], constants=[ConstDep("ma_target")],
            toolchain="leanprover/lean4:v4.30.0",
            manifest="sha256:" + "a" * 64,
            provenance=DERIVED_PROVENANCE_SCHEMA,
        ),
        provenance_verified=True,
    )
    return SimpleNamespace(
        authoritative=audit.authoritative,
        certification_trusted=True,
        compiled=True,
        audit=audit,
    )


def _stub_builder(results_by_name):
    """A build_and_run stand-in: returns the canned result for the profile's name."""
    def _b(profile, goal):
        return results_by_name[profile.name]
    return _b


def _profile(name, **kw):
    S = RoleSpec(provider=ProviderKey.scripted)
    kw.setdefault("roles", RolesProfile(prover=S, decomposer=S, reviewer=S, comparator=S, judge=S,
                                        formalizer=S, faithfulness=S, refiner=S))
    return RunProfile(name=name, **kw)


# ---- two stub profiles -> exactly two rows with the full schema ---------------------------------

def test_ablate_two_profiles_writes_two_rows(tmp_path):
    p1 = _profile("offline-a")
    p2 = _profile("offline-b", stages=StageProfile(decompose=False))
    builder = _stub_builder({
        "offline-a": _StubResult(proven=True, spent=5, nodes=2),
        "offline-b": _StubResult(proven=False, spent=3, nodes=1),
    })
    out = tmp_path / "ablation.jsonl"
    rows = ablate_cli.ablate([p1, p2], "G", out, builder=builder, verbose=False)

    assert len(rows) == 2
    written = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(written) == 2
    # full schema on every row.
    for row in written:
        assert set(row) == set(ablate_cli.ROW_FIELDS)
    by_name = {r["name"]: r for r in written}
    assert by_name["offline-a"]["proven"] is True
    assert by_name["offline-a"]["reporting_status"] == "soft_proven"
    assert by_name["offline-a"]["calls_spent"] == 5
    assert by_name["offline-a"]["nodes"] == 2
    assert by_name["offline-b"]["proven"] is False
    assert by_name["offline-b"]["reporting_status"] == "rejected"
    # the profile_hash distinguishes the two profiles.
    assert by_name["offline-a"]["profile_hash"] != by_name["offline-b"]["profile_hash"]
    assert by_name["offline-a"]["profile_hash"] == p1.profile_hash
    assert by_name["offline-a"]["code_revision"]
    assert by_name["offline-a"]["resolved_roles"] == {}


def test_ablate_row_records_actual_provider_and_policy_receipt():
    profile = _profile("receipt")
    result = _StubResult(
        proven=False,
        policy_digest="a" * 64,
        resolved_roles={
            "prover": {
                "role": "prover", "provider": "codex", "model": "gpt-5.5",
                "effort": "high", "timeout_s": 30, "fallback_selected": True,
            },
        },
    )
    row = ablate_cli.run_profile_row(profile, "G", builder=lambda *_args: result)
    assert row["toolkit_policy_sha256"] == "a" * 64
    assert row["resolved_roles"]["prover"]["provider"] == "codex"
    assert row["resolved_roles"]["prover"]["fallback_selected"] is True


# ---- reporting_status is categorical (authoritative when the terminal gate certified) ------------

def test_reporting_status_authoritative_when_terminal_gate_certifies(tmp_path):
    p = _profile("auth")
    builder = _stub_builder({
        "auth": _StubResult(
            proven=True, authoritative=True, terminal=_receipt_terminal()),
    })
    rows = ablate_cli.ablate([p], "G", tmp_path / "a.jsonl", builder=builder, verbose=False)
    assert rows[0]["reporting_status"] == "authoritative_elementary"


def test_reporting_status_audited_not_certified_when_audited_but_not_certified(tmp_path):
    p = _profile("aud")
    builder = _stub_builder({
        "aud": _StubResult(
            proven=True, authoritative=False,
            terminal=_receipt_terminal(LeanVerdict.REJECT)),
    })
    rows = ablate_cli.ablate([p], "G", tmp_path / "a.jsonl", builder=builder, verbose=False)
    assert rows[0]["reporting_status"] == "audited_not_certified"
    assert rows[0]["lean_audit"]["authoritative"] is False


@pytest.mark.parametrize("terminal", [
    type("Untrusted", (), {
        "authoritative": True, "certification_trusted": False, "compiled": True,
        "audit": type("Audit", (), {"passed": True})(),
    })(),
    type("TruthyAuthority", (), {
        "authoritative": "yes", "certification_trusted": True, "compiled": True,
        "audit": type("Audit", (), {"passed": True})(),
    })(),
    type("TruthyTrust", (), {
        "authoritative": True, "certification_trusted": 1, "compiled": True,
        "audit": type("Audit", (), {"passed": True})(),
    })(),
    type("TruthyAudit", (), {
        "authoritative": True, "certification_trusted": True, "compiled": True,
        "audit": type("Audit", (), {"passed": 1})(),
    })(),
    type("UnprovenancedAudit", (), {
        "authoritative": True, "certification_trusted": True, "compiled": True,
        "audit": type("Audit", (), {"passed": True, "authoritative": False})(),
    })(),
])
def test_reporting_authority_rejects_untrusted_or_malformed_terminal_flags(tmp_path, terminal):
    p = _profile("malformed")
    builder = _stub_builder({
        "malformed": _StubResult(proven=True, authoritative=True, terminal=terminal),
    })
    rows = ablate_cli.ablate([p], "G", tmp_path / "a.jsonl", builder=builder, verbose=False)
    assert rows[0]["reporting_status"] == "soft_proven"
    assert rows[0]["lean_audit"] is None


def test_reporting_authority_rejects_truthy_non_boolean_result_proven(tmp_path):
    class _Audit:
        passed = True
    class _Terminal:
        authoritative = True
        certification_trusted = True
        compiled = True
        audit = _Audit()

    p = _profile("truthy-proven")
    builder = _stub_builder({
        "truthy-proven": _StubResult(
            proven="yes", authoritative=True, terminal=_Terminal()),
    })
    rows = ablate_cli.ablate([p], "G", tmp_path / "a.jsonl", builder=builder, verbose=False)
    assert rows[0]["proven"] is False
    assert rows[0]["reporting_status"] == "rejected"


def test_unproven_retained_candidate_is_reported_incomplete(tmp_path):
    p = _profile("candidate")
    builder = _stub_builder({
        "candidate": _StubResult(proven=False, candidate="{\"claim\": \"G\"}"),
    })
    rows = ablate_cli.ablate([p], "G", tmp_path / "a.jsonl", builder=builder, verbose=False)
    assert rows[0]["reporting_status"] == "candidate_incomplete"


@pytest.mark.parametrize("terminal", [
    type("CompileFailed", (), {"compiled": False, "audit": None})(),
    type("AuditMissing", (), {"compiled": True, "audit": None})(),
])
def test_terminal_attempt_without_completed_audit_stays_soft_proven(tmp_path, terminal):
    p = _profile("attempted")
    builder = _stub_builder({
        "attempted": _StubResult(proven=True, authoritative=False, terminal=terminal),
    })
    rows = ablate_cli.ablate([p], "G", tmp_path / "a.jsonl", builder=builder, verbose=False)
    assert rows[0]["reporting_status"] == "soft_proven"


def test_inconsistent_authority_annotation_fails_closed(tmp_path):
    class _Audit:
        passed = True
    class _Terminal:
        compiled = True
        audit = _Audit()
    p = _profile("impossible")
    builder = _stub_builder({
        "impossible": _StubResult(proven=False, authoritative=True, terminal=_Terminal()),
    })
    rows = ablate_cli.ablate([p], "G", tmp_path / "a.jsonl", builder=builder, verbose=False)
    assert rows[0]["reporting_status"] == "rejected"


# ---- a raising profile becomes an error row; the sweep continues --------------------------------

def test_bad_profile_is_captured_as_error_row_and_sweep_continues(tmp_path):
    good = _profile("good")
    bad = _profile("bad")

    def builder(profile, goal):
        if profile.name == "bad":
            raise RuntimeError("boom")
        return _StubResult(proven=True)

    rows = ablate_cli.ablate([bad, good], "G", tmp_path / "a.jsonl", builder=builder, verbose=False)
    assert len(rows) == 2
    by_name = {r["name"]: r for r in rows}
    assert by_name["bad"]["error"] is not None and "boom" in by_name["bad"]["error"]
    assert by_name["bad"]["proven"] is False
    assert by_name["bad"]["reporting_status"] == "rejected"
    # the good profile after the bad one still ran.
    assert by_name["good"]["proven"] is True


# ---- CSV output: header + one row per profile ---------------------------------------------------

def test_csv_output_has_header_and_rows(tmp_path):
    p1 = _profile("c-a")
    p2 = _profile("c-b")
    builder = _stub_builder({"c-a": _StubResult(proven=True), "c-b": _StubResult(proven=False)})
    out = tmp_path / "ablation.csv"
    ablate_cli.ablate([p1, p2], "G", out, builder=builder, verbose=False)
    lines = out.read_text().splitlines()
    assert lines[0].split(",") == ablate_cli.ROW_FIELDS  # header
    assert len([ln for ln in lines if ln]) == 3          # header + 2 rows


# ---- reproducible: a re-run is byte-identical except wall_s -------------------------------------

def test_rerun_is_diffable_except_wall(tmp_path):
    p = _profile("repro")
    builder = _stub_builder({"repro": _StubResult(proven=True, spent=7, nodes=1)})
    r1 = ablate_cli.ablate([p], "G", tmp_path / "1.jsonl", builder=builder, verbose=False)[0]
    r2 = ablate_cli.ablate([p], "G", tmp_path / "2.jsonl", builder=builder, verbose=False)[0]
    r1.pop("wall_s"); r2.pop("wall_s")
    assert r1 == r2


# ---- collect_profiles: from a directory and from a base+sweep -----------------------------------

class _Args:
    profiles_dir = None
    profile = None
    base = None
    sweep = None


def test_sweepable_fields_are_an_explicit_reviewed_allowlist():
    assert set(ablate_cli.SWEEPABLE_STAGE_FIELDS) == {
        "decompose", "review", "population", "evolve_fallback", "refine", "memo",
    }
    assert "h0_consistency" not in ablate_cli.SWEEPABLE_STAGE_FIELDS


def test_collect_profiles_from_directory():
    args = _Args()
    args.profiles_dir = _REPO / "profiles" / "ablation"
    profiles = ablate_cli.collect_profiles(args)
    names = {p.name for p in profiles}
    assert {"ablation-no-decompose", "ablation-no-review", "ablation-no-refine"} <= names
    assert "ablation-no-h0" not in names


def test_collect_profiles_base_sweep_population(tmp_path):
    base = _profile("sweepbase")
    yaml_path = tmp_path / "base.yaml"
    import yaml
    yaml_path.write_text(yaml.safe_dump(base.model_dump(mode="json")), encoding="utf-8")
    args = _Args()
    args.base = yaml_path
    args.sweep = "population=0,2,4"
    profiles = ablate_cli.collect_profiles(args)
    assert [p.stages.population for p in profiles] == [0, 2, 4]
    assert all(p.name.startswith("sweepbase-population=") for p in profiles)


def test_collect_profiles_sweep_bool_field(tmp_path):
    base = _profile("b")
    yaml_path = tmp_path / "b.yaml"
    import yaml
    yaml_path.write_text(yaml.safe_dump(base.model_dump(mode="json")), encoding="utf-8")
    args = _Args()
    args.base = yaml_path
    args.sweep = "refine=false,true"
    profiles = ablate_cli.collect_profiles(args)
    assert [p.stages.refine for p in profiles] == [False, True]


@pytest.mark.parametrize("field", ["bogus", "model_dump", "__class__"])
def test_collect_profiles_rejects_unknown_sweep_field(tmp_path, field):
    base = _profile("b")
    yaml_path = tmp_path / "b.yaml"
    import yaml
    yaml_path.write_text(yaml.safe_dump(base.model_dump(mode="json")), encoding="utf-8")
    args = _Args()
    args.base = yaml_path
    args.sweep = f"{field}=1,2"
    with pytest.raises(ValueError):
        ablate_cli.collect_profiles(args)


def test_collect_profiles_refuses_mandatory_h0_sweep(tmp_path):
    base = _profile("h0-is-not-an-ablation")
    yaml_path = tmp_path / "base.yaml"
    import yaml
    yaml_path.write_text(yaml.safe_dump(base.model_dump(mode="json")), encoding="utf-8")
    args = _Args()
    args.base = yaml_path
    args.sweep = "h0_consistency=true,false"
    with pytest.raises(ValueError, match="mandatory soundness gate"):
        ablate_cli.collect_profiles(args)


@pytest.mark.parametrize("value", ["-1", "1001", "100000"])
def test_collect_profiles_revalidates_sweep_bounds(tmp_path, value):
    base = _profile("bounded")
    yaml_path = tmp_path / "base.yaml"
    import yaml
    yaml_path.write_text(yaml.safe_dump(base.model_dump(mode="json")), encoding="utf-8")
    args = _Args()
    args.base = yaml_path
    args.sweep = f"population={value}"
    # model_copy(update=...) skips Pydantic validation; generated profiles must instead honor the
    # StageProfile population ge=0/le=1000 contract just like a profile loaded directly from YAML.
    with pytest.raises(ValueError):
        ablate_cli.collect_profiles(args)


def test_collect_profiles_empty_selection_raises():
    with pytest.raises(ValueError):
        ablate_cli.collect_profiles(_Args())


# ---- main(): a directory sweep over scripted profiles writes the JSONL --------------------------

def test_main_runs_a_directory_sweep_over_real_scripted_profiles(tmp_path, monkeypatch):
    """End-to-end through main() + the REAL builder on the SCRIPTED-provider ablation profiles written to
    a temp dir (so no backend is touched). Writes one row per profile."""
    # Write two scripted offline profiles into a temp dir.
    pdir = tmp_path / "profs"
    pdir.mkdir()
    import yaml
    for nm, stages in (("offline-x", StageProfile()), ("offline-y", StageProfile(decompose=False))):
        prof = _profile(nm, stages=stages)
        (pdir / f"{nm}.yaml").write_text(yaml.safe_dump(prof.model_dump(mode="json")), encoding="utf-8")

    out = tmp_path / "out.jsonl"
    rc = ablate_cli.main(["G", "--profiles-dir", str(pdir), "--out", str(out), "--quiet"])
    assert rc == 0
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(rows) == 2
    assert {r["name"] for r in rows} == {"offline-x", "offline-y"}
    # Real builder ran: every row carries a reporting_status from the categorical ladder.
    valid = {"rejected", "candidate_incomplete", "soft_proven",
             "audited_not_certified", "authoritative_elementary"}
    assert all(r["reporting_status"] in valid for r in rows)


# ---- durable/no-clobber artifact publication ----------------------------------------------------
def test_existing_output_is_rejected_before_any_profile_runs(tmp_path):
    out = tmp_path / "evidence.jsonl"
    out.write_text("irreplaceable evidence\n", encoding="utf-8")
    calls = []

    with pytest.raises(FileExistsError):
        ablate_cli.ablate(
            [_profile("never-runs")], "G", out,
            builder=lambda profile, goal: calls.append(profile.name), verbose=False,
        )

    assert calls == []
    assert out.read_text(encoding="utf-8") == "irreplaceable evidence\n"

    def replacement_builder(profile, goal):
        # --force does not truncate the old artifact at startup; replacement occurs only after the
        # complete checkpoint has been written and fsynced.
        assert out.read_text(encoding="utf-8") == "irreplaceable evidence\n"
        return _StubResult(proven=True)

    ablate_cli.ablate(
        [_profile("replacement")], "G", out,
        builder=replacement_builder, verbose=False, overwrite=True,
    )
    assert json.loads(out.read_text(encoding="utf-8"))["name"] == "replacement"


def test_jsonl_completed_row_survives_process_level_crash(tmp_path):
    out = tmp_path / "crashed.jsonl"
    first = _profile("first")
    second = _profile("second")

    def builder(profile, goal):
        if profile.name == "second":
            raise KeyboardInterrupt("injected process interruption")
        return _StubResult(proven=True)

    with pytest.raises(KeyboardInterrupt, match="injected process interruption"):
        ablate_cli.ablate([first, second], "G", out, builder=builder, verbose=False)

    assert not out.exists()
    checkpoints = list(tmp_path.glob("crashed.jsonl.*.incomplete"))
    assert len(checkpoints) == 1
    payload = checkpoints[0].read_bytes()
    assert payload.endswith(b"\n")
    persisted = [json.loads(line) for line in payload.decode("utf-8").splitlines()]
    assert len(persisted) == 1 and persisted[0]["name"] == "first"


def test_destination_created_during_run_is_never_clobbered(tmp_path):
    out = tmp_path / "raced.jsonl"

    def builder(profile, goal):
        out.write_text("racing writer\n", encoding="utf-8")
        return _StubResult(proven=True)

    with pytest.raises(FileExistsError):
        ablate_cli.ablate([_profile("race")], "G", out, builder=builder, verbose=False)

    assert out.read_text(encoding="utf-8") == "racing writer\n"
    checkpoint = next(tmp_path.glob("raced.jsonl.*.incomplete"))
    assert json.loads(checkpoint.read_text(encoding="utf-8"))["name"] == "race"


def test_csv_is_complete_and_fsynced_before_publication(monkeypatch, tmp_path):
    out = tmp_path / "atomic.csv"
    captured = {}

    def interrupt_publish(checkpoint, final, *, overwrite=False):
        assert final == out and not final.exists()
        captured["checkpoint"] = checkpoint
        captured["payload"] = checkpoint.read_text(encoding="utf-8")
        raise RuntimeError("injected publication failure")

    monkeypatch.setattr(ablate_cli._artifacts, "publish_single", interrupt_publish)
    profiles = [_profile("csv-a"), _profile("csv-b")]
    builder = _stub_builder({
        "csv-a": _StubResult(proven=True), "csv-b": _StubResult(proven=False),
    })
    with pytest.raises(RuntimeError, match="publication failure"):
        ablate_cli.ablate(profiles, "G", out, builder=builder, verbose=False)

    assert not out.exists()
    assert captured["checkpoint"].exists()
    lines = captured["payload"].splitlines()
    assert lines[0].split(",") == ablate_cli.ROW_FIELDS
    assert len(lines) == 3
