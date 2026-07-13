"""Tests for the local BM25 Mathlib retriever (offline synthetic corpus; live build opt-in)."""
import json
import os
import pickle
import threading
import time

import pytest

from agent.tools.semantic_retrieval import (
    tokenize, extract_declarations, BM25, SemanticRetriever, HybridRetriever, _read_json_cache,
    _source_fingerprint, _atomic_write_json,
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


def test_extract_ignores_nested_comments_and_private_declarations():
    src = """
/- theorem documented_only : False := by sorry
   /- namespace Fake -/
-/
namespace Real
-- lemma commented_out : False := by sorry
private theorem implementation_detail : True := trivial
@[simp] protected theorem visible : True := trivial
def comment_marker : String := "-- this is a string, not a comment"
end Real
"""
    names = [name for name, _ in extract_declarations(src)]
    assert names == ["Real.visible", "Real.comment_marker"]


def test_extract_dotted_namespace_partial_end_preserves_outer_scope():
    src = """
namespace Outer
namespace Inner.Deep
theorem nested : True := trivial
end Deep
lemma still_outer : True := trivial
end Outer
lemma top : True := trivial
"""
    names = [name for name, _ in extract_declarations(src)]
    assert names == ["Outer.Inner.Deep.nested", "Outer.still_outer", "top"]


def test_extract_collects_balanced_multiline_signature_without_body():
    src = """
namespace Nat
theorem multiline
    (n : Nat)
    (h : (n + 1 = (n + 1))) :
    n = n := by
  rfl
end Nat
"""
    assert extract_declarations(src) == [(
        "Nat.multiline",
        "theorem multiline (n : Nat) (h : (n + 1 = (n + 1))) : n = n",
    )]


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


def _synthetic_project(tmp_path):
    project = tmp_path / "project"
    source = project / ".lake" / "packages" / "mathlib" / "Mathlib" / "Data" / "Nat" / "Test.lean"
    source.parent.mkdir(parents=True)
    source.write_text(
        "namespace Nat\n"
        "theorem add_zero_test (n : Nat) : n + 0 = n := by simp\n"
        "theorem gcd_test (m n : Nat) : gcd m n = gcd m n := rfl\n"
        "end Nat\n",
        encoding="utf-8",
    )
    return project


def test_semantic_cache_is_schema_validated_json_and_reused(tmp_path, monkeypatch):
    project, cache = _synthetic_project(tmp_path), tmp_path / "semantic.cache"
    first = SemanticRetriever(project_dir=str(project), modules=["Data/Nat"], cache_path=str(cache))
    assert first.retrieve("gcd natural", "")
    blob = json.loads(cache.read_text(encoding="utf-8"))
    assert blob["format"] == "mathagent-semantic-json"
    assert isinstance(blob["corpus"], list)
    assert all(len(row) == 2 for row in blob["corpus"])  # derived tokens are not cache payload

    def _must_not_rebuild(*_args, **_kwargs):
        raise AssertionError("valid cache was not reused")

    monkeypatch.setattr("agent.tools.semantic_retrieval.build_corpus", _must_not_rebuild)
    second = SemanticRetriever(project_dir=str(project), modules=["Data/Nat"], cache_path=str(cache))
    assert second.retrieve("gcd natural", "")
    assert not list(tmp_path.glob(".semantic.cache.*.tmp"))


def test_source_fingerprint_detects_same_size_same_mtime_replacement(tmp_path):
    root = tmp_path / "Mathlib"
    source = root / "Data" / "Nat" / "Test.lean"
    source.parent.mkdir(parents=True)
    source.write_text("theorem alpha : True := trivial\n", encoding="utf-8")
    before_stat = source.stat()
    before = _source_fingerprint(root, ["Data/Nat"])

    source.write_text("theorem omega : True := trivial\n", encoding="utf-8")
    os.utime(source, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns))

    assert source.stat().st_size == before_stat.st_size
    assert source.stat().st_mtime_ns == before_stat.st_mtime_ns
    assert _source_fingerprint(root, ["Data/Nat"]) != before


def test_semantic_cache_enforces_global_derived_token_budget(monkeypatch):
    import agent.tools.semantic_retrieval as sr
    monkeypatch.setattr(sr, "_MAX_CORPUS_TOKENS", 2)
    blob = {
        "format": sr._CACHE_FORMAT,
        "version": sr._VERSION,
        "modules": ["Data/Nat"],
        "source_fingerprint": "receipt",
        "corpus": [["Nat.many_tokens", "theorem many_tokens : True"]],
    }
    assert sr._validated_cached_corpus(blob, ["Data/Nat"], "receipt") is None


def test_concurrent_lazy_build_is_single_and_fully_published(tmp_path, monkeypatch):
    project = _synthetic_project(tmp_path)
    retriever = SemanticRetriever(
        project_dir=str(project), modules=["Data/Nat"], cache_path=str(tmp_path / "idx.json"))
    import agent.tools.semantic_retrieval as sr
    real_build = sr.build_corpus
    calls = 0
    calls_lock = threading.Lock()

    def slow_build(*args, **kwargs):
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.05)
        return real_build(*args, **kwargs)

    monkeypatch.setattr(sr, "build_corpus", slow_build)
    barrier = threading.Barrier(3)
    results = []

    def query():
        barrier.wait()
        results.append(retriever.retrieve("gcd natural", ""))

    threads = [threading.Thread(target=query) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert calls == 1
    assert len(results) == 2 and all(result and " : " in result[0] for result in results)


def test_legacy_pickle_bytes_are_never_deserialized(tmp_path, monkeypatch):
    project, cache = _synthetic_project(tmp_path), tmp_path / "legacy.pkl"
    cache.write_bytes(pickle.dumps({"version": 2, "bm25": "legacy"}))
    called = {"loads": False}

    def _must_not_unpickle(*_args, **_kwargs):
        called["loads"] = True
        raise AssertionError("pickle deserialization was reached")

    monkeypatch.setattr(pickle, "loads", _must_not_unpickle)
    retriever = SemanticRetriever(
        project_dir=str(project), modules=["Data/Nat"], cache_path=str(cache))
    assert retriever.retrieve("add zero", "")
    assert called["loads"] is False
    assert json.loads(cache.read_text(encoding="utf-8"))["format"] == "mathagent-semantic-json"


def test_cache_size_limit_is_checked_on_open_handle_and_bounded_read(tmp_path, monkeypatch):
    cache = tmp_path / "bounded.json"
    cache.write_text('{"ok":true}', encoding="utf-8")
    # A path-level pre-stat is both unnecessary and racy; the opened descriptor is authoritative.
    monkeypatch.setattr(type(cache), "stat", lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("path stat should not be used")))
    assert _read_json_cache(cache, 64) == {"ok": True}
    with pytest.raises(ValueError, match="too large"):
        _read_json_cache(cache, 4)


def test_atomic_cache_writer_aborts_before_publishing_oversized_json(tmp_path):
    cache = tmp_path / "too-large.json"
    with pytest.raises(ValueError, match="size limit"):
        _atomic_write_json(cache, {"payload": "x" * 100}, max_bytes=32)
    assert not cache.exists()
    assert not list(tmp_path.glob(".too-large.json.*.tmp"))


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO cache regression is POSIX-specific")
def test_json_cache_rejects_fifo_without_blocking(tmp_path):
    fifo = tmp_path / "cache.fifo"
    os.mkfifo(fifo)
    started = time.monotonic()
    with pytest.raises(ValueError, match="regular file"):
        _read_json_cache(fifo, 1024)
    assert time.monotonic() - started < 1.0


_LIVE = os.environ.get("MATHAGENT_LEAN_TESTS") == "1" and SemanticRetriever().available()


@pytest.mark.skipif(not _LIVE, reason="set MATHAGENT_LEAN_TESTS=1 with the Mathlib project for live build")
def test_live_semantic_build_and_query():
    # BM25 is lexical: query with token overlap ("gcd", not the phrase "greatest common divisor",
    # which would lexically match `findGreatest*`). Neural embeddings would close that abbreviation gap.
    r = SemanticRetriever(max_results=8)
    hits = r.retrieve("gcd of two natural numbers and coprime", "")
    assert hits and any("gcd" in h.lower() or "coprime" in h.lower() for h in hits)
