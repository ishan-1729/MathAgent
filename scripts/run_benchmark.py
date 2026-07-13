"""Run the ArXivMath benchmark non-contaminatively and write a run record.

The solver sees ONLY each problem's statement; gold answers stay in the held-out oracle and are used
only by the SymPy grader. By default this uses a bare Codex answer-solver (a baseline ~ vanilla
GPT-5.5); the elementary-proof harness + Layer-4 gate can later be slotted in behind the `Solver`
protocol to measure how much the harness adds.

Examples:
  python scripts/run_benchmark.py --jsonl benchmarks/datasets/arxivmath/fixtures/sample.jsonl --dry
  python scripts/run_benchmark.py --hf-config arxivmath-0326 --nt-only --out benchmarks/datasets/arxivmath/runs
"""
from __future__ import annotations

import argparse
import datetime as _dt
import functools
import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Dataset statements carry Unicode math (e.g. `∣`, `≤`, Greek); make stdout/stderr UTF-8 so --list /
# --dump don't crash on a legacy Windows codepage (cp1252).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

from agent.benchmarks.arxivmath import (ArxivMathDataset, BenchmarkReport, ItemResult,
                                        _MAX_ANSWER_CHARS, run_benchmark)
from agent.tools.answer_check import answers_equivalent
from scripts import _benchmark_artifacts as _artifacts

_DEFAULT_MODEL = "gpt-5.5"
_DEFAULT_EFFORT = "xhigh"
_DEFAULT_TIMEOUT = 1200
_DEFAULT_JUDGES = 1
_DEFAULT_PASSES = 2
_MAX_LIMIT = 1_000_000
_MAX_TIMEOUT = 86_400
_MAX_JUDGES = 16
_MAX_PASSES = 20
_REPO_ROOT = Path(__file__).resolve().parents[1]

_ARXIV_RECORD_FIELDS = frozenset({
    "idx", "predicted", "correct", "error", "error_detail", "grade_error",
    "grade_error_detail", "problem_type", "release", "dataset_source", "dataset_sha256",
    "dataset_fingerprint", "mode", "nt_only", "limit", "run_id", "code_revision",
    "runtime_versions", "receipt_sha256", "timestamp_utc",
})


def _file_sha256(path: Path) -> str:
    return _artifacts.file_sha256(path)


@functools.lru_cache(maxsize=1)
def _code_revision() -> str:
    return _artifacts.cached_code_revision(_REPO_ROOT)


def _extract_final_answer(msg: str) -> str:
    # Take the LAST 'FINAL ANSWER:' line, not the first: a model may emit a discarded mid-reasoning
    # guess before its real closing answer, and the closing line is the one to grade. Fall back to the
    # last non-empty line when no marker is present.
    matches = re.findall(r"FINAL ANSWER:\s*(.+?)\s*$", msg, re.IGNORECASE | re.MULTILINE)
    if matches:
        return matches[-1].strip()
    lines = [ln.strip() for ln in msg.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def _artifact_release_slug(release: str) -> str:
    """One safe, non-redundant release component for run-record filenames."""
    value = str(release).strip()
    if value.lower().startswith("arxivmath-"):
        value = value[len("arxivmath-"):]
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_") or "unknown"
    if len(value) > 80:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
        value = f"{value[:67]}_{digest}"
    return value


def _artifact_paths(outdir: Path, release: str, *, now=None, nonce: str | None = None
                    ) -> tuple[Path, Path, str]:
    """Return collision-resistant JSONL/Markdown paths and their UTC timestamp.

    Seconds-only names silently overwrote two runs launched in the same second.  Microseconds plus a
    random nonce make independently launched processes distinct; the existence loop also handles a
    deterministic/replayed nonce without overwriting an existing record.
    """
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_dt.timezone.utc)
    stamp = now.astimezone(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    token = re.sub(r"[^A-Za-z0-9]+", "", nonce or uuid.uuid4().hex[:12]) or "run"
    stem = f"arxivmath_{_artifact_release_slug(release)}_{stamp}_{token}"
    suffix = 1
    while True:
        candidate = outdir / (stem if suffix == 1 else f"{stem}_{suffix}")
        jsonl_path = candidate.parent / f"{candidate.name}.jsonl"
        md_path = candidate.parent / f"{candidate.name}.md"
        incomplete_path = Path(f"{jsonl_path}.incomplete")
        summary_incomplete = Path(f"{md_path}.incomplete")
        prepared_data = Path(f"{incomplete_path}.prepared")
        receipt = _artifacts.receipt_path(incomplete_path)
        if (not jsonl_path.exists() and not md_path.exists() and not incomplete_path.exists()
                and not summary_incomplete.exists() and not prepared_data.exists()
                and not prepared_data.is_symlink() and not receipt.exists()):
            return jsonl_path, md_path, stamp
        suffix += 1


def _loaded_dataset_sha256(dataset: ArxivMathDataset) -> str:
    """Canonical content fingerprint for HF-backed data, independent of cache implementation."""
    items = getattr(dataset, "_items", None)
    if not isinstance(items, list):
        raise ValueError("cannot fingerprint loaded ArXivMath rows")
    return _artifacts.object_sha256(items)


def _resume_artifact_paths(checkpoint: Path) -> tuple[Path, Path]:
    suffix = ".jsonl.incomplete"
    value = str(checkpoint)
    if not value.endswith(suffix):
        raise ValueError(f"--resume must name a {suffix} checkpoint")
    jsonl_path = Path(value[:-len(".incomplete")])
    return jsonl_path, jsonl_path.with_suffix(".md")


def _arxiv_receipt(*, run_id: str, release: str, dataset_source: str,
                   dataset_sha256: str | None, dataset_fingerprint: str | None, mode: str,
                   nt_only: bool, limit: int | None, code_revision: str,
                   runtime_versions: dict[str, object], selected_indices: list[str],
                   timestamp_utc: str, jsonl_path: Path, md_path: Path) -> dict[str, object]:
    return {
        "schema_version": 1,
        "benchmark": "arxivmath",
        "run_id": run_id,
        "release": release,
        "dataset_source": dataset_source,
        "dataset_sha256": dataset_sha256,
        "dataset_fingerprint": dataset_fingerprint,
        "mode": mode,
        "nt_only": nt_only,
        "limit": limit,
        "code_revision": code_revision,
        "runtime_versions": runtime_versions,
        "selected_count": len(selected_indices),
        "selected_indices_sha256": _artifacts.indices_sha256(selected_indices),
        "timestamp_utc": timestamp_utc,
        "jsonl_final_name": jsonl_path.name,
        "summary_final_name": md_path.name,
    }


def _validate_arxiv_receipt(receipt: dict, *, release: str, dataset_source: str,
                            dataset_sha256: str | None, dataset_fingerprint: str | None,
                            mode: str, nt_only: bool, limit: int | None, code_revision: str,
                            runtime_versions: dict[str, object], selected_indices: list[str],
                            jsonl_path: Path, md_path: Path) -> None:
    expected = {
        "schema_version": 1,
        "benchmark": "arxivmath",
        "release": release,
        "dataset_source": dataset_source,
        "dataset_sha256": dataset_sha256,
        "dataset_fingerprint": dataset_fingerprint,
        "mode": mode,
        "nt_only": nt_only,
        "limit": limit,
        "code_revision": code_revision,
        "runtime_versions": runtime_versions,
        "selected_count": len(selected_indices),
        "selected_indices_sha256": _artifacts.indices_sha256(selected_indices),
        "jsonl_final_name": jsonl_path.name,
        "summary_final_name": md_path.name,
    }
    if set(receipt) != set(expected) | {"run_id", "timestamp_utc"}:
        raise ValueError("resume receipt has an unexpected field set")
    for key, value in expected.items():
        if receipt.get(key) != value:
            raise ValueError(f"resume receipt mismatch for {key}")
    run_id = receipt.get("run_id")
    if not isinstance(run_id, str) or re.fullmatch(r"[0-9a-f]{32}", run_id) is None:
        raise ValueError("resume receipt has an invalid run_id")
    stamp = receipt.get("timestamp_utc")
    if not isinstance(stamp, str) or re.fullmatch(r"\d{8}T\d{12}Z", stamp) is None:
        raise ValueError("resume receipt has an invalid timestamp_utc")


def _item_from_resume_record(record: dict, *, expected_problem, oracle_answer: str,
                             receipt: dict, receipt_sha256: str) -> ItemResult:
    if set(record) != _ARXIV_RECORD_FIELDS:
        raise ValueError("checkpoint row has an unexpected field set")
    for key in (
        "release", "dataset_source", "dataset_sha256", "dataset_fingerprint", "mode", "nt_only",
        "limit", "run_id", "code_revision", "runtime_versions", "timestamp_utc",
    ):
        if record.get(key) != receipt.get(key):
            raise ValueError(f"checkpoint row provenance mismatch for {key}")
    if record.get("receipt_sha256") != receipt_sha256:
        raise ValueError("checkpoint row does not bind to its run receipt")
    if record.get("idx") != expected_problem.idx:
        raise ValueError("checkpoint rows are not the selected dataset prefix")
    predicted = record.get("predicted")
    if not isinstance(predicted, str) or len(predicted) > _MAX_ANSWER_CHARS:
        raise ValueError(f"checkpoint predicted answer must be a string of at most "
                         f"{_MAX_ANSWER_CHARS} characters")
    problem_type = record.get("problem_type")
    if (not isinstance(problem_type, list) or any(not isinstance(v, str) for v in problem_type)
            or tuple(problem_type) != expected_problem.problem_type):
        raise ValueError("checkpoint problem_type does not match the dataset")
    correct = record.get("correct")
    error = record.get("error")
    grade_error = record.get("grade_error")
    if not all(isinstance(value, bool) for value in (correct, error, grade_error)):
        raise ValueError("checkpoint result flags must be booleans")
    error_detail = record.get("error_detail")
    grade_error_detail = record.get("grade_error_detail")
    if error:
        if correct or grade_error or not isinstance(error_detail, str) or not error_detail:
            raise ValueError("checkpoint solver-error fields are inconsistent")
        if grade_error_detail is not None:
            raise ValueError("solver-error row cannot carry a grader error")
    elif grade_error:
        if correct or error_detail is not None or not isinstance(grade_error_detail, str) \
                or not grade_error_detail:
            raise ValueError("checkpoint grader-error fields are inconsistent")
    else:
        if error_detail is not None or grade_error_detail is not None:
            raise ValueError("successful checkpoint row carries an error detail")
        try:
            regraded = answers_equivalent(predicted, oracle_answer)
        except Exception as exc:
            raise ValueError(f"could not regrade checkpoint row {expected_problem.idx}: {exc}") from exc
        if not isinstance(regraded, bool) or regraded is not correct:
            raise ValueError("checkpoint correctness does not match deterministic regrading")
    return ItemResult(
        idx=expected_problem.idx,
        predicted=predicted,
        correct=correct,
        problem_type=tuple(problem_type),
        error=error,
        error_detail=error_detail,
        grade_error=grade_error,
        grade_error_detail=grade_error_detail,
    )


class _ArxivDatasetSlice:
    """Minimal statement/oracle view for running only the unpaid suffix during resume."""

    def __init__(self, dataset: ArxivMathDataset, problems):
        self.release = dataset.release
        self._problems = list(problems)
        oracle = dataset.oracle()
        self._oracle = {problem.idx: oracle[problem.idx] for problem in self._problems}

    def problems(self, *, number_theory_only: bool = False):
        if number_theory_only:
            raise ValueError("resume slice is already filtered")
        return list(self._problems)

    def oracle(self):
        return dict(self._oracle)


class NullSolver:
    """--dry: returns nothing (every item scored wrong). Smoke-tests the pipeline without a model."""

    def solve(self, statement: str) -> str:
        return ""


_SOLVE_PROMPT = (
    "Solve the following mathematics problem carefully, using methods appropriate to its subject. "
    "Then end your reply with exactly one line:\nFINAL ANSWER: <answer>\nwhere <answer> is "
    "only the final value (a number, closed-form expression, or set).\n\nProblem:\n{statement}"
)


class CodexAnswerSolver:
    """Bare Codex final-answer solver — the **vanilla GPT-5.5-xHigh** baseline. Sees only the statement."""

    def __init__(self, cfg):
        self.cfg = cfg

    def solve(self, statement: str) -> str:
        from agent.tools.codex_prover import _run_codex
        return _extract_final_answer(_run_codex(_SOLVE_PROMPT.format(statement=statement), self.cfg))


class AnswerRefinementSolver:
    """**GPT-5.5-xHigh + answer-only refinement.** Codex produces a solution, then the Codex
    Autoreason incumbent tournament (critic → author → synthesizer → judge panel, with PUCT +
    Bradley-Terry) refines it under a **prose denylist filter** (a candidate whose text trips a
    non-elementary prose smell cannot displace the incumbent); we then extract the final answer.
    NOTE: this is only a lowercase substring filter over `prose_terms` — NOT the authoritative
    elementary gate (no typed ledger, no `gate.evaluate`, no Lean Layer-4 audit). It is a soft
    admissibility heuristic for this final-answer benchmark; proof certification lives in `prove.py`.
    Measures what this answer-refinement loop adds over the bare model. It is deliberately not named
    the "MathAgent harness" because it does not construct/gate a typed proof ledger or run Layer 4.
    Still sees only the statement (non-contaminative)."""

    def __init__(self, cfg, *, n_judges: int = 1, passes: int = 2):
        from agent.gates.toolkit import load_toolkit
        from agent.tools.codex_prover import make_codex_refiner
        self.cfg = cfg
        self._deny = load_toolkit().prose_terms          # lowercased non-elementary prose smells
        self.refiner = make_codex_refiner(cfg, n_judges=n_judges, max_passes=passes)

    def _elementary_ok(self, candidate: str) -> bool:
        # Soft prose denylist filter only (substring scan) — see the class docstring; this is NOT the
        # authoritative Layer-4 elementary gate.
        low = candidate.lower()
        return not any(term in low for term in self._deny)

    def solve(self, statement: str) -> str:
        from agent.tools.codex_prover import _run_codex
        incumbent = _run_codex(_SOLVE_PROMPT.format(statement=statement), self.cfg)
        refined = self.refiner.refine(statement, incumbent, is_admissible=self._elementary_ok)
        return _extract_final_answer(refined.content)


def _run_list_dump(dataset_name: str, args) -> int:
    """--list / --dump support for the non-answer datasets (arxivlean, brokenarxiv, mathnet).

    These datasets are NOT runnable end-to-end yet: arxivlean needs a Lean checker, brokenarxiv needs a
    do-not-prove judge, and the MathNet answer-run path is not wired. So the v1 runner only inspects them
    — it never pretends to "run" what it cannot grade. Solver flags
    (--dry/--answer-refinement/model/effort) are rejected here so nothing is silently ignored."""
    for bad in ("dry", "answer_refinement"):
        if getattr(args, bad, False):
            print(f"ERROR: --{bad.replace('_', '-')} is not supported for --dataset {dataset_name} "
                  f"(only --list / --dump; proof/Lean/false-statement running arrives in a later phase).",
                  file=sys.stderr)
            return 2
    unsupported = []
    if args.model is not None:
        unsupported.append("--model")
    if args.effort is not None:
        unsupported.append("--effort")
    if args.timeout is not None:
        unsupported.append("--timeout")
    if args.n_judges is not None:
        unsupported.append("--n-judges")
    if args.passes is not None:
        unsupported.append("--passes")
    if args.nt_only:
        unsupported.append("--nt-only")
    if args.out is not None:
        unsupported.append("--out")
    if args.resume is not None:
        unsupported.append("--resume")
    if unsupported:
        print(f"ERROR: {', '.join(unsupported)} not supported for --dataset {dataset_name} "
              "inspection (only --list / --dump and dataset source/filter flags apply).",
              file=sys.stderr)
        return 2
    if args.jsonl and (args.split is not None or args.cache_dir is not None):
        print("ERROR: --split/--cache-dir apply only with --hf-config.", file=sys.stderr)
        return 2
    if not (args.list or args.dump):
        print(f"ERROR: --dataset {dataset_name} supports only --list or --dump for now "
              f"(proof/Lean/false-statement running arrives in a later phase).", file=sys.stderr)
        return 2

    if dataset_name == "arxivlean":
        from agent.benchmarks.arxivlean import ArxivLeanDataset as _DS
        stmt_attr = "formal_statement"
    elif dataset_name == "brokenarxiv":
        from agent.benchmarks.brokenarxiv import BrokenArxivDataset as _DS
        stmt_attr = "statement"
    elif dataset_name == "mathnet":
        from agent.benchmarks.mathnet import MathNetDataset as _DS
        stmt_attr = "statement"
    else:  # pragma: no cover - guarded by argparse choices
        print(f"ERROR: unknown dataset {dataset_name}", file=sys.stderr)
        return 2

    try:
        if args.jsonl:
            dataset = _DS.from_jsonl(args.jsonl)
        else:
            if dataset_name == "mathnet":
                dataset = _DS.from_huggingface(args.hf_config or "all", args.split or "train",
                                               args.cache_dir)
            else:
                dataset = _DS.from_huggingface(args.hf_config, args.split or "train", args.cache_dir)
    except Exception as e:
        source = args.jsonl or args.hf_config
        print(f"ERROR: could not load {dataset_name} ({source}): {e}", file=sys.stderr)
        if not args.jsonl:
            print("  (install with: pip install 'mathagent[benchmark]')", file=sys.stderr)
        return 2

    problems = dataset.problems()
    if args.limit is not None:
        problems = problems[:args.limit]
    print(f"# {dataset_name} [{dataset.release}] — {len(dataset)} raw items, "
          f"{len(problems)} after default filtering / selected for inspection "
          "(solver never sees held-out oracle fields)")
    for p in problems:
        text = getattr(p, stmt_attr, "")
        if args.dump:
            print(json.dumps({"idx": p.idx, stmt_attr: text}))
        else:
            print(f"  {p.idx}: {text[:100]!r}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Inspect / run MathAgent benchmark datasets (non-contaminative).",
        epilog="NOTE: only --dataset arxivmath runs end-to-end (final-answer solving + SymPy grading). "
               "arxivlean/brokenarxiv/mathnet support ONLY --list / --dump for now — Lean proving, "
               "do-not-prove judging, and MathNet answer-running arrive in a later phase.")
    ap.add_argument("--dataset", choices=["arxivmath", "arxivlean", "brokenarxiv", "mathnet"],
                    default="arxivmath",
                    help="which dataset to use (default: arxivmath — the only one that runs end-to-end; "
                         "the other three support only --list / --dump)")
    inspect_mode = ap.add_mutually_exclusive_group()
    inspect_mode.add_argument("--list", action="store_true",
                              help="(non-answer datasets) list problem idx + statement and exit")
    inspect_mode.add_argument("--dump", action="store_true",
                              help="(non-answer datasets) dump problem idx + statement as JSONL and exit")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--jsonl", help="local JSONL release (e.g. the synthetic fixture)")
    src.add_argument("--hf-config", help="HuggingFace release config, e.g. arxivmath-0326")
    ap.add_argument("--split", default=None, help="HF split (default: train; HF sources only)")
    ap.add_argument("--cache-dir", default=None, help="HF cache dir (keep OUTSIDE the repo)")
    ap.add_argument("--nt-only", action="store_true", help="number-theory subset only (v1 scope)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--model", default=None, help=f"Codex model (default: {_DEFAULT_MODEL})")
    ap.add_argument("--effort", choices=["low", "medium", "high", "xhigh"], default=None,
                    help=f"Codex reasoning effort (default: {_DEFAULT_EFFORT})")
    ap.add_argument("--timeout", type=int, default=None,
                    help=f"per-call timeout in seconds (default: {_DEFAULT_TIMEOUT})")
    ap.add_argument("--out", default=None, help="directory to write the run record into")
    ap.add_argument(
        "--resume", type=Path, default=None, metavar="JSONL.incomplete",
        help="resume a retained ArXivMath checkpoint after validating its fsynced receipt, dataset "
             "prefix, solver mode, code revision, and Python/package versions; supply the same source "
             "and selection/provider flags as the interrupted run",
    )
    solver_mode = ap.add_mutually_exclusive_group()
    solver_mode.add_argument("--dry", action="store_true",
                             help="use a null solver (no model) to smoke-test")
    solver_mode.add_argument("--answer-refinement", action="store_true",
                             help="use the answer-only Codex Autoreason tournament instead of the "
                                  "vanilla single-shot baseline (soft prose filter only; NOT the "
                                  "typed/Layer-4 proof harness)")
    ap.add_argument("--n-judges", type=int, default=None,
                    help=f"(answer-refinement) Codex judges per pass (default: {_DEFAULT_JUDGES})")
    ap.add_argument("--passes", type=int, default=None,
                    help=f"(answer-refinement) refinement passes (default: {_DEFAULT_PASSES})")
    args = ap.parse_args()

    if args.limit is not None and args.limit < 0:
        print("ERROR: --limit must be >= 0.", file=sys.stderr)
        return 2
    if args.limit is not None and args.limit > _MAX_LIMIT:
        print(f"ERROR: --limit must be <= {_MAX_LIMIT}.", file=sys.stderr)
        return 2
    if args.timeout is not None and not 1 <= args.timeout <= _MAX_TIMEOUT:
        print(f"ERROR: --timeout must be between 1 and {_MAX_TIMEOUT} seconds.", file=sys.stderr)
        return 2
    if args.n_judges is not None and not 1 <= args.n_judges <= _MAX_JUDGES:
        print(f"ERROR: --n-judges must be between 1 and {_MAX_JUDGES}.", file=sys.stderr)
        return 2
    if args.passes is not None and not 1 <= args.passes <= _MAX_PASSES:
        print(f"ERROR: --passes must be between 1 and {_MAX_PASSES}.", file=sys.stderr)
        return 2

    if args.dataset != "arxivmath":
        return _run_list_dump(args.dataset, args)

    if args.list or args.dump:
        print("ERROR: --list / --dump apply to the non-answer datasets "
              "(--dataset arxivlean|brokenarxiv|mathnet), not arxivmath.", file=sys.stderr)
        return 2

    if args.limit == 0:
        print("ERROR: --limit must be >= 1 for an ArXivMath scoring run.", file=sys.stderr)
        return 2

    if args.jsonl and (args.split is not None or args.cache_dir is not None):
        print("ERROR: --split/--cache-dir apply only with --hf-config.", file=sys.stderr)
        return 2

    if args.resume is not None and args.out is not None:
        try:
            if Path(args.out).resolve() != args.resume.resolve().parent:
                print("ERROR: with --resume, --out must be the checkpoint's parent directory.",
                      file=sys.stderr)
                return 2
        except OSError as exc:
            print(f"ERROR: invalid --resume/--out path: {exc}", file=sys.stderr)
            return 2

    provider_flags = [name for name, value in (
        ("--model", args.model), ("--effort", args.effort), ("--timeout", args.timeout)
    ) if value is not None]
    if args.dry and provider_flags:
        print(f"ERROR: {', '.join(provider_flags)} do not apply with --dry.", file=sys.stderr)
        return 2

    if not args.answer_refinement and (args.n_judges is not None or args.passes is not None):
        print("ERROR: --n-judges/--passes apply only with --answer-refinement.", file=sys.stderr)
        return 2

    try:
        if args.jsonl:
            source_path = Path(args.jsonl).resolve()
            dataset_sha256 = _file_sha256(source_path)
            dataset = ArxivMathDataset.from_jsonl(source_path)
            if _file_sha256(source_path) != dataset_sha256:
                raise ValueError("input JSONL changed while it was being loaded")
            dataset_source = f"jsonl:{source_path}"
            dataset_fingerprint = None
        else:
            split = args.split or "train"
            dataset = ArxivMathDataset.from_huggingface(args.hf_config, split, args.cache_dir)
            dataset_source = f"huggingface:MathArena/{args.hf_config}@{split}"
            # The HF implementation fingerprint is useful cache provenance, but only a canonical hash
            # of the normalized rows binds the actual benchmark content.
            dataset_sha256 = _loaded_dataset_sha256(dataset)
            dataset_fingerprint = getattr(dataset, "source_fingerprint", None)
    except Exception as e:
        if args.jsonl:
            print(f"ERROR: could not load ArXivMath JSONL {args.jsonl}: {e}", file=sys.stderr)
        else:
            print(f"ERROR: could not load MathArena/{args.hf_config}: {e}", file=sys.stderr)
            print("  (install with: pip install 'mathagent[benchmark]')", file=sys.stderr)
        return 2

    selected_problems = dataset.problems(number_theory_only=args.nt_only)
    if args.limit is not None:
        selected_problems = selected_problems[:args.limit]
    selected_count = len(selected_problems)
    if selected_count == 0:
        print("ERROR: the selected filters produced no benchmark rows.", file=sys.stderr)
        return 2

    if args.dry:
        solver, mode = NullSolver(), "null"
    else:
        from agent.tools.codex_prover import CodexConfig, CodexProver
        if not CodexProver.available():
            print("ERROR: codex CLI not found on PATH (use --dry to smoke-test).", file=sys.stderr)
            return 2
        model = args.model or _DEFAULT_MODEL
        effort = args.effort or _DEFAULT_EFFORT
        timeout = args.timeout or _DEFAULT_TIMEOUT
        try:
            cfg = CodexConfig(model=model, reasoning_effort=effort, timeout_s=timeout)
        except Exception as exc:
            print(f"ERROR: invalid Codex configuration: {exc}", file=sys.stderr)
            return 2
        try:
            if args.answer_refinement:
                n_judges = args.n_judges or _DEFAULT_JUDGES
                passes = args.passes or _DEFAULT_PASSES
                solver = AnswerRefinementSolver(cfg, n_judges=n_judges, passes=passes)
                mode = (f"answer-refinement({model}/{effort}, timeout={timeout}s, judges={n_judges}, "
                        f"passes={passes}; soft-only)")
            else:
                solver, mode = CodexAnswerSolver(cfg), f"vanilla({model}/{effort}, timeout={timeout}s)"
        except Exception as exc:
            print(f"ERROR: could not initialize benchmark solver: {exc}", file=sys.stderr)
            return 2

    code_revision = _code_revision()
    if code_revision == "unknown+unverified":
        print("ERROR: cannot run a benchmark with an unverified code revision.", file=sys.stderr)
        return 2
    runtime_versions = _artifacts.runtime_versions()
    selected_indices = [problem.idx for problem in selected_problems]
    run_id = uuid.uuid4().hex
    checkpoint_fh = None
    checkpoint_lock_fh = None
    checkpoint_path = None
    checkpoint_receipt_path = None
    receipt_sha256 = None
    receipt = None
    resumed_items: list[ItemResult] = []
    jsonl_path = None
    md_path = None
    summary_checkpoint = None
    prepared_data_checkpoint = None
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    if args.resume is not None:
        try:
            checkpoint_path = args.resume.resolve()
            jsonl_path, md_path = _resume_artifact_paths(checkpoint_path)
            checkpoint_lock_fh = _artifacts.acquire_checkpoint_lock(checkpoint_path)
            summary_checkpoint = Path(f"{md_path}.incomplete")
            prepared_data_checkpoint = Path(f"{checkpoint_path}.prepared")
            checkpoint_receipt_path = _artifacts.receipt_path(checkpoint_path)
            if jsonl_path.exists():
                raise FileExistsError("a final JSONL already exists; refusing ambiguous resume")
            if md_path.exists():
                # A kill/power loss can occur after the summary link but before the JSONL commit.  The
                # prepared summary remains in that window, so inode identity proves this is our orphan
                # rather than an unrelated writer's artifact and makes automatic rollback safe.
                try:
                    own_orphan = summary_checkpoint.exists() \
                        and os.path.samefile(md_path, summary_checkpoint)
                except OSError:
                    own_orphan = False
                if not own_orphan:
                    raise FileExistsError("an unrelated final summary exists; refusing ambiguous resume")
                md_path.unlink()
                _artifacts.fsync_directory(md_path.parent)
            receipt, receipt_sha256 = _artifacts.load_receipt(checkpoint_receipt_path)
            _validate_arxiv_receipt(
                receipt,
                release=dataset.release,
                dataset_source=dataset_source,
                dataset_sha256=dataset_sha256,
                dataset_fingerprint=dataset_fingerprint,
                mode=mode,
                nt_only=args.nt_only,
                limit=args.limit,
                code_revision=code_revision,
                runtime_versions=runtime_versions,
                selected_indices=selected_indices,
                jsonl_path=jsonl_path,
                md_path=md_path,
            )
            rows, durable_size = _artifacts.load_checkpoint_rows(
                checkpoint_path, max_rows=selected_count,
            )
            oracle = dataset.oracle()
            for position, record in enumerate(rows):
                resumed_items.append(_item_from_resume_record(
                    record,
                    expected_problem=selected_problems[position],
                    oracle_answer=oracle[selected_problems[position].idx],
                    receipt=receipt,
                    receipt_sha256=receipt_sha256,
                ))
            _artifacts.truncate_checkpoint(checkpoint_path, durable_size)
            checkpoint_fh = checkpoint_path.open("a", encoding="utf-8", newline="\n")
            run_id = str(receipt["run_id"])
            stamp = str(receipt["timestamp_utc"])
        except Exception as exc:
            if checkpoint_fh is not None:
                checkpoint_fh.close()
            if checkpoint_lock_fh is not None:
                checkpoint_lock_fh.close()
            print(f"ERROR: could not validate/resume run checkpoint: {exc}", file=sys.stderr)
            return 2
    elif args.out:
        try:
            outdir = Path(args.out)
            outdir.mkdir(parents=True, exist_ok=True)
            jsonl_path, md_path, stamp = _artifact_paths(outdir, dataset.release)
            checkpoint_path = Path(f"{jsonl_path}.incomplete")
            summary_checkpoint = Path(f"{md_path}.incomplete")
            prepared_data_checkpoint = Path(f"{checkpoint_path}.prepared")
            checkpoint_receipt_path = _artifacts.receipt_path(checkpoint_path)
            checkpoint_fh = checkpoint_path.open("x", encoding="utf-8", newline="\n")
            checkpoint_lock_fh = _artifacts.acquire_checkpoint_lock(checkpoint_path)
            receipt = _arxiv_receipt(
                run_id=run_id,
                release=dataset.release,
                dataset_source=dataset_source,
                dataset_sha256=dataset_sha256,
                dataset_fingerprint=dataset_fingerprint,
                mode=mode,
                nt_only=args.nt_only,
                limit=args.limit,
                code_revision=code_revision,
                runtime_versions=runtime_versions,
                selected_indices=selected_indices,
                timestamp_utc=stamp,
                jsonl_path=jsonl_path,
                md_path=md_path,
            )
            receipt_sha256 = _artifacts.write_receipt(checkpoint_receipt_path, receipt)
        except Exception as exc:
            if checkpoint_fh is not None:
                checkpoint_fh.close()
            if checkpoint_lock_fh is not None:
                checkpoint_lock_fh.close()
            if checkpoint_path is not None and checkpoint_receipt_path is not None \
                    and not checkpoint_receipt_path.exists():
                checkpoint_path.unlink(missing_ok=True)
            print(f"ERROR: could not initialize run checkpoint: {exc}", file=sys.stderr)
            return 2

    print(f"# ArXivMath [{dataset.release}] — {len(dataset)}/{dataset.raw_count} valid items "
          f"({dataset.skipped_count} malformed skipped); {selected_count} selected "
          f"({'NT only' if args.nt_only else 'all categories'}) — mode: {mode}")
    if resumed_items:
        print(f"# validated {len(resumed_items)}/{selected_count} completed rows; "
              "resuming the unpaid suffix")

    def _tick(item):
        if checkpoint_fh is not None:
            record = item.to_record()
            record.update({
                "release": dataset.release,
                "dataset_source": dataset_source,
                "dataset_sha256": dataset_sha256,
                "dataset_fingerprint": dataset_fingerprint,
                "mode": mode,
                "nt_only": args.nt_only,
                "limit": args.limit,
                "run_id": run_id,
                "code_revision": code_revision,
                "runtime_versions": runtime_versions,
                "receipt_sha256": receipt_sha256,
                "timestamp_utc": stamp,
            })
            checkpoint_fh.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
            checkpoint_fh.flush()
            os.fsync(checkpoint_fh.fileno())
        mark = "ERR" if item.error else ("GRD" if item.grade_error else ("OK " if item.correct else "xx "))
        print(f"  {mark}{item.idx}: {item.predicted[:70]!r}", flush=True)

    try:
        if resumed_items:
            report = BenchmarkReport(release=dataset.release, results=list(resumed_items))
        else:
            report = BenchmarkReport(release=dataset.release)
        remaining = selected_problems[len(resumed_items):]
        if remaining:
            suffix_report = run_benchmark(_ArxivDatasetSlice(dataset, remaining), solver,
                                          on_result=_tick)
            report.results.extend(suffix_report.results)
    except Exception as exc:
        if checkpoint_fh is not None:
            checkpoint_fh.close()
        print(f"ERROR: benchmark run failed: {exc}", file=sys.stderr)
        if checkpoint_path is not None:
            print(f"  completed-row checkpoint retained at {checkpoint_path}", file=sys.stderr)
            if checkpoint_receipt_path is not None:
                print(f"  resume with --resume {checkpoint_path} and the original flags; "
                      f"receipt: {checkpoint_receipt_path}", file=sys.stderr)
        if checkpoint_lock_fh is not None:
            checkpoint_lock_fh.close()
        return 2
    finally:
        if checkpoint_fh is not None and not checkpoint_fh.closed:
            checkpoint_fh.close()

    print("\n" + report.summary())

    if checkpoint_path is not None:
        try:
            assert jsonl_path is not None and md_path is not None and stamp is not None
            assert summary_checkpoint is not None and prepared_data_checkpoint is not None
            assert checkpoint_receipt_path is not None
            assert receipt is not None and receipt_sha256 is not None
            final_code_revision = _artifacts.code_revision(_REPO_ROOT)
            if final_code_revision == "unknown+unverified" or final_code_revision != code_revision:
                raise ValueError("code revision changed or became unverifiable during the run")
            # Re-read all durable evidence under the writer lock immediately before publication. This
            # prevents a stale in-memory report, partial tail, or cooperating concurrent writer from
            # being committed under a mismatched summary.
            final_receipt, final_receipt_sha256 = _artifacts.load_receipt(checkpoint_receipt_path)
            if final_receipt != receipt or final_receipt_sha256 != receipt_sha256:
                raise ValueError("checkpoint receipt changed before publication")
            final_rows, durable_size = _artifacts.load_checkpoint_rows(
                checkpoint_path, max_rows=selected_count,
            )
            if durable_size != checkpoint_path.stat().st_size or len(final_rows) != selected_count:
                raise ValueError("checkpoint is incomplete or has a partial trailing row")
            oracle = dataset.oracle()
            validated_items = [
                _item_from_resume_record(
                    record,
                    expected_problem=selected_problems[position],
                    oracle_answer=oracle[selected_problems[position].idx],
                    receipt=receipt,
                    receipt_sha256=receipt_sha256,
                )
                for position, record in enumerate(final_rows)
            ]
            if validated_items != report.results:
                raise ValueError("checkpoint rows changed before publication")
            _artifacts.prepare_data_checkpoint(checkpoint_path, prepared_data_checkpoint)
            prepared_rows, prepared_size = _artifacts.load_checkpoint_rows(
                prepared_data_checkpoint, max_rows=selected_count,
            )
            if (prepared_size != prepared_data_checkpoint.stat().st_size
                    or prepared_rows != final_rows):
                raise ValueError("prepared JSONL snapshot does not match the validated checkpoint")
            summary = (f"# ArXivMath run record\n\nrelease: {dataset.release}\nmode: {mode}\n"
                       f"source: {dataset_source}\n"
                       f"dataset_sha256: {dataset_sha256}\n"
                       f"dataset_fingerprint: {dataset_fingerprint}\n"
                       f"run_id: {run_id}\n"
                       f"code_revision: {code_revision}\n"
                       f"runtime_versions: "
                       f"{json.dumps(runtime_versions, sort_keys=True, ensure_ascii=False)}\n"
                       f"nt_only: {args.nt_only}\n"
                       f"limit: {args.limit}\n"
                       f"raw_items: {dataset.raw_count}\n"
                       f"valid_items: {len(dataset)}\n"
                       f"malformed_items_skipped: {dataset.skipped_count}\n"
                       f"selected_items: {report.total}\n"
                       f"total_accuracy: {report.accuracy:.4f} ({report.n_correct}/{report.total})\n"
                       f"answered_accuracy: {report.answered_accuracy:.4f} "
                       f"({report.n_correct}/{report.n_answered})\n"
                       f"graded_accuracy: {report.graded_accuracy:.4f} "
                       f"({report.n_correct}/{report.n_graded})\n"
                       f"coverage: {report.coverage:.4f} ({report.n_answered}/{report.total}; "
                       f"{report.n_errors} solver errors)\n"
                       f"grading_coverage: {report.grading_coverage:.4f} "
                       f"({report.n_graded}/{report.n_answered}; "
                       f"{report.n_grade_errors} grader errors)\n"
                       f"timestamp_utc: {stamp}\n\n{report.summary()}\n")
            # Both artifacts are fully prepared and fsynced before publication.  The summary is linked
            # first; linking the JSONL is the commit point, so a final JSONL from this run is never
            # observable without its summary.  Failed publication retains both recovery checkpoints.
            if summary_checkpoint.exists():
                if summary_checkpoint.is_symlink() or not summary_checkpoint.is_file():
                    raise ValueError("summary checkpoint must be a regular non-symlink file")
                if summary_checkpoint.stat().st_size > 1024 * 1024 \
                        or summary_checkpoint.read_text(encoding="utf-8") != summary:
                    summary_checkpoint.unlink()
            if not summary_checkpoint.exists():
                _artifacts.prepare_text_checkpoint(summary_checkpoint, summary)
            _artifacts.publish_pair(
                summary_checkpoint=summary_checkpoint,
                summary_final=md_path,
                data_checkpoint=prepared_data_checkpoint,
                data_final=jsonl_path,
            )
            _artifacts.finish_publication(
                (checkpoint_path, prepared_data_checkpoint, summary_checkpoint,
                 checkpoint_receipt_path),
            )
        except Exception as exc:
            print(f"ERROR: could not write run record: {exc}", file=sys.stderr)
            if checkpoint_path is not None and checkpoint_path.exists():
                print(f"  completed-row checkpoint retained at {checkpoint_path}", file=sys.stderr)
                print(f"  validate and retry with --resume {checkpoint_path} and the original flags",
                      file=sys.stderr)
            if checkpoint_lock_fh is not None:
                checkpoint_lock_fh.close()
            return 2
        print(f"\n# wrote {jsonl_path} and {md_path}")

    if checkpoint_lock_fh is not None:
        checkpoint_lock_fh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
