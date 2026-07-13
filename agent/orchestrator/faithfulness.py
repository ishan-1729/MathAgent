"""Adversarial statement-faithfulness check (the autoformalization-faithfulness wall).

A compiling, audited proof of the WRONG statement is worthless (AlphaProof_Nexus "solved" wrong
density variants; Goedel: >80% of failures were mis-formalized). This module checks that a formalized
Lean *statement* faithfully captures the informal claim, ADVERSARIALLY: several independent judges,
each with a different lens and each told to FIND a discrepancy and default to "unfaithful" if unsure.
The statement is accepted as faithful only if no lens flags it (configurable tolerance).

Model-agnostic: takes a single-judge callable; the Codex-backed judge lives in
`agent/tools/codex_prover.py`. Fully testable offline with a scripted judge.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

# Diverse adversarial lenses (perspective-diverse verification: each catches a different failure mode).
DEFAULT_LENSES = [
    "back_translation",   # translate the Lean statement back to English; does it match the claim?
    "quantifiers_domain",  # quantifiers, variable domains/types, edge values (0, negatives) correct?
    "vacuity",            # vacuously true? unsatisfiable hypotheses? trivially-true restatement?
    "strength",           # strictly weaker/stronger than the claim (a special case, or unrelated)?
]


@dataclass
class SingleVerdict:
    lens: str
    faithful: bool
    issues: list[str] = field(default_factory=list)
    confidence: float = 1.0


@dataclass
class FaithfulnessVerdict:
    faithful: bool
    votes: list[SingleVerdict] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def n_unfaithful(self) -> int:
        return sum(1 for v in self.votes if v.faithful is not True)

    def summary(self) -> str:
        return (f"faithful={self.faithful} "
                f"({len(self.votes) - self.n_unfaithful}/{len(self.votes)} lenses faithful)")


# A single-judge callable: (informal_claim, lean_source, theorem_name, lens) -> SingleVerdict
SingleJudge = Callable[[str, str, str, str], SingleVerdict]


def adversarial_check(informal_claim: str, lean_source: str, theorem_name: str,
                      judge: SingleJudge, lenses: Optional[list[str]] = None,
                      max_unfaithful: int = 0) -> FaithfulnessVerdict:
    """Run `judge` over each lens; faithful iff at most `max_unfaithful` lenses object.

    A live judge (e.g. a Codex-backed one) can raise on network/timeout/malformed output. Such a
    crash must NOT abort the whole DAG run after a proof was found: a lens that fails to produce a
    verdict conservatively counts as UNFAITHFUL (default-closed), so the statement is not silently
    accepted on the strength of a missing vote.
    """
    lenses = list(lenses or DEFAULT_LENSES)
    if (isinstance(max_unfaithful, bool) or not isinstance(max_unfaithful, int)
            or max_unfaithful < 0 or max_unfaithful >= len(lenses)):
        raise ValueError("max_unfaithful must be an integer in [0, number of lenses)")
    votes: list[SingleVerdict] = []
    for lens in lenses:
        try:
            vote = judge(informal_claim, lean_source, theorem_name, lens)
            if not isinstance(vote, SingleVerdict):
                raise TypeError("faithfulness judge did not return SingleVerdict")
            if vote.lens != lens:
                vote = SingleVerdict(
                    lens=lens, faithful=False,
                    issues=[f"judge returned verdict for unexpected lens {vote.lens!r}"],
                    confidence=0.0,
                )
            votes.append(vote)
        except Exception as e:  # default-closed: a crashing lens counts as unfaithful
            votes.append(SingleVerdict(lens=lens, faithful=False,
                                       issues=[f"judge error: {type(e).__name__}: {e}"]))
    issues = [f"[{v.lens}] {i}" for v in votes if v.faithful is not True
              for i in (v.issues or ["unfaithful"])]
    faithful = sum(1 for v in votes if v.faithful is not True) <= max_unfaithful
    return FaithfulnessVerdict(faithful=faithful, votes=votes, issues=issues)


class PanelFaithfulnessChecker:
    """Wrap a single-judge callable into an adversarial panel.

    The generic adapter is deliberately *not* a certification authority: any callable (including a
    lambda or scripted test double) can be supplied. Concrete live provider wrappers perform their
    own trust declaration after fixing the backend and prompt contract.
    """

    certification_trusted = False
    model_call_cost = 0

    def __init__(self, judge: SingleJudge, lenses: Optional[list[str]] = None, max_unfaithful: int = 0):
        self.judge = judge
        self.lenses = list(lenses or DEFAULT_LENSES)
        if (isinstance(max_unfaithful, bool) or not isinstance(max_unfaithful, int)
                or max_unfaithful < 0 or max_unfaithful >= len(self.lenses)):
            raise ValueError("max_unfaithful must be an integer in [0, number of lenses)")
        self.max_unfaithful = max_unfaithful

    def check(self, informal_claim: str, lean_source: str, theorem_name: str) -> FaithfulnessVerdict:
        return adversarial_check(informal_claim, lean_source, theorem_name,
                                 self.judge, self.lenses, self.max_unfaithful)


class ScriptedFaithfulnessChecker:
    """Returns a fixed verdict for tests/offline demos.

    A canned verdict is never production evidence. It is untrusted for certification by default;
    tests that deliberately simulate a trusted panel must opt in explicitly.
    """

    def __init__(self, faithful: bool, issues: Optional[list[str]] = None, *,
                 certification_trusted: bool = False):
        self._faithful = faithful
        self._issues = issues or []
        self.certification_trusted = bool(certification_trusted)
        self.model_call_cost = 0

    def check(self, informal_claim: str, lean_source: str, theorem_name: str) -> FaithfulnessVerdict:
        votes = [SingleVerdict(lens=l, faithful=self._faithful, issues=self._issues)
                 for l in DEFAULT_LENSES]
        return FaithfulnessVerdict(faithful=self._faithful, votes=votes,
                                   issues=[] if self._faithful else self._issues)
