"""Tests for the BrokenArXiv (do-not-prove) adapter (offline synthetic fixture)."""
from pathlib import Path

import pytest
import yaml

from agent.benchmarks.brokenarxiv import (
    BrokenArxivDataError, BrokenArxivDataset, BrokenProblem, ScriptedBrokenSolver, normalize_grade,
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


@pytest.mark.parametrize("grade", [True, False, "1", None, [], {}])
def test_normalize_grade_rejects_non_numeric_values(grade):
    with pytest.raises(TypeError, match="finite int or float"):
        normalize_grade(grade)


@pytest.mark.parametrize("grade", [float("nan"), float("inf"), float("-inf")])
def test_normalize_grade_rejects_non_finite_values(grade):
    with pytest.raises(ValueError, match="finite"):
        normalize_grade(grade)


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


def test_duplicate_json_keys_and_canonical_ids_are_rejected(tmp_path):
    duplicate_key = tmp_path / "duplicate-key.jsonl"
    duplicate_key.write_text(
        '{"problem_idx":"1","problem_idx":"2","problem":"false",'
        '"original_problem":"true","points":1}\n',
        encoding="utf-8",
    )
    with pytest.raises(BrokenArxivDataError, match="line 1.*duplicate JSON object key"):
        BrokenArxivDataset.from_jsonl(duplicate_key)
    with pytest.raises(BrokenArxivDataError, match="duplicate problem_idx '1'"):
        BrokenArxivDataset([
            {"problem_idx": 1, "problem": "false 1", "original_problem": "true 1", "points": 1},
            {"problem_idx": "1", "problem": "false 2", "original_problem": "true 2", "points": 1},
        ])


@pytest.mark.parametrize("bad", [
    None,
    {"problem_idx": {"nested": 1}, "problem": "false", "original_problem": "true", "points": 1},
    {"problem_idx": "x", "problem": {"nested": 1}, "original_problem": "true", "points": 1},
    {"problem_idx": "x", "problem": "false", "original_problem": ["true"], "points": 1},
    {"problem_idx": "x", "problem": "false", "original_problem": "true", "points": 1,
     "problem_type": [{"not": "a label"}]},
    {"problem_idx": "x", "problem": "false", "original_problem": "true", "points": 1,
     "unexpected_new_column": "schema drift"},
])
def test_structured_junk_and_schema_drift_are_never_stringified(bad):
    with pytest.raises(BrokenArxivDataError, match="malformed row"):
        BrokenArxivDataset([bad])


def test_local_jsonl_is_streamed_and_bounded(monkeypatch, tmp_path):
    import agent.benchmarks.brokenarxiv as ba

    path = tmp_path / "bounded.jsonl"
    path.write_bytes(b" " * (ba._MAX_JSONL_LINE_BYTES + 1) + b"\n")
    monkeypatch.setattr(Path, "read_text",
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("whole-file read")))
    with pytest.raises(BrokenArxivDataError, match="line 1 exceeds"):
        BrokenArxivDataset.from_jsonl(path)


def test_aliases_must_be_unambiguous():
    with pytest.raises(BrokenArxivDataError, match="multiple aliases supplied for problem_idx"):
        BrokenArxivDataset([{
            "problem_idx": "x", "id": "x", "problem": "false", "original_problem": "true",
            "points": 1,
        }])


def test_local_jsonl_has_an_aggregate_byte_budget(monkeypatch, tmp_path):
    import agent.benchmarks.brokenarxiv as ba

    path = tmp_path / "aggregate.jsonl"
    path.write_text(
        '{"problem_idx":"1","problem":"false 1","original_problem":"true 1","points":1}\n'
        '{"problem_idx":"2","problem":"false 2","original_problem":"true 2","points":1}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(ba, "_MAX_DATASET_BYTES", 100)
    with pytest.raises(BrokenArxivDataError, match="content exceeds 100 bytes"):
        BrokenArxivDataset.from_jsonl(path)
