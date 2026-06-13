"""Tests for the local BM25 Mathlib retriever (offline synthetic corpus; live build opt-in)."""
import os

import pytest

from agent.tools.semantic_retrieval import (
    tokenize, extract_declarations, BM25, SemanticRetriever, HybridRetriever,
)
from agent.tools.retrieval import ScriptedRetriever


# ---- tokenizer ----

def test_tokenize_splits_camel_and_snake():
    assert tokenize("ModEq") == ["mod", "eq"]
    assert tokenize("Nat.add_zero") == ["nat", "add", "zero"]


def test_tokenize_drops_stopwords_and_tiny():
    toks = tokenize("the sum of a and b")
    assert "the" not in toks and "of" not in toks and "and" not in toks
    assert "sum" in toks


# ---- declaration extraction with namespace scope ----

def test_extract_qualifies_names_by_namespace():
    src = "\n".join([
        "namespace Nat",
        "/-- doc -/",
        "theorem add_comm (a b : Nat) : a + b = b + a := by sorry",
        "section Foo",
        "lemma helper : True := trivial",
        "end Foo",
        "def myDef := 5",
        "end Nat",
        "theorem toplevel : 1 = 1 := rfl",
    ])
    names = [n for n, _ in extract_declarations(src)]
    assert "Nat.add_comm" in names      # namespace-qualified
    assert "Nat.helper" in names        # section does not add to the name
    assert "Nat.myDef" in names
    assert "toplevel" in names          # after `end Nat` the namespace is popped


# ---- BM25 ----

def test_bm25_ranks_relevant_doc_first():
    docs = [
        ("Nat.add_zero", tokenize("Nat.add_zero n + 0 = n")),
        ("Nat.mul_comm", tokenize("Nat.mul_comm a * b = b * a")),
        ("Int.even_iff", tokenize("Int.even_iff Even n iff n % 2 = 0")),
    ]
    bm = BM25(docs)
    res = bm.search(tokenize("even number modulo two"), k=2)
    assert docs[res[0][0]][0] == "Int.even_iff"


def test_bm25_empty_query():
    bm = BM25([("a", ["x"])])
    assert bm.search(tokenize("zzz nomatch"), k=3) == []


# ---- hybrid merge ----

def test_hybrid_interleaves_and_dedupes():
    h = HybridRetriever([ScriptedRetriever(["A : x", "B : y"]),
                         ScriptedRetriever(["B : y2", "C : z"])], max_results=5)
    names = [x.split(" :")[0].strip() for x in h.retrieve("claim", "err")]
    assert names == ["A", "B", "C"]


def test_hybrid_caps_results():
    h = HybridRetriever([ScriptedRetriever([f"L{i} : t" for i in range(10)])], max_results=3)
    assert len(h.retrieve("c", "e")) == 3


# ---- availability + live build (opt-in) ----

def test_available_is_bool():
    assert isinstance(SemanticRetriever().available(), bool)


_LIVE = os.environ.get("MATHAGENT_LEAN_TESTS") == "1" and SemanticRetriever().available()


@pytest.mark.skipif(not _LIVE, reason="set MATHAGENT_LEAN_TESTS=1 with the Mathlib project for live build")
def test_live_semantic_build_and_query():
    # BM25 is lexical: query with token overlap ("gcd", not the phrase "greatest common divisor",
    # which would lexically match `findGreatest*`). Neural embeddings would close that abbreviation gap.
    r = SemanticRetriever(max_results=8)
    hits = r.retrieve("gcd of two natural numbers and coprime", "")
    assert hits and any("gcd" in h.lower() or "coprime" in h.lower() for h in hits)
