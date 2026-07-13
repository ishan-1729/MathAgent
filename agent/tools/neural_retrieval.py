"""Neural (dense bi-encoder) Mathlib retrieval — closes the abbreviation/paraphrase gap that defeats
lexical IR (e.g. "greatest common divisor" ↔ `gcd`, which BM25 wrongly matches to `findGreatest*`).

This is the LeanExplore recipe (the training-free Lean retriever): embed each declaration as
`name : signature` with a small general sentence-encoder (`BAAI/bge-small-en-v1.5`, fallback
`all-MiniLM-L6-v2`), L2-normalize, and rank by cosine (inner product). Optionally rerank the top
candidates with a cross-encoder (`ms-marco-MiniLM-L-6-v2`).

Design:
  - The model is an OPTIONAL dependency (`pip install mathagent[neural]`). Absent it, `available()` is
    False and `HybridRetriever` silently falls back to Loogle + BM25 — nothing breaks.
  - The `Embedder` is injectable, so the retrieval *plumbing* is fully testable offline with the
    dependency-free `HashingEmbedder` (no torch, no model download, no network).
  - The corpus reuses `semantic_retrieval.build_corpus` over the curated elementary Mathlib subset;
    embeddings are cached to disk (under the gitignored `.lake/`). numpy is used for the matrix when
    present (it ships with sentence-transformers); a pure-Python path keeps offline tests dep-free.
"""
from __future__ import annotations

import hashlib
import math
import re
import threading
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from agent.gates.lean_bridge import find_mathlib_project
from agent.tools.semantic_retrieval import (
    DEFAULT_MODULES, _atomic_write_json, _read_json_cache, _source_fingerprint,
    build_corpus, tokenize,
)

try:                                   # numpy ships with sentence-transformers; optional for tests
    import numpy as _np
except Exception:                      # pragma: no cover - environment-dependent
    _np = None

_VERSION = 3
_CACHE_FORMAT = "mathagent-neural-json"
_MAX_CACHE_BYTES = 32 * 1024 * 1024
_MAX_CACHE_ROWS = 120_000
_MAX_CACHE_TEXT_CHARS = 16 * 1024 * 1024
_MAX_VECTOR_DIM = 8_192
_MAX_VECTOR_VALUES = 5_000_000
_MAX_CACHE_VECTOR_VALUES = 1_500_000
_EMBED_BATCH_SIZE = 256
_COMMIT_HASH = re.compile(r"^[0-9a-fA-F]{40,64}$")


def _normalized_vector(row: object, expected_dim: Optional[int] = None) -> Optional[list[float]]:
    if not isinstance(row, list) or not row:
        return None
    if len(row) > _MAX_VECTOR_DIM or (expected_dim is not None and len(row) != expected_dim):
        return None
    values: list[float] = []
    for item in row:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        try:
            number = float(item)
        except (OverflowError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        values.append(number)

    # Scale first so values larger than float32 (or whose squared sum overflows float64) are safely
    # normalized before conversion to the float32 matrix.  A zero vector has undefined cosine and
    # must never become an all-ties result.
    scale = max(abs(number) for number in values)
    if scale == 0.0:
        return None
    scaled = [number / scale for number in values]
    norm = math.sqrt(sum(number * number for number in scaled))
    if not math.isfinite(norm) or norm == 0.0:
        return None
    normalized = [number / norm for number in scaled]
    return normalized if all(math.isfinite(number) for number in normalized) else None


def _validated_vectors(value: object, expected_rows: Optional[int] = None,
                       expected_dim: Optional[int] = None,
                       max_values: Optional[int] = None) -> Optional[list[list[float]]]:
    value_limit = _MAX_VECTOR_VALUES if max_values is None else max_values
    if not isinstance(value, list) or not value or len(value) > _MAX_CACHE_ROWS:
        return None
    if expected_rows is not None and len(value) != expected_rows:
        return None
    dim: Optional[int] = None
    out: list[list[float]] = []
    for row in value:
        normalized = _normalized_vector(row, expected_dim if dim is None else dim)
        if normalized is None:
            return None
        if dim is None:
            dim = len(normalized)
            if dim > _MAX_VECTOR_DIM or len(value) * dim > value_limit:
                return None
        out.append(normalized)
    return out


def _validated_neural_cache(blob: object, modules: list[str], model: str, source_fingerprint: str
                            ) -> Optional[tuple[list[str], list[str], list[list[float]]]]:
    if not isinstance(blob, dict):
        return None
    if (blob.get("format") != _CACHE_FORMAT or blob.get("version") != _VERSION
            or blob.get("modules") != modules or blob.get("model") != model
            or blob.get("source_fingerprint") != source_fingerprint):
        return None
    raw_corpus = blob.get("corpus")
    if not isinstance(raw_corpus, list) or not raw_corpus or len(raw_corpus) > _MAX_CACHE_ROWS:
        return None
    names: list[str] = []
    docs: list[str] = []
    total_chars = 0
    for row in raw_corpus:
        if not isinstance(row, list) or len(row) != 2:
            return None
        name, signature = row
        if (not isinstance(name, str) or not name or len(name) > 512
                or not isinstance(signature, str) or len(signature) > 4_096):
            return None
        total_chars += len(name) + len(signature)
        if total_chars > _MAX_CACHE_TEXT_CHARS:
            return None
        names.append(name)
        docs.append(f"{name} : {signature}" if signature else name)
    vectors = _validated_vectors(
        blob.get("vectors"), len(names), max_values=_MAX_CACHE_VECTOR_VALUES)
    if vectors is None:
        return None
    return list(names), list(docs), vectors


@runtime_checkable
class Embedder(Protocol):
    def available(self) -> bool: ...
    def encode(self, texts: list[str]) -> list[list[float]]:
        """Return one L2-normalized vector per input text."""
        ...

    # Custom embedders may expose a stable non-secret `cache_tag` string.  Without one, disk cache
    # reuse is disabled because a class name/dimension does not identify a vector coordinate space.


def _l2(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else v


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _commit_from_model(model: object) -> Optional[str]:
    """Best-effort extraction of the immutable Hugging Face snapshot commit."""
    objects = [model]
    modules = getattr(model, "_modules", None)
    if isinstance(modules, dict):
        objects.extend(modules.values())
    cursor = 0
    while cursor < len(objects) and len(objects) < 64:
        obj = objects[cursor]
        cursor += 1
        for attr in ("auto_model", "config", "tokenizer"):
            child = getattr(obj, attr, None)
            if child is not None and not any(child is existing for existing in objects):
                objects.append(child)

    for obj in objects:
        for candidate in (
            getattr(obj, "_commit_hash", None),
            getattr(obj, "commit_hash", None),
            getattr(obj, "revision", None),
        ):
            if isinstance(candidate, str) and _COMMIT_HASH.fullmatch(candidate):
                return candidate.lower()
        for mapping_name in ("init_kwargs", "_model_config"):
            mapping = getattr(obj, mapping_name, None)
            if isinstance(mapping, dict):
                for key in ("_commit_hash", "commit_hash", "revision"):
                    candidate = mapping.get(key)
                    if isinstance(candidate, str) and _COMMIT_HASH.fullmatch(candidate):
                        return candidate.lower()
        for path_attr in ("name_or_path", "model_name_or_path", "cache_dir"):
            candidate = getattr(obj, path_attr, None)
            if isinstance(candidate, str):
                match = re.search(r"(?:^|[/\\])snapshots[/\\]([0-9a-fA-F]{40,64})(?:[/\\]|$)",
                                  candidate)
                if match:
                    return match.group(1).lower()
    return None


class SentenceTransformerEmbedder:
    """Pretrained bi-encoder via `sentence-transformers` (the default neural backend)."""

    # bge-small-en-v1.5: 33M params, 384-dim, ~130 MB, strong small-model retrieval; needs no prefix.
    DEFAULT_MODELS = ["BAAI/bge-small-en-v1.5", "sentence-transformers/all-MiniLM-L6-v2"]

    def __init__(self, model_name: Optional[str] = None, revision: Optional[str] = None):
        self.model_name = model_name
        self.revision = revision
        self._model = None
        self.loaded_name: Optional[str] = None
        self.loaded_revision: Optional[str] = None

    def available(self) -> bool:
        try:
            import sentence_transformers  # noqa: F401
            return True
        except Exception:
            return False

    def _load(self):
        if self._model is not None:
            return self._model
        from sentence_transformers import SentenceTransformer
        names = [self.model_name] if self.model_name else self.DEFAULT_MODELS
        last: Optional[Exception] = None
        for n in names:
            try:
                kwargs = {"revision": self.revision} if self.revision is not None else {}
                self._model = SentenceTransformer(n, **kwargs)
                self.loaded_name = n
                pinned = (self.revision.lower() if isinstance(self.revision, str)
                          and _COMMIT_HASH.fullmatch(self.revision) else None)
                try:
                    self.loaded_revision = _commit_from_model(self._model) or pinned
                except Exception:
                    # Revision introspection controls only cache reuse; it must not make an otherwise
                    # usable encoder unavailable.
                    self.loaded_revision = pinned
                return self._model
            except Exception as e:  # try the next fallback (offline / not downloaded)
                last = e
        raise RuntimeError(f"no sentence-transformers model could be loaded: {last}")

    def encode(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        vecs = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True,
                            show_progress_bar=False)
        return [list(map(float, v)) for v in vecs]


class HashingEmbedder:
    """Deterministic, dependency-free embedder: token hashing → fixed-dim L2-normalized bag vector.

    Not a real semantic model — it exists so the retrieval plumbing (build/cache/cosine/rerank) is
    testable without torch. Shared tokens still produce nonzero cosine, so token-overlapping
    query/doc pairs rank correctly in tests."""

    def __init__(self, dim: int = 256):
        self.dim = dim

    def available(self) -> bool:
        return True

    def encode(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            v = [0.0] * self.dim
            for tok in tokenize(t):
                h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16) % self.dim
                v[h] += 1.0
            out.append(_l2(v))
        return out


@runtime_checkable
class Reranker(Protocol):
    def available(self) -> bool: ...
    def rerank(self, query: str, docs: list[str]) -> list[int]:
        """Return doc indices best→worst."""
        ...


class CrossEncoderReranker:
    """Optional cross-encoder reranker (`ms-marco-MiniLM-L-6-v2`) over bi-encoder candidates."""

    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"):
        self.model_name = model_name
        self._model = None

    def available(self) -> bool:
        try:
            import sentence_transformers  # noqa: F401
            return True
        except Exception:
            return False

    def _load(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder
            self._model = CrossEncoder(self.model_name)
        return self._model

    def rerank(self, query: str, docs: list[str]) -> list[int]:
        model = self._load()
        scores = model.predict([(query, d) for d in docs])
        return sorted(range(len(docs)), key=lambda i: -float(scores[i]))


class NeuralRetriever:
    """Dense top-k cosine retrieval over the elementary Mathlib corpus (lazy build + on-disk cache)."""

    def __init__(self, embedder: Optional[Embedder] = None, project_dir: Optional[str] = None,
                 modules: Optional[list[str]] = None, max_results: int = 12,
                 cache_path: Optional[str] = None, corpus: Optional[list[tuple[str, str]]] = None,
                 reranker: Optional[Reranker] = None, rerank_pool: int = 50):
        self.embedder: Embedder = embedder if embedder is not None else SentenceTransformerEmbedder()
        self.project_dir = project_dir or find_mathlib_project()
        self.modules = modules or DEFAULT_MODULES
        self.max_results = max_results
        self.reranker = reranker
        self.rerank_pool = rerank_pool
        self._given_corpus = corpus
        self._cache_path = (Path(cache_path) if cache_path else
                            (Path(self.project_dir) / ".lake" / "mathagent_neural.json"
                             if (self.project_dir and corpus is None) else None))
        self._names: list[str] = []
        self._docs: list[str] = []
        self._mat = None        # numpy array or list[list[float]]
        self._index_lock = threading.RLock()

    def _mathlib_root(self) -> Optional[Path]:
        if not self.project_dir:
            return None
        root = Path(self.project_dir) / ".lake" / "packages" / "mathlib" / "Mathlib"
        return root if root.exists() else None

    def available(self) -> bool:
        if not self.embedder.available():
            return False
        return self._given_corpus is not None or self._mathlib_root() is not None

    def _build_corpus(self) -> list[tuple[str, str]]:
        if self._given_corpus is not None:
            return list(self._given_corpus)
        root = self._mathlib_root()
        if root is None:
            return []
        return [(name, sig) for name, sig, _toks in build_corpus(root, self.modules)]

    def _model_tag(self) -> Optional[str]:
        # Cache identity must name the actual vector space, not merely its dimension or a fallback
        # chain.  BGE and MiniLM are both 384-dimensional but their coordinates are incompatible;
        # reusing one model's document matrix with the other's query vector silently corrupts ranks.
        cls = self.embedder.__class__.__name__
        if type(self.embedder) is SentenceTransformerEmbedder:
            # A repo id or tag can move.  Reuse is safe only when the loaded snapshot exposes the
            # immutable commit that defines this exact vector space.
            self.embedder._load()
            selected = self.embedder.loaded_name
            revision = self.embedder.loaded_revision
            if (not isinstance(selected, str) or not selected
                    or not isinstance(revision, str) or not _COMMIT_HASH.fullmatch(revision)):
                return None
            return f"{cls}:{selected}@{revision.lower()}"
        explicit = getattr(self.embedder, "cache_tag", None)
        if isinstance(explicit, str) and explicit and len(explicit) <= 512:
            return f"{cls}:{explicit}"
        if type(self.embedder) is HashingEmbedder:
            return f"{cls}:{self.embedder.dim}"
        return None

    def _expected_embedding_dim(self) -> Optional[int]:
        dim = getattr(self.embedder, "dim", None)
        if isinstance(dim, int) and not isinstance(dim, bool):
            return int(dim) if 0 < dim <= _MAX_VECTOR_DIM else -1
        if isinstance(self.embedder, SentenceTransformerEmbedder):
            try:
                getter = getattr(self.embedder, "get_sentence_embedding_dimension", None)
                if callable(getter):
                    model_dim = getter()
                elif type(self.embedder) is SentenceTransformerEmbedder:
                    model = self.embedder._load()
                    getter = getattr(model, "get_sentence_embedding_dimension", None)
                    model_dim = getter() if callable(getter) else None
                else:
                    model_dim = None
            except Exception:
                return None
            if isinstance(model_dim, int) and not isinstance(model_dim, bool):
                return int(model_dim) if 0 < model_dim <= _MAX_VECTOR_DIM else -1
        return None

    def _cache_source_fingerprint(self) -> Optional[str]:
        if self._given_corpus is not None:
            h = hashlib.sha256()
            for name, signature in self._given_corpus:
                for value in (name, signature):
                    data = value.encode("utf-8")
                    h.update(len(data).to_bytes(8, "big")); h.update(data)
            return h.hexdigest()
        root = self._mathlib_root()
        if root is None:
            return None
        try:
            return _source_fingerprint(root, self.modules)
        except OSError:
            return None

    @staticmethod
    def _validated_corpus(corpus: object) -> Optional[list[tuple[str, str]]]:
        if not isinstance(corpus, list) or not corpus or len(corpus) > _MAX_CACHE_ROWS:
            return None
        out: list[tuple[str, str]] = []
        total_chars = 0
        for row in corpus:
            if not isinstance(row, (list, tuple)) or len(row) != 2:
                return None
            name, signature = row
            if (not isinstance(name, str) or not name or len(name) > 512
                    or not isinstance(signature, str) or len(signature) > 4_096):
                return None
            total_chars += len(name) + len(signature)
            if total_chars > _MAX_CACHE_TEXT_CHARS:
                return None
            out.append((name, signature))
        return out

    def _embed_documents(self, docs: list[str], expected_dim: Optional[int]
                         ) -> Optional[list[list[float]]]:
        if expected_dim is not None:
            if expected_dim <= 0 or len(docs) * expected_dim > _MAX_VECTOR_VALUES:
                return None
        vectors: list[list[float]] = []
        actual_dim = expected_dim
        for start in range(0, len(docs), _EMBED_BATCH_SIZE):
            batch = docs[start:start + _EMBED_BATCH_SIZE]
            encoded = self.embedder.encode(batch)
            validated = _validated_vectors(encoded, len(batch), actual_dim)
            if validated is None:
                return None
            if actual_dim is None:
                actual_dim = len(validated[0])
                # Discover the dimension from only one bounded batch, then reject before embedding
                # the rest of a corpus that could never fit the global vector budget.
                if len(docs) * actual_dim > _MAX_VECTOR_VALUES:
                    return None
            vectors.extend(validated)
        return vectors

    def _ensure_index(self) -> bool:
        # Serialize lazy construction and publish the matrix last. Concurrent proof repairs may
        # share one retriever; no caller may see names/docs from one generation with another matrix.
        with self._index_lock:
            return self._ensure_index_locked()

    def _ensure_index_locked(self) -> bool:
        if self._mat is not None:
            return True
        if not self.embedder.available():
            return False
        source_fingerprint = self._cache_source_fingerprint()
        if source_fingerprint is None:
            return False
        try:
            model_tag = self._model_tag()
        except Exception:
            return False
        if model_tag is not None and self._cache_path and self._cache_path.exists():
            try:
                blob = _read_json_cache(self._cache_path, _MAX_CACHE_BYTES)
                cached = _validated_neural_cache(
                    blob, self.modules, model_tag, source_fingerprint)
                if cached is not None:
                    names, docs, vecs = cached
                    expected = self._expected_embedding_dim()
                    if expected is None or len(vecs[0]) == expected:
                        mat = _np.asarray(vecs, dtype="float32") if _np is not None else vecs
                        self._names, self._docs = names, docs
                        self._mat = mat
                        return True
            except Exception:
                pass
        corpus = self._validated_corpus(self._build_corpus())
        if corpus is None:
            return False
        names = [n for n, _ in corpus]
        docs = [f"{n} : {s}" if s else n for n, s in corpus]
        expected = self._expected_embedding_dim()
        validated = self._embed_documents(docs, expected)
        if validated is None:
            # empty/ragged/non-finite/wrong-dimension embedder output → not usable
            return False
        mat = _np.asarray(validated, dtype="float32") if _np is not None else validated
        cacheable_vectors = len(validated) * len(validated[0]) <= _MAX_CACHE_VECTOR_VALUES
        if model_tag is not None and self._cache_path and cacheable_vectors:
            try:
                # Eight decimal places are ample for cosine ranking while preventing Python's
                # full-precision float representation from producing a cache larger than its read
                # limit.  Reload validation renormalizes the quantized rows.
                cached_vectors = [[round(value, 8) for value in row] for row in validated]
                _atomic_write_json(self._cache_path, {
                    "format": _CACHE_FORMAT,
                    "version": _VERSION,
                    "modules": self.modules,
                    "model": model_tag,
                    "source_fingerprint": source_fingerprint,
                    "corpus": [[name, signature] for name, signature in corpus],
                    "vectors": cached_vectors,
                }, _MAX_CACHE_BYTES)
            except Exception:
                pass
        self._names, self._docs = names, docs
        self._mat = mat  # readiness marker is published last
        return True

    def _topk(self, q: list[float], k: int) -> list[int]:
        if _np is not None and not isinstance(self._mat, list):
            scores = self._mat @ _np.asarray(q, dtype="float32")
            return [int(i) for i in scores.argsort()[::-1][:k]]
        scored = sorted(range(len(self._mat)), key=lambda i: -_dot(q, self._mat[i]))
        return scored[:k]

    def _matrix_dim(self) -> Optional[int]:
        if self._mat is None:
            return None
        if _np is not None and not isinstance(self._mat, list):
            return int(self._mat.shape[1]) if getattr(self._mat, "ndim", 0) == 2 else None
        if isinstance(self._mat, list) and self._mat and isinstance(self._mat[0], list):
            return len(self._mat[0])
        return None

    def retrieve(self, claim: str, error: str = "") -> list[str]:
        if not self._ensure_index() or self._mat is None:
            return []
        query = f"{claim} {error}".strip()
        encoded = self.embedder.encode([query])
        if not isinstance(encoded, list) or len(encoded) != 1 or not isinstance(encoded[0], list):
            return []
        dim = self._matrix_dim()
        if dim is None:
            return []
        q = _normalized_vector(encoded[0], dim)
        if q is None:
            return []
        pool = max(self.max_results, self.rerank_pool) if (self.reranker and
                                                           self.reranker.available()) else self.max_results
        idx = self._topk(q, pool)
        if self.reranker and self.reranker.available() and idx:
            cand_docs = [self._docs[i] for i in idx]
            order = self.reranker.rerank(query, cand_docs)
            idx = [idx[o] for o in order]
        return [self._docs[i] for i in idx[:self.max_results]]
