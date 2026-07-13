"""Mathlib lemma retrieval (Loogle) for the autoformalization repair loop.

A dominant autoformalization failure is the model citing a HALLUCINATED lemma ("unknown identifier
'Nat.foo_bar'"). Retrieval surfaces REAL nearby Mathlib declarations so the repair step uses lemmas
that exist. Backend: Loogle's documented HTTP JSON API (https://loogle.lean-lang.org/json?q=...),
which supports substring-on-name queries (`"foo"`) and name/type patterns. Network failures degrade
gracefully to no results (the formalizer just proceeds without hints).
"""
from __future__ import annotations

import json
import math
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

LOOGLE_URL = "https://loogle.lean-lang.org/json"
_MAX_LOOGLE_RESPONSE_BYTES = 1 * 1024 * 1024
_MAX_LOOGLE_QUERY_CHARS = 256
_MAX_HIT_NAME_CHARS = 512
_MAX_HIT_TYPE_CHARS = 4096
_MAX_HIT_MODULE_CHARS = 512
_MAX_RETRIEVAL_CLAIM_CHARS = 16 * 1024
_MAX_RETRIEVAL_ERROR_CHARS = 64 * 1024
_MAX_QUERY_TERM_CHARS = 128
_MAX_TIMEOUT_S = 60.0
_MAX_RESULTS = 100
_MAX_QUERIES = 32
_MAX_SCRIPTED_LEMMAS = 100
_MAX_SCRIPTED_LEMMA_CHARS = 4096

# Unknown-identifier / unknown-constant errors from Lean (the highest-value retrieval signal).
_UNKNOWN_RE = re.compile(r"unknown (?:identifier|constant|declaration)\s+'([^']+)'")
# Map common informal NT words to Loogle name-substring queries.
_KEYWORD_TERMS = {
    "congru": "ModEq", "modulo": "ModEq", "mod ": "ModEq",
    "divis": "dvd", "divides": "dvd", "divide": "dvd",
    "prime": "Prime", "gcd": "gcd", "coprime": "Coprime",
    "square": "sq", "parity": "even", "even": "even", "odd": "odd",
    "factor": "factor", "descent": "WellFounded",
}


@dataclass
class Hit:
    name: str
    type: str = ""
    module: str = ""

    def render(self, type_chars: int = 160) -> str:
        # Retrieved signatures are prompt data.  Collapse every Unicode whitespace separator so a
        # remote response cannot smuggle an extra instruction line through a declaration type.
        t = " ".join(self.type.split())
        if len(t) > type_chars:
            t = t[:type_chars] + "…"
        return f"{self.name} : {t}" if t else self.name


@runtime_checkable
class Retriever(Protocol):
    def retrieve(self, claim: str, error: str = "") -> list[str]:
        ...


class LoogleRetriever:
    def __init__(self, timeout_s: float = 8.0, max_results: int = 12, max_queries: int = 6):
        if (isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float))
                or not math.isfinite(timeout_s) or not 0 < timeout_s <= _MAX_TIMEOUT_S):
            raise ValueError(f"timeout_s must be finite and in (0, {_MAX_TIMEOUT_S}]")
        if (isinstance(max_results, bool) or not isinstance(max_results, int)
                or not 1 <= max_results <= _MAX_RESULTS):
            raise ValueError(f"max_results must be an integer in [1, {_MAX_RESULTS}]")
        if (isinstance(max_queries, bool) or not isinstance(max_queries, int)
                or not 1 <= max_queries <= _MAX_QUERIES):
            raise ValueError(f"max_queries must be an integer in [1, {_MAX_QUERIES}]")
        self.timeout_s = float(timeout_s)
        self.max_results = max_results
        self.max_queries = max_queries

    def _loogle(self, query: str) -> list[Hit]:
        if not isinstance(query, str) or not query or len(query) > _MAX_LOOGLE_QUERY_CHARS:
            return []
        url = f"{LOOGLE_URL}?q={urllib.parse.quote(query)}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "mathagent-retriever"})
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                payload = resp.read(_MAX_LOOGLE_RESPONSE_BYTES + 1)
            if not isinstance(payload, bytes) or len(payload) > _MAX_LOOGLE_RESPONSE_BYTES:
                return []
            data = json.loads(payload.decode("utf-8"))
        except Exception:
            return []  # graceful degradation: no network / bad response -> no hints
        if not isinstance(data, dict):
            return []
        raw_hits = data.get("hits")
        if not isinstance(raw_hits, list):
            return []

        out: list[Hit] = []
        for h in raw_hits:
            if not isinstance(h, dict):
                continue
            name, hit_type, module = h.get("name"), h.get("type", ""), h.get("module", "")
            if (not isinstance(name, str) or not name.strip()
                    or len(name) > _MAX_HIT_NAME_CHARS or not name.isprintable()
                    or any(ch.isspace() for ch in name)):
                continue
            if not isinstance(hit_type, str) or not isinstance(module, str):
                continue
            out.append(Hit(name=name, type=hit_type[:_MAX_HIT_TYPE_CHARS],
                           module=module[:_MAX_HIT_MODULE_CHARS]))
            if len(out) >= self.max_results:
                break
        return out

    def _queries(self, claim: str, error: str) -> list[str]:
        queries: list[str] = []
        seen: set[str] = set()

        def add(query: str) -> None:
            if len(queries) < self.max_queries and query not in seen:
                seen.add(query)
                queries.append(query)

        # 1. Real names near the model's hallucinated/unknown identifiers (best signal).
        raw_error = error if isinstance(error, str) else ""
        for match in _UNKNOWN_RE.finditer(raw_error[:_MAX_RETRIEVAL_ERROR_CHARS]):
            ident = match.group(1)
            comp = ident.split(".")[-1]
            if 3 <= len(comp) <= _MAX_QUERY_TERM_CHARS:
                add(f'"{comp}"')
            if len(queries) >= self.max_queries:
                break
        # 2. Concept keywords from the informal claim.
        raw_claim = claim if isinstance(claim, str) else ""
        low = raw_claim[:_MAX_RETRIEVAL_CLAIM_CHARS].lower()
        for kw, term in _KEYWORD_TERMS.items():
            if kw in low:
                add(f'"{term}"')
            if len(queries) >= self.max_queries:
                break
        return queries

    def retrieve(self, claim: str, error: str = "") -> list[str]:
        hits: list[str] = []
        seen: set[str] = set()
        for q in self._queries(claim, error):
            for h in self._loogle(q):
                if h.name not in seen:
                    seen.add(h.name)
                    hits.append(h.render())
                    if len(hits) >= self.max_results:
                        return hits
        return hits


class ScriptedRetriever:
    """Bounded in-memory test double; production configuration never instantiates this retriever."""

    def __init__(self, lemmas: list[str]):
        if (not isinstance(lemmas, list) or len(lemmas) > _MAX_SCRIPTED_LEMMAS
                or any(not isinstance(item, str) or len(item) > _MAX_SCRIPTED_LEMMA_CHARS
                       for item in lemmas)):
            raise ValueError("scripted lemmas exceed the offline test-double bounds")
        self.lemmas = list(lemmas)
        self.calls: list[tuple[str, str]] = []

    def retrieve(self, claim: str, error: str = "") -> list[str]:
        self.calls.append((claim, error))
        return list(self.lemmas)


class NullRetriever:
    def retrieve(self, claim: str, error: str = "") -> list[str]:
        return []
