"""Tests for Mathlib retrieval (query building, aggregation, graceful degradation) — no network."""
import json

import pytest

import agent.tools.retrieval as retrieval
from agent.tools.retrieval import LoogleRetriever, Hit, ScriptedRetriever, NullRetriever


class _Response:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.read_size = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int) -> bytes:
        self.read_size = size
        return self.payload[:size]


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


def test_query_inputs_are_truncated_before_scanning():
    r = LoogleRetriever(max_queries=2)
    error = ("unknown identifier 'A.aaa'\n" * 10_000)
    assert r._queries("x" * retrieval._MAX_RETRIEVAL_CLAIM_CHARS + " prime", error) == ['"aaa"']


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


def test_loogle_response_read_is_bounded(monkeypatch):
    response = _Response(b"x" * (retrieval._MAX_LOOGLE_RESPONSE_BYTES + 100))
    monkeypatch.setattr(retrieval.urllib.request, "urlopen", lambda *_a, **_kw: response)

    assert LoogleRetriever()._loogle('"dvd"') == []
    assert response.read_size == retrieval._MAX_LOOGLE_RESPONSE_BYTES + 1


@pytest.mark.parametrize("payload", [
    b"[]",
    b'{"hits": {"name": "not-a-list"}}',
    b'{"hits": [null, 1, {"name": 2}, {"name": "x", "type": 3}]}',
    b"\xff",
])
def test_loogle_malformed_response_fails_closed(monkeypatch, payload):
    monkeypatch.setattr(retrieval.urllib.request, "urlopen",
                        lambda *_a, **_kw: _Response(payload))
    assert LoogleRetriever()._loogle('"dvd"') == []


def test_loogle_skips_bad_hits_and_caps_valid_hits(monkeypatch):
    payload = json.dumps({"hits": [
        {"name": 7, "type": "bad"},
        {"name": "bad\ninstruction", "type": "malformed"},
        {"name": "Nat.dvd_add", "type": "a ∣ b\n→\ta ∣ c", "module": "Mathlib"},
        {"name": "Nat.dvd_sub", "type": "second"},
    ]}).encode()
    monkeypatch.setattr(retrieval.urllib.request, "urlopen",
                        lambda *_a, **_kw: _Response(payload))

    hits = LoogleRetriever(max_results=1)._loogle('"dvd"')
    assert [(hit.name, hit.module) for hit in hits] == [("Nat.dvd_add", "Mathlib")]
    assert hits[0].render() == "Nat.dvd_add : a ∣ b → a ∣ c"


@pytest.mark.parametrize("kwargs", [
    {"timeout_s": 0}, {"timeout_s": float("nan")}, {"timeout_s": float("inf")},
    {"max_results": 0}, {"max_results": True}, {"max_queries": 0},
])
def test_loogle_config_is_bounded(kwargs):
    with pytest.raises(ValueError):
        LoogleRetriever(**kwargs)


def test_scripted_retriever_is_bounded_test_only():
    with pytest.raises(ValueError):
        ScriptedRetriever(["x"] * (retrieval._MAX_SCRIPTED_LEMMAS + 1))
    with pytest.raises(ValueError):
        ScriptedRetriever(["x" * (retrieval._MAX_SCRIPTED_LEMMA_CHARS + 1)])


def test_scripted_and_null_retrievers():
    sr = ScriptedRetriever(["L1", "L2"])
    assert sr.retrieve("c", "e") == ["L1", "L2"]
    assert sr.calls == [("c", "e")]
    assert NullRetriever().retrieve("c", "e") == []
