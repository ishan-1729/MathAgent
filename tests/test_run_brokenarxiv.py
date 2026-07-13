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

from agent.benchmarks.brokenarxiv import (BrokenArxivDataError, BrokenArxivDataset,
                                          BrokenProblem)
from agent.orchestrator.run_profile import RunProfile
from agent.orchestrator.trace import RunTrace

FIXTURE = _REPO / "benchmarks" / "datasets" / "brokenarxiv" / "fixtures" / "sample.jsonl"


@pytest.fixture(autouse=True)
def _stable_repo_revision_check(monkeypatch):
    """Avoid recomputing the large shared dirty diff in every offline sweep test."""
    real = rba._artifacts.code_revision
    start = rba._code_revision()

    def _revision(path):
        return start if Path(path).resolve() == _REPO.resolve() else real(path)

    monkeypatch.setattr(rba._artifacts, "code_revision", _revision)


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


def test_builder_exception_is_ungraded_with_error_not_a_false_score():
    def _boom(prof, goal):
        raise RuntimeError("codex unavailable")
    row = rba.run_one(_problem(), _profile(), builder=_boom)
    assert row["proven"] is None
    assert row["harness_grade"] is None and row["normalized"] is None
    assert row["reporting_status"] is None and row["outcome_kind"] == "builder_error"
    assert "RuntimeError" in row["error"]
    assert "codex unavailable" in row["error"]


def test_artifact_failure_does_not_erase_proven_bluff(monkeypatch, tmp_path):
    def _artifact_boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(rba, "_dump_ledgers", _artifact_boom)
    row = rba.run_one(_problem(), _profile(), builder=lambda prof, goal: _proven_result(),
                      ledgers_dir=tmp_path / "ledgers")
    assert row["proven"] is True
    assert row["harness_grade"] == rba.GRADE_BLUFFED
    assert row["reporting_status"] == "bluffed"
    assert row["error"] is None                 # build/run outcome remains a valid measured row
    assert "OSError: disk full" in row["artifact_error"]


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


def _boom_builder(prof, goal):
    raise RuntimeError("build backend down")


# --------------------------------------------------------------------------------------------------
# An all-crash sweep must NOT print a clean-looking headline. run_sweep excludes ungradable rows; when
# every row errored it suppresses the
# headline entirely.
# --------------------------------------------------------------------------------------------------
def test_all_error_sweep_suppresses_headline(tmp_path, capsys):
    dataset = BrokenArxivDataset.from_jsonl(FIXTURE)
    rba.run_sweep(dataset, _profile(), tmp_path / "o.jsonl", builder=_boom_builder, verbose=True)
    err = capsys.readouterr().err
    # No misleading bluff_rate headline; an explicit all-errored line instead.
    assert "NO GRADABLE ROWS (builder errors=5/5; grader errors=0/5)" in err
    assert "headline suppressed" in err
    assert "bluff_rate" not in err


def test_mixed_error_sweep_reports_error_count_and_nonerror_denominator(tmp_path, capsys):
    dataset = BrokenArxivDataset.from_jsonl(FIXTURE)

    # idx-1 ("finitely many prime") bluffs; the other four crash -> 1 non-error bluff, 4 errors.
    def _mixed(prof, goal):
        if "finitely many prime" in goal:
            return _proven_result()
        raise RuntimeError("build backend down")

    rba.run_sweep(dataset, _profile(), tmp_path / "o.jsonl", builder=_mixed, verbose=True)
    err = capsys.readouterr().err
    assert "errors = 4/5" in err
    # bluff-rate denominator is the one gradable row, not the full five.
    assert "(1/1 gradable rows)" in err
    assert "= 1.0000" in err  # 1 bluff / 1 non-error row


def test_no_error_sweep_reports_zero_errors_and_full_denominator(tmp_path, capsys):
    dataset = BrokenArxivDataset.from_jsonl(FIXTURE)
    rba.run_sweep(dataset, _profile(), tmp_path / "o.jsonl",
                  builder=lambda prof, goal: _unproven_plain_result(), verbose=True)
    err = capsys.readouterr().err
    # No bluffs, all 5 rows are non-error -> bluff_rate 0 over 5, errors 0/5.
    assert "(0/5 gradable rows)" in err
    assert "builder_errors = 0/5" in err
    assert "mean normalized = 0.5000" in err


# --------------------------------------------------------------------------------------------------
# FIX 2: ledger filenames disambiguate same-named profiles and cannot escape ledgers_dir.
# --------------------------------------------------------------------------------------------------
def test_ledger_filename_disambiguates_and_stays_in_dir(tmp_path):
    from agent.orchestrator.run_profile import StageProfile

    p = _problem("1")
    ldir = tmp_path / "ledgers"
    base = _profile()
    # Two DIFFERENT profiles sharing a name -> distinct files (profile_hash discriminator).
    p1 = base.model_copy(update={"name": "dup", "stages": StageProfile()})
    p2 = base.model_copy(update={"name": "dup", "stages": StageProfile(decompose=False)})
    assert p1.profile_hash != p2.profile_hash
    rba.run_one(p, p1, builder=lambda prof, goal: _proven_result(), ledgers_dir=ldir)
    rba.run_one(p, p2, builder=lambda prof, goal: _proven_result(), ledgers_dir=ldir)
    files = sorted(f.name for f in ldir.glob("*.json"))
    assert len(files) == 2 and files[0] != files[1]

    # A path-traversing name is sanitized -> stays inside ledgers_dir.
    evil = base.model_copy(update={"name": "../evil"})
    rba.run_one(p, evil, builder=lambda prof, goal: _proven_result(), ledgers_dir=ldir)
    assert not list(tmp_path.glob("*evil*.json"))     # never escaped one level up
    inside = [f for f in ldir.glob("*.json") if "evil" in f.name]
    assert len(inside) == 1 and inside[0].parent == ldir


# --------------------------------------------------------------------------------------------------
# FIX 4 (residual): the UNTRUSTED idx component (dataset row's problem_idx/id/source) is sanitized too,
# so a row with idx='../pwned' cannot write one directory ABOVE ledgers_dir. Same class as the
# profile-name traversal above, but on the dataset-derived idx the earlier fix left unsanitized.
# --------------------------------------------------------------------------------------------------
def test_untrusted_idx_cannot_escape_ledgers_dir(tmp_path):
    ldir = tmp_path / "ledgers"
    evil = _problem(idx="../pwned")     # idx comes from the dataset row -> untrusted content
    rba.run_one(evil, _profile(), builder=lambda prof, goal: _proven_result(), ledgers_dir=ldir)

    # The escaped parent path did NOT get the file...
    assert not list(tmp_path.glob("*pwned*.json")), "idx traversal escaped ledgers_dir"
    assert not (tmp_path / "pwned").exists()
    # ...the sanitized name landed INSIDE ledgers_dir, in a single component (no subdirectory created).
    inside = list(ldir.glob("*.json"))
    assert len(inside) == 1
    assert inside[0].parent == ldir
    assert "pwned" in inside[0].name          # the sanitized idx is still recognizable
    assert "/" not in inside[0].name and "\\" not in inside[0].name
    # Direct helper check: the filename has no path separators / traversal after sanitization.
    fname = rba._ledger_filename("../pwned", _profile())
    assert fname.startswith(".._pwned__") and "/" not in fname and "\\" not in fname


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
# NUMERIC TRIAGE (--triage, opt-in): confirmed spec evidence is diagnostic only; it never awards grade 2
# and never skips the ordinary build.
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


def test_triage_confirmed_candidate_is_diagnostic_and_does_not_skip_build():
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

    assert row["harness_grade"] == rba.GRADE_BLUFFED == 0  # builder's proven result remains decisive
    assert row["normalized"] == 0.0
    assert row["reporting_status"] == "bluffed"
    assert row["refutation_signal"] is None
    assert row["triage_signal"] == rba.TRIAGE_SIGNAL
    assert row["triage_status"] == "candidate_confirmed_modulo_translation"
    assert row["triage_counterexample"] == {"n": 40}
    assert row["triage_spec"] is not None and "solution_set" in row["triage_spec"]
    assert row["proven"] is True
    assert build_called["n"] == 1
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
    # A triage error must NEVER falsely refute; it falls through to the build (grade 1 here). It is not
    # a builder error, but its provenance remains visible in the dedicated triage_error field.
    assert row["harness_grade"] == 1
    assert row["refutation_signal"] is None
    assert row["error"] is None
    assert "RuntimeError: triage backend down" in row["triage_error"]


def test_triage_proven_build_still_bluffs_when_no_counterexample():
    # Triage finds nothing, then the build ADMITS a proof of the false statement -> still a bluff.
    row = rba.run_one(_problem("6"), _profile(),
                      builder=lambda prof, goal: _proven_result(),
                      triage=True, triage_fn=lambda s: _triage_no_signal())
    assert row["harness_grade"] == rba.GRADE_BLUFFED == 0
    assert row["proven"] is True


def test_triage_sweep_records_diagnostic_without_changing_grades(tmp_path):
    dataset = BrokenArxivDataset.from_jsonl(FIXTURE)
    out = tmp_path / "runs.jsonl"
    # Triage refutes the idx-1 statement; everything else has no numeric signal and builds to grade 1.
    def _triage_fn(statement):
        return _triage_refuted() if "finitely many prime" in statement else _triage_no_signal()

    rows = rba.run_sweep(dataset, _profile(), out,
                         builder=lambda prof, goal: _unproven_plain_result(),
                         triage=True, triage_fn=_triage_fn, verbose=False)
    by_idx = {r["idx"]: r for r in rows}
    assert by_idx["1"]["harness_grade"] == 1
    assert by_idx["1"]["refutation_signal"] is None
    assert by_idx["1"]["triage_signal"] == rba.TRIAGE_SIGNAL
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


def test_truthy_non_boolean_proven_is_grade_error_not_a_false_bluff():
    malformed = SimpleNamespace(proven="false", trace=RunTrace("stub"), budget=_budget(),
                                dag=SimpleNamespace(nodes={}))
    row = rba.run_one(_problem(), _profile(), builder=lambda prof, goal: malformed)
    assert row["proven"] is None
    assert row["harness_grade"] is None and row["normalized"] is None
    assert row["outcome_kind"] == "grade_error"
    assert "result.proven must be bool" in row["grade_error"]
    assert row["error"] is None


def test_grade_errors_are_excluded_from_benchmark_headline(tmp_path, capsys):
    dataset = BrokenArxivDataset.from_jsonl(FIXTURE)
    malformed = SimpleNamespace(proven=None, trace=None, budget=None, dag=SimpleNamespace(nodes={}))
    rows = rba.run_sweep(dataset, _profile(), tmp_path / "bad.jsonl",
                         builder=lambda prof, goal: malformed, verbose=True)
    assert all(row["grade_error"] for row in rows)
    err = capsys.readouterr().err
    assert "NO GRADABLE ROWS (builder errors=0/5; grader errors=5/5)" in err
    assert "bluff_rate" not in err


@pytest.mark.parametrize("limit", [-1, 0, 1.2, True, rba._MAX_LIMIT + 1])
def test_run_sweep_rejects_invalid_limit_without_creating_output(tmp_path, limit):
    out = tmp_path / "o.jsonl"
    with pytest.raises(ValueError, match="limit"):
        rba.run_sweep(BrokenArxivDataset.from_jsonl(FIXTURE), _profile(), out,
                      limit=limit, builder=lambda prof, goal: _unproven_plain_result(), verbose=False)
    assert not out.exists()


def test_main_rejects_negative_limit_cleanly(capsys):
    assert rba.main(["--jsonl", str(FIXTURE), "--limit", "-1"]) == 2
    assert "--limit must be >= 0" in capsys.readouterr().err


def test_empty_duplicate_and_blank_dataset_views_fail_before_output(tmp_path):
    cases = [
        [],
        [
            {"problem_idx": 1, "problem": "false A", "points": 1},
            {"problem_idx": "1", "problem": "false B", "points": 1},
        ],
        [{"problem_idx": "", "problem": "false", "points": 1}],
        [{"problem_idx": "x", "problem": "", "points": 1}],
        [{"problem_idx": "x", "problem": {"structured": "junk"}, "points": 1}],
    ]
    for i, raw in enumerate(cases):
        out = tmp_path / f"{i}.jsonl"
        with pytest.raises(BrokenArxivDataError):
            BrokenArxivDataset(raw)
        assert not out.exists()


def test_invalid_oracle_points_fail_before_model_calls(tmp_path):
    calls = {"n": 0}

    def _builder(prof, goal):
        calls["n"] += 1
        return _unproven_plain_result()

    with pytest.raises(BrokenArxivDataError, match="positive integer"):
        BrokenArxivDataset([{
            "problem_idx": "x", "problem": "false", "original_problem": "true",
            "points": float("nan"),
        }])
    assert calls["n"] == 0


def test_missing_points_cannot_silently_bias_weighted_headline(tmp_path):
    with pytest.raises(BrokenArxivDataError, match="positive integer"):
        BrokenArxivDataset([{
            "problem_idx": "x", "problem": "false", "original_problem": "true",
        }])


@pytest.mark.parametrize("triage_result", [
    SimpleNamespace(refuted_modulo_translation="true", spec="{}", candidate_counterexample={"n": 1}),
    SimpleNamespace(refuted_modulo_translation=True, spec=None, candidate_counterexample={"n": 1}),
    SimpleNamespace(refuted_modulo_translation=True, spec="{}", candidate_counterexample={"n": True}),
    SimpleNamespace(),
])
def test_malformed_triage_result_cannot_create_grade_2(triage_result):
    row = rba.run_one(_problem(), _profile(), builder=lambda prof, goal: _unproven_plain_result(),
                      triage=True, triage_fn=lambda statement: triage_result)
    assert row["harness_grade"] == rba.GRADE_UNIDENTIFIED
    assert row["refutation_signal"] is None
    assert row["triage_error"]


def test_unconfirmed_triage_spec_cannot_create_grade_2_from_a_claimed_boolean():
    fake = SimpleNamespace(
        refuted_modulo_translation=True,
        spec='{"kind":"solution_set","expression":"n-1","variables":["n"],'
             '"bounds":{"n":[0,2]},"claimed":[{"n":2}]}',  # checker finds n=1, not claimed n=2
        candidate_counterexample={"n": 2},
    )
    row = rba.run_one(_problem(), _profile(), builder=lambda prof, goal: _unproven_plain_result(),
                      triage=True, triage_fn=lambda statement: fake)
    assert row["harness_grade"] == rba.GRADE_UNIDENTIFIED
    assert row["refutation_signal"] is None
    assert "does not deterministically confirm" in row["triage_error"]


def test_bad_oracle_metadata_is_recorded_without_changing_valid_grade():
    row = rba.run_one(_problem(), _profile(), oracle_entry=["not", "a", "mapping"],
                      builder=lambda prof, goal: _proven_result())
    assert row["harness_grade"] == rba.GRADE_BLUFFED and row["proven"] is True
    assert row["metadata_error"] == "oracle metadata is not a mapping"


def test_invalid_calls_spent_is_not_published_as_provenance():
    malformed_budget = SimpleNamespace(calls_spent="many")
    result = SimpleNamespace(proven=False, trace=RunTrace("stub"), budget=malformed_budget,
                             dag=SimpleNamespace(nodes={}))
    row = rba.run_one(_problem(), _profile(), builder=lambda prof, goal: result)
    assert row["harness_grade"] == rba.GRADE_UNIDENTIFIED
    assert row["calls_spent"] is None
    assert "budget.calls_spent" in row["metadata_error"]


def test_point_weighted_and_item_metrics_are_reported_separately(tmp_path, capsys):
    dataset = BrokenArxivDataset([
        {"problem_idx": "heavy", "problem": "false heavy", "original_problem": "true heavy",
         "points": 9},
        {"problem_idx": "light", "problem": "false light", "original_problem": "true light",
         "points": 1},
    ])

    def _builder(prof, goal):
        return _proven_result() if "heavy" in goal else _unproven_plain_result()

    rba.run_sweep(dataset, _profile(), tmp_path / "weighted.jsonl", builder=_builder, verbose=True)
    err = capsys.readouterr().err
    assert "item_bluff_rate" in err and "= 0.5000 (1/2 gradable rows)" in err
    assert "point_weighted_bluff_rate = 0.9000 (9/10 points)" in err


def test_error_rows_serialize_null_scores(tmp_path):
    out = tmp_path / "errors.jsonl"
    rows = rba.run_sweep(BrokenArxivDataset.from_jsonl(FIXTURE), _profile(), out,
                         builder=_boom_builder, verbose=False)
    assert all(r["harness_grade"] is None and r["normalized"] is None for r in rows)
    persisted = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert all(r["harness_grade"] is None and r["reporting_status"] is None for r in persisted)


def test_existing_output_is_preserved_unless_overwrite_is_explicit(tmp_path):
    out = tmp_path / "evidence.jsonl"
    out.write_text("do not destroy\n", encoding="utf-8")
    with pytest.raises(FileExistsError):
        rba.run_sweep(BrokenArxivDataset.from_jsonl(FIXTURE), _profile(), out,
                      builder=lambda prof, goal: _unproven_plain_result(), verbose=False)
    assert out.read_text(encoding="utf-8") == "do not destroy\n"
    rows = rba.run_sweep(BrokenArxivDataset.from_jsonl(FIXTURE), _profile(), out, limit=1,
                         builder=lambda prof, goal: _unproven_plain_result(), verbose=False,
                         overwrite=True)
    assert len(rows) == 1 and "do not destroy" not in out.read_text(encoding="utf-8")


def test_destination_created_during_run_is_never_clobbered(tmp_path):
    out = tmp_path / "raced.jsonl"

    def _builder(prof, goal):
        out.write_text("racing writer\n", encoding="utf-8")
        return _unproven_plain_result()

    with pytest.raises(FileExistsError):
        rba.run_sweep(BrokenArxivDataset.from_jsonl(FIXTURE), _profile(), out, limit=1,
                      builder=_builder, verbose=False)
    assert out.read_text(encoding="utf-8") == "racing writer\n"
    assert list(tmp_path.glob("*.incomplete"))  # our completed checkpoint is retained for recovery
    checkpoint = next(tmp_path.glob("*.incomplete"))
    assert Path(f"{checkpoint}.receipt.json").exists()


def test_resume_validates_prefix_discards_partial_tail_and_runs_only_unpaid_rows(tmp_path):
    dataset = BrokenArxivDataset.from_jsonl(FIXTURE)
    profile = _profile()
    out = tmp_path / "resume.jsonl"
    first_calls = {"n": 0}

    def _interrupt_after_one(prof, goal):
        first_calls["n"] += 1
        if first_calls["n"] == 2:
            raise KeyboardInterrupt("simulated process interruption")
        return _unproven_plain_result()

    with pytest.raises(KeyboardInterrupt):
        rba.run_sweep(dataset, profile, out, builder=_interrupt_after_one, verbose=False)
    checkpoint = next(tmp_path.glob("resume.jsonl.*.incomplete"))
    with checkpoint.open("ab") as fh:
        fh.write(b'{"partial":')  # no newline: never a committed/fsynced row receipt

    resumed_goals = []

    def _resume_builder(prof, goal):
        resumed_goals.append(goal)
        return _unproven_plain_result()

    rows = rba.run_sweep(dataset, profile, out, builder=_resume_builder, verbose=False,
                         resume_checkpoint=checkpoint)
    assert len(rows) == 5 and len(resumed_goals) == 4
    assert rows[0]["idx"] == "1" and rows[0]["runtime_versions"]["python"]
    assert [json.loads(line)["idx"] for line in out.read_text(encoding="utf-8").splitlines()] \
        == ["1", "2", "3", "4", "5"]
    assert not checkpoint.exists() and not Path(f"{checkpoint}.receipt.json").exists()


def test_resume_rejects_tampered_row_before_builder_call(tmp_path):
    dataset = BrokenArxivDataset.from_jsonl(FIXTURE)
    profile = _profile()
    out = tmp_path / "tampered.jsonl"
    n = {"value": 0}

    def _interrupt_after_one(prof, goal):
        n["value"] += 1
        if n["value"] == 2:
            raise KeyboardInterrupt
        return _unproven_plain_result()

    with pytest.raises(KeyboardInterrupt):
        rba.run_sweep(dataset, profile, out, builder=_interrupt_after_one, verbose=False)
    checkpoint = next(tmp_path.glob("tampered.jsonl.*.incomplete"))
    row = json.loads(checkpoint.read_text(encoding="utf-8"))
    row["points"] = 999
    checkpoint.write_text(json.dumps(row) + "\n", encoding="utf-8")
    calls = {"n": 0}

    def _never(prof, goal):
        calls["n"] += 1
        return _unproven_plain_result()

    with pytest.raises(ValueError, match="oracle annotations"):
        rba.run_sweep(dataset, profile, out, builder=_never, verbose=False,
                      resume_checkpoint=checkpoint)
    assert calls["n"] == 0 and checkpoint.exists()


def test_resume_rejects_same_ids_with_different_dataset_content(tmp_path):
    def _rows(label):
        return [
            {"problem_idx": "1", "problem": f"false {label} one",
             "original_problem": f"true {label} one", "points": 1, "source": "same-1"},
            {"problem_idx": "2", "problem": f"false {label} two",
             "original_problem": f"true {label} two", "points": 1, "source": "same-2"},
        ]

    dataset_a = BrokenArxivDataset(_rows("A"), release="same")
    dataset_b = BrokenArxivDataset(_rows("B"), release="same")
    out = tmp_path / "mixed.jsonl"
    calls = {"n": 0}

    def _interrupt(prof, goal):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        return _unproven_plain_result()

    with pytest.raises(KeyboardInterrupt):
        rba.run_sweep(dataset_a, _profile(), out, builder=_interrupt, verbose=False)
    checkpoint = next(tmp_path.glob("mixed.jsonl.*.incomplete"))
    resumed = {"n": 0}
    with pytest.raises(ValueError, match=r"dataset_(?:content_)?sha256"):
        rba.run_sweep(
            dataset_b,
            _profile(),
            out,
            builder=lambda prof, goal: resumed.__setitem__("n", resumed["n"] + 1),
            verbose=False,
            resume_checkpoint=checkpoint,
        )
    assert resumed["n"] == 0


def test_concurrent_resume_is_rejected_before_duplicate_paid_work(tmp_path):
    dataset = BrokenArxivDataset.from_jsonl(FIXTURE)
    profile = _profile()
    out = tmp_path / "locked.jsonl"

    with pytest.raises(KeyboardInterrupt):
        rba.run_sweep(dataset, profile, out, verbose=False,
                      builder=lambda prof, goal: (_ for _ in ()).throw(KeyboardInterrupt()))
    checkpoint = next(tmp_path.glob("locked.jsonl.*.incomplete"))
    lock_fh = rba._artifacts.acquire_checkpoint_lock(checkpoint)
    calls = {"n": 0}
    try:
        with pytest.raises(OSError):
            rba.run_sweep(
                dataset,
                profile,
                out,
                verbose=False,
                resume_checkpoint=checkpoint,
                builder=lambda prof, goal: calls.__setitem__("n", calls["n"] + 1),
            )
    finally:
        lock_fh.close()
    assert calls["n"] == 0


def test_persisted_rows_carry_run_dataset_profile_and_code_provenance(tmp_path):
    out = tmp_path / "provenance.jsonl"
    rows = rba.run_sweep(BrokenArxivDataset.from_jsonl(FIXTURE), _profile(), out, limit=1,
                         builder=lambda prof, goal: _unproven_plain_result(), verbose=False,
                         dataset_source="jsonl:/fixture", dataset_sha256="a" * 64)
    row = rows[0]
    assert row["release"] == "sample" and row["dataset_source"] == "jsonl:/fixture"
    assert row["dataset_sha256"] == "a" * 64 and row["dataset_content_sha256"]
    assert row["code_revision"] == rba._code_revision()
    assert row["profile_name"] == _profile().name and row["profile_hash"] == _profile().profile_hash
    assert row["run_id"] and row["limit"] == 1 and row["triage_enabled"] is False


def test_ledger_names_hash_raw_ids_and_never_overwrite(tmp_path):
    profile = _profile()
    assert (rba._ledger_filename("a/b", profile, artifact_id="same")
            != rba._ledger_filename("a?b", profile, artifact_id="same"))
    ldir = tmp_path / "ledgers"
    for _ in range(2):
        rba.run_one(_problem(idx="same"), profile, builder=lambda prof, goal: _proven_result(),
                    ledgers_dir=ldir)
    assert len(list(ldir.glob("*.json"))) == 2


def test_malformed_proven_value_cannot_make_ledger_claim_true(tmp_path):
    malformed = SimpleNamespace(proven="false", trace=RunTrace("stub"), budget=_budget(),
                                dag=SimpleNamespace(nodes={}))
    row = rba.run_one(_problem(), _profile(), builder=lambda prof, goal: malformed,
                      ledgers_dir=tmp_path / "ledgers")
    assert row["grade_error"] and row["ledger_path"]
    artifact = json.loads(Path(row["ledger_path"]).read_text(encoding="utf-8"))
    assert artifact["proven"] is None


def test_default_triage_discloses_call_and_ambiguous_backend_status(monkeypatch):
    monkeypatch.setattr(rba, "_default_triage_fn", lambda: (lambda statement: _triage_no_signal()))
    row = rba.run_one(_problem(), _profile(), builder=lambda prof, goal: _unproven_plain_result(),
                      triage=True)
    assert row["triage_calls_attempted"] == 1 and row["triage_calls_spent"] is None
    assert row["triage_status"] == "no_signal_or_backend_failure"


def test_main_rejects_jsonl_hf_flags_and_input_overwrite(capsys, tmp_path):
    assert rba.main(["--jsonl", str(FIXTURE), "--split", "train",
                     "--out", str(tmp_path / "x.jsonl")]) == 2
    assert "apply only with --hf-config" in capsys.readouterr().err
    assert rba.main(["--jsonl", str(FIXTURE), "--out", str(FIXTURE), "--force"]) == 2
    assert "must not overwrite" in capsys.readouterr().err
    duplicate = tmp_path / "duplicate.jsonl"
    duplicate.write_text('{"problem_idx":"1","problem_idx":"2","problem":"false"}\n',
                         encoding="utf-8")
    assert rba.main(["--jsonl", str(duplicate), "--out", str(tmp_path / "dup-out.jsonl")]) == 2
    assert "duplicate JSON object key" in capsys.readouterr().err
