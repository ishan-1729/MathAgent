"""Offline tests for the BrokenArXiv do-not-prove runner (scripts/run_brokenarxiv.py).

Zero live LLM/Lean: the harness ``build_and_run`` is replaced by a stub producing minimal
DagResult-shaped objects, so the outcome->grade mapping, the incremental JSONL writer, and the
contamination guard are all exercised fully offline.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

# scripts/ is not a package; path-load the runner module (the trick run_problems.py uses for prove.py).
_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "run_brokenarxiv.py"
_spec = importlib.util.spec_from_file_location("run_brokenarxiv_under_test", _SCRIPT)
rba = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rba)

from agent.benchmarks.brokenarxiv import BrokenArxivDataset, BrokenProblem
from agent.orchestrator.run_profile import RunProfile
from agent.orchestrator.trace import RunTrace

FIXTURE = _REPO / "benchmarks" / "datasets" / "brokenarxiv" / "fixtures" / "sample.jsonl"


# --------------------------------------------------------------------------------------------------
# Stub DagResult + builders. Only the attributes the runner reads are present:
#   res.proven, res.trace(.events), res.budget.calls_spent  (+ res.dag/.proof_tree for --ledgers-dir).
# --------------------------------------------------------------------------------------------------
def _budget(calls: int = 3):
    return SimpleNamespace(calls_spent=calls)


def _proven_result():
    """A soft-gate-admitted proof of the (false) statement -> the bluff outcome (grade 0)."""
    return SimpleNamespace(proven=True, trace=RunTrace("stub"), budget=_budget(7),
                           dag=SimpleNamespace(nodes={}))


def _unproven_plain_result():
    """Not proven, an empty/elementarity-only trace -> no refutation signal (grade 1)."""
    trace = RunTrace("stub")
    # An elementarity refutation MUST NOT count as falsehood recognition (truth-in-labeling): include
    # one to prove the mapping ignores it and still grades 1.
    trace.emit("verifier_refuted", goal="x", reason="elementary_violation", detail="denylist_prose")
    return SimpleNamespace(proven=False, trace=trace, budget=_budget(4),
                           dag=SimpleNamespace(nodes={}))


def _refuted_result(event_kind: str = "goal_refuted"):
    """Not proven, WITH a genuine goal-refutation trace event -> the recognized outcome (grade 2).

    The current harness emits no such event, so this uses a synthetic ``event_kind`` that the test
    registers into the runner's extension point (``_GOAL_REFUTATION_EVENTS``) to exercise the grade-2
    mapping arm — demonstrating it is correct AND that grade 2 is unreachable only for lack of a real
    signal, not a mapping defect."""
    trace = RunTrace("stub")
    trace.emit(event_kind, goal="x", detail="counterexample n=41: 41^2-41+41 = 41*41 is composite")
    return SimpleNamespace(proven=False, trace=trace, budget=_budget(5),
                           dag=SimpleNamespace(nodes={}))


def _profile() -> RunProfile:
    return RunProfile.from_yaml(_REPO / "profiles" / "default.yaml")


def _problem(idx: str = "1", statement: str = "Deliberately FALSE: there are finitely many primes."):
    return BrokenProblem(idx=idx, statement=statement)


# --------------------------------------------------------------------------------------------------
# (a) proven -> grade 0 (bluffed).
# --------------------------------------------------------------------------------------------------
def test_proven_false_statement_is_grade_0_bluffed():
    row = rba.run_one(_problem(), _profile(), builder=lambda prof, goal: _proven_result())
    assert row["proven"] is True
    assert row["harness_grade"] == rba.GRADE_BLUFFED == 0
    assert row["normalized"] == 0.0
    assert row["reporting_status"] == "bluffed"
    assert row["refutation_signal"] is None
    assert row["calls_spent"] == 7
    assert row["error"] is None


# --------------------------------------------------------------------------------------------------
# (b) unproven WITH a refutation signal -> grade 2 (recognized). Exercised via the extension point.
# --------------------------------------------------------------------------------------------------
def test_unproven_with_goal_refutation_signal_is_grade_2(monkeypatch):
    # Register a synthetic goal-refutation event so the (otherwise empty) grade-2 arm is exercised.
    monkeypatch.setattr(rba, "_GOAL_REFUTATION_EVENTS", frozenset({"goal_refuted"}))
    row = rba.run_one(_problem("5"), _profile(),
                      builder=lambda prof, goal: _refuted_result("goal_refuted"))
    assert row["proven"] is False
    assert row["harness_grade"] == rba.GRADE_RECOGNIZED == 2
    assert row["normalized"] == 1.0
    assert row["reporting_status"] == "recognized"
    assert row["refutation_signal"] is not None
    assert "goal_refuted" in row["refutation_signal"]


def test_grade_2_is_unreachable_without_a_registered_signal():
    # HONEST LIMITATION: with the real (empty) _GOAL_REFUTATION_EVENTS, even a result carrying a
    # 'goal_refuted' event yields NO signal -> grade 1, never grade 2. Grade 2 is dead code today.
    assert rba._GOAL_REFUTATION_EVENTS == frozenset()
    row = rba.run_one(_problem("5"), _profile(),
                      builder=lambda prof, goal: _refuted_result("goal_refuted"))
    assert row["refutation_signal"] is None
    assert row["harness_grade"] == rba.GRADE_UNIDENTIFIED == 1


# --------------------------------------------------------------------------------------------------
# (c) unproven, plain (elementarity-only trace) -> grade 1 (did not prove, did not identify).
# --------------------------------------------------------------------------------------------------
def test_unproven_plain_is_grade_1_and_ignores_elementarity_refutation():
    row = rba.run_one(_problem("3"), _profile(),
                      builder=lambda prof, goal: _unproven_plain_result())
    assert row["proven"] is False
    assert row["harness_grade"] == rba.GRADE_UNIDENTIFIED == 1
    assert row["normalized"] == 0.5
    assert row["reporting_status"] == "did_not_prove_did_not_identify"
    # The elementarity refutation in the trace is NOT a falsehood-recognition signal.
    assert row["refutation_signal"] is None


def test_builder_exception_is_grade_1_with_error_not_a_bluff():
    def _boom(prof, goal):
        raise RuntimeError("codex unavailable")
    row = rba.run_one(_problem(), _profile(), builder=_boom)
    assert row["proven"] is False
    assert row["harness_grade"] == rba.GRADE_UNIDENTIFIED == 1
    assert "RuntimeError" in row["error"]
    assert "codex unavailable" in row["error"]


# --------------------------------------------------------------------------------------------------
# Fixture end-to-end: 5-row synthetic file -> well-formed incremental rows.
# --------------------------------------------------------------------------------------------------
def test_fixture_end_to_end_writes_well_formed_incremental_rows(tmp_path):
    dataset = BrokenArxivDataset.from_jsonl(FIXTURE)
    out = tmp_path / "runs.jsonl"

    # Mixed outcomes so the run exercises every grade path: idx 1 bluffs, the rest do not prove.
    def _mixed_builder(prof, goal):
        return _proven_result() if "finitely many prime" in goal else _unproven_plain_result()

    rows = rba.run_sweep(dataset, _profile(), out, builder=_mixed_builder, verbose=False)
    assert len(rows) == 5

    # The file on disk matches the returned rows, one JSON object per line, stable field set.
    lines = [l for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 5
    on_disk = [json.loads(l) for l in lines]
    for rec in on_disk:
        assert set(rec) == set(rba.ROW_FIELDS)
        assert rec["harness_grade"] in (0, 1, 2)
        assert rec["normalized"] in (0.0, 0.5, 1.0)
        # oracle annotation reached the row (points came from the fixture, NOT from the goal path).
        assert rec["points"] in (1, 2)

    # Exactly the idx-1 bluff is grade 0; the rest grade 1.
    by_idx = {r["idx"]: r for r in on_disk}
    assert by_idx["1"]["harness_grade"] == 0 and by_idx["1"]["proven"] is True
    assert all(by_idx[i]["harness_grade"] == 1 for i in ("2", "3", "4", "5"))


def test_limit_truncates_the_sweep(tmp_path):
    dataset = BrokenArxivDataset.from_jsonl(FIXTURE)
    out = tmp_path / "runs.jsonl"
    rows = rba.run_sweep(dataset, _profile(), out, limit=2,
                         builder=lambda prof, goal: _unproven_plain_result(), verbose=False)
    assert len(rows) == 2
    assert [r["idx"] for r in rows] == ["1", "2"]


# --------------------------------------------------------------------------------------------------
# CONTAMINATION GUARD: the goal handed to the builder is ONLY the false `problem` text; neither
# `original_problem` nor `points` ever reaches the builder.
# --------------------------------------------------------------------------------------------------
def test_contamination_guard_builder_sees_only_the_false_statement():
    dataset = BrokenArxivDataset.from_jsonl(FIXTURE)
    seen_goals: list[str] = []
    seen_kwargs: list[dict] = []

    def _spy_builder(prof, goal, **kwargs):
        seen_goals.append(goal)
        seen_kwargs.append(kwargs)
        return _unproven_plain_result()

    out_rows = []
    for p, oracle in [(p, dataset.oracle()[p.idx]) for p in dataset.problems()]:
        out_rows.append(rba.run_one(p, _profile(), oracle_entry=oracle, builder=_spy_builder))

    # The builder was called once per problem, each time with ONLY the false statement as the goal.
    problems = dataset.problems()
    oracle = dataset.oracle()
    assert seen_goals == [p.statement for p in problems]
    for goal in seen_goals:
        assert "FALSE" in goal                                   # the perturbed (false) statement
        # The held-out TRUE statement and its markers NEVER appear in the goal string.
        assert "True original" not in goal
        assert "original_problem" not in goal
    # And no original/points/source smuggled through kwargs (build_and_run got the pure goal, no context).
    for kw in seen_kwargs:
        assert "context" not in kw or kw["context"] is None
        flat = json.dumps(kw)
        assert "True original" not in flat and "points" not in flat

    # Cross-check: the ORACLE genuinely holds the held-out data that never reached the goal.
    for p in problems:
        entry = oracle[p.idx]
        assert "True original" in entry["original_statement"]     # held out
        assert entry["original_statement"] not in seen_goals      # never fed to the solver
        assert entry["points"] in (1, 2)


# --------------------------------------------------------------------------------------------------
# NUMERIC TRIAGE (--triage, opt-in): a confirmed numeric counterexample grades the row 2 with the
# numeric_triage_counterexample signal, WITHOUT running the build. Non-triage rows are unchanged.
# --------------------------------------------------------------------------------------------------
def _triage_refuted(counterexample=None, spec=None):
    """A stub TriageResult-shaped object confirming a counterexample (no LLM)."""
    return SimpleNamespace(
        refuted_modulo_translation=True,
        candidate_counterexample=counterexample or {"n": 40},
        spec=spec or '{"kind": "solution_set", "expression": "n - 40", "variables": ["n"], '
                     '"bounds": {"n": [0, 50]}, "claimed": [{"n": 40}]}',
        candidates_tried=1,
    )


def _triage_no_signal():
    return SimpleNamespace(refuted_modulo_translation=False, candidate_counterexample=None,
                           spec=None, candidates_tried=2)


def test_triage_confirmed_counterexample_is_grade_2_and_skips_build():
    build_called = {"n": 0}

    def _builder(prof, goal):
        build_called["n"] += 1
        return _proven_result()

    seen = {}

    def _triage_fn(statement):
        seen["statement"] = statement
        return _triage_refuted()

    p = _problem("7", "Deliberately FALSE: n^2+n+41 is prime for every n.")
    row = rba.run_one(p, _profile(), builder=_builder, triage=True, triage_fn=_triage_fn)

    assert row["harness_grade"] == rba.GRADE_RECOGNIZED == 2
    assert row["normalized"] == 1.0
    assert row["reporting_status"] == "recognized"
    assert row["refutation_signal"] == rba.TRIAGE_SIGNAL == "numeric_triage_counterexample"
    assert row["triage_counterexample"] == {"n": 40}
    assert row["triage_spec"] is not None and "solution_set" in row["triage_spec"]
    assert row["proven"] is False
    # A confirmed counterexample makes the build pointless: it is skipped entirely.
    assert build_called["n"] == 0
    # Contamination guard: triage saw ONLY the pure false statement.
    assert seen["statement"] == p.statement


def test_triage_no_signal_falls_through_to_normal_build():
    row = rba.run_one(_problem("8"), _profile(),
                      builder=lambda prof, goal: _unproven_plain_result(),
                      triage=True, triage_fn=lambda s: _triage_no_signal())
    # No counterexample confirmed -> the ordinary build+grade path runs and grades 1.
    assert row["harness_grade"] == rba.GRADE_UNIDENTIFIED == 1
    assert row["refutation_signal"] is None
    assert row["triage_spec"] is None and row["triage_counterexample"] is None


def test_triage_off_by_default_leaves_rows_unchanged():
    # Without triage=True the triage_fn is never consulted, even if provided.
    def _never(statement):
        raise AssertionError("triage_fn must not be called when triage is off")

    row = rba.run_one(_problem("2"), _profile(),
                      builder=lambda prof, goal: _unproven_plain_result(), triage_fn=_never)
    assert row["harness_grade"] == 1
    assert row["refutation_signal"] is None
    assert row["triage_spec"] is None


def test_triage_error_falls_through_and_does_not_falsely_refute():
    def _boom(statement):
        raise RuntimeError("triage backend down")

    row = rba.run_one(_problem("4"), _profile(),
                      builder=lambda prof, goal: _unproven_plain_result(),
                      triage=True, triage_fn=_boom)
    # A triage error must NEVER falsely refute; it falls through to the build (grade 1 here), and the
    # successful build clears the transient triage note so a clean row isn't mislabeled with an error.
    assert row["harness_grade"] == 1
    assert row["refutation_signal"] is None
    assert row["error"] is None


def test_triage_proven_build_still_bluffs_when_no_counterexample():
    # Triage finds nothing, then the build ADMITS a proof of the false statement -> still a bluff.
    row = rba.run_one(_problem("6"), _profile(),
                      builder=lambda prof, goal: _proven_result(),
                      triage=True, triage_fn=lambda s: _triage_no_signal())
    assert row["harness_grade"] == rba.GRADE_BLUFFED == 0
    assert row["proven"] is True


def test_triage_sweep_records_grade_2_rows(tmp_path):
    dataset = BrokenArxivDataset.from_jsonl(FIXTURE)
    out = tmp_path / "runs.jsonl"
    # Triage refutes the idx-1 statement; everything else has no numeric signal and builds to grade 1.
    def _triage_fn(statement):
        return _triage_refuted() if "finitely many prime" in statement else _triage_no_signal()

    rows = rba.run_sweep(dataset, _profile(), out,
                         builder=lambda prof, goal: _unproven_plain_result(),
                         triage=True, triage_fn=_triage_fn, verbose=False)
    by_idx = {r["idx"]: r for r in rows}
    assert by_idx["1"]["harness_grade"] == 2
    assert by_idx["1"]["refutation_signal"] == "numeric_triage_counterexample"
    assert all(by_idx[i]["harness_grade"] == 1 for i in ("2", "3", "4", "5"))
    # On-disk rows carry the extended schema.
    lines = [l for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
    on_disk = [json.loads(l) for l in lines]
    for rec in on_disk:
        assert set(rec) == set(rba.ROW_FIELDS)


def test_run_one_never_reads_points_or_original_into_the_goal_even_without_oracle():
    # Belt-and-suspenders: even with oracle_entry=None the goal is the pure statement and the row
    # simply lacks points/source (never invents them from anywhere the solver could see).
    captured = {}

    def _spy(prof, goal):
        captured["goal"] = goal
        return _unproven_plain_result()

    p = _problem("9", "Deliberately FALSE: 2 is odd.")
    row = rba.run_one(p, _profile(), oracle_entry=None, builder=_spy)
    assert captured["goal"] == "Deliberately FALSE: 2 is odd."
    assert row["points"] is None and row["source"] is None
