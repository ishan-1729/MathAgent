"""An INDEPENDENT adversarial elementary-verifier (T100 P1 item 5, forge_relevance_study §4/§7).

The prover/decomposer produces a proof and the deterministic gate *admits* it (PASSED_DETERMINISTIC
or NEEDS_REVIEW). A NEEDS_REVIEW verdict means the gate flagged something it cannot rule on
deterministically (an elastic justification, a denylist-prose hit) and would normally route to an
LLM judge panel. When no judge is available, the old behaviour was to DISCARD the node (mark it
exhausted) — a silent give-up that loses the partial result and never says *why*.

This module instead runs a deterministic, *refuting* check that is DISTINCT from the prover: rather
than re-deriving the proof, it tries to find a concrete reason the proof is NOT elementary. It is the
"adversarial verifier + downgrade-don't-discard" import from Forge:

  * REFUTED  -> the node is FAILED_ELEMENTARY recording the SPECIFIC offending step (a logged gap,
               not a silent discard).
  * NOT REFUTED -> the verifier found no elementary violation it can prove; the node may proceed
               (the driver still treats this conservatively — see dag_driver).

ANTI-VACUITY (the Forge "running 0 tests fails loud" trap): a verifier that inspected NOTHING — an
empty ledger, or a ledger with no step it could even reason about, or a run that matched no rule at
all — must FAIL LOUD (return REFUTED), NEVER vacuously pass. A check that proves nothing must not be
mistaken for a check that proved the proof safe.

Refutation rules (each names a specific offending step where possible):
  1. denylist-prose hit  : a step's prose names a non-elementary method (class group, elliptic
                           curve, ...) not explained by an allow-context word.
  2. undischarged elastic : an elastic justification (descent/vieta_jumping/bounding/...) whose
                           required obligation is missing OR self-asserted-but-false (descent that
                           does not assert strictly_decreases & stays_in_domain; bounding with no
                           inequality; a relabeled step whose obligation was stripped).
  3. goal binding        : the terminal conclusion does not bind to the requested goal.

Everything here is deterministic and prover-independent. It NEVER execs/evals/imports model output.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from agent.gates.ledger import Ledger, parse_ledger, LedgerError
from agent.gates.scanner import ELASTIC_JUSTIFICATIONS
from agent.gates.toolkit import Toolkit, load_toolkit
from agent.orchestrator.dag import goal_hash


@dataclass(frozen=True)
class Refutation:
    """One concrete reason the proof is not (verifiably) elementary."""

    code: str
    message: str
    step_id: Optional[str] = None

    def __str__(self) -> str:
        loc = f" [{self.step_id}]" if self.step_id else ""
        return f"{self.code}{loc}: {self.message}"


@dataclass
class VerifierResult:
    refuted: bool
    refutations: list[Refutation] = field(default_factory=list)
    inspected_steps: int = 0          # how many steps the verifier actually reasoned about
    rules_evaluated: int = 0          # how many refutation rules it ran (anti-vacuity tripwire)

    @property
    def offending_step(self) -> Optional[str]:
        for r in self.refutations:
            if r.step_id:
                return r.step_id
        return None

    def summary(self) -> str:
        if not self.refuted:
            return f"not refuted (inspected {self.inspected_steps} steps)"
        return "REFUTED: " + "; ".join(str(r) for r in self.refutations)


class VacuousVerificationError(Exception):
    """Raised when the verifier was asked to certify but had NOTHING to inspect (anti-vacuity)."""


# Elastic justification -> the obligation key whose PRESENCE+SHAPE makes the tag load-bearing.
_ELASTIC_OBLIGATION = {
    "descent": "descent",
    "vieta_jumping": "descent",
    "bounding": "bounding",
    "squeeze": "bounding",
    "factorization": None,   # factorization is elastic but has no mandatory structured obligation;
                             # it is refutable only via prose/goal-binding, so we don't demand a key.
}


def _refute_denylist_prose(ledger: Ledger, toolkit: Toolkit) -> list[Refutation]:
    """A step whose prose names a denylisted non-elementary method (not explained by allow-context)."""
    out: list[Refutation] = []
    terms = toolkit.prose_terms
    # allow_context_terms are enforced by CONSTRUCTION (see scanner.scan_prose): prose_terms are
    # multi-word phrases disjoint from the single-word allow vocabulary, so no bare allow word can
    # suppress a hit. The prior `not any(term in ctx or ctx == term for ctx in allow)` guard compared
    # a multi-word phrase against short allow words, so it could never fire; it was dead and is removed.
    for s in ledger.steps:
        texts = [s.claim]
        if s.method_ref:
            texts.append(s.method_ref)
        low = "   ".join(texts).lower()
        for term in terms:
            if term in low:
                out.append(Refutation("denylist_prose",
                                      f"step prose names non-elementary method {term!r}", s.id))
                break
    return out


def _refute_undischarged_elastic(ledger: Ledger) -> list[Refutation]:
    """An elastic justification whose load-bearing obligation is missing or self-asserted-false.

    This is the core attack the verifier exists to catch: a step RELABELED with an elastic tag whose
    obligation was stripped (so the deterministic gate's *presence* check upstream would reject it,
    but a NEEDS_REVIEW ledger that slipped past presence — e.g. one whose obligation dict is present
    but FALSE — must still be refuted here independently)."""
    out: list[Refutation] = []
    for s in ledger.steps:
        if s.justification not in ELASTIC_JUSTIFICATIONS:
            continue
        key = _ELASTIC_OBLIGATION.get(s.justification, None)
        if key is None:
            continue  # elastic but no mandatory structured obligation (e.g. factorization)
        ob = s.obligations.get(key) if s.obligations else None
        if not ob:
            out.append(Refutation("undischarged_elastic",
                                  f"elastic justification {s.justification!r} carries no {key!r} "
                                  f"obligation (its decrease/bound is unverified)", s.id))
            continue
        if key == "descent":
            if ob.get("strictly_decreases") is not True or ob.get("stays_in_domain") is not True:
                out.append(Refutation("undischarged_elastic",
                                      f"descent step does not assert strictly_decreases & "
                                      f"stays_in_domain (decrease not established)", s.id))
        elif key == "bounding":
            if not ob.get("inequality"):
                out.append(Refutation("undischarged_elastic",
                                      "bounding step carries no concrete inequality", s.id))
    return out


def _refute_goal_binding(ledger: Ledger, goal: Optional[str]) -> list[Refutation]:
    """The terminal conclusion must bind to the requested goal (when a goal is supplied)."""
    if goal is None:
        return []
    conclusions = [s for s in ledger.steps if s.justification == "conclusion"]
    if len(conclusions) != 1:
        return [Refutation("goal_binding",
                           f"ledger has {len(conclusions)} conclusion steps; expected exactly one")]
    concl = conclusions[0]
    if goal_hash(concl.claim) != goal_hash(goal):
        return [Refutation("goal_binding",
                           "conclusion does not bind to the requested goal", concl.id)]
    return []


def refute_elementary(source, toolkit: Optional[Toolkit] = None,
                      goal: Optional[str] = None) -> VerifierResult:
    """Adversarially try to REFUTE that `source` is an elementary proof of `goal`.

    `source` is a ledger dict/text or a parsed `Ledger`. Deterministic, prover-independent.

    ANTI-VACUITY: if the ledger does not parse, has no steps, or the verifier could not run a single
    refutation rule over a single step, this raises `VacuousVerificationError` (the caller MUST treat
    a vacuous inspection as a refutation / fail-loud, never as a pass). A non-empty ledger with no
    violation returns `refuted=False`."""
    toolkit = toolkit or load_toolkit()

    if isinstance(source, Ledger):
        ledger: Optional[Ledger] = source
    else:
        try:
            ledger = parse_ledger(source)
        except LedgerError:
            ledger = None

    # Anti-vacuity tripwire #1: nothing to inspect at all.
    if ledger is None or not ledger.steps:
        raise VacuousVerificationError(
            "elementary verifier had no parseable ledger / no steps to inspect (vacuous)")

    refutations: list[Refutation] = []
    refutations += _refute_denylist_prose(ledger, toolkit)
    refutations += _refute_undischarged_elastic(ledger)
    refutations += _refute_goal_binding(ledger, goal)

    # The verifier ALWAYS runs all three rules over the ledger's steps, so a non-empty ledger always
    # has a non-zero inspection footprint. Record it so the anti-vacuity invariant is observable.
    inspected = len(ledger.steps)
    rules = 3

    # Anti-vacuity tripwire #2 (defensive): a "successful" certification that somehow inspected zero
    # steps or ran zero rules must FAIL LOUD rather than vacuously pass.
    if inspected == 0 or rules == 0:
        raise VacuousVerificationError(
            "elementary verifier matched no rule / inspected no step (vacuous certification)")

    return VerifierResult(refuted=bool(refutations), refutations=refutations,
                          inspected_steps=inspected, rules_evaluated=rules)
