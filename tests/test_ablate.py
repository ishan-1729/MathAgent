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

import pytest

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
    def __init__(self, proven, *, authoritative=False, terminal=None, spent=4, nodes=1):
        self.proven = proven
        self._authoritative = authoritative
        self.terminal = terminal
        self.budget = _StubBudget(spent)
        self.dag = _StubDag(nodes)

    @property
    def authoritative_elementary(self):
        return self._authoritative


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


# ---- reporting_status is categorical (authoritative when the terminal gate certified) ------------

def test_reporting_status_authoritative_when_terminal_gate_certifies(tmp_path):
    class _Terminal:
        authoritative = True
    p = _profile("auth")
    builder = _stub_builder({"auth": _StubResult(proven=True, authoritative=True, terminal=_Terminal())})
    rows = ablate_cli.ablate([p], "G", tmp_path / "a.jsonl", builder=builder, verbose=False)
    assert rows[0]["reporting_status"] == "authoritative_elementary"


def test_reporting_status_formalized_not_elementary_when_audited_but_not_certified(tmp_path):
    class _Terminal:
        authoritative = False
    p = _profile("aud")
    builder = _stub_builder({"aud": _StubResult(proven=True, authoritative=False, terminal=_Terminal())})
    rows = ablate_cli.ablate([p], "G", tmp_path / "a.jsonl", builder=builder, verbose=False)
    assert rows[0]["reporting_status"] == "formalized_not_elementary"


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


def test_collect_profiles_from_directory():
    args = _Args()
    args.profiles_dir = _REPO / "profiles" / "ablation"
    profiles = ablate_cli.collect_profiles(args)
    names = {p.name for p in profiles}
    assert {"ablation-no-decompose", "ablation-no-review",
            "ablation-no-h0", "ablation-no-refine"} <= names


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


def test_collect_profiles_rejects_unknown_sweep_field(tmp_path):
    base = _profile("b")
    yaml_path = tmp_path / "b.yaml"
    import yaml
    yaml_path.write_text(yaml.safe_dump(base.model_dump(mode="json")), encoding="utf-8")
    args = _Args()
    args.base = yaml_path
    args.sweep = "bogus=1,2"
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
             "formalized_not_elementary", "authoritative_elementary"}
    assert all(r["reporting_status"] in valid for r in rows)
