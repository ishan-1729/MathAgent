"""Numeric statement triage (Layer-3 SEARCH signal — NOT a soundness verdict).

This is the missing Layer-3 capability PLAN §5 describes ("the witness/search tool confirms the
statement ... and hunts counterexamples"): aim the deterministic exact-integer checker
(:mod:`agent.tools.numeric`) at a GOAL STATEMENT to catch FALSE universal statements early, before
the proving machinery wastes effort trying to prove something untrue.

--------------------------------------------------------------------------------------------------
WHAT THIS IS — and, LOUDLY, WHAT IT IS NOT
--------------------------------------------------------------------------------------------------
A numeric counterexample REFUTES a universal statement deterministically. But there are TWO links in
the chain, and only ONE of them is deterministic:

  (1) statement  --(LLM-proposed *translation*)-->  falsification spec        [HEURISTIC, not sound]
  (2) falsification spec  --(exact-integer checker)-->  confirmed witness      [DETERMINISTIC]

Step (2) is airtight: :func:`agent.tools.numeric.check_witness_spec` decides, with exact integer
arithmetic and no ``eval``/``exec``, whether a spec's OWN claim holds (e.g. "the solution set of
``E == 0`` in this box is exactly S"). Step (1) — that confirming this spec actually contradicts the
statement — is proposed by an LLM and is NOT verified anywhere. A cluster of odd numbers is a genuine
mathematical fact; whether it refutes "every integer is even" depends on the LLM having translated the
statement faithfully into the expression, and nothing here checks that.

Therefore this module NEVER emits a field called ``refuted`` and NEVER claims "the statement is
false". It emits:

  * ``refuted_modulo_translation`` — True only when the checker DETERMINISTICALLY confirmed a concrete
    integer witness that the SPEC'S OWN claimed semantics tie to the statement's falsity. The
    "modulo_translation" is not decoration: it is the honest caveat that the spec<->statement link is
    heuristic.
  * ``candidate_counterexample`` — the concrete witness assignment the checker found (NOT
    ``counterexample``; it is a *candidate*, pending a human/formal confirmation of the translation).

This is a STRONG TRIAGE / SEARCH signal (in BrokenArXiv's own-protocol terms it is grade-2-worthy,
comparable to the upstream LLM-judge protocol — both have an LLM in the loop; ours *ends* in a
deterministic integer check, theirs ends in a judge opinion). It is USEFUL for: killing false goals
early, prioritizing effort, flagging a statement for review.

It is NEVER a soundness verdict inside the proving pipeline and MUST NOT feed the gate. Nothing in
``agent/gates/`` may consume this. A failed hunt proves NOTHING — no "confirmed true" signal is ever
emitted (absence of a counterexample in a bounded box is not truth).

--------------------------------------------------------------------------------------------------
SAFETY INVARIANTS (non-negotiable)
--------------------------------------------------------------------------------------------------
  * The LLM only ever PROPOSES falsification specs as INERT JSON data.
  * The deterministic exact-integer checker (:mod:`agent.tools.numeric`) is the ONLY thing that
    decides. Nothing here ``exec``/``eval``/``import``s model output; a proposed spec is text handed
    to ``json.loads`` (data only) and then to the no-eval integer AST inside the checker.
  * A proposed spec that fails to parse/validate is DISCARDED (fail-closed to "no signal") — never a
    false refutation. A confirmed-nothing spec likewise yields no signal.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from agent.tools.claude_cli import ClaudeConfig, ClaudeError, _run_claude
from agent.tools.numeric import (
    NumericError,
    check_witness_spec,
    verify_solution_set,
    _coerce_box,
)

# A single fenced ```json block OR a bare JSON value. We tolerate fences and prose; each captured
# candidate is validated independently, and unparseable ones are silently discarded.
_FENCED_RE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


@dataclass
class TriageResult:
    """The outcome of aiming the exact-integer checker at a statement's falsity.

    HONEST EPISTEMICS (see the module docstring): ``refuted_modulo_translation`` is True ONLY when the
    checker deterministically confirmed ``candidate_counterexample`` as a witness for a spec whose OWN
    semantics the (heuristic, LLM-proposed) translation ties to the statement being false. This is a
    triage/search signal, NOT a soundness verdict; it MUST NOT feed the gate.
    """

    refuted_modulo_translation: bool = False
    candidate_counterexample: Optional[dict] = None   # concrete integer witness the checker confirmed
    spec: Optional[str] = None                         # the confirming spec (JSON text), for inspection
    candidates_tried: int = 0                          # how many proposed specs were run through checker
    detail: str = ""                                   # checker detail for the confirming spec
    notes: list[str] = field(default_factory=list)     # per-candidate discard reasons (no signal cases)


# --------------------------------------------------------------------------------------------------
# Prompt: ask the model to PROPOSE (as inert JSON) small-integer falsification specs. The schema below
# MIRRORS agent/tools/numeric.check_witness_spec exactly — the model must emit specs the checker can run.
# --------------------------------------------------------------------------------------------------
_PROMPT = """You are a COUNTEREXAMPLE HUNTER for number-theory statements. You do NOT prove anything.

If the statement below is FALSE, a small integer counterexample often exists. Your job: emit up to \
{k} FALSIFICATION SPECS as pure JSON so a deterministic exact-integer checker can CONFIRM a concrete \
counterexample. You only PROPOSE; the checker decides. If you cannot construct a plausible \
falsification, emit an empty JSON array `[]` — never guess or fabricate.

STATEMENT:
{statement}

A falsification spec is a JSON object in the "solution_set" witness-spec schema. Its confirmed \
solutions must be integer assignments that CONTRADICT the statement (i.e. each solution is a concrete \
counterexample to the universal claim). The checker verifies that the `claimed` list is EXACTLY the \
set of integer solutions of `expression == 0` inside the box — so put your candidate counterexample \
witness(es) in `claimed`, and choose `expression` so its box-solutions are precisely those \
counterexamples.

Schema (emit specs of THIS exact shape — every field required):
  {{
    "kind": "solution_set",
    "expression": "<polynomial in the variables; '==0' at a point means that point is a counterexample>",
    "variables": ["n", ...],
    "bounds": {{"n": [lo, hi], ...}},   // small closed integer box, |bound| <= 1e9, box <= a few thousand points
    "claimed": [{{"n": <int>, ...}}]     // the exact counterexample witness(es); must be ALL box solutions
  }}

RULES:
  - Expressions use ONLY + - * and non-negative integer powers (** or ^) over integer literals and the \
declared variables. No division, no functions, no floats, no other operators.
  - Pick the SMALLEST box that contains your witness and no other solution (so `claimed` is exact).
  - Each spec's confirmed solution(s) must, by their meaning, contradict the statement.

EXAMPLE — statement "every integer n satisfies n is even":
  A counterexample is any odd n, e.g. n = 1 (since 1 = 2*0 + 1). Spec:
  {{"kind": "solution_set", "expression": "n - 1", "variables": ["n"], "bounds": {{"n": [0, 3]}}, \
"claimed": [{{"n": 1}}]}}
  (The box {{0..3}} solution set of `n - 1 == 0` is exactly {{n=1}}, an odd integer, contradicting \
"every integer is even".)

Output ONLY a JSON array of spec objects (or `[]`). No prose. Do not read or modify any files."""


def _parse_candidates(raw: str, k: int) -> list[str]:
    """Extract up to ``k`` candidate spec JSON strings from CLI output (tolerating fences/prose).

    Returns a list of JSON *object* texts. Anything that does not decode to a JSON object is discarded
    here (fail-closed to "no candidate"); the checker independently rejects any that slip through. This
    NEVER eval/exec's — it only ``json.loads``es extracted spans and re-``json.dumps``es objects.
    """
    text = (raw or "").strip()
    out: list[str] = []

    def _add(obj) -> None:
        if isinstance(obj, dict) and len(out) < k:
            out.append(json.dumps(obj))

    def _consume(val) -> None:
        if isinstance(val, list):
            for item in val:
                _add(item)
        else:
            _add(val)

    # 1. Every fenced block — parse each; a fenced array or object both contribute candidates.
    matched_any = False
    for m in _FENCED_RE.finditer(text):
        body = m.group(1).strip()
        try:
            _consume(json.loads(body))
            matched_any = True
        except json.JSONDecodeError:
            continue
    if out:
        return out[:k]

    # 2. No usable fenced block: try the whole text as a JSON value (array of specs, or one spec).
    if not matched_any:
        try:
            _consume(json.loads(text))
        except json.JSONDecodeError:
            pass
    return out[:k]


def propose_falsification_specs(statement: str, cfg: Optional[ClaudeConfig] = None,
                                k: int = 3) -> list[str]:
    """ONE LLM call proposing up to ``k`` falsification specs (INERT JSON strings) for ``statement``.

    The model is prompted (see ``_PROMPT``) to emit ``solution_set`` witness specs whose confirmed
    integer solutions would CONTRADICT the statement. The response is parsed fence-tolerantly into
    candidate JSON-object strings; unparseable candidates are discarded (fail-closed to no candidate).

    SAFETY: the returned strings are INERT DATA — they are only ever handed to
    :func:`agent.tools.numeric.check_witness_spec` (which parses via ``json.loads`` + the no-eval AST).
    Nothing is executed. On any CLI error, returns ``[]`` (no signal), never raising into the caller.
    """
    if k <= 0:
        return []
    cfg = cfg or ClaudeConfig(model="sonnet")
    prompt = _PROMPT.format(k=k, statement=statement)
    try:
        raw = _run_claude(prompt, cfg)
    except ClaudeError:
        return []
    return _parse_candidates(raw, k)


def _confirmed_counterexample(spec_text: str) -> Optional[dict]:
    """If ``spec_text`` is a valid ``solution_set`` spec the checker CONFIRMS, return the concrete
    witness assignment; else None.

    Two-stage, both deterministic and no-eval:
      1. ``check_witness_spec`` must return ``ok=True`` with kind ``solution_set`` (the claimed set is
         exactly the box solution set — no missing, no spurious). ANY error / non-confirm / other kind
         yields None (no signal).
      2. Recover the ACTUAL witness by re-running ``verify_solution_set`` and reading its ``solutions``
         (the real box solutions the checker found) — we do not merely trust the model's ``claimed``.
    A ``solution_set`` whose confirmed solution set is EMPTY is not a counterexample (nothing to
    exhibit), so it yields None too.
    """
    check = check_witness_spec(spec_text)
    if not (check.ok and check.kind == "solution_set"):
        return None
    # Recover the concrete witness deterministically (not from the model's `claimed`, but from the
    # checker's own recomputed solution set). Re-parsing here reuses the same no-eval integer AST.
    try:
        obj = json.loads(spec_text)
        variables = list(obj["variables"])
        box = _coerce_box(obj.get("bounds", {}), variables)
        res = verify_solution_set(obj["expression"], variables, box, obj.get("claimed", []))
    except (NumericError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not res.ok or not res.solutions:
        return None
    return dict(res.solutions[0])


def triage_statement(statement: str, cfg: Optional[ClaudeConfig] = None, k: int = 3) -> TriageResult:
    """Aim the exact-integer checker at ``statement``'s falsity via LLM-proposed specs.

    Runs :func:`propose_falsification_specs` (ONE LLM call) then feeds each candidate through the
    deterministic checker. Returns a :class:`TriageResult`. ``refuted_modulo_translation`` is True ONLY
    when the checker deterministically confirms a concrete counterexample whose spec semantics the
    (heuristic, LLM-proposed) translation ties to the statement's falsity — a TRIAGE signal, NOT a
    soundness verdict, and it MUST NOT feed the gate (see the module docstring).

    A failed hunt (no candidate confirms) returns ``refuted_modulo_translation=False`` with NO claim of
    truth. Fail-closed throughout: unparseable/invalid candidates are discarded, never a false refute.
    """
    candidates = propose_falsification_specs(statement, cfg, k=k)
    result = TriageResult(candidates_tried=len(candidates))
    for spec_text in candidates:
        witness = _confirmed_counterexample(spec_text)
        if witness is not None:
            result.refuted_modulo_translation = True
            result.candidate_counterexample = witness
            result.spec = spec_text
            result.detail = check_witness_spec(spec_text).detail
            return result
        result.notes.append("candidate did not confirm a counterexample (discarded, no signal)")
    return result


__all__ = [
    "TriageResult",
    "propose_falsification_specs",
    "triage_statement",
]
