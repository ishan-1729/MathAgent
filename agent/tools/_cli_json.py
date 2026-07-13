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
import math
import re
from array import array
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
_MAX_RAW_CLI_OUTPUT_CHARS = 4 * 1024 * 1024
_MAX_JSON_PAYLOAD_CHARS = 512 * 1024
_MAX_JSON_NODES = 20_000
_MAX_JSON_DEPTH = 64
_MAX_JSON_STRING_CHARS = 65_536
_MAX_STRING_ARRAY_ITEMS = 256
_MAX_STRING_ARRAY_ITEM_CHARS = 4096
_INVALID_JSON = object()


def _prepare_text(raw: str) -> str | None:
    if not isinstance(raw, str) or len(raw) > _MAX_RAW_CLI_OUTPUT_CHARS:
        return None
    return raw.strip()


def _reject_json_constant(value: str):
    raise ValueError(f"non-finite JSON number {value!r} is not allowed")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    value: dict = {}
    for key, child in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key {key!r}")
        value[key] = child
    return value


def _safe_json_loads(span: str):
    """Decode one size-bounded JSON value and reject shape/memory amplification."""
    if len(span) > _MAX_JSON_PAYLOAD_CHARS:
        return _INVALID_JSON
    try:
        value = json.loads(span, parse_constant=_reject_json_constant,
                           object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return _INVALID_JSON

    nodes = 0
    stack: list[tuple[object, int]] = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > _MAX_JSON_NODES or depth > _MAX_JSON_DEPTH:
            return _INVALID_JSON
        if isinstance(item, str):
            if len(item) > _MAX_JSON_STRING_CHARS:
                return _INVALID_JSON
        elif isinstance(item, float):
            if not math.isfinite(item):
                return _INVALID_JSON
        elif isinstance(item, dict):
            if nodes + 2 * len(item) > _MAX_JSON_NODES:
                return _INVALID_JSON
            for key, child in item.items():
                if len(key) > _MAX_JSON_STRING_CHARS:
                    return _INVALID_JSON
                stack.append((key, depth + 1))
                stack.append((child, depth + 1))
        elif isinstance(item, list):
            if nodes + len(item) > _MAX_JSON_NODES:
                return _INVALID_JSON
            stack.extend((child, depth + 1) for child in item)
    return value


def _string_array(value) -> list[str]:
    if (not isinstance(value, list) or len(value) > _MAX_STRING_ARRAY_ITEMS
            or any(not isinstance(item, str) or len(item) > _MAX_STRING_ARRAY_ITEM_CHARS
                   for item in value)):
        return []
    return list(value)


def _last_balanced_span(text: str, open_ch: str, close_ch: str) -> tuple[int, int] | None:
    """Indices of the last balanced delimiter span, ignoring delimiters inside strings.

    One forward lexical pass records every matching pair and keeps the latest-ending, outermost pair.
    JSON strings are skipped, and a raw newline resets an unmatched prose quote because valid JSON
    strings cannot contain literal newlines. The algorithm is linear even for hostile unmatched input.
    """
    if len(open_ch) != 1 or len(close_ch) != 1 or open_ch == close_ch:
        raise ValueError("open_ch and close_ch must be distinct single characters")

    open_stack = array("I")
    best: tuple[int, int] | None = None
    in_json_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_json_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_json_string = False
            elif ch in "\r\n":
                # Literal newlines are invalid inside JSON strings. Treat this as a malformed prose
                # quote boundary so a later line's valid payload remains recoverable.
                in_json_string = False
            continue
        if ch == '"':
            in_json_string = True
        elif ch == open_ch:
            open_stack.append(i)
        elif ch == close_ch and open_stack:
            start = open_stack.pop()
            if best is None or i > best[1] or (i == best[1] and start < best[0]):
                best = (start, i)
    return best


def _last_balanced(text: str, open_ch: str, close_ch: str) -> Optional[str]:
    """The last balanced ``open_ch..close_ch`` text span, or ``None``."""
    span = _last_balanced_span(text, open_ch, close_ch)
    return None if span is None else text[span[0]:span[1] + 1]


def _extract_json(raw: str):
    """Parse the trailing JSON value out of CLI output. Prefer a fenced ```json block, else the
    last balanced object/array. Returns the decoded value, or None if nothing valid is found."""
    text = _prepare_text(raw)
    if text is None:
        return None
    # 1. Fenced block (most reliable; mirrors ledger._extract_json). A DECOY fence can precede the
    #    real value, so scan ALL fenced blocks and take the LAST that parses — the real payload is
    #    conventionally emitted last, so an earlier decoy cannot shadow it.
    fenced = _INVALID_JSON
    for m in _FENCED_JSON_RE.finditer(text):
        value = _safe_json_loads(m.group(1))
        if value is not _INVALID_JSON:
            fenced = value
    if fenced is not _INVALID_JSON:
        return fenced
    # 2. Last balanced object/array. Take whichever appears later in the text so a trailing JSON
    #    array isn't shadowed by an earlier object (and vice versa).
    obj = _last_balanced_span(text, "{", "}")
    arr = _last_balanced_span(text, "[", "]")
    candidates: list[tuple[int, int, str]] = []
    for span in (obj, arr):
        if span is not None:
            start, end = span
            candidate = text[start:end + 1]
            # Rank by END position, then prefer the earlier-starting (outer) value on a tie.
            candidates.append((end, -start, candidate))
    for _end, _outer, candidate in sorted(candidates, reverse=True):
        value = _safe_json_loads(candidate)
        if value is not _INVALID_JSON:
            return value
    return None


def _extract_json_object(raw: str) -> Optional[dict]:
    """The trailing JSON OBJECT from CLI output (fenced block preferred), or None."""
    text = _prepare_text(raw)
    if text is None:
        return None
    # Prefer the LAST parseable fenced dict: a decoy fence can precede the real one, so an earlier
    # block must not shadow the trailing payload (mirrors ledger._extract_json). This helper decodes
    # the FAITHFULNESS verdict, so a decoy-before-real must not be able to swap the wall's answer.
    fenced: dict | None = None
    for m in _FENCED_JSON_RE.finditer(text):
        value = _safe_json_loads(m.group(1))
        if isinstance(value, dict):
            fenced = value
    if fenced is not None:
        return fenced
    span = _last_balanced(text, "{", "}")
    if span is not None:
        value = _safe_json_loads(span)
        if isinstance(value, dict):
            return value
    return None


def _json_array(raw: str) -> list[str]:
    """The trailing JSON ARRAY from CLI output (fenced block preferred), as a list of strings.

    Returns [] on genuine parse failure or when the trailing JSON value is not an array — keeping the
    critic's fail-closed semantics while no longer being fooled by stray brackets in prose (e.g.
    `[citations]`, Lean list syntax) that precede the real array."""
    text = _prepare_text(raw)
    if text is None:
        return []
    # Prefer the LAST parseable fenced list (decoy-fence resistant; mirrors ledger._extract_json).
    fenced: list[str] | None = None
    for m in _FENCED_JSON_RE.finditer(text):
        value = _safe_json_loads(m.group(1))
        if isinstance(value, list):
            fenced = _string_array(value)
    if fenced is not None:
        return fenced
    span = _last_balanced(text, "[", "]")
    if span is None:
        return []
    value = _safe_json_loads(span)
    return _string_array(value)


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


def _strip_lean_comments(src: str) -> str:
    """Remove Lean comments from `src` so model-authored prose inside them cannot reach a judge prompt.

    Strips line comments (``-- ...`` to end of line) and NESTED block comments (``/- ... -/`` — which
    includes doc ``/-- ... -/`` and ``/-! ... -/``, since all open with ``/-``). Comments are
    irrelevant to statement faithfulness, but they survive ``extract_lean`` and compile cleanly
    (adding no dependencies, so the Layer-4 audit never sees them) — making them a channel for
    prompt-injection steering aimed at the faithfulness judge. Stripping removes that carrier.

    Deliberately simple: it lexes ONLY Lean's comment delimiters, not string/char literals. Treating
    ``--`` / ``/-`` / ``-/`` as comment tokens matches Lean's own lexing everywhere except inside a
    string literal — and a string literal is part of the formal statement the Layer-4 audit already
    sees, so it is not an injection carrier. In the rare case a statement's string literal contains
    one of these byte pairs, over-stripping only makes the ADVERSARIAL judge more likely to flag
    unfaithful (fail-closed), never less.
    """
    out: list[str] = []
    i, n = 0, len(src)
    depth = 0  # block-comment nesting depth (0 == outside any block comment)
    while i < n:
        pair = src[i:i + 2]
        if depth > 0:                       # inside a /- ... -/ block: consume until it balances
            if pair == "/-":
                depth += 1
                i += 2
            elif pair == "-/":
                depth -= 1
                i += 2
            else:
                i += 1
            continue
        if pair == "/-":                    # opens /- , /-- , or /-!
            depth += 1
            i += 2
        elif pair == "--":                  # line comment: drop through end of line, keep the newline
            nl = src.find("\n", i)
            if nl == -1:
                break
            i = nl
        else:
            out.append(src[i])
            i += 1
    return "".join(out)


__all__ = [
    "_FENCED_JSON_RE",
    "_CLOSERS",
    "_last_balanced",
    "_extract_json",
    "_extract_json_object",
    "_json_array",
    "_strip_lean_comments",
    "children_from_sketch",
]
