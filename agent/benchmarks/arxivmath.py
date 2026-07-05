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
    if value is None:
        return []
    if isinstance(value, str):
        return [t.strip() for t in value.split(",") if t.strip()]
    return [str(t).strip() for t in value if str(t).strip()]


def _is_number_theory(types: Iterable[str]) -> bool:
    return any("number theory" in t.lower() for t in types)


class ArxivMathDataset:
    """A loaded ArXivMath release. Raw items keep the answer; `problems()` strips it out."""

    def __init__(self, items: list[dict], *, release: str = ""):
        self._items = items
        self.release = release

    def __len__(self) -> int:
        return len(self._items)

    def problems(self, *, number_theory_only: bool = False) -> list[Problem]:
        out: list[Problem] = []
        for it in self._items:
            types = _as_types(it.get("problem_type"))
            if number_theory_only and not _is_number_theory(types):
                continue
            out.append(Problem(idx=str(it["problem_idx"]), statement=str(it["problem"]),
                               problem_type=tuple(types)))
        return out

    def oracle(self) -> dict[str, str]:
        """idx → gold answer. Held out — never handed to a solver."""
        return {str(it["problem_idx"]): str(it.get("answer", "")) for it in self._items}

    @classmethod
    def from_jsonl(cls, path: str | Path, release: str = "") -> "ArxivMathDataset":
        p = Path(path)
        items = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
        return cls(items, release=release or p.stem)

    @classmethod
    def from_huggingface(cls, config: str = "arxivmath-0326", split: str = "train",
                         cache_dir: Optional[str] = None) -> "ArxivMathDataset":
        from datasets import load_dataset            # optional dep (mathagent[benchmark])
        ds = load_dataset(f"MathArena/{config}", split=split, cache_dir=cache_dir)
        return cls([dict(r) for r in ds], release=config)


# --------------------------------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------------------------------

@dataclass
class ItemResult:
    idx: str
    predicted: str
    correct: bool
    problem_type: tuple[str, ...] = ()
    error: bool = False           # the solver raised (e.g. a Codex timeout) on this item


@dataclass
class BenchmarkReport:
    release: str
    results: list[ItemResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def n_correct(self) -> int:
        return sum(1 for r in self.results if r.correct)

    @property
    def n_errors(self) -> int:
        return sum(1 for r in self.results if r.error)

    @property
    def accuracy(self) -> float:
        return self.n_correct / self.total if self.total else 0.0

    def by_type(self) -> dict[str, tuple[int, int]]:
        """category → (correct, total). A multi-label item counts under each of its labels."""
        agg: dict[str, list[int]] = {}
        for r in self.results:
            for t in (r.problem_type or ("(untagged)",)):
                agg.setdefault(t, [0, 0])
                agg[t][1] += 1
                agg[t][0] += int(r.correct)
        return {t: (c, n) for t, (c, n) in agg.items()}

    def summary(self) -> str:
        err = f" ({self.n_errors} errored)" if self.n_errors else ""
        lines = [f"ArXivMath[{self.release}]: {self.n_correct}/{self.total} = {self.accuracy:.2%}{err}"]
        for t, (c, n) in sorted(self.by_type().items()):
            lines.append(f"  {t}: {c}/{n} = {c / n:.2%}")
        return "\n".join(lines)

    def to_jsonl(self) -> str:
        return "\n".join(json.dumps({"idx": r.idx, "predicted": r.predicted, "correct": r.correct,
                                     "error": r.error, "problem_type": list(r.problem_type)})
                         for r in self.results)


def run_benchmark(dataset: ArxivMathDataset, solver: Solver, *, number_theory_only: bool = False,
                  limit: Optional[int] = None, atol: float = 1e-9,
                  on_result=None) -> BenchmarkReport:
    """Run `solver` over the dataset's problems (statement only) and grade against the held-out oracle.

    The solver never receives the answer — only `problem.statement` — so the run is non-contaminative."""
    problems = dataset.problems(number_theory_only=number_theory_only)
    if limit is not None:
        problems = problems[:limit]
    oracle = dataset.oracle()
    report = BenchmarkReport(release=dataset.release)
    for prob in problems:
        # A per-item solver failure (e.g. a Codex timeout) is recorded and skipped — it must NOT
        # abort the whole benchmark run.
        try:
            predicted, err = solver.solve(prob.statement), False
        except Exception as e:
            predicted, err = f"<error: {type(e).__name__}: {e}>"[:200], True
        if err:
            correct = False
        else:
            # Grade in its OWN try: a grader exception (e.g. a malformed model answer that trips
            # SymPy) must NOT abort the whole benchmark run. On grader failure the item is scored
            # incorrect and we continue. ItemResult.error tracks SOLVER failures (a bool), so it
            # stays as-is (False) here; only `correct` is affected.
            try:
                correct = answers_equivalent(predicted, oracle.get(prob.idx), atol=atol)
            except Exception:
                correct = False
        item = ItemResult(idx=prob.idx, predicted=predicted, correct=correct,
                          problem_type=prob.problem_type, error=err)
        report.results.append(item)
        if on_result is not None:
            on_result(item)
    return report
