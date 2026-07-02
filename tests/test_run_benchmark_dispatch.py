"""Tests that scripts/run_benchmark.py --dataset selection dispatches to the right loader (offline)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_benchmark.py"
DATASETS = ROOT / "benchmarks" / "datasets"


def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, cwd=str(ROOT))


def test_arxivlean_list_dispatches():
    fixture = DATASETS / "arxivlean" / "fixtures" / "sample.jsonl"
    r = _run("--dataset", "arxivlean", "--jsonl", str(fixture), "--list")
    assert r.returncode == 0, r.stderr
    assert "arxivlean" in r.stdout
    # 5 problems listed, each by idx; the gold `answer` must NOT appear in the listing
    assert "synthetic_add_zero" in r.stdout          # the formal_statement IS shown
    assert "by sorry" in r.stdout                     # statements ship with an open goal
    assert ":= by rfl" not in r.stdout                # the gold proof (answer) is NOT leaked


def test_brokenarxiv_dump_dispatches():
    fixture = DATASETS / "brokenarxiv" / "fixtures" / "sample.jsonl"
    r = _run("--dataset", "brokenarxiv", "--jsonl", str(fixture), "--dump")
    assert r.returncode == 0, r.stderr
    assert "brokenarxiv" in r.stdout
    # the false statement is dumped; the held-out original TRUE statement is not
    assert "FALSE" in r.stdout
    assert "True original" not in r.stdout


def test_mathnet_list_applies_nt_english_filter():
    fixture = DATASETS / "mathnet" / "fixtures" / "sample.jsonl"
    r = _run("--dataset", "mathnet", "--jsonl", str(fixture), "--list")
    assert r.returncode == 0, r.stderr
    # 3 of 5 rows survive the default NT+English filter
    assert "3 after default filtering" in r.stdout
    assert "syn1" in r.stdout and "syn5" not in r.stdout  # French row filtered out


def test_new_datasets_reject_solver_modes():
    fixture = DATASETS / "arxivlean" / "fixtures" / "sample.jsonl"
    r = _run("--dataset", "arxivlean", "--jsonl", str(fixture), "--dry")
    assert r.returncode == 2
    assert "not supported" in r.stderr


def test_new_datasets_require_list_or_dump():
    fixture = DATASETS / "arxivlean" / "fixtures" / "sample.jsonl"
    r = _run("--dataset", "arxivlean", "--jsonl", str(fixture))
    assert r.returncode == 2
    assert "only --list or --dump" in r.stderr


def test_arxivmath_remains_default_and_runs_end_to_end():
    # Default --dataset arxivmath still runs the answer pipeline (--dry null solver, offline).
    fixture = DATASETS / "arxivmath" / "fixtures" / "sample.jsonl"
    r = _run("--jsonl", str(fixture), "--dry")
    assert r.returncode == 0, r.stderr
    assert "ArXivMath" in r.stdout
