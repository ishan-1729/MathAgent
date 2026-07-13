"""MathArena **ArXivMath** adapter + a non-contaminative evaluation harness.

ArXivMath (matharena.ai, ETH SRI Lab + INSAIT) is a *final-answer*, contamination-resistant benchmark:
problems are reverse-engineered from very recent arXiv papers and refreshed monthly; only the gold
`answer` ships (no worked solution). Vanilla GPT-5.5-xHigh scores ~77.5% on the 03/2026 release — so the
question MathAgent asks is *how much the elementary-proof harness adds on top of the base model*.

**Non-contaminative by construction** (PLAN §8.3, system_design §9):
  - `problems()` returns `Problem` objects that carry ONLY the statement (+ category labels) — there is
    no `answer` (or `source` arXiv id) field on a `Problem`, so a solver literally cannot see the answer
    or the originating paper.
  - the gold answers live in a separate `oracle()` map, used only by the grader.
  - downloaded data (which contains answers) is cached OUTSIDE the repo (HF cache / a chosen dir) and is
    never committed; the repo ships only this adapter, a manifest, and a tiny *synthetic* fixture.

Grading is SymPy answer-equivalence (`agent.tools.answer_check`). The harness is `run_benchmark`; the
solver is a Protocol so the live Codex solver and an offline scripted stub are interchangeable.
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Protocol, runtime_checkable

from agent.tools.answer_check import answers_equivalent


@dataclass(frozen=True)
class Problem:
    """What a solver sees — deliberately NO answer/source field (the contamination guard)."""
    idx: str
    statement: str
    problem_type: tuple[str, ...] = ()


@runtime_checkable
class Solver(Protocol):
    def solve(self, statement: str) -> str:
        """Return a final answer for the problem statement (sees only the statement)."""
        ...


class ScriptedSolver:
    """Maps a statement → answer (offline tests). Unknown statements return `default`."""

    def __init__(self, answers: dict[str, str], default: str = ""):
        self.answers = answers
        self.default = default
        self.seen: list[str] = []

    def solve(self, statement: str) -> str:
        self.seen.append(statement)
        return self.answers.get(statement, self.default)


def _as_types(value) -> list[str]:
    """Normalize the two upstream encodings without stringifying structured junk."""
    if value is None:
        return []
    if isinstance(value, str):
        return [t.strip() for t in value.split(",") if t.strip()]
    if not isinstance(value, (list, tuple)):
        raise TypeError("problem_type must be a string, list of strings, or null")
    if any(not isinstance(t, str) for t in value):
        raise TypeError("problem_type entries must be strings")
    return [t.strip() for t in value if t.strip()]


def _is_number_theory(types: Iterable[str]) -> bool:
    return any("number theory" in t.lower() for t in types)


#: Fields a raw ArXivMath item MUST carry to yield a gradable problem.  ``answer`` is required too:
#: treating a missing gold as ``""`` would turn a data error into an ordinary model miss (or, under a
#: permissive grader, even a false hit).
_REQUIRED_FIELDS = ("problem_idx", "problem", "answer")
_MAX_IDX_CHARS = 512
_MAX_STATEMENT_CHARS = 100_000
_MAX_ANSWER_CHARS = 2_000       # matches the deterministic answer checker's public input contract
_MAX_PROBLEM_TYPES = 64
_MAX_PROBLEM_TYPE_CHARS = 256
_MAX_ROW_WARNINGS = 20
_MAX_DATASET_ROWS = 1_000_000
_MAX_JSONL_LINE_BYTES = 1024 * 1024
_MAX_DATASET_BYTES = 64 * 1024 * 1024


class ArxivMathDataError(ValueError):
    """The release has no unambiguous, gradable problem view."""


def _json_object_no_duplicates(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ArxivMathDataError(f"duplicate JSON object key {key!r}")
        obj[key] = value
    return obj


def _exception_text(exc: Exception) -> str:
    try:
        message = str(exc)
    except Exception:
        message = "<unprintable exception>"
    return f"{type(exc).__name__}: {message}"[:500]


def _coerce_idx(value) -> str:
    # Upstream IDs are integers; local fixtures and future releases may use strings.  Booleans are not
    # IDs even though ``bool`` subclasses ``int`` in Python.
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise TypeError("problem_idx must be a string or integer")
    idx = str(value).strip()
    if not idx:
        raise ValueError("problem_idx must not be blank")
    if len(idx) > _MAX_IDX_CHARS:
        raise ValueError(f"problem_idx exceeds {_MAX_IDX_CHARS} characters")
    return idx


def _coerce_statement(value) -> str:
    if not isinstance(value, str):
        raise TypeError("problem must be a string")
    statement = value.strip()
    if not statement:
        raise ValueError("problem must not be blank")
    if len(statement) > _MAX_STATEMENT_CHARS:
        raise ValueError(f"problem exceeds {_MAX_STATEMENT_CHARS} characters")
    return statement


def _coerce_answer(value) -> str:
    # Golds in real releases are strings, but accepting finite JSON numbers is harmless and useful for
    # hand-authored fixtures.  Never stringify containers: ``{'x': 1}`` is a schema error, not an answer.
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise TypeError("answer must be a string or finite number")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("answer must be finite")
    answer = str(value).strip()
    if not answer:
        raise ValueError("answer must not be blank")
    if len(answer) > _MAX_ANSWER_CHARS:
        raise ValueError(f"answer exceeds {_MAX_ANSWER_CHARS} characters")
    return answer


def _normalize_row(it: object) -> tuple[dict[str, object] | None, str | None]:
    """Return a canonical row, or a non-sensitive reason for skipping malformed input."""
    if not isinstance(it, dict):
        return None, f"row must be an object (got {type(it).__name__})"
    missing = [key for key in _REQUIRED_FIELDS if key not in it or it[key] is None]
    if missing:
        return None, f"missing required field(s): {', '.join(missing)}"
    try:
        problem_types = _as_types(it.get("problem_type"))
        if len(problem_types) > _MAX_PROBLEM_TYPES:
            raise ValueError(f"problem_type exceeds {_MAX_PROBLEM_TYPES} entries")
        if any(len(label) > _MAX_PROBLEM_TYPE_CHARS for label in problem_types):
            raise ValueError(f"problem_type entry exceeds {_MAX_PROBLEM_TYPE_CHARS} characters")
        normalized_labels = [label.casefold() for label in problem_types]
        if len(set(normalized_labels)) != len(normalized_labels):
            raise ValueError("problem_type contains duplicate labels")
        row: dict[str, object] = {
            "problem_idx": _coerce_idx(it["problem_idx"]),
            "problem": _coerce_statement(it["problem"]),
            "answer": _coerce_answer(it["answer"]),
            "problem_type": tuple(problem_types),
        }
    except (TypeError, ValueError) as exc:
        return None, str(exc)
    return row, None


class ArxivMathDataset:
    """A loaded ArXivMath release. Raw items keep the answer; `problems()` strips it out."""

    def __init__(self, items: list[object], *, release: str = "", allow_malformed: bool = False):
        if not isinstance(items, list):
            raise ArxivMathDataError("ArXivMath items must be a list")
        if len(items) > _MAX_DATASET_ROWS:
            raise ArxivMathDataError(f"ArXivMath release exceeds {_MAX_DATASET_ROWS} rows")
        normalized: list[dict[str, object]] = []
        seen: dict[str, int] = {}
        skipped = 0
        normalized_bytes = 0
        issues: list[tuple[int, str | None]] = []
        for line_no, raw in enumerate(items, start=1):
            row, reason = _normalize_row(raw)
            if row is None:
                skipped += 1
                if len(issues) < _MAX_ROW_WARNINGS:
                    issues.append((line_no, reason))
                continue
            idx = str(row["problem_idx"])
            if idx in seen:
                raise ArxivMathDataError(
                    f"duplicate problem_idx {idx!r} at rows {seen[idx]} and {line_no}"
                )
            seen[idx] = line_no
            normalized_bytes += len(json.dumps(
                row, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            ).encode("utf-8"))
            if normalized_bytes > _MAX_DATASET_BYTES:
                raise ArxivMathDataError(
                    f"ArXivMath normalized content exceeds {_MAX_DATASET_BYTES} bytes"
                )
            normalized.append(row)
        if skipped and not allow_malformed:
            first_line, first_reason = issues[0]
            raise ArxivMathDataError(
                f"ArXivMath release has {skipped} malformed row(s); first at row {first_line}: "
                f"{first_reason}. Refusing denominator drift"
            )
        if skipped:
            for line_no, reason in issues:
                print(f"WARNING: ArXivMath: skipping malformed row {line_no}: {reason}",
                      file=sys.stderr)
            if skipped > _MAX_ROW_WARNINGS:
                print(f"WARNING: ArXivMath: skipped {skipped - _MAX_ROW_WARNINGS} additional malformed "
                      "rows (warnings capped)", file=sys.stderr)
        if not normalized:
            raise ArxivMathDataError("ArXivMath release contains no valid, gradable rows")
        self._items = normalized
        self.raw_count = len(items)
        self.skipped_count = skipped
        self.release = str(release).strip()
        self.source_fingerprint: Optional[str] = None

    def __len__(self) -> int:
        return len(self._items)

    def problems(self, *, number_theory_only: bool = False) -> list[Problem]:
        out: list[Problem] = []
        for it in self._items:
            types = list(it["problem_type"])
            if number_theory_only and not _is_number_theory(types):
                continue
            out.append(Problem(idx=str(it["problem_idx"]), statement=str(it["problem"]),
                               problem_type=tuple(types)))
        return out

    def oracle(self) -> dict[str, str]:
        """idx → gold answer. Held out — never handed to a solver."""
        return {str(it["problem_idx"]): str(it["answer"]) for it in self._items}

    @classmethod
    def from_jsonl(cls, path: str | Path, release: str = "", *,
                   allow_malformed: bool = False) -> "ArxivMathDataset":
        p = Path(path)
        items: list[object] = []
        total_bytes = 0
        with p.open("rb") as fh:
            line_no = 0
            while True:
                raw = fh.readline(_MAX_JSONL_LINE_BYTES + 1)
                if not raw:
                    break
                line_no += 1
                if len(raw) > _MAX_JSONL_LINE_BYTES:
                    raise ArxivMathDataError(
                        f"JSONL line {line_no} exceeds {_MAX_JSONL_LINE_BYTES} bytes"
                    )
                if not raw.strip():
                    continue
                total_bytes += len(raw)
                if total_bytes > _MAX_DATASET_BYTES:
                    raise ArxivMathDataError(
                        f"ArXivMath JSONL content exceeds {_MAX_DATASET_BYTES} bytes"
                    )
                if len(items) >= _MAX_DATASET_ROWS:
                    raise ArxivMathDataError(f"ArXivMath release exceeds {_MAX_DATASET_ROWS} rows")
                try:
                    line = raw.decode("utf-8")
                    items.append(json.loads(line, object_pairs_hook=_json_object_no_duplicates))
                except UnicodeDecodeError as exc:
                    raise ArxivMathDataError(
                        f"invalid UTF-8 in {p} at line {line_no}: {exc}"
                    ) from exc
                except json.JSONDecodeError as exc:
                    raise ArxivMathDataError(
                        f"invalid JSON in {p} at line {line_no}, column {exc.colno}: {exc.msg}"
                    ) from exc
                except ArxivMathDataError as exc:
                    raise ArxivMathDataError(
                        f"invalid JSON object in {p} at line {line_no}: {exc}"
                    ) from exc
        return cls(items, release=release or p.stem, allow_malformed=allow_malformed)

    @classmethod
    def from_huggingface(cls, config: str = "arxivmath-0326", split: str = "train",
                         cache_dir: Optional[str] = None, *,
                         allow_malformed: bool = False) -> "ArxivMathDataset":
        from datasets import load_dataset            # optional dep (mathagent[benchmark])
        ds = load_dataset(f"MathArena/{config}", split=split, cache_dir=cache_dir)
        items: list[object] = []
        total_bytes = 0
        for row_no, raw in enumerate(ds, start=1):
            if row_no > _MAX_DATASET_ROWS:
                raise ArxivMathDataError(f"ArXivMath release exceeds {_MAX_DATASET_ROWS} rows")
            row = dict(raw)
            total_bytes += len(json.dumps(row, ensure_ascii=False, allow_nan=False).encode("utf-8"))
            if total_bytes > _MAX_DATASET_BYTES:
                raise ArxivMathDataError(
                    f"ArXivMath HF content exceeds {_MAX_DATASET_BYTES} bytes"
                )
            items.append(row)
        result = cls(items, release=config, allow_malformed=allow_malformed)
        fingerprint = getattr(ds, "_fingerprint", None)
        result.source_fingerprint = str(fingerprint) if fingerprint is not None else None
        return result


# --------------------------------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------------------------------

@dataclass
class ItemResult:
    idx: str
    predicted: str
    correct: bool
    problem_type: tuple[str, ...] = ()
    error: bool = False           # solver failed (kept for run-record compatibility)
    error_detail: Optional[str] = None
    grade_error: bool = False     # deterministic grader raised; distinct from a model failure
    grade_error_detail: Optional[str] = None

    def to_record(self) -> dict[str, object]:
        return {
            "idx": self.idx,
            "predicted": self.predicted,
            "correct": self.correct,
            "error": self.error,
            "error_detail": self.error_detail,
            "grade_error": self.grade_error,
            "grade_error_detail": self.grade_error_detail,
            "problem_type": list(self.problem_type),
        }


@dataclass
class BenchmarkReport:
    release: str
    results: list[ItemResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def n_correct(self) -> int:
        return sum(1 for r in self.results
                   if r.correct is True and not r.error and not r.grade_error)

    @property
    def n_errors(self) -> int:
        """Solver/backend errors (not grader errors)."""
        return sum(1 for r in self.results if r.error)

    @property
    def n_grade_errors(self) -> int:
        return sum(1 for r in self.results if r.grade_error)

    @property
    def n_answered(self) -> int:
        """Rows for which the solver returned normally (including returned-but-wrong answers)."""
        return self.total - self.n_errors

    @property
    def n_graded(self) -> int:
        """Rows on which both the solver and deterministic grader completed normally."""
        return sum(1 for r in self.results if not r.error and not r.grade_error)

    @property
    def accuracy(self) -> float:
        """Primary benchmark accuracy over every selected item, including solver failures as misses."""
        return self.n_correct / self.total if self.total else 0.0

    @property
    def answered_accuracy(self) -> float:
        """Accuracy conditional on the solver returning an answer (diagnostic, not the headline)."""
        return self.n_correct / self.n_answered if self.n_answered else 0.0

    @property
    def graded_accuracy(self) -> float:
        """Accuracy conditional on both the solver and grader completing normally."""
        return self.n_correct / self.n_graded if self.n_graded else 0.0

    @property
    def coverage(self) -> float:
        """Fraction of selected items for which the solver returned normally."""
        return self.n_answered / self.total if self.total else 0.0

    @property
    def grading_coverage(self) -> float:
        """Fraction of returned answers for which the grader completed normally."""
        return self.n_graded / self.n_answered if self.n_answered else 0.0

    def by_type(self) -> dict[str, tuple[int, int]]:
        """category → (correct, total selected). A multi-label item counts under each label."""
        agg: dict[str, list[int]] = {}
        for r in self.results:
            for t in (r.problem_type or ("(untagged)",)):
                agg.setdefault(t, [0, 0])
                agg[t][1] += 1
                agg[t][0] += int(r.correct is True and not r.error and not r.grade_error)
        return {t: (c, n) for t, (c, n) in agg.items()}

    def by_type_answered(self) -> dict[str, tuple[int, int]]:
        """category → (correct, answered), the conditional diagnostic matching answered_accuracy."""
        agg: dict[str, list[int]] = {}
        for r in self.results:
            if r.error:
                continue
            for t in (r.problem_type or ("(untagged)",)):
                agg.setdefault(t, [0, 0])
                agg[t][1] += 1
                agg[t][0] += int(r.correct is True and not r.grade_error)
        return {t: (c, n) for t, (c, n) in agg.items()}

    def by_type_graded(self) -> dict[str, tuple[int, int]]:
        """category → (correct, successfully graded), excluding both infrastructure error types."""
        agg: dict[str, list[int]] = {}
        for r in self.results:
            if r.error or r.grade_error:
                continue
            for t in (r.problem_type or ("(untagged)",)):
                agg.setdefault(t, [0, 0])
                agg[t][1] += 1
                agg[t][0] += int(r.correct is True)
        return {t: (c, n) for t, (c, n) in agg.items()}

    def summary(self) -> str:
        lines = [
            f"ArXivMath[{self.release}]: total accuracy {self.n_correct}/{self.total} = "
            f"{self.accuracy:.2%}; answered accuracy {self.n_correct}/{self.n_answered} = "
            f"{self.answered_accuracy:.2%}; coverage {self.n_answered}/{self.total} = "
            f"{self.coverage:.2%} ({self.n_errors} solver errors; {self.n_grade_errors} grader "
            f"errors; grading coverage {self.n_graded}/{self.n_answered} = {self.grading_coverage:.2%}; "
            f"graded accuracy {self.n_correct}/{self.n_graded} = {self.graded_accuracy:.2%})"
        ]
        answered = self.by_type_answered()
        for t, (c, n) in sorted(self.by_type().items()):
            ac, an = answered.get(t, (0, 0))
            conditional = ac / an if an else 0.0
            lines.append(
                f"  {t}: total {c}/{n} = {c / n:.2%}; answered {ac}/{an} = "
                f"{conditional:.2%}; coverage {an}/{n} = {an / n:.2%}"
            )
        return "\n".join(lines)

    def to_jsonl(self) -> str:
        return "\n".join(json.dumps(r.to_record()) for r in self.results)


def run_benchmark(dataset: ArxivMathDataset, solver: Solver, *, number_theory_only: bool = False,
                  limit: Optional[int] = None, atol: float = 1e-9,
                  on_result=None) -> BenchmarkReport:
    """Run `solver` over the dataset's problems (statement only) and grade against the held-out oracle.

    The solver never receives the answer — only `problem.statement` — so the run is non-contaminative."""
    if isinstance(limit, bool) or (limit is not None and (not isinstance(limit, int) or limit <= 0)):
        raise ValueError("limit must be a positive integer or None")
    if (isinstance(atol, bool) or not isinstance(atol, (int, float))
            or not math.isfinite(float(atol)) or not 0 <= float(atol) < 1):
        raise ValueError("atol must be a finite number in [0, 1)")
    problems = dataset.problems(number_theory_only=number_theory_only)
    if limit is not None:
        problems = problems[:limit]
    oracle = dataset.oracle()
    report = BenchmarkReport(release=dataset.release)
    for prob in problems:
        # A per-item solver failure (e.g. a Codex timeout) is recorded and skipped — it must NOT
        # abort the whole benchmark run.
        solver_error_detail = None
        try:
            predicted = solver.solve(prob.statement)
            if not isinstance(predicted, str):
                raise TypeError(f"solver returned {type(predicted).__name__}, expected str")
            if len(predicted) > _MAX_ANSWER_CHARS:
                raise ValueError(f"solver answer exceeds {_MAX_ANSWER_CHARS} characters")
            err = False
        except Exception as e:
            solver_error_detail = _exception_text(e)
            predicted, err = f"<error: {solver_error_detail}>"[:500], True
        grade_error = False
        grade_error_detail = None
        if err:
            correct = False
        else:
            # Grade in its OWN try: a grader exception (e.g. a malformed model answer that trips
            # SymPy) must NOT abort the whole benchmark run. On grader failure the item is scored
            # incorrect and we continue. ItemResult.error tracks SOLVER failures (a bool), so it
            # stays as-is (False) here; only `correct` is affected.
            try:
                correct = answers_equivalent(predicted, oracle[prob.idx], atol=float(atol))
                if not isinstance(correct, bool):
                    raise TypeError(f"grader returned {type(correct).__name__}, expected bool")
            except Exception as exc:
                correct = False
                grade_error = True
                grade_error_detail = _exception_text(exc)
        item = ItemResult(idx=prob.idx, predicted=predicted, correct=correct,
                          problem_type=prob.problem_type, error=err,
                          error_detail=solver_error_detail, grade_error=grade_error,
                          grade_error_detail=grade_error_detail)
        report.results.append(item)
        if on_result is not None:
            on_result(item)
    return report
