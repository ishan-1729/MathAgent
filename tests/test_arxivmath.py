"""Tests for the ArXivMath adapter + non-contaminative harness (offline synthetic fixture)."""
from pathlib import Path

import pytest

from agent.benchmarks.arxivmath import (
    ArxivMathDataError, ArxivMathDataset, Problem, ScriptedSolver, run_benchmark,
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


def test_grader_exception_does_not_abort_run(monkeypatch):
    # FIX 3: a grader (answers_equivalent) exception on one item must NOT kill the whole run.
    # It is now caught per-item -> that item is scored incorrect, the run completes, and because the
    # SOLVER did not fail, n_errors stays 0.
    import agent.benchmarks.arxivmath as am

    def _boom(pred, gold, *, atol=1e-9):
        raise ValueError("grader blew up on a malformed answer")

    monkeypatch.setattr(am, "answers_equivalent", _boom)
    report = run_benchmark(_dataset(), ScriptedSolver({}, default="anything"))
    assert report.total == 5
    assert all(r.correct is False for r in report.results)
    assert report.n_errors == 0     # a grader failure is not a solver error
    assert report.n_grade_errors == 5 and report.n_graded == 0
    assert report.graded_accuracy == 0.0 and report.grading_coverage == 0.0
    assert all(r.grade_error and "ValueError" in r.grade_error_detail for r in report.results)
    assert "5 grader errors" in report.summary()


def test_report_discloses_total_answered_accuracy_and_coverage():
    # Solver failures count against the benchmark's total-set headline, while answered-only accuracy
    # and coverage remain available as separate diagnostics.  This prevents 2 correct answers plus a
    # timeout from being advertised as a 100%-accurate benchmark run.
    ds = ArxivMathDataset([
        {"problem_idx": "a", "problem": "Qa", "answer": "1"},
        {"problem_idx": "b", "problem": "Qb", "answer": "2"},   # this one errors
        {"problem_idx": "c", "problem": "Qc", "answer": "3"},
    ])

    class _RaiseOnB:
        def solve(self, statement):
            if statement == "Qb":
                raise RuntimeError("simulated timeout")
            return {"Qa": "1", "Qc": "3"}[statement]           # the other two are correct

    report = run_benchmark(ds, _RaiseOnB())
    assert report.total == 3 and report.n_errors == 1 and report.n_answered == 2
    assert report.accuracy == 2 / 3
    assert report.answered_accuracy == 1.0
    assert report.coverage == 2 / 3
    assert report.by_type().get("(untagged)") == (2, 3)        # primary metric keeps the error row
    assert report.by_type_answered().get("(untagged)") == (2, 2)
    summary = report.summary()
    assert "total accuracy 2/3" in summary
    assert "answered accuracy 2/2" in summary
    assert "coverage 2/3" in summary


def test_all_error_run_accuracy_is_not_a_misleading_fraction():
    # FIX 1: an all-error run has no answered rows; accuracy is 0.0 (never a clean-looking number), and
    # the errors are disclosed via n_errors / the summary, not hidden by folding them into the total.
    ds = ArxivMathDataset([
        {"problem_idx": "a", "problem": "Qa", "answer": "1"},
        {"problem_idx": "b", "problem": "Qb", "answer": "2"},
    ])

    class _AlwaysRaises:
        def solve(self, statement):
            raise RuntimeError("solver down")

    report = run_benchmark(ds, _AlwaysRaises())
    assert report.total == 2 and report.n_errors == 2 and report.n_answered == 0
    assert report.accuracy == 0.0
    assert report.answered_accuracy == 0.0 and report.coverage == 0.0
    assert "2 solver errors" in report.summary()                 # errors are flagged, not swallowed


def test_malformed_dataset_row_is_skipped_not_fatal():
    # A row missing a required field ('problem', 'problem_idx', or held-out 'answer') is skipped, not a
    # fatal KeyError
    # that aborts the whole sweep. oracle() drops the same rows so its idx set stays consistent.
    ds = ArxivMathDataset([
        {"problem_idx": "1", "problem": "Q1", "answer": "a"},
        {"problem_idx": "2", "answer": "b"},                    # missing 'problem'      -> skipped
        {"problem": "Q3", "answer": "c"},                       # missing 'problem_idx'  -> skipped
        {"problem_idx": "3b", "problem": "Q3b"},               # missing gold answer    -> skipped
        {"problem_idx": "4", "problem": "Q4", "answer": "d"},
    ], allow_malformed=True)
    probs = ds.problems()
    assert [p.idx for p in probs] == ["1", "4"]                 # the good rows survive; bad rows dropped
    assert set(ds.oracle()) == {"1", "4"}                       # oracle consistent with problems()
    # end-to-end: the run completes over the surviving problems (no abort).
    report = run_benchmark(ds, ScriptedSolver({"Q1": "a", "Q4": "d"}))
    assert report.total == 2 and report.accuracy == 1.0


def test_non_object_json_rows_are_skipped_not_fatal(tmp_path):
    path = tmp_path / "malformed.jsonl"
    path.write_text(
        'null\n[]\n"text"\n{"problem_idx":"ok","problem":"Q","answer":"1"}\n',
        encoding="utf-8",
    )
    ds = ArxivMathDataset.from_jsonl(path, allow_malformed=True)
    assert [p.idx for p in ds.problems()] == ["ok"]
    assert ds.oracle() == {"ok": "1"}
    assert run_benchmark(ds, ScriptedSolver({"Q": "1"})).accuracy == 1.0


def test_empty_or_all_malformed_release_is_rejected():
    with pytest.raises(ArxivMathDataError, match="no valid, gradable rows"):
        ArxivMathDataset([])
    with pytest.raises(ArxivMathDataError, match="malformed row"):
        ArxivMathDataset([None, {"problem_idx": "x", "problem": "Q"}])


def test_duplicate_ids_after_canonical_coercion_are_rejected():
    with pytest.raises(ArxivMathDataError, match="duplicate problem_idx '1'"):
        ArxivMathDataset([
            {"problem_idx": 1, "problem": "Q1", "answer": "a"},
            {"problem_idx": "1", "problem": "Q2", "answer": "b"},
        ])


def test_duplicate_json_object_keys_are_rejected_with_line_context(tmp_path):
    path = tmp_path / "duplicate-key.jsonl"
    path.write_text(
        '{"problem_idx":"1","problem_idx":"2","problem":"Q","answer":"1"}\n',
        encoding="utf-8",
    )
    with pytest.raises(ArxivMathDataError, match="line 1.*duplicate JSON object key"):
        ArxivMathDataset.from_jsonl(path)


def test_local_jsonl_is_streamed_and_line_bounded(monkeypatch, tmp_path):
    import agent.benchmarks.arxivmath as am

    path = tmp_path / "bounded.jsonl"
    path.write_bytes(b" " * (am._MAX_JSONL_LINE_BYTES + 1) + b"\n")
    monkeypatch.setattr(Path, "read_text",
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("whole-file read")))
    with pytest.raises(ArxivMathDataError, match="line 1 exceeds"):
        ArxivMathDataset.from_jsonl(path)


def test_local_jsonl_row_count_is_bounded_before_accumulation(monkeypatch, tmp_path):
    import agent.benchmarks.arxivmath as am

    path = tmp_path / "too-many.jsonl"
    path.write_text(
        '{"problem_idx":"1","problem":"Q1","answer":"1"}\n'
        '{"problem_idx":"2","problem":"Q2","answer":"2"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(am, "_MAX_DATASET_ROWS", 1)
    with pytest.raises(ArxivMathDataError, match="exceeds 1 rows"):
        ArxivMathDataset.from_jsonl(path)


def test_local_jsonl_has_an_aggregate_byte_budget(monkeypatch, tmp_path):
    import agent.benchmarks.arxivmath as am

    path = tmp_path / "aggregate.jsonl"
    path.write_text(
        '{"problem_idx":"1","problem":"Q1","answer":"1"}\n'
        '{"problem_idx":"2","problem":"Q2","answer":"2"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(am, "_MAX_DATASET_BYTES", 60)
    with pytest.raises(ArxivMathDataError, match="content exceeds 60 bytes"):
        ArxivMathDataset.from_jsonl(path)


def test_schema_coercion_accepts_safe_scalars_but_never_stringifies_containers():
    ds = ArxivMathDataset([
        {"problem_idx": 7, "problem": "  Q  ", "answer": 3, "problem_type": "Algebra, Geometry"},
        {"problem_idx": {"nested": "id"}, "problem": "bad", "answer": "1"},
        {"problem_idx": "bad-answer", "problem": "bad", "answer": [1]},
        {"problem_idx": "bad-type", "problem": "bad", "answer": "1", "problem_type": {"x": 1}},
    ], allow_malformed=True)
    assert ds.problems() == [Problem("7", "Q", ("Algebra", "Geometry"))]
    assert ds.oracle() == {"7": "3"}


def test_oversized_gold_is_rejected_instead_of_becoming_an_ungradable_permanent_miss():
    with pytest.raises(ArxivMathDataError, match="malformed row"):
        ArxivMathDataset([{"problem_idx": "x", "problem": "Q", "answer": "1" * 2001}])


def test_duplicate_problem_types_are_rejected_before_category_aggregation():
    with pytest.raises(ArxivMathDataError, match="duplicate labels"):
        ArxivMathDataset([{
            "problem_idx": "x", "problem": "Q", "answer": "1",
            "problem_type": ["Algebra", " algebra "],
        }])


@pytest.mark.parametrize("limit", [-1, 0, 1.5, True])
def test_run_benchmark_rejects_invalid_api_limit(limit):
    with pytest.raises(ValueError, match="limit"):
        run_benchmark(_dataset(), ScriptedSolver({}), limit=limit)


@pytest.mark.parametrize("atol", [-1, float("inf"), float("nan"), True, "1e-9", 1.0, 1e100])
def test_run_benchmark_rejects_invalid_tolerance(atol):
    with pytest.raises(ValueError, match="atol"):
        run_benchmark(_dataset(), ScriptedSolver({}), atol=atol)


def test_non_string_solver_output_is_a_solver_error_not_a_grader_error():
    class _BadSolver:
        def solve(self, statement):
            return {"answer": 4}

    report = run_benchmark(_dataset(), _BadSolver(), limit=1)
    assert report.n_errors == 1 and report.n_grade_errors == 0
    assert "expected str" in report.results[0].error_detail


def test_oversized_solver_output_is_bounded_before_grading_or_persistence():
    class _HugeSolver:
        def solve(self, statement):
            return "9" * 10_000_000

    report = run_benchmark(_dataset(), _HugeSolver(), limit=1)
    item = report.results[0]
    assert item.error is True and item.correct is False
    assert len(item.predicted) < 1_000
    assert "exceeds 2000 characters" in item.error_detail


def test_non_boolean_grader_result_is_accounted_as_grade_error(monkeypatch):
    import agent.benchmarks.arxivmath as am

    monkeypatch.setattr(am, "answers_equivalent", lambda *args, **kwargs: "yes")
    report = run_benchmark(_dataset(), ScriptedSolver({}, default="x"), limit=1)
    assert report.n_errors == 0 and report.n_grade_errors == 1
    assert report.n_correct == 0
