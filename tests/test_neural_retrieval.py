"""Tests for the neural (dense bi-encoder) Mathlib retriever.

Offline tests inject the dependency-free HashingEmbedder over a synthetic corpus (no torch / model /
network), exercising the build→cache→cosine→rerank plumbing. The live test (opt-in) uses the real
bge model over Mathlib and asserts the *semantic* win lexical IR can't get: matching the phrase
"greatest common divisor" to the identifier `gcd`.
"""
import os

import pytest

from agent.tools.neural_retrieval import (
    NeuralRetriever, HashingEmbedder, SentenceTransformerEmbedder,
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


# ---- live (opt-in): the real bge model closes the abbreviation gap that BM25 cannot ----

_LIVE = os.environ.get("MATHAGENT_NEURAL_TESTS") == "1" and SentenceTransformerEmbedder().available()


@pytest.mark.skipif(not _LIVE, reason="set MATHAGENT_NEURAL_TESTS=1 with sentence-transformers installed")
def test_live_bge_matches_phrase_to_identifier():
    # The semantic claim: "greatest common divisor" (no token overlap with `gcd`) still surfaces gcd.
    r = NeuralRetriever(embedder=SentenceTransformerEmbedder(), corpus=CORPUS, max_results=2)
    hits = r.retrieve("the greatest common divisor of two integers", "")
    assert any("gcd" in h.lower() for h in hits)
