"""Autoreason incumbent-tournament revision controller (training-free).

The literature is blunt that *naive* critique-and-revise actively DEGRADES proofs: the model deletes or
bloats content it cannot evaluate, and a "better-sounding but non-elementary" rewrite can displace a
correct elementary proof (synthesis drift). Autoreason's fix — adopted here — makes "do nothing"
first-class and only lets a challenger win if a blind judge panel prefers it by a margin.

Each pass:
  1. **Critic** does failure analysis on the incumbent (problems only, no fixes).
  2. **Author** revises the incumbent to address the critique  → candidate B.
  3. **Synthesizer** merges the incumbent and B (blind to which is which) → candidate AB.
  4. An **admissibility gate** (e.g. the elementary gate) drops any challenger that fails it, so a
     non-elementary "improvement" can never even enter the tournament.
  5. A **judge panel** (pairwise `Comparator`s) compares the surviving candidates round-robin. The
     DISPLACEMENT decision is a PAIRWISE head-to-head tally: each candidate's NET vote count against
     the incumbent (a +1 for each judge that prefers it to the incumbent, -1 for each that prefers the
     incumbent), NOT a Borda count over the whole field.
  6. A challenger displaces the incumbent only if it beats it **head-to-head by `margin`** net judge
     votes; ties go to the incumbent ("do nothing"). The loop stops when the incumbent survives
     `k_stop` consecutive passes (convergence) or the budget is exhausted.

The Bradley-Terry latent-strength fit (`set_ratings_from_bradley_terry`) and PUCT seed selection
(`select_puct`) used between passes are imported from the population SEARCH machinery
(`population.EloPopulation`); they only ORDER which surviving challenger to consider first and which
seed to revise next. They are NOT part of AutoReason's training-free critique-revise core — the
AutoReason paper disclaims voting / game-theoretic ranking machinery — so the DISPLACEMENT itself is
decided purely by the pairwise net-vote + margin rule above, never by a BT score.

This is the **synthesis-drift / elementary-incumbent guard**. The admitted output is the incumbent that
survived every pass, so refinement does not regress RELATIVE TO THE JUDGE PANEL + admissibility gate —
a challenger has to clear both to win. This is NOT an absolute no-regression guarantee: the panel may be
a single soft LLM judge, which could still prefer a worse (but admissible) rewrite; "monotone" holds
only with respect to that panel's preferences and the gate, not in any ground-truth sense. Deterministic
given the stubs + seed, so fully testable offline.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from random import Random
from typing import Callable, Optional, Protocol, runtime_checkable

from agent.orchestrator.population import Candidate, Comparator, EloPopulation
from agent.orchestrator.state import Budget
from agent.orchestrator.trace import RunTrace


@runtime_checkable
class RevisionCritic(Protocol):
    def critique(self, goal: str, incumbent: str) -> list[str]:
        """Failure analysis of the incumbent proof — list problems only, propose no fixes."""
        ...


@runtime_checkable
class RevisionAuthor(Protocol):
    def revise(self, goal: str, incumbent: str, critique: list[str]) -> str:
        """Return a revised proof addressing the critique (a fresh candidate)."""
        ...


@runtime_checkable
class Synthesizer(Protocol):
    def synthesize(self, goal: str, a: str, b: str) -> str:
        """Merge two candidate proofs into one (blind — must not assume which is the incumbent)."""
        ...


@dataclass
class RefineResult:
    content: str                       # the final incumbent (== input if nothing beat it)
    changed: bool
    passes: int                        # passes actually run
    displacements: int                 # times a challenger displaced the incumbent
    history: list[dict] = field(default_factory=list)


class RevisionController:
    """Run the incumbent tournament to refine a single proof artifact.

    No-regression is RELATIVE: the admitted output is the incumbent that survived every pass, so it
    never regresses with respect to the judge PANEL + the admissibility gate (a challenger must clear
    both to displace it). It is NOT an absolute guarantee — with a single soft judge the panel could
    prefer a worse-but-admissible rewrite."""

    def __init__(
        self,
        critic: RevisionCritic,
        author: RevisionAuthor,
        synthesizer: Synthesizer,
        judges: list[Comparator],
        *,
        max_passes: int = 8,
        k_stop: int = 2,
        margin: int = 1,
        c_explore: float = 1.4,
        budget: Optional[Budget] = None,
        trace: Optional[RunTrace] = None,
        seed: int = 0,
    ):
        if not judges:
            raise ValueError("RevisionController needs at least one judge")
        self.critic = critic
        self.author = author
        self.synthesizer = synthesizer
        self.judges = judges
        self.max_passes = max_passes
        self.k_stop = k_stop                  # consecutive incumbent wins → converged (Autoreason k=2)
        self.margin = margin                  # net head-to-head judge votes a challenger must clear
        self.c_explore = c_explore
        self.budget = budget
        self.trace = trace
        self._rng = Random(seed)

    # --- budget helpers (one model call per critic/author/synth/comparison) ---
    def _afford(self) -> bool:
        return self.budget is None or self.budget.can_call()

    def _spend(self) -> bool:
        if self.budget is None:
            return True
        if not self.budget.can_call():
            return False
        self.budget.spend_call()
        return True

    def refine(self, goal: str, incumbent: str,
               is_admissible: Optional[Callable[[str], bool]] = None) -> RefineResult:
        pop = EloPopulation()
        inc = pop.add(Candidate(id="rev0", content=incumbent, goal=goal))
        seed_cand = inc
        consec = 0
        displacements = 0
        history: list[dict] = []

        for p in range(self.max_passes):
            # A pass needs at least critic + author + synth before any judging.
            if not self._afford():
                break

            base = seed_cand.content
            if not self._spend():
                break
            critique = self.critic.critique(goal, base)
            if not self._spend():
                break
            cand_b = self.author.revise(goal, base, critique)
            if not self._spend():
                break
            cand_ab = self.synthesizer.synthesize(goal, base, cand_b)

            # Admissibility gate: a challenger that fails it cannot enter (elementary-incumbent guard).
            fresh = [c for c in (cand_b, cand_ab) if c and c.strip() and c != inc.content]
            admissible = [c for c in fresh if is_admissible is None or is_admissible(c)]
            # pop.add returns None if a (population-level) hard filter rejects a candidate; drop those
            # so new_cands holds only admitted candidates. (The refiner sets no hard filter today, so
            # this is purely defensive against a future one.)
            new_cands = [c for c in
                         (pop.add(Candidate(id=f"rev{len(pop.candidates)}", content=cc, goal=goal))
                          for cc in _dedupe(admissible))
                         if c is not None]

            if not new_cands:
                consec += 1
                history.append({"pass": p, "challengers": 0, "displaced": False, "reason": "no_admissible"})
                if self.trace:
                    self.trace.emit("revise_pass", goal=goal[:80], passno=p, challengers=0, displaced=False)
                if consec >= self.k_stop:
                    break
                seed_cand = pop.select_puct(self.c_explore) or inc
                continue

            # Judge panel → per-pass PAIRWISE net head-to-head votes vs the incumbent (the displacement
            # signal) plus the win matrix the population machinery uses for its own BT/PUCT bookkeeping.
            pool = [inc] + new_cands
            net, judged_fully = self._judge_round(pop, pool, inc)
            pop.set_ratings_from_bradley_terry()      # population-search BT ratings (used ONLY to order
                                                      # the iteration below; never the displacement test)

            # Displacement is decided by the PAIRWISE net-vote margin, NOT by the BT rating. The BT order
            # only breaks ties on WHICH qualifying challenger to promote first; a candidate is admitted
            # iff its net head-to-head vote vs the incumbent clears `margin`.
            displaced = False
            for c in sorted(new_cands, key=lambda c: c.rating, reverse=True):
                if net.get(c.id, 0) >= self.margin:
                    inc = c
                    displacements += 1
                    displaced = True
                    break

            history.append({"pass": p, "challengers": len(new_cands), "displaced": displaced})
            if self.trace:
                self.trace.emit("revise_pass", goal=goal[:80], passno=p,
                                challengers=len(new_cands), displaced=displaced)

            consec = 0 if displaced else consec + 1
            if consec >= self.k_stop or not judged_fully:
                break
            # PUCT chooses the seed to revise next (exploit strong + explore under-visited).
            seed_cand = pop.select_puct(self.c_explore) or inc

        return RefineResult(content=inc.content, changed=(inc.content != incumbent),
                            passes=len(history), displacements=displacements, history=history)

    def _judge_round(self, pop: EloPopulation, pool: list[Candidate],
                     incumbent: Candidate) -> tuple[dict[str, int], bool]:
        """Round-robin pairwise judging by each judge (blind orientation). Records every comparison
        into `pop` (feeding BT + Elo + PUCT) and returns the net head-to-head votes of each candidate
        against the incumbent. The bool is False if judging stopped early on the budget."""
        net = {c.id: 0 for c in pool}
        pairs = list(itertools.combinations(pool, 2))
        for judge in self.judges:
            self._rng.shuffle(pairs)
            for a, b in pairs:
                if not self._spend():
                    return net, False
                x, y = (a, b) if self._rng.random() < 0.5 else (b, a)   # blind orientation
                outcome = judge.compare(x, y)
                if outcome > 0:
                    pop.record(x, y, 1.0); win, lose = x, y
                elif outcome < 0:
                    pop.record(x, y, 0.0); win, lose = y, x
                else:
                    pop.record(x, y, 0.5); continue
                if lose is incumbent:
                    net[win.id] += 1
                elif win is incumbent:
                    net[lose.id] -= 1
        return net, True


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out


# --------------------------------------------------------------------------------------------------
# Scripted stubs (offline tests / demos).
# --------------------------------------------------------------------------------------------------

class ScriptedCritic:
    def __init__(self, critiques: list[list[str]]):
        self._c = critiques or [[]]
        self.calls = 0

    def critique(self, goal: str, incumbent: str) -> list[str]:
        idx = min(self.calls, len(self._c) - 1)
        self.calls += 1
        return self._c[idx]


class ScriptedAuthor:
    def __init__(self, revisions: list[str]):
        if not revisions:
            raise ValueError("ScriptedAuthor needs at least one revision")
        self._r = revisions
        self.calls = 0

    def revise(self, goal: str, incumbent: str, critique: list[str]) -> str:
        idx = min(self.calls, len(self._r) - 1)
        self.calls += 1
        return self._r[idx]


class ScriptedSynthesizer:
    def __init__(self, syntheses: list[str]):
        if not syntheses:
            raise ValueError("ScriptedSynthesizer needs at least one synthesis")
        self._s = syntheses
        self.calls = 0

    def synthesize(self, goal: str, a: str, b: str) -> str:
        idx = min(self.calls, len(self._s) - 1)
        self.calls += 1
        return self._s[idx]
