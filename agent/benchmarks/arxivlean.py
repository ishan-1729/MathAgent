"""MathArena **ArXivLean** adapter — a Lean-proof, contamination-resistant benchmark.

ArXivLean (matharena.ai, ETH SRI Lab + INSAIT) is the *formal* sibling of ArXivMath: instead of a
final numeric answer, each item ships a Lean 4 `formal_statement` and the task is to **produce a Lean
proof that closes it**. The upstream release `arxivlean-0326` targets Lean **v4.29.0**; our toolchain
is Lean **4.30**, so the adapter never assumes a proof compiles cross-version — compile-compatibility is
a **per-problem** fact to be recorded by the runner, not assumed here (see manifest `toolchain`).

Upstream schema (verified 2026-07-03 via the HF datasets-server): `problem_idx` (int), `problem`
(natural-language statement), `answer` (the gold column — in `arxivlean-0326` it holds the target Lean
`theorem …` to close), `formal_statement` (the Lean statement), and provenance `source`/`title`/`authors`.
There is **no** category/`problem_type` field and **no** Lean-version metadata field.

**Non-contaminative by construction** (mirrors `arxivmath.py`):
  - `problems()` returns `LeanProblem` objects carrying ONLY the fields a solver may see — the id
    and the `formal_statement` to prove. There is NO `answer` and NO `source`/`title`/`authors`
    on a problem, so a solver cannot see the intended answer or the originating paper.
  - the gold answers live in a separate `oracle()` map, held out for grading only.
  - downloaded data (which contains gold answers + source) is cached OUTSIDE the repo and never
    committed; the repo ships only this adapter, a manifest, this README's sibling, and a tiny
    *synthetic* fixture.

SAFETY: this module only parses data. It never `exec`/`eval`/`import`s any dataset content; a
`formal_statement` is inert Lean text, handed to the (out-of-band) Lean checker as data.

Grading a Lean proof requires the Lean checker and is out of scope for v1 of the runner (which supports
only --list/--dump for this dataset); the harness plumbing here is the dataset + oracle + a `Solver`
Protocol so a real Lean-proving solver can be slotted in later.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class LeanProblem:
    """What a solver sees — the Lean statement to close. NO gold_answer / source (the contamination guard)."""
    idx: str
    formal_statement: str
    problem_type: tuple[str, ...] = ()


@runtime_checkable
class LeanSolver(Protocol):
    def solve(self, formal_statement: str) -> str:
        """Return a Lean proof term/tactic block that closes `formal_statement` (sees only the statement)."""
        ...


class ScriptedLeanSolver:
    """Maps a formal_statement → proof text (offline tests). Unknown statements return `default`."""

    def __init__(self, proofs: dict[str, str], default: str = ""):
        self.proofs = proofs
        self.default = default
        self.seen: list[str] = []

    def solve(self, formal_statement: str) -> str:
        self.seen.append(formal_statement)
        return self.proofs.get(formal_statement, self.default)


def _as_types(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [t.strip() for t in value.split(",") if t.strip()]
    return [str(t).strip() for t in value if str(t).strip()]


def _is_number_theory(types: Iterable[str]) -> bool:
    return any("number theory" in t.lower() for t in types)


class ArxivLeanDataset:
    """A loaded ArXivLean release. Raw items keep gold_answer/source; `problems()` strips them out."""

    def __init__(self, items: list[dict], *, release: str = ""):
        self._items = items
        self.release = release

    def __len__(self) -> int:
        return len(self._items)

    def _idx(self, it: dict) -> str:
        for key in ("problem_idx", "idx", "id", "problem_id"):
            if key in it and it[key] is not None:
                return str(it[key])
        # fall back to source (metadata) only as a last resort for keying — never surfaced to a solver
        return str(it.get("source", ""))

    def problems(self, *, number_theory_only: bool = False) -> list[LeanProblem]:
        out: list[LeanProblem] = []
        for it in self._items:
            types = _as_types(it.get("problem_type"))
            if number_theory_only and not _is_number_theory(types):
                continue
            out.append(LeanProblem(idx=self._idx(it),
                                   formal_statement=str(it["formal_statement"]),
                                   problem_type=tuple(types)))
        return out

    def oracle(self) -> dict[str, str]:
        """idx → gold answer. Held out — never handed to a solver.

        Upstream names this column `answer`; in `arxivlean-0326` it holds the target Lean `theorem …`
        statement to close (same text as `formal_statement`), not a separate proof term."""
        return {self._idx(it): str(it.get("answer", it.get("gold_answer", ""))) for it in self._items}

    def sources(self) -> dict[str, str]:
        """idx → arXiv source id (metadata only — never put in a prompt)."""
        return {self._idx(it): str(it.get("source", "")) for it in self._items}

    @classmethod
    def from_jsonl(cls, path: str | Path, release: str = "") -> "ArxivLeanDataset":
        p = Path(path)
        items = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
        return cls(items, release=release or p.stem)

    @classmethod
    def from_huggingface(cls, config: str = "arxivlean-0326", split: str = "train",
                         cache_dir: Optional[str] = None) -> "ArxivLeanDataset":
        from datasets import load_dataset            # optional dep (mathagent[benchmark])
        ds = load_dataset(f"MathArena/{config}", split=split, cache_dir=cache_dir)
        return cls([dict(r) for r in ds], release=config)
