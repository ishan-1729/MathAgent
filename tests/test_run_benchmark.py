"""Offline tests for scripts/run_benchmark.py helpers (no model/CLI invoked).

scripts/ is not a package, so the script is loaded by path (mirrors tests/test_ablate.py). Importing it
runs only cheap module-level setup (stdout reconfigure + the arxivmath import); main() is __main__-guarded
and the Codex/HF backends are lazy-imported inside functions, so nothing network/model is touched here.
"""
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from agent.benchmarks.arxivmath import BenchmarkReport, ItemResult

_REPO = Path(__file__).resolve().parents[1]
_RB_PATH = _REPO / "scripts" / "run_benchmark.py"
_spec = importlib.util.spec_from_file_location("run_benchmark_cli", _RB_PATH)
run_benchmark_cli = importlib.util.module_from_spec(_spec)
sys.modules["run_benchmark_cli"] = run_benchmark_cli
_spec.loader.exec_module(run_benchmark_cli)

_extract = run_benchmark_cli._extract_final_answer


@pytest.fixture(autouse=True)
def _stable_repo_revision_check(monkeypatch):
    """Avoid recomputing the large shared dirty diff in every offline CLI test."""
    real = run_benchmark_cli._artifacts.code_revision
    start = run_benchmark_cli._code_revision()

    def _revision(path):
        return start if Path(path).resolve() == _REPO.resolve() else real(path)

    monkeypatch.setattr(run_benchmark_cli._artifacts, "code_revision", _revision)


def test_extract_takes_the_last_final_answer():
    # FIX 3: a discarded mid-reasoning 'FINAL ANSWER:' must NOT be graded — the model's CLOSING line is.
    msg = "Let me guess.\nFINAL ANSWER: 7\nWait, reconsidering the bound...\nFINAL ANSWER: 42"
    assert _extract(msg) == "42"


def test_extract_is_case_insensitive_and_strips():
    msg = "working...\nfinal answer:   -3   "
    assert _extract(msg) == "-3"


def test_extract_falls_back_to_last_nonempty_line_when_no_marker():
    # FIX 3: with no 'FINAL ANSWER:' marker, fall back to the last non-empty line.
    msg = "some reasoning\n   \nthe answer is 13\n   \n"
    assert _extract(msg) == "the answer is 13"


def test_extract_empty_or_blank_message_is_empty():
    assert _extract("") == ""
    assert _extract("   \n  \n\t") == ""


def test_run_record_release_slug_is_safe_and_not_double_prefixed():
    slug = run_benchmark_cli._artifact_release_slug
    assert slug("arxivmath-0326") == "0326"
    assert slug("ArxivMath-0526") == "0526"
    assert slug("../custom/release") == ".._custom_release"
    assert "/" not in slug("../custom/release") and "\\" not in slug("../custom/release")


def test_help_names_answer_only_refinement_honestly(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["run_benchmark.py", "--help"])
    with pytest.raises(SystemExit) as exc:
        run_benchmark_cli.main()
    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    flat_help = " ".join(help_text.split())
    assert "--answer-refinement" in flat_help
    assert "soft prose filter only" in flat_help
    assert "--harness" not in help_text
    assert "full MathAgent harness" not in help_text


def test_dry_run_record_reports_all_three_metrics(monkeypatch, tmp_path):
    fixture = (_REPO / "benchmarks" / "datasets" / "arxivmath" / "fixtures" / "sample.jsonl")
    monkeypatch.setattr(sys, "argv", [
        "run_benchmark.py", "--jsonl", str(fixture), "--dry", "--out", str(tmp_path),
    ])
    assert run_benchmark_cli.main() == 0
    records = list(tmp_path.glob("*.md"))
    assert len(records) == 1
    text = records[0].read_text(encoding="utf-8")
    assert "total_accuracy:" in text
    assert "answered_accuracy:" in text
    assert "graded_accuracy:" in text
    assert "coverage:" in text
    assert "grading_coverage:" in text
    assert "source: jsonl:" in text
    rows = list(tmp_path.glob("*.jsonl"))[0].read_text(encoding="utf-8").splitlines()
    assert rows and all('"grade_error": false' in row for row in rows)
    first = json.loads(rows[0])
    assert first["dataset_sha256"] and first["run_id"] and first["code_revision"]
    assert first["release"] == "sample" and first["mode"] == "null"
    assert not list(tmp_path.glob("*.incomplete"))


def test_all_category_prompt_is_category_neutral():
    prompt = run_benchmark_cli._SOLVE_PROMPT.lower()
    assert "mathematics problem" in prompt
    assert "number-theory problem" not in prompt
    assert "gcd/coprimality" not in prompt


@pytest.mark.parametrize("extra", [
    ["--model", "gpt-5.5"],
    ["--effort", "xhigh"],
    ["--timeout", "1200"],
])
def test_dry_rejects_even_explicit_default_provider_knobs(monkeypatch, capsys, tmp_path, extra):
    fixture = (_REPO / "benchmarks" / "datasets" / "arxivmath" / "fixtures" / "sample.jsonl")
    monkeypatch.setattr(sys, "argv", [
        "run_benchmark.py", "--jsonl", str(fixture), "--dry", "--out", str(tmp_path), *extra,
    ])
    assert run_benchmark_cli.main() == 2
    assert "do not apply with --dry" in capsys.readouterr().err


@pytest.mark.parametrize("extra, expected", [
    (["--timeout", "0"], "--timeout"),
    (["--timeout", "86401"], "--timeout"),
    (["--answer-refinement", "--n-judges", "0"], "--n-judges"),
    (["--answer-refinement", "--passes", "21"], "--passes"),
    (["--limit", "0"], "--limit"),
    (["--limit", "1000001"], "--limit"),
])
def test_numeric_cli_bounds_fail_cleanly(monkeypatch, capsys, extra, expected):
    fixture = (_REPO / "benchmarks" / "datasets" / "arxivmath" / "fixtures" / "sample.jsonl")
    mode = [] if "--answer-refinement" in extra else ["--dry"]
    monkeypatch.setattr(sys, "argv", ["run_benchmark.py", "--jsonl", str(fixture), *mode, *extra])
    assert run_benchmark_cli.main() == 2
    assert expected in capsys.readouterr().err


def test_local_dataset_validation_error_is_clean_not_a_traceback(monkeypatch, capsys, tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["run_benchmark.py", "--jsonl", str(empty), "--dry"])
    assert run_benchmark_cli.main() == 2
    err = capsys.readouterr().err
    assert "no valid, gradable rows" in err
    assert "Traceback" not in err


def test_jsonl_rejects_hf_only_flags_instead_of_ignoring_them(monkeypatch, capsys):
    fixture = (_REPO / "benchmarks" / "datasets" / "arxivmath" / "fixtures" / "sample.jsonl")
    monkeypatch.setattr(sys, "argv", [
        "run_benchmark.py", "--jsonl", str(fixture), "--dry", "--split", "train",
    ])
    assert run_benchmark_cli.main() == 2
    assert "apply only with --hf-config" in capsys.readouterr().err


def test_artifact_paths_never_reuse_an_existing_same_timestamp_name(tmp_path):
    now = datetime(2026, 7, 13, 1, 2, 3, 456789, tzinfo=timezone.utc)
    first_jsonl, first_md, stamp = run_benchmark_cli._artifact_paths(
        tmp_path, "arxivmath-0326", now=now, nonce="same"
    )
    first_jsonl.write_text("existing", encoding="utf-8")
    second_jsonl, second_md, second_stamp = run_benchmark_cli._artifact_paths(
        tmp_path, "arxivmath-0326", now=now, nonce="same"
    )
    assert stamp == second_stamp and first_jsonl != second_jsonl and first_md != second_md
    assert second_jsonl.stem.endswith("_2")


def test_release_slug_is_bounded_without_collapsing_long_values():
    a = run_benchmark_cli._artifact_release_slug("a" * 500)
    b = run_benchmark_cli._artifact_release_slug("a" * 499 + "b")
    assert len(a) <= 80 and len(b) <= 80 and a != b


def test_interrupted_run_retains_flushed_incomplete_checkpoint(monkeypatch, capsys, tmp_path):
    fixture = (_REPO / "benchmarks" / "datasets" / "arxivmath" / "fixtures" / "sample.jsonl")

    def _interrupt(dataset, solver, **kwargs):
        kwargs["on_result"](ItemResult(idx="1", predicted="4", correct=True))
        raise RuntimeError("simulated interruption")

    monkeypatch.setattr(run_benchmark_cli, "run_benchmark", _interrupt)
    monkeypatch.setattr(sys, "argv", [
        "run_benchmark.py", "--jsonl", str(fixture), "--dry", "--out", str(tmp_path),
    ])
    assert run_benchmark_cli.main() == 2
    checkpoints = list(tmp_path.glob("*.incomplete"))
    assert len(checkpoints) == 1
    row = json.loads(checkpoints[0].read_text(encoding="utf-8").strip())
    assert row["idx"] == "1" and row["run_id"] and row["dataset_sha256"]
    assert "checkpoint retained" in capsys.readouterr().err


def test_mixed_malformed_release_fails_closed_in_cli(monkeypatch, capsys, tmp_path):
    bad = tmp_path / "mixed.jsonl"
    bad.write_text(
        '{"problem_idx":"ok","problem":"Q","answer":"1"}\n'
        '{"problem_idx":"bad","problem":"missing gold"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["run_benchmark.py", "--jsonl", str(bad), "--dry"])
    assert run_benchmark_cli.main() == 2
    assert "Refusing denominator drift" in capsys.readouterr().err


def test_destination_race_never_clobbers_another_run(monkeypatch, tmp_path):
    fixture = (_REPO / "benchmarks" / "datasets" / "arxivmath" / "fixtures" / "sample.jsonl")
    final_json = tmp_path / "fixed.jsonl"
    final_md = tmp_path / "fixed.md"
    monkeypatch.setattr(run_benchmark_cli, "_artifact_paths",
                        lambda *args, **kwargs: (final_json, final_md, "stamp"))

    def _raced(dataset, solver, **kwargs):
        item = ItemResult(idx="1", predicted="4", correct=True,
                          problem_type=("Number Theory",))
        kwargs["on_result"](item)
        final_json.write_text("other run\n", encoding="utf-8")
        return BenchmarkReport(release=dataset.release, results=[item])

    monkeypatch.setattr(run_benchmark_cli, "run_benchmark", _raced)
    monkeypatch.setattr(sys, "argv", [
        "run_benchmark.py", "--jsonl", str(fixture), "--dry", "--limit", "1",
        "--out", str(tmp_path),
    ])
    assert run_benchmark_cli.main() == 2
    assert final_json.read_text(encoding="utf-8") == "other run\n"
    assert (tmp_path / "fixed.jsonl.incomplete").exists()
    assert not final_md.exists()  # our summary link was rolled back when the JSONL commit lost the race
    assert (tmp_path / "fixed.md.incomplete").exists()
    assert (tmp_path / "fixed.jsonl.incomplete.receipt.json").exists()


def test_resume_validates_receipt_and_runs_only_unpaid_suffix(monkeypatch, tmp_path):
    from agent.benchmarks.arxivmath import run_benchmark as real_run_benchmark

    fixture = (_REPO / "benchmarks" / "datasets" / "arxivmath" / "fixtures" / "sample.jsonl")

    def _interrupt(dataset, solver, **kwargs):
        kwargs["on_result"](ItemResult(idx="1", predicted="4", correct=True,
                                            problem_type=("Number Theory",)))
        raise RuntimeError("simulated interruption")

    monkeypatch.setattr(run_benchmark_cli, "run_benchmark", _interrupt)
    monkeypatch.setattr(sys, "argv", [
        "run_benchmark.py", "--jsonl", str(fixture), "--dry", "--out", str(tmp_path),
    ])
    assert run_benchmark_cli.main() == 2
    checkpoint = next(tmp_path.glob("*.jsonl.incomplete"))

    calls = {"n": 0}

    def _solve(self, statement):
        calls["n"] += 1
        return ""

    monkeypatch.setattr(run_benchmark_cli, "run_benchmark", real_run_benchmark)
    monkeypatch.setattr(run_benchmark_cli.NullSolver, "solve", _solve)
    monkeypatch.setattr(sys, "argv", [
        "run_benchmark.py", "--jsonl", str(fixture), "--dry", "--resume", str(checkpoint),
    ])
    assert run_benchmark_cli.main() == 0
    assert calls["n"] == 4
    final = Path(str(checkpoint)[:-len(".incomplete")])
    rows = [json.loads(line) for line in final.read_text(encoding="utf-8").splitlines()]
    assert [row["idx"] for row in rows] == ["1", "2", "3", "4", "5"]
    assert rows[0]["predicted"] == "4"
    assert all(row["runtime_versions"]["python"] for row in rows)
    assert not list(tmp_path.glob("*.incomplete*"))


def test_resume_rejects_tampered_receipt_before_solver_call(monkeypatch, tmp_path):
    fixture = (_REPO / "benchmarks" / "datasets" / "arxivmath" / "fixtures" / "sample.jsonl")

    def _interrupt(dataset, solver, **kwargs):
        kwargs["on_result"](ItemResult(idx="1", predicted="4", correct=True,
                                            problem_type=("Number Theory",)))
        raise RuntimeError("simulated interruption")

    monkeypatch.setattr(run_benchmark_cli, "run_benchmark", _interrupt)
    monkeypatch.setattr(sys, "argv", [
        "run_benchmark.py", "--jsonl", str(fixture), "--dry", "--out", str(tmp_path),
    ])
    assert run_benchmark_cli.main() == 2
    checkpoint = next(tmp_path.glob("*.jsonl.incomplete"))
    receipt_path = Path(f"{checkpoint}.receipt.json")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["dataset_sha256"] = "0" * 64
    receipt_path.write_text(json.dumps(receipt) + "\n", encoding="utf-8")
    called = {"n": 0}
    monkeypatch.setattr(run_benchmark_cli.NullSolver, "solve",
                        lambda self, statement: called.__setitem__("n", called["n"] + 1) or "")
    monkeypatch.setattr(sys, "argv", [
        "run_benchmark.py", "--jsonl", str(fixture), "--dry", "--resume", str(checkpoint),
    ])
    assert run_benchmark_cli.main() == 2
    assert called["n"] == 0 and checkpoint.exists()


def test_pair_publication_links_summary_before_jsonl_commit(monkeypatch, tmp_path):
    fixture = (_REPO / "benchmarks" / "datasets" / "arxivmath" / "fixtures" / "sample.jsonl")
    linked_suffixes = []
    original_link = os.link

    def _link(source, destination, *args, **kwargs):
        linked_suffixes.append(Path(destination).suffix)
        return original_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(run_benchmark_cli._artifacts.os, "link", _link)
    monkeypatch.setattr(sys, "argv", [
        "run_benchmark.py", "--jsonl", str(fixture), "--dry", "--out", str(tmp_path),
    ])
    assert run_benchmark_cli.main() == 0
    assert linked_suffixes[-2:] == [".md", ".jsonl"]


def test_resume_recovers_summary_only_power_loss_window(monkeypatch, tmp_path):
    fixture = (_REPO / "benchmarks" / "datasets" / "arxivmath" / "fixtures" / "sample.jsonl")
    real_publish_pair = run_benchmark_cli._artifacts.publish_pair

    def _power_loss_after_summary(*, summary_checkpoint, summary_final,
                                  data_checkpoint, data_final):
        os.link(summary_checkpoint, summary_final)
        raise KeyboardInterrupt("simulated power loss before JSONL commit")

    monkeypatch.setattr(run_benchmark_cli._artifacts, "publish_pair", _power_loss_after_summary)
    monkeypatch.setattr(sys, "argv", [
        "run_benchmark.py", "--jsonl", str(fixture), "--dry", "--out", str(tmp_path),
    ])
    with pytest.raises(KeyboardInterrupt):
        run_benchmark_cli.main()
    checkpoint = next(tmp_path.glob("*.jsonl.incomplete"))
    final_json = Path(str(checkpoint)[:-len(".incomplete")])
    final_md = final_json.with_suffix(".md")
    assert final_md.exists() and not final_json.exists()

    calls = {"n": 0}
    monkeypatch.setattr(run_benchmark_cli._artifacts, "publish_pair", real_publish_pair)
    monkeypatch.setattr(run_benchmark_cli.NullSolver, "solve",
                        lambda self, statement: calls.__setitem__("n", calls["n"] + 1) or "")
    monkeypatch.setattr(sys, "argv", [
        "run_benchmark.py", "--jsonl", str(fixture), "--dry", "--resume", str(checkpoint),
    ])
    assert run_benchmark_cli.main() == 0
    assert calls["n"] == 0  # every paid/completed row came from the validated checkpoint
    assert final_md.exists() and final_json.exists()


def test_dirty_revision_hashes_tracked_and_relevant_untracked_content(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "tracked.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=repo, check=True)
    subprocess.run([
        "git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "-qm", "initial",
    ], cwd=repo, check=True)
    clean = run_benchmark_cli._artifacts.code_revision(repo)
    (repo / "benchmarks" / "evaluation" / "runs").mkdir(parents=True)
    (repo / "benchmarks" / "evaluation" / "runs" / "generated.jsonl").write_text(
        "{}\n", encoding="utf-8",
    )
    assert run_benchmark_cli._artifacts.code_revision(repo) == clean
    (repo / "tracked.py").write_text("VALUE = 2\n", encoding="utf-8")
    tracked_dirty = run_benchmark_cli._artifacts.code_revision(repo)
    assert tracked_dirty.startswith(clean + "+dirty.") and len(tracked_dirty.rsplit(".", 1)[1]) == 64
    (repo / "tracked.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "agent").mkdir()
    (repo / "agent" / "new.py").write_text("VALUE = 3\n", encoding="utf-8")
    untracked_a = run_benchmark_cli._artifacts.code_revision(repo)
    (repo / "agent" / "new.py").write_text("VALUE = 4\n", encoding="utf-8")
    untracked_b = run_benchmark_cli._artifacts.code_revision(repo)
    assert untracked_a.startswith(clean + "+dirty.") and untracked_a != untracked_b
    (repo / "agent" / "gates" / "lean").mkdir(parents=True)
    lean_source = repo / "agent" / "gates" / "lean" / "Extra.lean"
    lean_source.write_text("theorem extra : True := trivial\n", encoding="utf-8")
    lean_a = run_benchmark_cli._artifacts.code_revision(repo)
    lean_source.write_text("theorem extra : 1 = 1 := rfl\n", encoding="utf-8")
    lean_b = run_benchmark_cli._artifacts.code_revision(repo)
    assert lean_a != lean_b
    (repo / "profiles").mkdir()
    profile_source = repo / "profiles" / "__init__.py"
    profile_source.write_text("VALUE = 1\n", encoding="utf-8")
    profile_a = run_benchmark_cli._artifacts.code_revision(repo)
    profile_source.write_text("VALUE = 2\n", encoding="utf-8")
    profile_b = run_benchmark_cli._artifacts.code_revision(repo)
    assert profile_a != profile_b


def test_code_revision_does_not_borrow_outer_repo_head_for_nested_install(
        tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    nested = repo / ".venv" / "lib" / "python3.12" / "site-packages"
    nested.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "tracked.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=repo, check=True)
    subprocess.run([
        "git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "-qm", "initial",
    ], cwd=repo, check=True)
    installed = "dist:mathagent@0.1.0+payload.sha256:" + "a" * 64
    monkeypatch.setattr(
        run_benchmark_cli._artifacts, "installed_distribution_revision",
        lambda distribution="mathagent", *, expected_root=None:
            installed if expected_root == nested.resolve() else "unknown+unverified",
    )
    assert run_benchmark_cli._artifacts.code_revision(nested) == installed


def test_code_revision_fails_unverified_after_exact_worktree_identity(
        tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "tracked.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=repo, check=True)
    subprocess.run([
        "git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "-qm", "initial",
    ], cwd=repo, check=True)
    real_run = subprocess.run

    def transient_diff_failure(command, *args, **kwargs):
        if command[:2] == ["git", "diff"]:
            raise subprocess.TimeoutExpired(command, 30)
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(run_benchmark_cli._artifacts.subprocess, "run", transient_diff_failure)
    monkeypatch.setattr(
        run_benchmark_cli._artifacts, "installed_distribution_revision",
        lambda distribution="mathagent", *, expected_root=None: "dist:must-not-be-used",
    )
    assert run_benchmark_cli._artifacts.code_revision(repo) == "unknown+unverified"


def test_installed_distribution_revision_hashes_current_payload_bytes(
        tmp_path, monkeypatch):
    root = tmp_path / "site-packages"
    (root / "agent").mkdir(parents=True)
    (root / "scripts").mkdir()
    agent_file = root / "agent" / "__init__.py"
    script_file = root / "scripts" / "prove.py"
    agent_file.write_text("VERSION = 1\n", encoding="utf-8")
    script_file.write_text("def main(): pass\n", encoding="utf-8")

    class FakeDistribution:
        version = "9.9.9"
        files = [Path("agent/__init__.py"), Path("scripts/prove.py")]

        @staticmethod
        def locate_file(entry):
            return root / Path(entry)

    monkeypatch.setattr(
        run_benchmark_cli._artifacts.importlib.metadata, "distribution",
        lambda _name: FakeDistribution(),
    )
    first = run_benchmark_cli._artifacts.installed_distribution_revision()
    assert first.startswith("dist:mathagent@9.9.9+payload.sha256:")
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    assert (run_benchmark_cli._artifacts.installed_distribution_revision(expected_root=unrelated)
            == "unknown+unverified")
    assert run_benchmark_cli._artifacts.code_revision(unrelated) == "unknown+unverified"
    injected = root / "agent" / "injected.py"
    injected.write_text("MALICIOUS = True\n", encoding="utf-8")
    assert (run_benchmark_cli._artifacts.installed_distribution_revision()
            == "unknown+unverified")
    injected.unlink()
    agent_file.write_text("VERSION = 2\n", encoding="utf-8")
    second = run_benchmark_cli._artifacts.installed_distribution_revision()
    assert second.startswith("dist:mathagent@9.9.9+payload.sha256:")
    assert second != first
    script_file.unlink()
    assert (run_benchmark_cli._artifacts.installed_distribution_revision()
            == "unknown+unverified")


def test_dirty_revision_untracked_records_are_prefix_free(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("stable\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run([
        "git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "-qm", "initial",
    ], cwd=repo, check=True)
    agent_dir = repo / "agent"
    agent_dir.mkdir()
    marker = b"\0untracked\0agent/b.py\0file\0"
    (agent_dir / "a.py").write_bytes(b"prefix" + marker + b"suffix")
    one_file = run_benchmark_cli._artifacts.code_revision(repo)

    (agent_dir / "a.py").write_bytes(b"prefix")
    (agent_dir / "b.py").write_bytes(b"suffix")
    two_files = run_benchmark_cli._artifacts.code_revision(repo)
    assert one_file != two_files


@pytest.mark.parametrize("extra", [
    ["--model", "other"],
    ["--effort", "high"],
    ["--timeout", "1"],
    ["--n-judges", "2"],
    ["--passes", "3"],
    ["--nt-only"],
    ["--out", "ignored"],
])
def test_non_answer_inspection_rejects_inapplicable_flags(monkeypatch, capsys, extra):
    fixture = (_REPO / "benchmarks" / "datasets" / "brokenarxiv" / "fixtures" / "sample.jsonl")
    monkeypatch.setattr(sys, "argv", [
        "run_benchmark.py", "--dataset", "brokenarxiv", "--jsonl", str(fixture), "--list", *extra,
    ])
    assert run_benchmark_cli.main() == 2
    assert "not supported" in capsys.readouterr().err


def test_non_answer_inspection_applies_limit(monkeypatch, capsys):
    fixture = (_REPO / "benchmarks" / "datasets" / "brokenarxiv" / "fixtures" / "sample.jsonl")
    monkeypatch.setattr(sys, "argv", [
        "run_benchmark.py", "--dataset", "brokenarxiv", "--jsonl", str(fixture),
        "--list", "--limit", "1",
    ])
    assert run_benchmark_cli.main() == 0
    out = capsys.readouterr().out
    assert "selected for inspection" in out
    assert sum(line.startswith("  ") for line in out.splitlines()) == 1


def test_negative_limit_fails_cleanly(monkeypatch, capsys):
    fixture = (_REPO / "benchmarks" / "datasets" / "arxivmath" / "fixtures" / "sample.jsonl")
    monkeypatch.setattr(sys, "argv", [
        "run_benchmark.py", "--jsonl", str(fixture), "--dry", "--limit", "-1",
    ])
    assert run_benchmark_cli.main() == 2
    assert "--limit must be >= 0" in capsys.readouterr().err
