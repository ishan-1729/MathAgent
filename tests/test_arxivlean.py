"""Tests for the ArXivLean adapter (offline synthetic fixture)."""
from pathlib import Path

from agent.benchmarks.arxivlean import (
    ArxivLeanDataset, LeanProblem, ScriptedLeanSolver,
)

FIXTURE = (Path(__file__).resolve().parents[1] /
           "benchmarks" / "datasets" / "arxivlean" / "fixtures" / "sample.jsonl")


def _dataset():
    return ArxivLeanDataset.from_jsonl(FIXTURE)


def test_fixture_loads_with_right_row_count():
    ds = _dataset()
    assert len(ds) == 5


def test_problems_carry_only_formal_statement_no_answer_or_source():
    # Contamination guard: a LeanProblem must not expose the gold answer or the source paper.
    probs = _dataset().problems()
    assert probs and all(isinstance(p, LeanProblem) for p in probs)
    assert not hasattr(probs[0], "answer")
    assert not hasattr(probs[0], "source")
    assert probs[0].formal_statement  # the thing a solver IS handed


def test_oracle_is_separate_and_holds_gold_answer_field():
    ds = _dataset()
    oracle = ds.oracle()
    assert set(oracle) == {p.idx for p in ds.problems()}
    # upstream gold column is `answer`; fixture row 1 closes with rfl
    assert "synthetic_add_zero" in oracle["1"]
    assert "sorry" not in oracle["1"]  # the gold answer closes the statement


def test_sources_held_out_from_problems():
    ds = _dataset()
    srcs = ds.sources()
    assert srcs["1"] == "SYNTHETIC-0001"
    # none of the source ids should appear as a formal_statement a solver sees
    seen_statements = {p.formal_statement for p in ds.problems()}
    assert all(s not in seen_statements for s in srcs.values())


def test_scripted_lean_solver_sees_only_statements():
    ds = _dataset()
    solver = ScriptedLeanSolver({})
    for p in ds.problems():
        solver.solve(p.formal_statement)
    oracle_values = set(ds.oracle().values())
    assert all(seen not in oracle_values for seen in solver.seen)
