"""Tests for Mathlib retrieval (query building, aggregation, graceful degradation) — no network."""
from agent.tools.retrieval import LoogleRetriever, Hit, ScriptedRetriever, NullRetriever


def test_unknown_identifier_drives_queries():
    r = LoogleRetriever()
    qs = r._queries(claim="", error="error: unknown identifier 'Nat.foo_bar'")
    assert '"foo_bar"' in qs


def test_claim_keywords_drive_queries():
    r = LoogleRetriever()
    qs = r._queries(claim="n is congruent to 0 modulo 4 and 3 divides n", error="")
    assert '"ModEq"' in qs and '"dvd"' in qs


def test_queries_capped():
    r = LoogleRetriever(max_queries=2)
    err = "unknown identifier 'A.aaa'\nunknown identifier 'B.bbb'\nunknown identifier 'C.ccc'"
    assert len(r._queries("", err)) == 2


def test_hit_render_truncates():
    h = Hit(name="Nat.add_zero", type="x" * 500)
    out = h.render(type_chars=20)
    assert out.startswith("Nat.add_zero : ") and out.endswith("…")


def test_retrieve_aggregates_and_dedupes(monkeypatch):
    r = LoogleRetriever(max_results=3)
    monkeypatch.setattr(r, "_loogle", lambda q: [
        Hit("Nat.add_zero", "n + 0 = n"), Hit("Nat.zero_add", "0 + n = n"),
        Hit("Nat.add_zero", "dup"),
    ])
    out = r.retrieve("about addition with zero", "unknown identifier 'addzero'")
    names = [x.split(" :")[0] for x in out]
    assert names == ["Nat.add_zero", "Nat.zero_add"]  # deduped, capped at 3


def test_retrieve_degrades_on_network_failure(monkeypatch):
    r = LoogleRetriever()
    monkeypatch.setattr(r, "_loogle", lambda q: [])  # simulate no network / no hits
    assert r.retrieve("claim", "unknown identifier 'Foo.bar'") == []


def test_scripted_and_null_retrievers():
    sr = ScriptedRetriever(["L1", "L2"])
    assert sr.retrieve("c", "e") == ["L1", "L2"]
    assert sr.calls == [("c", "e")]
    assert NullRetriever().retrieve("c", "e") == []
