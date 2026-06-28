"""Shared CLI-output JSON parsers for the model-backed roles (Codex AND Claude).

Both the Codex CLI and the headless Claude CLI return a free-form text message whose payload is a
trailing JSON value (a ledger, a verdict object, or a flaw array) — often preceded by prose that
itself contains stray braces/brackets (set notation ``{1,2,3}``, Lean terms ``[a, b]``,
``[citations]``). A greedy ``\\{.*\\}`` / ``\\[.*\\]`` (DOTALL) would span from the first delimiter to
the last and make ``json.loads`` fail, silently taking the fail-closed branch and losing the verdict.

So the strategy here is: prefer a fenced ```json block (as ``agent/gates/ledger.py:_extract_json``
does), then fall back to scanning from the END for the last *balanced* object/array, skipping
delimiters inside string literals.

These parsers were lifted VERBATIM out of ``agent/tools/codex_prover.py`` so the two CLI backends
share ONE implementation (``codex_prover`` re-exports them for backwards compatibility — no behavior
change). They never ``exec``/``eval``/``import`` anything; pure ``json.loads`` on extracted spans.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from agent.gates.ledger import parse_ledger, LedgerError
from agent.orchestrator.dag import goal_hash

# Codex/Claude prose routinely contains stray braces/brackets BEFORE the final JSON (sets {1,2,3},
# Lean terms, [citations]). A greedy `\{.*\}` / `\[.*\]` (DOTALL) would span from the first delimiter
# to the last and make json.loads fail, silently taking the fail-closed branch and losing the verdict.
# Instead: prefer a fenced ```json block (as agent/gates/ledger.py:_extract_json does), then fall
# back to scanning from the END for the last balanced object/array.
_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*([\[{].*?[\]}])\s*```", re.DOTALL)

# Matching close -> open delimiter, for the balanced backward scan.
_CLOSERS = {"}": "{", "]": "["}


def _last_balanced(text: str, open_ch: str, close_ch: str) -> Optional[str]:
    """The LAST balanced `open_ch..close_ch` span in `text`, ignoring delimiters inside strings.

    Scans from the end: anchor on the final `close_ch`, then walk backwards tracking depth until the
    matching `open_ch`. Returns None if there is no balanced span. String literals (single/double
    quoted) are skipped so braces/brackets inside JSON string values don't perturb the depth count.
    """
    end = text.rfind(close_ch)
    while end != -1:
        depth = 0
        i = end
        in_str = False
        quote = ""
        while i >= 0:
            ch = text[i]
            if in_str:
                # We're walking backwards inside a string literal; the opening quote ends it. A
                # backslash immediately before the quote would escape it, so count preceding
                # backslashes and treat an odd run as an escaped (non-terminating) quote.
                if ch == quote:
                    bs = 0
                    j = i - 1
                    while j >= 0 and text[j] == "\\":
                        bs += 1
                        j -= 1
                    if bs % 2 == 0:
                        in_str = False
            elif ch in ("'", '"'):
                in_str = True
                quote = ch
            elif ch == close_ch:
                depth += 1
            elif ch == open_ch:
                depth -= 1
                if depth == 0:
                    return text[i:end + 1]
            i -= 1
        # No matching opener for this closer; try an earlier close delimiter.
        end = text.rfind(close_ch, 0, end)
    return None


def _extract_json(raw: str):
    """Parse the trailing JSON value out of CLI output. Prefer a fenced ```json block, else the
    last balanced object/array. Returns the decoded value, or None if nothing valid is found."""
    text = (raw or "").strip()
    # 1. Fenced block (most reliable; mirrors ledger._extract_json).
    for m in _FENCED_JSON_RE.finditer(text):
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
    # 2. Last balanced object/array. Take whichever appears later in the text so a trailing JSON
    #    array isn't shadowed by an earlier object (and vice versa).
    obj = _last_balanced(text, "{", "}")
    arr = _last_balanced(text, "[", "]")
    candidates = [c for c in (obj, arr) if c is not None]
    candidates.sort(key=lambda c: text.rfind(c))      # later occurrence wins
    for cand in reversed(candidates):
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            continue
    return None


def _extract_json_object(raw: str) -> Optional[dict]:
    """The trailing JSON OBJECT from CLI output (fenced block preferred), or None."""
    text = (raw or "").strip()
    for m in _FENCED_JSON_RE.finditer(text):
        try:
            val = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(val, dict):
            return val
    span = _last_balanced(text, "{", "}")
    if span is not None:
        try:
            val = json.loads(span)
        except json.JSONDecodeError:
            return None
        if isinstance(val, dict):
            return val
    return None


def _json_array(raw: str) -> list[str]:
    """The trailing JSON ARRAY from CLI output (fenced block preferred), as a list of strings.

    Returns [] on genuine parse failure or when the trailing JSON value is not an array — keeping the
    critic's fail-closed semantics while no longer being fooled by stray brackets in prose (e.g.
    `[citations]`, Lean list syntax) that precede the real array."""
    text = (raw or "").strip()
    for m in _FENCED_JSON_RE.finditer(text):
        try:
            val = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(val, list):
            return [str(x) for x in val]
    span = _last_balanced(text, "[", "]")
    if span is None:
        return []
    try:
        arr = json.loads(span)
    except json.JSONDecodeError:
        return []
    return [str(x) for x in arr] if isinstance(arr, list) else []


def children_from_sketch(sketch: str) -> list[str]:
    """The deduped claims of a sketch's `lemma` steps (the child goals). [] if it won't parse."""
    try:
        led = parse_ledger(sketch)
    except LedgerError:
        return []
    seen: set[str] = set()
    children: list[str] = []
    for s in led.steps:
        if s.justification == "lemma":
            k = goal_hash(s.claim)
            if k not in seen:
                seen.add(k)
                children.append(s.claim)
    return children


__all__ = [
    "_FENCED_JSON_RE",
    "_CLOSERS",
    "_last_balanced",
    "_extract_json",
    "_extract_json_object",
    "_json_array",
    "children_from_sketch",
]
