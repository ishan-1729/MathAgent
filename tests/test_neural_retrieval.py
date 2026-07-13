"""Tests for the neural (dense bi-encoder) Mathlib retriever.

Offline tests inject the dependency-free HashingEmbedder over a synthetic corpus (no torch / model /
network), exercising the build→cache→cosine→rerank plumbing. The live test (opt-in) uses the real
bge model over Mathlib and asserts the *semantic* win lexical IR can't get: matching the phrase
"greatest common divisor" to the identifier `gcd`.
"""
import json
import os
import pickle
import threading
import time

import pytest

import agent.tools.neural_retrieval as nr
from agent.tools.neural_retrieval import (
    NeuralRetriever, HashingEmbedder, SentenceTransformerEmbedder, _validated_vectors,
)

CORPUS = [
    ("Nat.gcd", "def gcd (m n : Nat) : Nat"),
    ("Nat.Coprime", "def Coprime (m n : Nat) : Prop := gcd m n = 1"),
    ("Nat.add_comm", "theorem add_comm (a b : Nat) : a + b = b + a"),
    ("Int.even_iff", "theorem even_iff : Even n ↔ n % 2 = 0"),
]


class _OffEmbedder:
    def available(self):
        return False

    def encode(self, texts):
        return []


class _ReverseReranker:
    def available(self):
        return True

    def rerank(self, query, docs):
        return list(range(len(docs)))[::-1]


def test_hashing_embedder_is_available_and_normalized():
    emb = HashingEmbedder(dim=32)
    assert emb.available()
    [v] = emb.encode(["Nat.gcd coprime"])
    assert abs(sum(x * x for x in v) - 1.0) < 1e-9


def test_vector_validation_normalizes_nonunit_and_float32_overflow_values():
    vectors = _validated_vectors([[3.0, 4.0], [1e300, -1e300]])
    assert vectors is not None
    assert all(abs(sum(x * x for x in row) - 1.0) < 1e-12 for row in vectors)
    assert all(abs(x) <= 1.0 for row in vectors for x in row)


@pytest.mark.parametrize("row", [
    [0.0, 0.0], [float("inf"), 1.0], [True, 1.0], [10 ** 400, 1.0],
])
def test_vector_validation_rejects_undefined_or_non_numeric_cosine_rows(row):
    assert _validated_vectors([row]) is None


def test_neural_retriever_ranks_token_overlapping_docs():
    r = NeuralRetriever(embedder=HashingEmbedder(dim=64), corpus=CORPUS, max_results=2)
    assert r.available()
    hits = r.retrieve("gcd of two naturals and coprime", "")
    assert hits and any("gcd" in h.lower() or "coprime" in h.lower() for h in hits)


def test_unavailable_embedder_degrades_gracefully():
    r = NeuralRetriever(embedder=_OffEmbedder(), corpus=CORPUS)
    assert r.available() is False
    assert r.retrieve("anything") == []


def test_reranker_reorders_candidates():
    base = NeuralRetriever(embedder=HashingEmbedder(dim=64), corpus=CORPUS, max_results=4)
    reranked = NeuralRetriever(embedder=HashingEmbedder(dim=64), corpus=CORPUS, max_results=4,
                               reranker=_ReverseReranker(), rerank_pool=4)
    a = base.retrieve("gcd coprime", "")
    b = reranked.retrieve("gcd coprime", "")
    assert set(a) == set(b)          # same candidate set
    assert a != b                    # reranker changed the order


def test_results_capped():
    r = NeuralRetriever(embedder=HashingEmbedder(dim=64), corpus=CORPUS, max_results=2)
    assert len(r.retrieve("nat", "")) <= 2


def test_concurrent_neural_lazy_build_is_single_and_atomically_published():
    class _SlowHashing(HashingEmbedder):
        def __init__(self):
            super().__init__(dim=32)
            self.document_calls = 0
            self.calls_lock = threading.Lock()

        def encode(self, texts):
            if len(texts) > 1:
                with self.calls_lock:
                    self.document_calls += 1
                time.sleep(0.05)
            return super().encode(texts)

    embedder = _SlowHashing()
    retriever = NeuralRetriever(embedder=embedder, corpus=CORPUS, max_results=2)
    barrier = threading.Barrier(3)
    results = []

    def query():
        barrier.wait()
        results.append(retriever.retrieve("gcd coprime"))

    threads = [threading.Thread(target=query) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert embedder.document_calls == 1
    assert len(results) == 2 and all(result and " : " in result[0] for result in results)


class _CountingHashingEmbedder(HashingEmbedder):
    def __init__(self, dim=32):
        super().__init__(dim)
        self.calls = 0
        self.cache_tag = f"hashing-md5-v1:{dim}"

    def encode(self, texts):
        self.calls += 1
        return super().encode(texts)


def test_neural_cache_is_data_only_json_and_reused_without_reembedding(tmp_path):
    cache = tmp_path / "neural.cache"
    first_embedder = _CountingHashingEmbedder()
    first = NeuralRetriever(embedder=first_embedder, corpus=CORPUS, cache_path=str(cache))
    assert first._ensure_index()
    assert first_embedder.calls == 1
    blob = json.loads(cache.read_text(encoding="utf-8"))
    assert blob["format"] == "mathagent-neural-json"
    assert isinstance(blob["vectors"], list) and isinstance(blob["vectors"][0], list)
    assert "corpus" in blob and "names" not in blob and "docs" not in blob

    second_embedder = _CountingHashingEmbedder()
    second = NeuralRetriever(embedder=second_embedder, corpus=CORPUS, cache_path=str(cache))
    assert second._ensure_index()
    assert second_embedder.calls == 0
    assert not list(tmp_path.glob(".neural.cache.*.tmp"))


def test_neural_legacy_pickle_bytes_are_never_deserialized(tmp_path, monkeypatch):
    cache = tmp_path / "legacy.pkl"
    cache.write_bytes(pickle.dumps({"version": 1, "mat": [[1.0]]}))
    called = {"loads": False}

    def _must_not_unpickle(*_args, **_kwargs):
        called["loads"] = True
        raise AssertionError("pickle deserialization was reached")

    monkeypatch.setattr(pickle, "loads", _must_not_unpickle)
    retriever = NeuralRetriever(
        embedder=HashingEmbedder(dim=16), corpus=CORPUS, cache_path=str(cache))
    assert retriever._ensure_index()
    assert called["loads"] is False
    assert json.loads(cache.read_text(encoding="utf-8"))["format"] == "mathagent-neural-json"


def test_neural_cache_rejects_nonfinite_or_ragged_vectors_and_rebuilds(tmp_path):
    cache = tmp_path / "bad-neural.json"
    embedder = _CountingHashingEmbedder(dim=16)
    retriever = NeuralRetriever(embedder=embedder, corpus=CORPUS, cache_path=str(cache))
    cache.write_text(json.dumps({
        "format": "mathagent-neural-json", "version": nr._VERSION,
        "modules": ["Data/Nat", "Data/Int", "Data/ZMod"],
        "model": "_CountingHashingEmbedder:hashing-md5-v1:16",
        "source_fingerprint": retriever._cache_source_fingerprint(),
        "corpus": [["bad", "bad"]], "vectors": [[float("nan"), 1.0]],
    }), encoding="utf-8")
    assert retriever._ensure_index()
    assert embedder.calls == 1
    rewritten = json.loads(cache.read_text(encoding="utf-8"))
    assert len(rewritten["vectors"]) == len(CORPUS)


def test_neural_cache_wrong_dimension_is_rebuilt_not_truncated(tmp_path):
    cache = tmp_path / "wrong-dim.json"
    retriever = NeuralRetriever(
        embedder=HashingEmbedder(dim=16), corpus=CORPUS, cache_path=str(cache))
    cache.write_text(json.dumps({
        "format": "mathagent-neural-json", "version": nr._VERSION,
        "modules": ["Data/Nat", "Data/Int", "Data/ZMod"],
        "model": "HashingEmbedder:16",
        "source_fingerprint": retriever._cache_source_fingerprint(),
        "corpus": [[name, sig] for name, sig in CORPUS],
        "vectors": [[1.0, 0.0] for _ in CORPUS],
    }), encoding="utf-8")
    assert retriever.retrieve("gcd", "")
    rewritten = json.loads(cache.read_text(encoding="utf-8"))
    assert len(rewritten["vectors"][0]) == 16


def test_sentence_fallback_cache_is_bound_to_actual_vector_space(tmp_path):
    class _TwoSpaceEmbedder(SentenceTransformerEmbedder):
        DEFAULT_MODELS = ["space-a", "space-b"]

        def __init__(self, selected):
            super().__init__()
            self.selected = selected
            self.calls = 0
            self.cache_tag = f"two-space:{selected}"

        def available(self):
            return True

        def _load(self):
            self.loaded_name = self.selected
            self._model = self
            return self

        def encode(self, texts):
            self._load()
            self.calls += 1
            out = []
            for text in texts:
                apple = "apple" in text.lower()
                if self.selected == "space-a":
                    out.append([1.0, 0.0] if apple else [0.0, 1.0])
                else:
                    out.append([0.0, 1.0] if apple else [1.0, 0.0])
            return out

        def get_sentence_embedding_dimension(self):
            return 2

    corpus = [("Apple", "apple theorem"), ("Banana", "banana theorem")]
    cache = tmp_path / "fallback-space.json"
    first_embedder = _TwoSpaceEmbedder("space-a")
    first = NeuralRetriever(
        embedder=first_embedder, corpus=corpus, cache_path=str(cache), max_results=1)
    assert first.retrieve("apple")[0].startswith("Apple")

    second_embedder = _TwoSpaceEmbedder("space-b")
    second = NeuralRetriever(
        embedder=second_embedder, corpus=corpus, cache_path=str(cache), max_results=1)
    assert second.retrieve("apple")[0].startswith("Apple")
    # The changed actual model identity forced document re-embedding; a dimension-only/cache-chain
    # check would make just the query call and incorrectly rank Banana in the incompatible space.
    assert second_embedder.calls == 2
    assert json.loads(cache.read_text(encoding="utf-8"))["model"].endswith(":space-b")


def test_sentence_cache_wrong_dimension_is_rebuilt(tmp_path):
    class _ThreeDimensionalSentence(SentenceTransformerEmbedder):
        def __init__(self):
            super().__init__()
            self.calls = 0
            self.cache_tag = "three-space-config"

        def available(self):
            return True

        def _load(self):
            self.loaded_name = "three-space"
            self._model = self
            return self

        def get_sentence_embedding_dimension(self):
            return 3

        def encode(self, texts):
            self._load()
            self.calls += 1
            return [[1.0, 0.0, 0.0] for _ in texts]

    corpus = [("One", "theorem one"), ("Two", "theorem two")]
    cache = tmp_path / "sentence-wrong-dim.json"
    embedder = _ThreeDimensionalSentence()
    retriever = NeuralRetriever(
        embedder=embedder, corpus=corpus, cache_path=str(cache), max_results=1)
    cache.write_text(json.dumps({
        "format": "mathagent-neural-json", "version": nr._VERSION,
        "modules": ["Data/Nat", "Data/Int", "Data/ZMod"],
        "model": "_ThreeDimensionalSentence:three-space-config",
        "source_fingerprint": retriever._cache_source_fingerprint(),
        "corpus": [[name, sig] for name, sig in corpus],
        "vectors": [[1.0, 0.0] for _ in corpus],
    }), encoding="utf-8")

    assert retriever.retrieve("one")
    assert embedder.calls == 2  # document rebuild plus query encode
    rewritten = json.loads(cache.read_text(encoding="utf-8"))
    assert len(rewritten["vectors"][0]) == 3


def test_anonymous_custom_embedder_does_not_reuse_ambiguous_disk_cache(tmp_path):
    class _CustomSpace:
        def __init__(self, reverse=False):
            self.reverse = reverse
            self.model_name = "shared-but-insufficient-name"

        def available(self):
            return True

        def encode(self, texts):
            out = []
            for text in texts:
                one = "one" in text.lower()
                if self.reverse:
                    out.append([0.0, 1.0] if one else [1.0, 0.0])
                else:
                    out.append([1.0, 0.0] if one else [0.0, 1.0])
            return out

    corpus = [("One", "one theorem"), ("Two", "two theorem")]
    cache = tmp_path / "anonymous-custom.json"
    first = NeuralRetriever(
        embedder=_CustomSpace(), corpus=corpus, cache_path=str(cache), max_results=1)
    second = NeuralRetriever(
        embedder=_CustomSpace(reverse=True), corpus=corpus, cache_path=str(cache), max_results=1)

    assert first.retrieve("one")[0].startswith("One")
    assert second.retrieve("one")[0].startswith("One")
    assert not cache.exists()  # no explicit stable cache_tag => no ambiguous matrix persisted


def test_sentence_subclass_requires_explicit_cache_tag(tmp_path):
    class _CustomSentence(SentenceTransformerEmbedder):
        def available(self):
            return True

        def encode(self, texts):
            return [[1.0, 0.0] for _ in texts]

    cache = tmp_path / "sentence-subclass.json"
    retriever = NeuralRetriever(
        embedder=_CustomSentence(), corpus=[("One", "one theorem")], cache_path=str(cache))
    assert retriever.retrieve("one")
    assert not cache.exists()


def test_nonunit_document_and_query_vectors_are_cosine_normalized():
    class _ScaledSpace:
        dim = 2

        def available(self):
            return True

        def encode(self, texts):
            out = []
            for text in texts:
                if text.startswith("A :"):
                    out.append([100.0, 0.0])
                elif text.startswith("B :"):
                    out.append([1.0, 1.0])
                else:
                    out.append([10.0, 10.0])
            return out

    retriever = NeuralRetriever(
        embedder=_ScaledSpace(), corpus=[("A", "first"), ("B", "second")], max_results=1)
    assert retriever.retrieve("query")[0].startswith("B :")


def test_zero_query_vector_fails_closed_after_valid_index_build():
    class _ZeroQuerySpace:
        dim = 2

        def available(self):
            return True

        def encode(self, texts):
            if len(texts) == 1 and texts[0] == "query":
                return [[0.0, 0.0]]
            return [[1.0, 0.0] for _ in texts]

    retriever = NeuralRetriever(
        embedder=_ZeroQuerySpace(), corpus=[("A", "first"), ("B", "second")])
    assert retriever._ensure_index()
    assert retriever.retrieve("query") == []


def test_known_dimension_preflight_rejects_oversized_corpus_before_encode(monkeypatch):
    class _KnownSpace:
        dim = 4

        def __init__(self):
            self.calls = 0

        def available(self):
            return True

        def encode(self, texts):
            self.calls += 1
            return [[1.0, 0.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setattr(nr, "_MAX_VECTOR_VALUES", 8)
    embedder = _KnownSpace()
    retriever = NeuralRetriever(
        embedder=embedder, corpus=[("A", "a"), ("B", "b"), ("C", "c")])
    assert retriever._ensure_index() is False
    assert embedder.calls == 0


def test_unknown_dimension_uses_one_bounded_batch_before_global_limit(monkeypatch):
    class _UnknownSpace:
        def __init__(self):
            self.calls = 0

        def available(self):
            return True

        def encode(self, texts):
            self.calls += 1
            return [[1.0, 0.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setattr(nr, "_MAX_VECTOR_VALUES", 8)
    monkeypatch.setattr(nr, "_EMBED_BATCH_SIZE", 1)
    embedder = _UnknownSpace()
    retriever = NeuralRetriever(
        embedder=embedder, corpus=[("A", "a"), ("B", "b"), ("C", "c")])
    assert retriever._ensure_index() is False
    assert embedder.calls == 1


def test_corpus_embedding_is_batched(monkeypatch):
    class _UnknownSpace:
        def __init__(self):
            self.calls = 0

        def available(self):
            return True

        def encode(self, texts):
            self.calls += 1
            return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(nr, "_EMBED_BATCH_SIZE", 2)
    embedder = _UnknownSpace()
    corpus = [(str(i), f"theorem {i}") for i in range(5)]
    assert NeuralRetriever(embedder=embedder, corpus=corpus)._ensure_index()
    assert embedder.calls == 3


def test_neural_cache_enforces_total_text_budget(monkeypatch):
    monkeypatch.setattr(nr, "_MAX_CACHE_TEXT_CHARS", 3)
    blob = {
        "format": nr._CACHE_FORMAT,
        "version": nr._VERSION,
        "modules": ["Data/Nat"],
        "model": "space@commit",
        "source_fingerprint": "receipt",
        "corpus": [["Long", "signature"]],
        "vectors": [[1.0]],
    }
    assert nr._validated_neural_cache(
        blob, ["Data/Nat"], "space@commit", "receipt") is None


def test_sentence_transformer_cache_tag_requires_immutable_commit(monkeypatch):
    commit = "a" * 40
    embedder = SentenceTransformerEmbedder()

    def pinned_load():
        embedder.loaded_name = "org/model"
        embedder.loaded_revision = commit
        embedder._model = object()
        return embedder._model

    monkeypatch.setattr(embedder, "_load", pinned_load)
    retriever = NeuralRetriever(embedder=embedder, corpus=[("A", "a")])
    assert retriever._model_tag() == f"SentenceTransformerEmbedder:org/model@{commit}"

    embedder.loaded_revision = None

    def unpinned_load():
        embedder.loaded_name = "org/model"
        embedder.loaded_revision = None
        return object()

    monkeypatch.setattr(embedder, "_load", unpinned_load)
    assert retriever._model_tag() is None


# ---- live (opt-in): the real bge model closes the abbreviation gap that BM25 cannot ----

_LIVE = os.environ.get("MATHAGENT_NEURAL_TESTS") == "1" and SentenceTransformerEmbedder().available()


@pytest.mark.skipif(not _LIVE, reason="set MATHAGENT_NEURAL_TESTS=1 with sentence-transformers installed")
def test_live_bge_matches_phrase_to_identifier():
    # The semantic claim: "greatest common divisor" (no token overlap with `gcd`) still surfaces gcd.
    r = NeuralRetriever(embedder=SentenceTransformerEmbedder(), corpus=CORPUS, max_results=2)
    hits = r.retrieve("the greatest common divisor of two integers", "")
    assert any("gcd" in h.lower() for h in hits)
