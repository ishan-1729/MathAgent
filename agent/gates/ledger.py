"""Layer 1a: parse and deterministically validate a proof step-ledger.

A ledger is a DAG of justified steps (see ledger.schema.json). This module is the *truly
deterministic* part of the gate: it parses the ledger out of model output, validates it against
the JSON Schema, and checks structural invariants (unique ids, allowed justifications, no dangling
or cyclic dependencies, exactly one terminal, required obligations present). Malformed input is a
deterministic REJECT, never a silent pass.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import jsonschema

from agent.gates.report import (
    Finding,
    Severity,
    LAYER_STRUCTURE,
    LAYER_OBLIGATION,
)
from agent.gates.toolkit import Toolkit

_SCHEMA_PATH = Path(__file__).resolve().parent / "ledger.schema.json"
_SCHEMA = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
_VALIDATOR = jsonschema.Draft7Validator(_SCHEMA)

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class LedgerError(ValueError):
    """Raised when ledger text cannot be parsed into a schema-valid object."""


@dataclass(frozen=True)
class Step:
    id: str
    claim: str
    justification: str
    depends_on: tuple[str, ...] = ()
    method_ref: Optional[str] = None
    obligations: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Ledger:
    problem: str
    claim: str
    steps: tuple[Step, ...]

    def by_id(self, step_id: str) -> Optional[Step]:
        for s in self.steps:
            if s.id == step_id:
                return s
        return None

    def ids(self) -> list[str]:
        return [s.id for s in self.steps]


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _extract_json(text: str) -> dict:
    """Pull a ledger object out of raw text: a dict, a JSON string, or a fenced ```json block."""
    text = text.strip()
    # Try a fenced block first (most common in LLM output).
    m = _FENCE_RE.search(text)
    candidate = m.group(1) if m else text
    try:
        obj = json.loads(candidate)
    except json.JSONDecodeError as e:
        raise LedgerError(f"could not parse ledger JSON: {e}") from e
    if not isinstance(obj, dict):
        raise LedgerError("ledger must be a JSON object")
    return obj


def parse_ledger(source: Any) -> Ledger:
    """Parse + schema-validate a ledger from a dict or text. Raises LedgerError on malformed input."""
    obj = source if isinstance(source, dict) else _extract_json(str(source))

    errors = sorted(_VALIDATOR.iter_errors(obj), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        loc = "/".join(str(p) for p in first.path) or "(root)"
        raise LedgerError(f"schema violation at {loc}: {first.message}")

    steps = tuple(
        Step(
            id=s["id"],
            claim=_nfc(s["claim"]),
            justification=s["justification"],
            depends_on=tuple(s.get("depends_on", [])),
            method_ref=s.get("method_ref"),
            obligations=s.get("obligations", {}) or {},
        )
        for s in obj["steps"]
    )
    return Ledger(problem=_nfc(obj["problem"]), claim=_nfc(obj["claim"]), steps=steps)


def reachable_from(ledger: Ledger, start_ids: list[str]) -> set[str]:
    """Transitive depends_on closure of start_ids (includes start_ids). Iterative (heap-bounded)."""
    id_set = {s.id for s in ledger.steps}
    seen: set[str] = set()
    stack = [sid for sid in start_ids]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        step = ledger.by_id(cur)
        if step:
            stack.extend(d for d in step.depends_on if d in id_set)
    return seen


def _find_cycle(ledger: Ledger) -> Optional[list[str]]:
    """Return the ids participating in a dependency cycle, or None if acyclic.

    Iterative Kahn's topological peel (no recursion, so a long acyclic chain cannot blow the stack).
    A cycle exists iff some node is never peeled; we return those remaining nodes.
    """
    id_set = {s.id for s in ledger.steps}
    adj = {s.id: [d for d in s.depends_on if d in id_set] for s in ledger.steps}
    indeg = {sid: 0 for sid in adj}
    for vs in adj.values():
        for v in vs:
            indeg[v] += 1

    queue = [n for n, d in indeg.items() if d == 0]
    removed = 0
    while queue:
        u = queue.pop()
        removed += 1
        for v in adj[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                queue.append(v)

    if removed == len(adj):
        return None
    return sorted(n for n, d in indeg.items() if d > 0)


# Obligation kind -> the key expected under step.obligations
_OBLIGATION_KEY = {
    "case_cover": "case_cover",
    "descent": "descent",
    "bounding": "bounding",
    "split_coprimality": "split_coprimality",
}


def validate_structure(ledger: Ledger, toolkit: Toolkit) -> list[Finding]:
    """Deterministic structural checks. REJECT findings are authoritative failures."""
    findings: list[Finding] = []
    ids = ledger.ids()
    id_set = set(ids)

    # 1. unique ids
    seen: set[str] = set()
    for sid in ids:
        if sid in seen:
            findings.append(Finding(LAYER_STRUCTURE, Severity.REJECT, "dup_id",
                                    f"duplicate step id {sid!r}", sid))
        seen.add(sid)

    # 2. justification in the closed vocabulary
    for s in ledger.steps:
        if not toolkit.is_allowed(s.justification):
            findings.append(Finding(LAYER_STRUCTURE, Severity.REJECT, "bad_justification",
                                    f"justification {s.justification!r} is not in the allowed toolkit",
                                    s.id))

    # 3. depends_on must reference existing ids (no dangling)
    for s in ledger.steps:
        for dep in s.depends_on:
            if dep not in id_set:
                findings.append(Finding(LAYER_STRUCTURE, Severity.REJECT, "dangling_dep",
                                        f"depends_on references unknown step {dep!r}", s.id))

    # 4. acyclicity
    cycle = _find_cycle(ledger)
    if cycle:
        findings.append(Finding(LAYER_STRUCTURE, Severity.REJECT, "cycle",
                                "dependency cycle involves steps: " + ", ".join(cycle)))

    # 5. exactly one terminal 'conclusion' step
    conclusions = [s for s in ledger.steps if s.justification == "conclusion"]
    if len(conclusions) == 0:
        findings.append(Finding(LAYER_STRUCTURE, Severity.REJECT, "no_conclusion",
                                "ledger has no terminal 'conclusion' step"))
    elif len(conclusions) > 1:
        findings.append(Finding(LAYER_STRUCTURE, Severity.REJECT, "multi_conclusion",
                                f"ledger has {len(conclusions)} 'conclusion' steps; expected exactly one"))

    # 5b. a lone conclusion that depends on nothing, while other steps exist, "proves itself".
    if len(conclusions) == 1 and len(ledger.steps) > 1 and not conclusions[0].depends_on:
        findings.append(Finding(LAYER_STRUCTURE, Severity.REJECT, "disconnected_conclusion",
                                "conclusion has empty depends_on while other steps exist "
                                "(the proof body is disconnected from the conclusion)",
                                conclusions[0].id))

    # 6. orphan steps (not in the conclusion's dependency closure) -> advisory only
    if len(conclusions) == 1 and not cycle:
        reachable = reachable_from(ledger, [conclusions[0].id])
        for s in ledger.steps:
            if s.id not in reachable:
                findings.append(Finding(LAYER_STRUCTURE, Severity.INFO, "orphan_step",
                                        "step is not used by the conclusion", s.id))

    # 7. required obligations present (presence/shape only; numeric truth is Layer 3)
    for s in ledger.steps:
        if not toolkit.is_allowed(s.justification):
            continue
        kind = toolkit.obligation_for(s.justification)
        if kind == "none":
            continue
        key = _OBLIGATION_KEY.get(kind)
        if key and key not in s.obligations:
            findings.append(Finding(LAYER_OBLIGATION, Severity.REJECT, "missing_obligation",
                                    f"justification {s.justification!r} requires a {key!r} obligation",
                                    s.id))

    return findings
