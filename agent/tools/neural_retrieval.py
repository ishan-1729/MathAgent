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
import pickle
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from agent.gates.lean_bridge import find_mathlib_project
from agent.tools.semantic_retrieval import DEFAULT_MODULES, build_corpus, tokenize

try:                                   # numpy ships with sentence-transformers; optional for tests
    import numpy as _np
except Exception:                      # pragma: no cover - environment-dependent
    _np = None

_VERSION = 1


@runtime_checkable
class Embedder(Protocol):
    def available(self) -> bool: ...
    def encode(self, texts: list[str]) -> list[list[float]]:
        """Return one L2-normalized vector per input text."""
        ...


def _l2(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else v


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class SentenceTransformerEmbedder:
    """Pretrained bi-encoder via `sentence-transformers` (the default neural backend)."""

    # bge-small-en-v1.5: 33M params, 384-dim, ~130 MB, strong small-model retrieval; needs no prefix.
    DEFAULT_MODELS = ["BAAI/bge-small-en-v1.5", "sentence-transformers/all-MiniLM-L6-v2"]

    def __init__(self, model_name: Optional[str] = None):
        self.model_name = model_name
        self._model = None
        self.loaded_name: Optional[str] = None

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
                self._model = SentenceTransformer(n)
                self.loaded_name = n
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
                            (Path(self.project_dir) / ".lake" / "mathagent_neural.pkl"
                             if (self.project_dir and corpus is None) else None))
        self._names: list[str] = []
        self._docs: list[str] = []
        self._mat = None        # numpy array or list[list[float]]

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

    def _model_tag(self) -> str:
        return getattr(self.embedder, "loaded_name", None) or self.embedder.__class__.__name__

    def _ensure_index(self) -> bool:
        if self._mat is not None:
            return True
        if not self.embedder.available():
            return False
        if self._cache_path and self._cache_path.exists():
            try:
                blob = pickle.loads(self._cache_path.read_bytes())
                if (blob.get("version") == _VERSION and blob.get("modules") == self.modules
                        and blob.get("model") == self._model_tag()):
                    self._names, self._docs, self._mat = blob["names"], blob["docs"], blob["mat"]
                    return True
            except Exception:
                pass
        corpus = self._build_corpus()
        if not corpus:
            return False
        names = [n for n, _ in corpus]
        docs = [f"{n} : {s}" if s else n for n, s in corpus]
        vecs = self.embedder.encode(docs)
        if not vecs:                       # embedder produced nothing → not usable
            return False
        self._names, self._docs = names, docs
        self._mat = _np.asarray(vecs, dtype="float32") if _np is not None else vecs
        if self._cache_path:
            try:
                self._cache_path.parent.mkdir(parents=True, exist_ok=True)
                self._cache_path.write_bytes(pickle.dumps(
                    {"version": _VERSION, "modules": self.modules, "model": self._model_tag(),
                     "names": self._names, "docs": self._docs, "mat": self._mat}))
            except Exception:
                pass
        return True

    def _topk(self, q: list[float], k: int) -> list[int]:
        if _np is not None and not isinstance(self._mat, list):
            scores = self._mat @ _np.asarray(q, dtype="float32")
            return [int(i) for i in scores.argsort()[::-1][:k]]
        scored = sorted(range(len(self._mat)), key=lambda i: -_dot(q, self._mat[i]))
        return scored[:k]

    def retrieve(self, claim: str, error: str = "") -> list[str]:
        if not self._ensure_index() or self._mat is None:
            return []
        query = f"{claim} {error}".strip()
        q = self.embedder.encode([query])[0]
        pool = max(self.max_results, self.rerank_pool) if (self.reranker and
                                                           self.reranker.available()) else self.max_results
        idx = self._topk(q, pool)
        if self.reranker and self.reranker.available() and idx:
            cand_docs = [self._docs[i] for i in idx]
            order = self.reranker.rerank(query, cand_docs)
            idx = [idx[o] for o in order]
        return [self._docs[i] for i in idx[:self.max_results]]
