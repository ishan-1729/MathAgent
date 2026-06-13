"""Local semantic-ish Mathlib retrieval (BM25 over the elementary Mathlib source).

Loogle matches names/types by pattern; this complements it with relevance ranking by *meaning words*
(BM25 over tokenized declaration names + signatures), so an informal claim with no obvious identifier
overlap ("n squared is congruent ... modulo 4") still surfaces relevant lemmas (ModEq, sq, ...).

Design notes:
  - Corpus: declarations extracted from a curated, ELEMENTARY subset of the Mathlib source tree
    (Data/Nat, Data/Int, Data/ZMod by default) — aligned with the "retrieval restricted to an
    elementary corpus" principle, and keeps the index small + fast.
  - Names are fully qualified via namespace/section tracking (so Codex gets `Nat.add_comm`, not
    `add_comm`).
  - Ranking: BM25 (the standard local lexical-IR baseline). The backend is pluggable — a neural
    embedding index could replace BM25 without changing the `Retriever` interface.
  - Built once and cached to disk (under the gitignored `.lake/`); graceful no-Mathlib degradation.
"""
from __future__ import annotations

import math
import pickle
import re
from collections import Counter, defaultdict
from itertools import zip_longest
from pathlib import Path
from typing import Optional

from agent.gates.lean_bridge import find_mathlib_project

_VERSION = 2
_TOKEN_SPLIT = re.compile(r"[^A-Za-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_DECL = re.compile(
    r"^\s*(?:(?:private|protected|noncomputable|scoped|local|@\[[^\]]*\])\s+)*"
    r"(theorem|lemma|def|abbrev)\s+([A-Za-z_][A-Za-z0-9_'!?]*)")
_NS = re.compile(r"^\s*namespace\s+([A-Za-z_][\w.]*)")
_SEC = re.compile(r"^\s*section\b\s*([A-Za-z_][\w.]*)?")
_END = re.compile(r"^\s*end\b")

DEFAULT_MODULES = ["Data/Nat", "Data/Int", "Data/ZMod"]
STOPWORDS = {"the", "and", "for", "all", "every", "with", "that", "are", "is", "of", "to", "in",
            "if", "then", "let", "be", "an", "by"}


def tokenize(text: str) -> list[str]:
    out: list[str] = []
    for raw in _TOKEN_SPLIT.split(text or ""):
        if not raw:
            continue
        for part in _CAMEL.split(raw):
            p = part.lower()
            if len(p) >= 2 and p not in STOPWORDS:
                out.append(p)
    return out


def extract_declarations(text: str) -> list[tuple[str, str]]:
    """Return (fully_qualified_name, signature_line) for declarations, tracking namespace scope."""
    stack: list[tuple[str, str]] = []  # (kind, name); kind in {"ns","sec"}
    out: list[tuple[str, str]] = []
    for line in text.split("\n"):
        mns = _NS.match(line)
        if mns:
            stack.append(("ns", mns.group(1)))
            continue
        if _SEC.match(line):
            stack.append(("sec", ""))
            continue
        if _END.match(line):
            if stack:
                stack.pop()
            continue
        m = _DECL.match(line)
        if m:
            prefix = ".".join(n for k, n in stack if k == "ns" and n)
            full = f"{prefix}.{m.group(2)}" if prefix else m.group(2)
            out.append((full, line.strip()[:200]))
    return out


class BM25:
    def __init__(self, docs: list[tuple[str, list[str]]], k1: float = 1.5, b: float = 0.75):
        self.ids = [d[0] for d in docs]
        self.k1, self.b = k1, b
        self.N = len(docs)
        self.dl = [len(t) for t in (d[1] for d in docs)]
        self.avgdl = (sum(self.dl) / self.N) if self.N else 0.0
        self.df: Counter = Counter()
        self.inv: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for i, (_id, toks) in enumerate(docs):
            tf = Counter(toks)
            for term, f in tf.items():
                self.df[term] += 1
                self.inv[term].append((i, f))

    def _idf(self, term: str) -> float:
        n = self.df.get(term, 0)
        return math.log(1 + (self.N - n + 0.5) / (n + 0.5))

    def search(self, query_tokens: list[str], k: int = 10) -> list[tuple[int, float]]:
        scores: dict[int, float] = defaultdict(float)
        for term in set(query_tokens):
            postings = self.inv.get(term)
            if not postings:
                continue
            idf = self._idf(term)
            for i, f in postings:
                denom = f + self.k1 * (1 - self.b + self.b * self.dl[i] / (self.avgdl or 1))
                scores[i] += idf * (f * (self.k1 + 1)) / denom
        return sorted(scores.items(), key=lambda x: -x[1])[:k]


def build_corpus(mathlib_root: Path, modules: list[str],
                 max_decls: int = 120_000) -> list[tuple[str, str, list[str]]]:
    """Return (name, signature, tokens) per declaration found under the given module subdirs."""
    corpus: list[tuple[str, str, list[str]]] = []
    for mod in modules:
        base = mathlib_root / mod
        files = [base] if base.is_file() else sorted(base.rglob("*.lean")) if base.exists() else []
        for f in files:
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for name, sig in extract_declarations(text):
                # Boost name tokens (3x) so name matches rank above incidental signature words.
                toks = tokenize(name) * 3 + tokenize(sig)
                corpus.append((name, sig, toks))
                if len(corpus) >= max_decls:
                    return corpus
    return corpus


class SemanticRetriever:
    """BM25 retrieval over the elementary Mathlib corpus (lazy build + on-disk cache)."""

    def __init__(self, project_dir: Optional[str] = None, modules: Optional[list[str]] = None,
                 max_results: int = 12, cache_path: Optional[str] = None):
        self.project_dir = project_dir or find_mathlib_project()
        self.modules = modules or DEFAULT_MODULES
        self.max_results = max_results
        self._cache_path = (Path(cache_path) if cache_path else
                            (Path(self.project_dir) / ".lake" / "mathagent_semantic.pkl"
                             if self.project_dir else None))
        self._bm25: Optional[BM25] = None
        self._sigs: dict[str, str] = {}

    def _mathlib_root(self) -> Optional[Path]:
        if not self.project_dir:
            return None
        root = Path(self.project_dir) / ".lake" / "packages" / "mathlib" / "Mathlib"
        return root if root.exists() else None

    def available(self) -> bool:
        return self._mathlib_root() is not None

    def _ensure_index(self) -> bool:
        if self._bm25 is not None:
            return True
        # try cache
        if self._cache_path and self._cache_path.exists():
            try:
                blob = pickle.loads(self._cache_path.read_bytes())
                if blob.get("version") == _VERSION and blob.get("modules") == self.modules:
                    self._bm25, self._sigs = blob["bm25"], blob["sigs"]
                    return True
            except Exception:
                pass
        root = self._mathlib_root()
        if root is None:
            return False
        corpus = build_corpus(root, self.modules)
        if not corpus:
            return False
        self._bm25 = BM25([(name, toks) for name, _sig, toks in corpus])
        self._sigs = {name: sig for name, sig, _toks in corpus}
        if self._cache_path:
            try:
                self._cache_path.parent.mkdir(parents=True, exist_ok=True)
                self._cache_path.write_bytes(pickle.dumps(
                    {"version": _VERSION, "modules": self.modules,
                     "bm25": self._bm25, "sigs": self._sigs}))
            except Exception:
                pass
        return True

    def retrieve(self, claim: str, error: str = "") -> list[str]:
        if not self._ensure_index() or self._bm25 is None:
            return []
        tokens = tokenize(f"{claim} {error}")
        hits = self._bm25.search(tokens, k=self.max_results)
        out = []
        for idx, _score in hits:
            name = self._bm25.ids[idx]
            sig = self._sigs.get(name, "")
            out.append(f"{name} : {sig}" if sig else name)
        return out


class HybridRetriever:
    """Combine several retrievers (e.g. Loogle for error-identifiers + Semantic for the claim),
    interleaving their results and deduping by declaration name."""

    def __init__(self, retrievers: list, max_results: int = 14):
        self.retrievers = retrievers
        self.max_results = max_results

    def retrieve(self, claim: str, error: str = "") -> list[str]:
        results = [r.retrieve(claim, error) for r in self.retrievers]
        seen: set[str] = set()
        out: list[str] = []
        for tier in zip_longest(*results):
            for item in tier:
                if not item:
                    continue
                name = item.split(" :")[0].strip()
                if name not in seen:
                    seen.add(name)
                    out.append(item)
                    if len(out) >= self.max_results:
                        return out
        return out
