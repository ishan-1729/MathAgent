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

import hashlib
import json
import math
import os
import re
import stat
import tempfile
import threading
from collections import Counter, defaultdict
from itertools import zip_longest
from pathlib import Path
from typing import Optional

from agent.gates.lean_bridge import find_mathlib_project

_VERSION = 4
_CACHE_FORMAT = "mathagent-semantic-json"
_MAX_CACHE_BYTES = 64 * 1024 * 1024
_MAX_CACHE_DECLS = 120_000
_MAX_CACHE_TEXT_CHARS = 32 * 1024 * 1024
_MAX_CORPUS_TOKENS = 5_000_000
_MAX_TOKENS_PER_DECL = 512
_MAX_SIGNATURE_CHARS = 2_000
_TOKEN_SPLIT = re.compile(r"[^A-Za-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_DECL = re.compile(
    r"^\s*((?:(?:private|protected|noncomputable|scoped|local)\s+|@\[[^\]]*\]\s+)*)"
    r"(theorem|lemma|def|abbrev)\s+"
    r"([A-Za-z_][A-Za-z0-9_'!?]*(?:\.[A-Za-z_][A-Za-z0-9_'!?]*)*)")
_NS = re.compile(r"^\s*namespace\s+([A-Za-z_][\w.]*)")
_SEC = re.compile(r"^\s*section\b\s*([A-Za-z_][\w.]*)?")
_END = re.compile(r"^\s*end\b(?:\s+([A-Za-z_][\w.]*))?")

DEFAULT_MODULES = ["Data/Nat", "Data/Int", "Data/ZMod"]
STOPWORDS = {"the", "and", "for", "all", "every", "with", "that", "are", "is", "of", "to", "in",
            "if", "then", "let", "be", "an", "by"}


def _read_json_cache(path: Path, max_bytes: int) -> object:
    """Read a bounded, data-only cache.  No object hooks or executable deserialization exist."""
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        # Check the opened handle, not the path (which could be replaced between stat and open), and
        # require a regular file so a FIFO/device cannot block the cache reader at open/read.
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError("cache is not a regular file")
        if opened.st_size > max_bytes:
            raise ValueError("cache is too large")
        with os.fdopen(fd, "rb", closefd=False) as fh:
            payload = fh.read(max_bytes + 1)
    finally:
        os.close(fd)
    if len(payload) > max_bytes:
        raise ValueError("cache grew beyond its size limit")
    return json.loads(payload)


def _atomic_write_json(path: Path, value: object, max_bytes: Optional[int] = None) -> None:
    """Write JSON beside the destination, fsync it, then atomically replace the cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        encoder = json.JSONEncoder(
            ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        total = 0
        with os.fdopen(fd, "wb") as fh:
            for chunk in encoder.iterencode(value):
                encoded = chunk.encode("utf-8")
                total += len(encoded)
                if max_bytes is not None and total > max_bytes:
                    raise ValueError("cache serialization exceeds its size limit")
                fh.write(encoded)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _source_fingerprint(mathlib_root: Path, modules: list[str]) -> str:
    """Content receipt over the exact source files feeding the corpus.

    File timestamps are neither stable identifiers nor reliable invalidators: a checkout can alter
    mtimes without changing content, and an attacker can preserve an mtime while replacing content.
    Hash each relative path and its bytes instead.
    """
    h = hashlib.sha256()
    for mod in modules:
        base = mathlib_root / mod
        files = [base] if base.is_file() else sorted(base.rglob("*.lean")) if base.exists() else []
        for path in files:
            rel = path.relative_to(mathlib_root).as_posix().encode("utf-8")
            h.update(len(rel).to_bytes(4, "big")); h.update(rel)
            file_hash = hashlib.sha256()
            with path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    file_hash.update(chunk)
            h.update(file_hash.digest())
    return h.hexdigest()


def _validated_cached_corpus(blob: object, modules: list[str], source_fingerprint: str
                             ) -> Optional[list[tuple[str, str, list[str]]]]:
    if not isinstance(blob, dict):
        return None
    if (blob.get("format") != _CACHE_FORMAT or blob.get("version") != _VERSION
            or blob.get("modules") != modules
            or blob.get("source_fingerprint") != source_fingerprint):
        return None
    raw = blob.get("corpus")
    if not isinstance(raw, list) or not raw or len(raw) > _MAX_CACHE_DECLS:
        return None
    corpus: list[tuple[str, str, list[str]]] = []
    total_chars = 0
    total_tokens = 0
    for row in raw:
        # Tokens are deterministic derivatives of name + signature.  Omitting them from the JSON
        # avoids an attacker-controlled nested-list amplification and substantially shrinks caches.
        if not isinstance(row, list) or len(row) != 2:
            return None
        name, sig = row
        if (not isinstance(name, str) or not name or len(name) > 512
                or not isinstance(sig, str) or len(sig) > _MAX_SIGNATURE_CHARS):
            return None
        total_chars += len(name) + len(sig)
        if total_chars > _MAX_CACHE_TEXT_CHARS:
            return None
        toks = (tokenize(name) * 3 + tokenize(sig))[:_MAX_TOKENS_PER_DECL]
        total_tokens += len(toks)
        if total_tokens > _MAX_CORPUS_TOKENS:
            return None
        corpus.append((name, sig, toks))
    return corpus


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


def _without_comments(text: str) -> str:
    """Replace Lean comments with spaces while preserving lines and string literals.

    Lean block comments nest.  Keeping every newline and replacing other comment characters with
    spaces leaves line-oriented scope parsing intact without indexing declarations written in docs
    or comments.  Comment markers inside strings are ordinary characters.
    """
    out = list(text)
    depth = 0
    in_string = False
    escaped = False
    i = 0
    while i < len(text):
        if depth:
            if text.startswith("/-", i):
                out[i] = out[i + 1] = " "
                depth += 1
                i += 2
                continue
            if text.startswith("-/", i):
                out[i] = out[i + 1] = " "
                depth -= 1
                i += 2
                continue
            if text[i] != "\n":
                out[i] = " "
            i += 1
            continue
        if in_string:
            if escaped:
                escaped = False
            elif text[i] == "\\":
                escaped = True
            elif text[i] == '"':
                in_string = False
            i += 1
            continue
        if text.startswith("--", i):
            while i < len(text) and text[i] != "\n":
                out[i] = " "
                i += 1
            continue
        if text.startswith("/-", i):
            out[i] = out[i + 1] = " "
            depth = 1
            i += 2
            continue
        if text[i] == '"':
            in_string = True
        i += 1
    return "".join(out)


def _scope_matches(opened: str, closing: str) -> bool:
    """Lean permits the final component when closing a dotted namespace (``end Bar``)."""
    return closing == opened or closing == opened.rsplit(".", 1)[-1]


def _declaration_header(lines: list[str], start: int, match: re.Match[str]) -> tuple[str, int]:
    """Collect a declaration header through its balanced, possibly multiline signature.

    The result ends before a top-level body introducer.  This is deliberately a small lexer rather
    than a Lean parser, but it avoids truncating binders/types merely because they cross a newline.
    """
    pieces: list[str] = []
    delimiters: list[str] = []
    pairs = {')': '(', ']': '[', '}': '{'}
    in_string = False
    escaped = False
    last = start

    for line_no in range(start, min(len(lines), start + 64)):
        line = lines[line_no]
        segment = line[match.start():] if line_no == start else line
        stripped = segment.strip()
        if line_no > start and not delimiters:
            if stripped.startswith("|"):
                break
            if _DECL.match(segment) or _NS.match(segment) or _SEC.match(segment) or _END.match(segment):
                break

        stop = len(segment)
        i = 0
        while i < len(segment):
            ch = segment[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                i += 1
                continue
            if ch == '"':
                in_string = True
                i += 1
                continue
            if ch in "([{":
                delimiters.append(ch)
            elif ch in pairs:
                if delimiters and delimiters[-1] == pairs[ch]:
                    delimiters.pop()
            elif not delimiters and segment.startswith(":=", i):
                stop = i
                break
            elif (not delimiters and segment.startswith("where", i)
                  and (i == 0 or not (segment[i - 1].isalnum() or segment[i - 1] == "_"))
                  and (i + 5 == len(segment)
                       or not (segment[i + 5].isalnum() or segment[i + 5] == "_"))):
                stop = i
                break
            i += 1

        before_body = segment[:stop].strip()
        if before_body:
            pieces.append(before_body)
        last = line_no
        if stop != len(segment):
            break
        if sum(len(piece) for piece in pieces) >= _MAX_SIGNATURE_CHARS:
            break

    return " ".join(pieces)[:_MAX_SIGNATURE_CHARS], last


def extract_declarations(text: str) -> list[tuple[str, str]]:
    """Return usable public declarations with namespace-qualified, balanced signatures."""
    lines = _without_comments(text).split("\n")
    stack: list[tuple[str, str]] = []  # (kind, name); kind in {"ns","sec"}
    out: list[tuple[str, str]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        mns = _NS.match(line)
        if mns:
            stack.append(("ns", mns.group(1)))
            i += 1
            continue
        msec = _SEC.match(line)
        if msec:
            stack.append(("sec", msec.group(1) or ""))
            i += 1
            continue
        mend = _END.match(line)
        if mend:
            closing = mend.group(1)
            if stack and (closing is None or not stack[-1][1]
                          or _scope_matches(stack[-1][1], closing)):
                stack.pop()
            i += 1
            continue
        m = _DECL.match(line)
        if m:
            modifiers = m.group(1).split()
            if "private" in modifiers:
                i += 1
                continue
            prefix = ".".join(n for k, n in stack if k == "ns" and n)
            declared = m.group(3)
            full = f"{prefix}.{declared}" if prefix else declared
            signature, last = _declaration_header(lines, i, m)
            out.append((full, signature))
            i = last + 1
            continue
        i += 1
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
    total_tokens = 0
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
                toks = (tokenize(name) * 3 + tokenize(sig))[:_MAX_TOKENS_PER_DECL]
                if total_tokens + len(toks) > _MAX_CORPUS_TOKENS:
                    return corpus
                corpus.append((name, sig, toks))
                total_tokens += len(toks)
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
                            (Path(self.project_dir) / ".lake" / "mathagent_semantic.json"
                             if self.project_dir else None))
        self._bm25: Optional[BM25] = None
        self._sigs: dict[str, str] = {}
        self._index_lock = threading.RLock()

    def _mathlib_root(self) -> Optional[Path]:
        if not self.project_dir:
            return None
        root = Path(self.project_dir) / ".lake" / "packages" / "mathlib" / "Mathlib"
        return root if root.exists() else None

    def available(self) -> bool:
        return self._mathlib_root() is not None

    def _ensure_index(self) -> bool:
        # Index construction and publication are one transaction. Without this lock, concurrent
        # terminal-gate repairs could observe ``_bm25`` after it was assigned but before ``_sigs``,
        # returning identifier-only or mismatched results from a partially published index.
        with self._index_lock:
            return self._ensure_index_locked()

    def _ensure_index_locked(self) -> bool:
        if self._bm25 is not None:
            return True
        root = self._mathlib_root()
        if root is None:
            return False
        try:
            source_fingerprint = _source_fingerprint(root, self.modules)
        except OSError:
            return False
        # try cache
        if self._cache_path and self._cache_path.exists():
            try:
                blob = _read_json_cache(self._cache_path, _MAX_CACHE_BYTES)
                cached = _validated_cached_corpus(blob, self.modules, source_fingerprint)
                if cached is not None:
                    bm25 = BM25([(name, toks) for name, _sig, toks in cached])
                    sigs = {name: sig for name, sig, _toks in cached}
                    self._sigs = sigs
                    self._bm25 = bm25  # readiness marker is published last
                    return True
            except Exception:
                pass
        corpus = build_corpus(root, self.modules)
        if not corpus:
            return False
        bm25 = BM25([(name, toks) for name, _sig, toks in corpus])
        sigs = {name: sig for name, sig, _toks in corpus}
        if self._cache_path:
            try:
                _atomic_write_json(self._cache_path, {
                    "format": _CACHE_FORMAT,
                    "version": _VERSION,
                    "modules": self.modules,
                    "source_fingerprint": source_fingerprint,
                    "corpus": [[name, sig] for name, sig, _toks in corpus],
                }, _MAX_CACHE_BYTES)
            except Exception:
                pass
        self._sigs = sigs
        self._bm25 = bm25  # readiness marker is published last
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
