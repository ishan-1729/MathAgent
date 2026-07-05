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


# A conclusion is conventionally restated with a leading connective ("Hence/Therefore/…") and a
# trailing period; these are folded so such a restatement still binds to the goal.
_CONCLUSION_LEAD = re.compile(
    r"^(?:hence|therefore|thus|so|then|it\s+follows\s+that|"
    r"we\s+conclude\s+that|we\s+have\s+(?:shown|proved|proven)\s+that)[\s,:]+",
    re.IGNORECASE)


def _norm_claim(s: str) -> str:
    """NFC + whitespace-collapsed form for comparing a goal against a step's claim.

    This is a deliberately *local* normalization (gates must stay free of orchestrator imports):
    it folds Unicode form, whitespace, a trailing period, and a leading concluding connective
    ("Hence/Therefore/…"), but never notation/synonyms. A genuine restatement of the same goal —
    differing only in spacing or a connective — therefore still binds, while a different statement
    does not.
    """
    out = re.sub(r"\s+", " ", _nfc(s or "")).strip()
    out = _CONCLUSION_LEAD.sub("", out).strip()
    out = out.rstrip(".").strip()
    # Fold only the sentence-initial letter's case (capitalization is not mathematically meaningful
    # at the start of a sentence, but mid-string case IS — `x` != `X` as variables — so it stays).
    if out:
        out = out[0].lower() + out[1:]
    return out


def _extract_json(text: str) -> dict:
    """Pull a ledger object out of raw text: a dict, a JSON string, or a fenced ```json block."""
    text = text.strip()
    # Prefer fenced blocks (most common in LLM output). A DECOY fence can precede the real ledger, so
    # scan ALL fenced blocks and take the LAST one that parses as JSON — the real ledger is
    # conventionally emitted last, so an earlier decoy cannot win. Fall back to the raw text when
    # there is no fenced block.
    matches = list(_FENCE_RE.finditer(text))
    candidates = [m.group(1) for m in matches] if matches else [text]
    parsed = None
    last_err: Optional[json.JSONDecodeError] = None
    for cand in reversed(candidates):
        try:
            parsed = json.loads(cand)
            break
        except json.JSONDecodeError as e:
            last_err = e
    if parsed is None:
        raise LedgerError(f"could not parse ledger JSON: {last_err}") from last_err
    if not isinstance(parsed, dict):
        raise LedgerError("ledger must be a JSON object")
    return parsed


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

    # 5a. goal<->claim binding: the conclusion must actually conclude the ledger's stated goal.
    #     A proof whose terminal step claims something other than `ledger.claim` proves a DIFFERENT
    #     statement than the one requested, so it is not a proof of this goal. Compared under
    #     NFC + whitespace normalization (folding a leading "Hence/Therefore" connective and a
    #     trailing period, which carry no mathematical content), so a benign restatement still binds.
    #     To stay free of false positives on placeholder/scaffolding ledgers (whose claims are not
    #     genuine restatements of each other), the REJECT fires only on a *genuine* mismatch: the
    #     conclusion restates some OTHER body step's claim instead of the goal — i.e. the proof
    #     concludes an intermediate lemma, not the requested statement. (Robust goal binding against the
    #     *requested* goal is enforced in the orchestrator via goal_hash — FlatDriver/DagDriver/CLI.)
    if len(conclusions) == 1:
        concl = conclusions[0]
        norm_concl = _norm_claim(concl.claim)
        if norm_concl != _norm_claim(ledger.claim):
            body_claims = {_norm_claim(s.claim) for s in ledger.steps if s.id != concl.id}
            if norm_concl in body_claims:
                findings.append(Finding(LAYER_STRUCTURE, Severity.REJECT, "goal_claim_mismatch",
                                        "conclusion step restates an intermediate step's claim "
                                        "rather than the ledger's stated goal (the proof does not "
                                        "conclude the requested claim)", concl.id))

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
