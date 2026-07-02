"""**MathNet** adapter — a large topic-tagged math benchmark, filtered to Number-Theory + English.

MathNet (MIT, `ShadenA/MathNet`, config "all", ~27.8k rows) is a broad final-answer math dataset whose
items carry hierarchical topic paths in `topics_flat` and a `language` tag. For MathAgent v1 we care about
the **Number Theory** slice in **English**, so the loader supports:
  - `topics_flat` filtering: keep a row iff any of its topic paths starts with a given prefix
    (default `"Number Theory"`), matched case-insensitively at the head of the path.
  - `language` filtering: default keep English only. NOTE: upstream `language` is **nullable**; a row
    with an empty/null language is NOT filtered out (we only drop rows whose language is set and differs).

Upstream schema (verified 2026-07-03 via the HF datasets-server): `id` (string slug), `problem_markdown`
(the statement — the only thing a solver sees), `final_answer` (the gold column), `topics_flat` (a flat
**list of strings**, each a hierarchical path using `" > "` as the separator, e.g.
`"Discrete Mathematics > Combinatorics > Coloring schemes"`), `language` (string, nullable),
`problem_type`, plus `solutions_markdown`/`images`/`country`/`competition`.

`topics_flat` is parsed flexibly (a list of path strings, a list of path segment-lists, or a single
delimited string) so the loader is robust to encoding variation; each path's HEAD segment is compared to
the prefix (splitting on `" > "`, `/`, or `:`), so `"Number Theory > Diophantine equations"` matches
prefix `"Number Theory"`.

**Non-contaminative by construction** (mirrors `arxivmath.py`):
  - `problems()` returns `Problem` objects carrying ONLY the question statement (+ topic labels). There
    is NO `answer` field on a problem, so a solver cannot see the gold answer.
  - the gold answers live in a separate `oracle()` map, held out for grading only.
  - the repo ships only this adapter, a manifest, a sibling README, and a tiny *synthetic* fixture;
    real data is downloaded behind the benchmark extra and never committed.

SAFETY: this module only parses data. It never `exec`/`eval`/`import`s any dataset content.

Grading is SymPy answer-equivalence (like ArXivMath); the v1 runner supports --list/--dump for this
dataset. The plumbing here is the dataset + oracle + a `Solver` Protocol.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

DEFAULT_TOPIC_PREFIX = "Number Theory"
DEFAULT_LANGUAGE = "English"


@dataclass(frozen=True)
class Problem:
    """What a solver sees — deliberately NO answer field (the contamination guard)."""
    idx: str
    statement: str
    topics: tuple[str, ...] = ()      # normalized "/"-joined topic paths
    language: str = ""


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


def _normalize_paths(topics_flat) -> list[str]:
    """Coerce the upstream `topics_flat` into a list of "/"-joined path strings.

    Accepts: a list of path strings, a list of path segment-lists, or a single (possibly ';'/'|'
    delimited) string. Empty / None → []."""
    if topics_flat is None:
        return []
    if isinstance(topics_flat, str):
        parts = [topics_flat]
        for sep in (";", "|", "\n"):
            if sep in topics_flat:
                parts = topics_flat.split(sep)
                break
        return [p.strip() for p in parts if p.strip()]
    out: list[str] = []
    for entry in topics_flat:
        if isinstance(entry, str):
            s = entry.strip()
        elif isinstance(entry, (list, tuple)):
            s = "/".join(str(seg).strip() for seg in entry if str(seg).strip())
        else:
            s = str(entry).strip()
        if s:
            out.append(s)
    return out


def _path_head(path: str) -> str:
    """First segment of a topic path, splitting on '/', '>' or ':'."""
    head = path
    for sep in ("/", ">", ":"):
        if sep in head:
            head = head.split(sep, 1)[0]
            break
    return head.strip()


def _matches_topic_prefix(paths: list[str], prefix: str) -> bool:
    """True iff any path's head segment equals `prefix` (case-insensitive), i.e. the path starts with it."""
    want = prefix.strip().lower()
    return any(_path_head(p).lower() == want for p in paths)


class MathNetDataset:
    """A loaded MathNet split. Raw items keep the answer; `problems()` strips it out."""

    def __init__(self, items: list[dict], *, release: str = "all"):
        self._items = items
        self.release = release

    def __len__(self) -> int:
        return len(self._items)

    def _idx(self, it: dict, fallback: int) -> str:
        for key in ("id", "problem_idx", "idx", "problem_id", "uid"):
            if key in it and it[key] is not None:
                return str(it[key])
        return str(fallback)

    def _statement(self, it: dict) -> str:
        for key in ("problem_markdown", "question", "problem", "statement", "prompt"):
            if key in it and it[key] is not None:
                return str(it[key])
        return ""

    def _language(self, it: dict) -> str:
        for key in ("language", "lang"):
            if key in it and it[key] is not None:
                return str(it[key])
        return ""

    def problems(self, *, topic_prefix: Optional[str] = DEFAULT_TOPIC_PREFIX,
                 language: Optional[str] = DEFAULT_LANGUAGE) -> list[Problem]:
        """Filtered problems. `topic_prefix=None` disables topic filtering; `language=None` disables
        language filtering. Both default to Number-Theory + English."""
        out: list[Problem] = []
        for i, it in enumerate(self._items):
            paths = _normalize_paths(it.get("topics_flat"))
            if topic_prefix is not None and not _matches_topic_prefix(paths, topic_prefix):
                continue
            lang = self._language(it)
            if language is not None and lang and lang.strip().lower() != language.strip().lower():
                continue
            out.append(Problem(idx=self._idx(it, i), statement=self._statement(it),
                               topics=tuple(paths), language=lang))
        return out

    def oracle(self) -> dict[str, str]:
        """idx → gold answer. Held out — never handed to a solver. Upstream column is `final_answer`."""
        return {self._idx(it, i): str(it.get("final_answer", "") or it.get("answer", ""))
                for i, it in enumerate(self._items)}

    @classmethod
    def from_jsonl(cls, path: str | Path, release: str = "all") -> "MathNetDataset":
        p = Path(path)
        items = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
        return cls(items, release=release)

    @classmethod
    def from_huggingface(cls, config: str = "all", split: str = "train",
                         cache_dir: Optional[str] = None) -> "MathNetDataset":
        from datasets import load_dataset            # optional dep (mathagent[benchmark])
        ds = load_dataset("ShadenA/MathNet", config, split=split, cache_dir=cache_dir)
        return cls([dict(r) for r in ds], release=config)
