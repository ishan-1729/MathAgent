"""Population / Elo search over candidate sketches (AlphaProof_Nexus / AlphaEvolve).

When several candidate decompositions (or proof sketches) compete for a goal, we don't know a priori
which to expand. AlphaProof_Nexus ranks *incomplete* sketches by a latent strength estimated from
cheap pairwise LLM-judge comparisons, then resamples promising ones. This module provides that layer:

  - `Candidate`        : a competitor (a sketch + its children), with an Elo rating + visit stats.
  - `Comparator`       : judges which of two candidates is more promising (pairwise).
  - `EloPopulation`    : runs a comparison tournament, maintains Elo ratings, ranks, and supports
                         PUCT selection (exploit rating + explore under-visited candidates).
  - `fit_bradley_terry`: a Bradley-Terry MLE (MM algorithm) for latent strengths from a win matrix —
                         the Plackett-Luce analog, more stable than incremental Elo for a batch.

All deterministic given the comparator and pairing order (default: round-robin), so it is fully
testable offline with a scripted/keyed comparator; live comparators are resolved from the run profile.

.. note::

    **NOT evolutionary search — it is a one-shot ranker (be honest about the label).** Despite the
    "population" name and the AlphaEvolve attribution above, this module does **not** evolve a
    population: there is **no mutation, no crossover, and no persisted cross-episode population DB**.
    Within a *single* `decompose` call it ranks a fixed pool of K candidate sketches via cheap
    pairwise comparisons (Elo / Bradley-Terry) and picks one to expand (PUCT). That is **rank-K-then-
    pick**, not generational evolution — the pool is never mutated and is discarded when the call
    returns; nothing carries across episodes. (The one path in the project that genuinely *mutates*
    a population is the optional OpenEvolve backend in `tools/openevolve_bridge.py`, which is a
    separate component.) BT/PUCT here only reorder candidates the deterministic gate already
    admitted; they never change *whether* a proof is accepted (that is Layer 4's job).
"""
from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol, runtime_checkable

DEFAULT_RATING = 1500.0
DEFAULT_K = 32.0


@dataclass
class Candidate:
    id: str
    content: str                       # the sketch ledger text
    goal: str = ""
    children: list[str] = field(default_factory=list)
    rating: float = DEFAULT_RATING
    visits: int = 0
    wins: float = 0.0
    games: int = 0
    alive: bool = True
    meta: dict = field(default_factory=dict)


@runtime_checkable
class Comparator(Protocol):
    def compare(self, a: Candidate, b: Candidate) -> int:
        """Return 1 if a is more promising, -1 if b is, 0 for a tie."""
        ...


class KeyComparator:
    """Deterministic comparator by a numeric key (higher = better). For tests/offline use."""

    def __init__(self, key: Callable[[Candidate], float]):
        self.key = key
        self.calls = 0

    def compare(self, a: Candidate, b: Candidate) -> int:
        self.calls += 1
        ka, kb = self.key(a), self.key(b)
        return (ka > kb) - (ka < kb)


class ScriptedComparator:
    """Returns a fixed sequence of outcomes (1/-1/0). Repeats the last when exhausted."""

    def __init__(self, outcomes: list[int]):
        if not outcomes:
            raise ValueError("ScriptedComparator needs at least one outcome")
        self._outcomes = outcomes
        self.calls = 0

    def compare(self, a: Candidate, b: Candidate) -> int:
        idx = min(self.calls, len(self._outcomes) - 1)
        self.calls += 1
        return self._outcomes[idx]


def _expected(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((rb - ra) / 400.0))


class EloPopulation:
    def __init__(self, k: float = DEFAULT_K, seed: int = 0,
                 hard_filter: Optional[Callable[[Candidate], bool]] = None):
        self.candidates: list[Candidate] = []
        self.k = k
        self._rng = random.Random(seed)
        self._win_matrix: dict[tuple[str, str], int] = {}
        # HARD PRE-GATE (P3 / openevolve_stacking_brief §9 #5): a deterministic filter (goal-binding +
        # obligation-discharge) every candidate must pass BEFORE it is admitted to the ranking. A
        # candidate failing the hard filter is NEVER added, so the Elo/BT/PUCT machinery — which is
        # UNCHANGED — never sees, ranks, or selects a gameable candidate. None => admit everything
        # (back-compat). Rejected candidates are counted for observability.
        self.hard_filter = hard_filter
        self.rejected_by_filter = 0

    def admits(self, c: Candidate) -> bool:
        """True iff candidate ``c`` passes the hard pre-gate (always True when no filter is set)."""
        if self.hard_filter is None:
            return True
        try:
            return bool(self.hard_filter(c))
        except Exception:
            # Fail closed: a hard filter that errors on a candidate REJECTS it (never admits a
            # candidate the deterministic gate could not vouch for).
            return False

    def add(self, c: Candidate) -> Optional[Candidate]:
        """Admit ``c`` to the population IFF it passes the hard pre-gate; else drop it (returns None).

        The hard filter runs FIRST, before any rating/visit/comparison touches the candidate, so a
        gate-gaming (off-goal / obligation-undischarged) candidate can never enter the tournament or be
        selected. With no filter this is the original unconditional append."""
        if not self.admits(c):
            self.rejected_by_filter += 1
            return None
        self.candidates.append(c)
        return c

    @property
    def win_matrix(self) -> dict[tuple[str, str], int]:
        """A copy of the accumulated pairwise win counts (`[(i, j)] = times i beat j`)."""
        return dict(self._win_matrix)

    def set_ratings_from_bradley_terry(self, iters: int = 200) -> dict[str, float]:
        """Refit ratings from the cumulative win matrix via Bradley-Terry (a stable batch estimate
        over noisy online Elo) and write the latent strengths onto the candidates. Returns them."""
        ids = [c.id for c in self.candidates]
        strengths = fit_bradley_terry(ids, self._win_matrix, iters=iters)
        for c in self.candidates:
            if c.id in strengths:
                c.rating = strengths[c.id]
        return strengths

    # --- Elo updates from a single comparison ---
    def record(self, a: Candidate, b: Candidate, outcome_a: float) -> None:
        """outcome_a in {1.0 win, 0.5 tie, 0.0 loss} for candidate a."""
        ea = _expected(a.rating, b.rating)
        a.rating += self.k * (outcome_a - ea)
        b.rating += self.k * ((1.0 - outcome_a) - (1.0 - ea))
        a.games += 1
        b.games += 1
        a.wins += outcome_a
        b.wins += 1.0 - outcome_a
        if outcome_a == 1.0:
            self._win_matrix[(a.id, b.id)] = self._win_matrix.get((a.id, b.id), 0) + 1
        elif outcome_a == 0.0:
            self._win_matrix[(b.id, a.id)] = self._win_matrix.get((b.id, a.id), 0) + 1

    def _pairs(self, max_comparisons: Optional[int]):
        alive = [c for c in self.candidates if c.alive]
        pairs = list(itertools.combinations(alive, 2))
        if max_comparisons is not None and len(pairs) > max_comparisons:
            self._rng.shuffle(pairs)
            pairs = pairs[:max_comparisons]
        return pairs

    def tournament(self, comparator: Comparator, rounds: int = 1,
                   max_comparisons: Optional[int] = None,
                   budget_ok: Optional[Callable[[], bool]] = None,
                   trace: Optional[object] = None) -> int:
        """Round-robin pairwise comparisons (×rounds), updating Elo. Returns #comparisons made.

        `budget_ok` (if given) is checked before each comparison; comparison stops when it returns
        False, so the tournament respects a shared call budget.

        ROBUSTNESS (R-ROBUST): the comparator is a live model call (Codex/Claude CLI) and can RAISE
        (subprocess timeout, non-zero exit, malformed output). A single raised comparison MUST NOT
        crash the whole tournament: a raising `comparator.compare()` is treated as a TIE (outcome 0)
        for that pairwise comparison, so the Elo/Bradley-Terry ranking degrades gracefully (one less
        informative pairing) instead of aborting the population search. The budget for the failed
        comparison is already spent (via `budget_ok`), so the loop stays bounded and still terminates.
        The exception is surfaced on the (optional) `trace`, never silently swallowed.
        """
        made = 0
        for _ in range(rounds):
            for a, b in self._pairs(max_comparisons):
                if budget_ok is not None and not budget_ok():
                    return made
                try:
                    outcome = comparator.compare(a, b)
                except Exception as e:
                    # Degrade a raising comparison to a TIE so the tournament continues (never crashes).
                    if trace is not None:
                        trace.emit("comparator_error", a=a.id, b=b.id,
                                   error_type=type(e).__name__, detail=str(e)[:160])
                    outcome = 0
                made += 1
                if outcome > 0:
                    self.record(a, b, 1.0)
                elif outcome < 0:
                    self.record(a, b, 0.0)
                else:
                    self.record(a, b, 0.5)
        return made

    # --- selection ---
    def ranking(self) -> list[Candidate]:
        return sorted((c for c in self.candidates if c.alive), key=lambda c: c.rating, reverse=True)

    def best(self) -> Optional[Candidate]:
        r = self.ranking()
        return r[0] if r else None

    def select_puct(self, c_explore: float = 1.4) -> Optional[Candidate]:
        """Pick a candidate balancing rating (exploit) and low visit count (explore), then visit it."""
        alive = [c for c in self.candidates if c.alive]
        if not alive:
            return None
        ratings = [c.rating for c in alive]
        lo, hi = min(ratings), max(ratings)
        span = (hi - lo) or 1.0
        total_visits = sum(c.visits for c in alive)
        logN = math.log(total_visits + 1.0)
        # softmax priors over (normalized) ratings
        exps = [math.exp((c.rating - hi) / (span)) for c in alive]
        z = sum(exps) or 1.0
        priors = [e / z for e in exps]

        def score(c: Candidate, p: float) -> float:
            q = (c.rating - lo) / span
            return q + c_explore * p * math.sqrt(logN) / (1.0 + c.visits)

        chosen = max(zip(alive, priors), key=lambda cp: score(cp[0], cp[1]))[0]
        chosen.visits += 1
        return chosen


def fit_bradley_terry(ids: list[str], win_matrix: dict[tuple[str, str], int],
                      iters: int = 200, tol: float = 1e-9) -> dict[str, float]:
    """Bradley-Terry latent strengths from a win matrix via the MM (minorization-maximization)
    algorithm. `win_matrix[(i, j)]` = times i beat j. Returns normalized strengths (sum = n)."""
    n = len(ids)
    if n == 0:
        return {}
    p = {i: 1.0 for i in ids}
    wins = {i: 0 for i in ids}
    for (i, j), c in win_matrix.items():
        wins[i] = wins.get(i, 0) + c
    games = {i: 0 for i in ids}
    for (i, j), c in win_matrix.items():
        games[i] = games.get(i, 0) + c
        games[j] = games.get(j, 0) + c

    for _ in range(iters):
        new = {}
        for i in ids:
            denom = 0.0
            for j in ids:
                if i == j:
                    continue
                nij = win_matrix.get((i, j), 0) + win_matrix.get((j, i), 0)
                if nij:
                    denom += nij / (p[i] + p[j])
            new[i] = (wins[i] / denom) if denom > 0 else p[i]
        # normalize to mean 1 (geometric-ish stabilization)
        s = sum(new.values()) or 1.0
        new = {i: v * n / s for i, v in new.items()}
        if max(abs(new[i] - p[i]) for i in ids) < tol:
            p = new
            break
        p = new
    return p
