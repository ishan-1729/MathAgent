"""Offline tests for the deterministic witness-check battery (Mode B; NO LLM, NO eval/exec).

Exercises the committed spec files under benchmarks/evaluation/witness_specs and the
scripts/run_witness_checks.py runner: each CONFIRM spec confirms (score 1.0); each REJECTED control
is rejected with a RECORDED error (never a crash); and the runner writes one well-formed JSONL row
per spec. Everything is deterministic exact-integer arithmetic — no network, no model.
"""
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SPECS_DIR = REPO / "benchmarks" / "evaluation" / "witness_specs"

CONFIRM_SPECS = [
    "amc12_2000_p1_solution_set.json",
    "mordell_k7_no_solutions.json",
    "squares_mod8_cover.json",
]
REJECTED_SPECS = [
    "kissing_number_dim11_REJECTED.json",
    "tensor_rank_444_REJECTED.json",
]


def _load_runner():
    """Import scripts/run_witness_checks.py as a module (it is a script, not a package member)."""
    path = REPO / "scripts" / "run_witness_checks.py"
    spec = importlib.util.spec_from_file_location("run_witness_checks", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


RUNNER = _load_runner()


def test_specs_dir_exists_and_holds_all_five():
    present = {p.name for p in SPECS_DIR.glob("*.json")}
    assert set(CONFIRM_SPECS) | set(REJECTED_SPECS) <= present


@pytest.mark.parametrize("name", CONFIRM_SPECS)
def test_confirm_spec_confirms(name):
    row = RUNNER.check_spec_file(SPECS_DIR / name)
    assert row["valid_schema"] is True
    assert row["confirmed"] is True
    assert row["score"] == 1.0
    assert row["error"] is None


@pytest.mark.parametrize("name", REJECTED_SPECS)
def test_rejected_control_is_rejected_with_recorded_error(name):
    row = RUNNER.check_spec_file(SPECS_DIR / name)
    # The rejection IS the measurement: not confirmed, and an error string is RECORDED (not a crash).
    assert row["confirmed"] is False
    assert row["valid_schema"] is False
    assert row["score"] == 0.0
    assert isinstance(row["error"], str) and row["error"]


def test_runner_writes_well_formed_rows(tmp_path):
    out = tmp_path / "RESULTS.jsonl"
    rows = RUNNER.run(SPECS_DIR, out)
    # One row per spec file, in sorted order, all five present.
    names = [r["spec"] for r in rows]
    assert set(names) == set(CONFIRM_SPECS) | set(REJECTED_SPECS)
    assert names == sorted(names)

    # The file is valid JSONL with exactly the same rows and the required keys on each.
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(rows)
    required = {"spec", "valid_schema", "confirmed", "score", "error"}
    parsed = [json.loads(line) for line in lines]
    for p in parsed:
        assert required <= set(p.keys())
    assert parsed == rows

    # Every CONFIRM spec confirmed and every REJECTED control was rejected-with-error in the written file.
    by_name = {p["spec"]: p for p in parsed}
    for name in CONFIRM_SPECS:
        assert by_name[name]["confirmed"] is True and by_name[name]["error"] is None
    for name in REJECTED_SPECS:
        assert by_name[name]["confirmed"] is False and isinstance(by_name[name]["error"], str)


def test_runner_never_crashes_on_a_rejected_spec_directory(tmp_path):
    """A directory containing ONLY rejected controls still produces recorded rows (no exception)."""
    d = tmp_path / "only_rejected"
    d.mkdir()
    for name in REJECTED_SPECS:
        (d / name).write_text((SPECS_DIR / name).read_text(encoding="utf-8"), encoding="utf-8")
    rows = RUNNER.run(d, tmp_path / "out.jsonl")
    assert len(rows) == len(REJECTED_SPECS)
    assert all(r["confirmed"] is False and r["error"] for r in rows)
