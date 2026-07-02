"""Tests for the BrokenArXiv (do-not-prove) adapter (offline synthetic fixture)."""
from pathlib import Path

import yaml

from agent.benchmarks.brokenarxiv import (
    BrokenArxivDataset, BrokenProblem, ScriptedBrokenSolver, normalize_grade,
    JUDGE_MIN, JUDGE_MAX,
)

DATASET_DIR = (Path(__file__).resolve().parents[1] /
               "benchmarks" / "datasets" / "brokenarxiv")
FIXTURE = DATASET_DIR / "fixtures" / "sample.jsonl"
MANIFEST = DATASET_DIR / "manifest.yaml"


def _dataset():
    return BrokenArxivDataset.from_jsonl(FIXTURE)


def test_fixture_loads_with_right_row_count():
    assert len(_dataset()) == 5


def test_problems_show_only_false_statement_not_original():
    # Contamination guard: the solver must not see the held-out TRUE statement or the source.
    probs = _dataset().problems()
    assert probs and all(isinstance(p, BrokenProblem) for p in probs)
    assert not hasattr(probs[0], "original_problem")
    assert not hasattr(probs[0], "source")
    assert "FALSE" in probs[0].statement  # fixture labels the perturbed statement


def test_oracle_holds_original_true_statement_and_points():
    ds = _dataset()
    oracle = ds.oracle()
    assert set(oracle) == {p.idx for p in ds.problems()}
    entry = oracle["1"]
    assert "True original" in entry["original_statement"]
    assert entry["points"] == 2


def test_normalize_grade_maps_0_2_to_0_1():
    assert normalize_grade(0) == 0.0
    assert normalize_grade(1) == 0.5
    assert normalize_grade(2) == 1.0
    # clamps out-of-range
    assert normalize_grade(-1) == 0.0
    assert normalize_grade(5) == 1.0
    assert (JUDGE_MIN, JUDGE_MAX) == (0, 2)


def test_manifest_documents_the_0_2_judge_rubric():
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    rubric = manifest["judge_rubric"]
    assert rubric["scale"] == [0, 2]
    levels = rubric["levels"]
    # all three rubric levels present and describe the intended behavior
    assert set(levels) == {0, 1, 2}
    assert "proved the false statement" in levels[0]
    assert "silent repair" in levels[1].lower() or "silent repair" in levels[1]
    assert "recogniz" in levels[2].lower()
    assert "grade / 2.0" in rubric["normalization"]
    assert manifest["task_type"] == "do-not-prove"


def test_scripted_solver_sees_only_false_statements():
    ds = _dataset()
    solver = ScriptedBrokenSolver({})
    for p in ds.problems():
        solver.solve(p.statement)
    originals = {e["original_statement"] for e in ds.oracle().values()}
    assert all(seen not in originals for seen in solver.seen)
