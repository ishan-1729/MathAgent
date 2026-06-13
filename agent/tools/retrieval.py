"""Mathlib lemma retrieval (Loogle) for the autoformalization repair loop.

A dominant autoformalization failure is the model citing a HALLUCINATED lemma ("unknown identifier
'Nat.foo_bar'"). Retrieval surfaces REAL nearby Mathlib declarations so the repair step uses lemmas
that exist. Backend: Loogle's documented HTTP JSON API (https://loogle.lean-lang.org/json?q=...),
which supports substring-on-name queries (`"foo"`) and name/type patterns. Network failures degrade
gracefully to no results (the formalizer just proceeds without hints).
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

LOOGLE_URL = "https://loogle.lean-lang.org/json"

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
        t = self.type.replace("\n", " ").strip()
        if len(t) > type_chars:
            t = t[:type_chars] + "…"
        return f"{self.name} : {t}" if t else self.name


@runtime_checkable
class Retriever(Protocol):
    def retrieve(self, claim: str, error: str = "") -> list[str]:
        ...


class LoogleRetriever:
    def __init__(self, timeout_s: float = 8.0, max_results: int = 12, max_queries: int = 6):
        self.timeout_s = timeout_s
        self.max_results = max_results
        self.max_queries = max_queries

    def _loogle(self, query: str) -> list[Hit]:
        url = f"{LOOGLE_URL}?q={urllib.parse.quote(query)}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "mathagent-retriever"})
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception:
            return []  # graceful degradation: no network / bad response -> no hints
        out = []
        for h in data.get("hits", []) or []:
            if isinstance(h, dict) and h.get("name"):
                out.append(Hit(name=h["name"], type=h.get("type", "") or "",
                               module=h.get("module", "") or ""))
        return out

    def _queries(self, claim: str, error: str) -> list[str]:
        queries: list[str] = []
        # 1. Real names near the model's hallucinated/unknown identifiers (best signal).
        for ident in _UNKNOWN_RE.findall(error or ""):
            comp = ident.split(".")[-1]
            if len(comp) >= 3:
                queries.append(f'"{comp}"')
        # 2. Concept keywords from the informal claim.
        low = (claim or "").lower()
        for kw, term in _KEYWORD_TERMS.items():
            if kw in low and f'"{term}"' not in queries:
                queries.append(f'"{term}"')
        # dedupe, cap
        seen, uniq = set(), []
        for q in queries:
            if q not in seen:
                seen.add(q)
                uniq.append(q)
        return uniq[: self.max_queries]

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
    """Returns a fixed lemma list (tests/offline)."""

    def __init__(self, lemmas: list[str]):
        self.lemmas = lemmas
        self.calls: list[tuple[str, str]] = []

    def retrieve(self, claim: str, error: str = "") -> list[str]:
        self.calls.append((claim, error))
        return list(self.lemmas)


class NullRetriever:
    def retrieve(self, claim: str, error: str = "") -> list[str]:
        return []
