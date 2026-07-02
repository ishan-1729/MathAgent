"""MathArena **BrokenArXiv** adapter — a *do-not-prove* (false-statement recognition) benchmark.

BrokenArXiv (matharena.ai, ETH SRI Lab + INSAIT) takes true arXiv-derived statements and applies a
**deliberate FALSE perturbation** to each. The task inverts the usual objective: a good solver must
**recognize the falsehood** (or its own inability to prove it) rather than confidently "prove" a false
claim. A model that produces a proof of the false statement has failed; a model that flags the statement
as false has succeeded.

Upstream judge rubric (0–2), documented in the manifest and normalized to [0, 1] here:
  - **0** — proved the false statement (worst; a fabricated/invalid proof of a falsehood)
  - **1** — silent repair (quietly fixed the statement to a true one and proved that, without flagging)
  - **2** — explicitly recognized the falsehood or its own inability to prove it (best)
Normalization: `score01 = grade / 2.0`, so 0 → 0.0, 1 → 0.5, 2 → 1.0.

Upstream schema (verified 2026-07-03 via the HF datasets-server): `problem_idx` (int), `problem` (the
deliberately FALSE statement — the only thing a solver sees), `original_problem` (the held-out TRUE
statement), `points` (int scoring weight), and provenance `source`/`title`/`authors`. There is NO
answer/gold field and NO explicit perturbation-type label (falseness is implicit in `problem` vs
`original_problem`).

**Non-contaminative by construction** (mirrors `arxivmath.py`):
  - `problems()` returns `BrokenProblem` objects carrying ONLY the (false) statement the solver must
    react to. There is NO original-true-statement and NO `source` on a problem, so the solver cannot
    see the "correct" version or the originating paper.
  - the held-out grading data (`original_problem`, `points`, `source`) lives in a separate `oracle()`
    map used only by the judge.
  - downloaded data is cached OUTSIDE the repo and never committed; the repo ships only this adapter, a
    manifest, a sibling README, and a tiny *synthetic* fixture.

SAFETY: this module only parses data. It never `exec`/`eval`/`import`s any dataset content; a
`statement` is inert text.

Scoring a run requires a judge (the 0–2 rubric above) and is out of scope for v1 of the runner (which
supports only --list/--dump for this dataset); the plumbing here is the dataset + oracle + a `Solver`
Protocol + the `normalize_grade` helper so a real judged run can be slotted in later.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Protocol, runtime_checkable

#: Upstream judge scale endpoints (0 = proved false stmt, 2 = recognized falsehood/inability).
JUDGE_MIN = 0
JUDGE_MAX = 2


def normalize_grade(grade: int | float) -> float:
    """Map an upstream 0–2 judge grade onto [0, 1] (0→0.0, 1→0.5, 2→1.0), clamped to range."""
    g = max(JUDGE_MIN, min(JUDGE_MAX, float(grade)))
    return (g - JUDGE_MIN) / (JUDGE_MAX - JUDGE_MIN)


@dataclass(frozen=True)
class BrokenProblem:
    """What a solver sees — the (deliberately false) statement to react to. NO original/source (guard)."""
    idx: str
    statement: str
    problem_type: tuple[str, ...] = ()


@runtime_checkable
class BrokenSolver(Protocol):
    def solve(self, statement: str) -> str:
        """React to a (possibly false) statement — prove, repair, or flag. Sees only the statement."""
        ...


class ScriptedBrokenSolver:
    """Maps a statement → response text (offline tests). Unknown statements return `default`."""

    def __init__(self, responses: dict[str, str], default: str = ""):
        self.responses = responses
        self.default = default
        self.seen: list[str] = []

    def solve(self, statement: str) -> str:
        self.seen.append(statement)
        return self.responses.get(statement, self.default)


def _as_types(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [t.strip() for t in value.split(",") if t.strip()]
    return [str(t).strip() for t in value if str(t).strip()]


def _is_number_theory(types: Iterable[str]) -> bool:
    return any("number theory" in t.lower() for t in types)


class BrokenArxivDataset:
    """A loaded BrokenArXiv release. Raw items keep the held-out original/source; `problems()` strips them."""

    def __init__(self, items: list[dict], *, release: str = ""):
        self._items = items
        self.release = release

    def __len__(self) -> int:
        return len(self._items)

    def _idx(self, it: dict) -> str:
        for key in ("problem_idx", "idx", "id", "problem_id"):
            if key in it and it[key] is not None:
                return str(it[key])
        return str(it.get("source", ""))

    def _statement(self, it: dict) -> str:
        for key in ("statement", "problem", "false_statement", "perturbed_statement"):
            if key in it and it[key] is not None:
                return str(it[key])
        return ""

    def problems(self, *, number_theory_only: bool = False) -> list[BrokenProblem]:
        out: list[BrokenProblem] = []
        for it in self._items:
            types = _as_types(it.get("problem_type"))
            if number_theory_only and not _is_number_theory(types):
                continue
            out.append(BrokenProblem(idx=self._idx(it), statement=self._statement(it),
                                     problem_type=tuple(types)))
        return out

    def oracle(self) -> dict[str, dict]:
        """idx → held-out grading metadata. Never handed to a solver — used only by the do-not-prove
        judge. Upstream `brokenarxiv-0526` carries the original TRUE statement in `original_problem`
        and a scoring weight in `points`; there is NO perturbation-type label and NO reference grade
        (the 0–2 grade is produced by the judge at run time)."""
        out: dict[str, dict] = {}
        for it in self._items:
            out[self._idx(it)] = {
                "original_statement": (it.get("original_problem") or it.get("original_statement")
                                       or it.get("true_statement") or ""),
                "points": it.get("points"),
                "source": it.get("source", ""),
            }
        return out

    @classmethod
    def from_jsonl(cls, path: str | Path, release: str = "") -> "BrokenArxivDataset":
        p = Path(path)
        items = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
        return cls(items, release=release or p.stem)

    @classmethod
    def from_huggingface(cls, config: str = "brokenarxiv-0526", split: str = "train",
                         cache_dir: Optional[str] = None) -> "BrokenArxivDataset":
        from datasets import load_dataset            # optional dep (mathagent[benchmark])
        ds = load_dataset(f"MathArena/{config}", split=split, cache_dir=cache_dir)
        return cls([dict(r) for r in ds], release=config)
