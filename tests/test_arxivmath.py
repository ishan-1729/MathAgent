"""Tests for the ArXivMath adapter + non-contaminative harness (offline synthetic fixture)."""
from pathlib import Path

from agent.benchmarks.arxivmath import (
    ArxivMathDataset, Problem, ScriptedSolver, run_benchmark,
)

FIXTURE = (Path(__file__).resolve().parents[1] /
           "benchmarks" / "datasets" / "arxivmath" / "fixtures" / "sample.jsonl")


def _dataset():
    return ArxivMathDataset.from_jsonl(FIXTURE)


def test_problems_carry_no_answer_field():
    # The contamination guard: a Problem must not expose the answer (or source).
    probs = _dataset().problems()
    assert probs and all(isinstance(p, Problem) for p in probs)
    assert not hasattr(probs[0], "answer")
    assert not hasattr(probs[0], "source")


def test_number_theory_subset_filter():
    ds = _dataset()
    nt = ds.problems(number_theory_only=True)
    allp = ds.problems()
    assert 0 < len(nt) < len(allp)
    assert all(any("number theory" in t.lower() for t in p.problem_type) for p in nt)


def test_oracle_is_separate_from_problems():
    ds = _dataset()
    oracle = ds.oracle()
    assert set(oracle) == {p.idx for p in ds.problems()}
    assert oracle["1"] == "4"


def test_run_benchmark_grades_against_held_out_oracle():
    ds = _dataset()
    # A solver that answers every fixture item correctly (incl. an equivalent-but-reordered set).
    answers = {
        "Compute 2 + 2.": "4",
        "How many primes are strictly less than 10?": "4",
        "Simplify 1/2 + 1/2.": "1",
        "What is the area of a circle of radius 1?": "pi",
        "Find all integers n with n^2 = 9.": "{3, -3}",
    }
    report = run_benchmark(ds, ScriptedSolver(answers))
    assert report.total == 5
    assert report.accuracy == 1.0


def test_solver_only_sees_statements_not_answers():
    ds = _dataset()
    solver = ScriptedSolver({})
    run_benchmark(ds, solver)
    # Everything the solver saw is a statement; no gold answer string leaked into its inputs.
    oracle_values = set(ds.oracle().values())
    assert all(seen not in oracle_values for seen in solver.seen)


def test_wrong_answers_score_zero_and_report_by_type():
    ds = _dataset()
    report = run_benchmark(ds, ScriptedSolver({}, default="definitely wrong"))
    assert report.accuracy == 0.0
    by_type = report.by_type()
    assert "Number Theory" in by_type
    assert by_type["Number Theory"][1] >= 2      # at least 2 NT items present


def test_limit_and_nt_only():
    ds = _dataset()
    report = run_benchmark(ds, ScriptedSolver({}), number_theory_only=True, limit=1)
    assert report.total == 1


def test_per_item_solver_error_is_recorded_not_fatal():
    # A solver that raises (e.g. a Codex timeout) on one item must not abort the whole run.
    class _Boom:
        def __init__(self):
            self.n = 0

        def solve(self, statement):
            self.n += 1
            if self.n == 1:
                raise RuntimeError("simulated timeout")
            return ""

    report = run_benchmark(_dataset(), _Boom())
    assert report.total == 5                 # every item processed despite the first raising
    assert report.n_errors == 1
    assert report.results[0].error is True
    assert report.results[0].correct is False
