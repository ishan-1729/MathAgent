"""Tests for the MathNet adapter — NT + English filtering (offline synthetic fixture)."""
from pathlib import Path

from agent.benchmarks.mathnet import MathNetDataset, Problem, ScriptedSolver

FIXTURE = (Path(__file__).resolve().parents[1] /
           "benchmarks" / "datasets" / "mathnet" / "fixtures" / "sample.jsonl")


def _dataset():
    return MathNetDataset.from_jsonl(FIXTURE)


def test_fixture_loads_with_right_row_count():
    assert len(_dataset()) == 5   # raw rows, before filtering


def test_problems_carry_no_answer_field():
    # Contamination guard: no final_answer / solutions on a Problem.
    probs = _dataset().problems()  # default NT + English
    assert probs and all(isinstance(p, Problem) for p in probs)
    assert not hasattr(probs[0], "answer")
    assert not hasattr(probs[0], "final_answer")


def test_default_filter_keeps_number_theory_english_plus_nullable_language():
    ds = _dataset()
    probs = ds.problems()  # topic_prefix="Number Theory", language="English"
    ids = {p.idx for p in probs}
    # syn1, syn2 (NT+English) and syn3 (NT, null language — NOT dropped) survive;
    # syn4 (Algebra) and syn5 (French) are filtered out.
    assert ids == {"syn1", "syn2", "syn3"}


def test_disabling_language_filter_admits_french_nt_row():
    ds = _dataset()
    probs = ds.problems(language=None)  # NT topic only, any language
    ids = {p.idx for p in probs}
    assert ids == {"syn1", "syn2", "syn3", "syn5"}  # syn4 is Algebra, still excluded


def test_disabling_topic_filter_admits_all_english_and_null():
    ds = _dataset()
    probs = ds.problems(topic_prefix=None)  # any topic, English (+ null) only
    ids = {p.idx for p in probs}
    # everything except the French row (syn5); syn3's null language is admitted.
    assert ids == {"syn1", "syn2", "syn3", "syn4"}


def test_topic_paths_normalized_and_language_recorded():
    ds = _dataset()
    p = next(p for p in ds.problems() if p.idx == "syn1")
    assert any(path.startswith("Number Theory") for path in p.topics)
    assert p.language == "English"


def test_oracle_holds_final_answer_and_is_separate():
    ds = _dataset()
    oracle = ds.oracle()
    # oracle keys ALL raw rows; problems() is filtered, so oracle is a superset.
    assert oracle["syn1"] == "4"
    assert oracle["syn2"] == "6"
    prob_ids = {p.idx for p in ds.problems()}
    assert prob_ids.issubset(set(oracle))


def test_scripted_solver_sees_only_statements():
    ds = _dataset()
    solver = ScriptedSolver({})
    for p in ds.problems():
        solver.solve(p.statement)
    oracle_values = set(ds.oracle().values())
    assert all(seen not in oracle_values for seen in solver.seen)
