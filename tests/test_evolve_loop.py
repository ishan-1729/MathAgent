"""PROVE the OpenEvolve loop ACTUALLY EVOLVES — breadth exploration + real fitness selection.

These tests hit the REAL ``evolve_sketches`` / ``evolve_prove`` path (the genuine OpenEvolve
``run_evolution`` controller + MAP-Elites island database), NOT a stub of the loop. The LLM is the
only mocked piece: a :class:`StubEvolveLLM` plays the role of the breadth/depth model so the suite is
OFFLINE (no Claude subprocess), while the selection pressure is the REAL, deterministic
``score_ledger`` fitness oracle. That is the contract the directive demands: "a low-fitness seed
improves to a higher-fitness champion over generations under a mocked breadth model that proposes
better candidates, SELECTED by the real fitness oracle."

What each test pins:
  * ``test_population_evolves_low_seed_to_higher_champion`` — the headline proof: a low-fitness seed
    (goal-bound NEEDS_REVIEW) evolves to a strictly-higher-fitness PASSED champion. A no-op loop (the
    old unpicklable-closure-factory bug, or a parse crash that drops every mutation) would leave the
    champion stuck at the seed's score.
  * ``test_fitness_oracle_selects_best_of_diverse_candidates`` — breadth proposes MANY DIVERSE
    candidates of VARYING quality (an off-goal HARD-ZERO, a trivial PASSED, an obligation-discharging
    PASSED). The REAL fitness oracle SELECTS the obligation-discharging one as champion — it does NOT
    settle for the off-goal candidate (which the HARD goal-binding gate zeroes) nor the weaker trivial
    PASSED. This is the elementarity-aware selection the antidote relies on.
  * ``test_off_goal_only_breadth_yields_no_winning_champion`` — if EVERY proposed candidate is off-goal
    (HARD zero), the loop cannot manufacture a goal-bound champion: ``evolve_prove`` reports
    ``accepted is False``. The fitness gate is a real filter, not a rubber stamp.
  * ``test_evolve_prove_accepts_goal_bound_passed_champion`` — the FIRST-CLASS acceptance bar: a
    goal-bound PASSED champion (fitness well below 1.0) is ACCEPTED, where the old ``== 1.0`` gate would
    have discarded it and degraded ``--evolve`` to a fallback-only no-op.

The whole module is opt-in on the package being importable (``pytest.importorskip('openevolve')``); the
offline gate-scoring invariants live in test_evolve_fitness.py / test_openevolve_bridge.py.
"""
import json

import pytest

from agent.tools.openevolve_bridge import (
    NEEDS_REVIEW_FLOOR,
    PASSED_FLOOR,
    StubEvolveLLM,
    binds_to_goal,
    evolve_prove,
    evolve_sketches,
    score_ledger,
)
from agent.gates.ledger import parse_ledger

GOAL = "A demo theorem."


# --------------------------------------------------------------------------------------------------
# Candidate ledger builders spanning the fitness bands the real oracle must rank.
# --------------------------------------------------------------------------------------------------
def _needs_review_seed(goal: str = GOAL) -> str:
    """Goal-bound but NEEDS_REVIEW (a ``factorization`` step routes to review) — a LOW-fitness seed."""
    return json.dumps({
        "problem": "demo",
        "claim": goal,
        "steps": [
            {"id": "s1", "claim": "A hypothesis.", "justification": "given", "depends_on": []},
            {"id": "s2", "claim": "A factorization holds.", "justification": "factorization",
             "depends_on": ["s1"]},
            {"id": "s3", "claim": goal, "justification": "conclusion", "depends_on": ["s2"]},
        ],
    })


def _off_goal_candidate() -> str:
    """A structurally-clean ledger that proves a DIFFERENT statement — HARD goal-binding zero (0.0)."""
    return json.dumps({
        "problem": "demo",
        "claim": "A different, unrequested statement.",
        "steps": [
            {"id": "s1", "claim": "A hypothesis.", "justification": "given", "depends_on": []},
            {"id": "s2", "claim": "A different, unrequested statement.",
             "justification": "conclusion", "depends_on": ["s1"]},
        ],
    })


def _trivial_passed_candidate(goal: str = GOAL) -> str:
    """Goal-bound PASSED with NO discharged obligation — clears the band but scores below a discharge."""
    return json.dumps({
        "problem": "demo",
        "claim": goal,
        "steps": [
            {"id": "s1", "claim": "A hypothesis.", "justification": "given", "depends_on": []},
            {"id": "s2", "claim": goal, "justification": "conclusion", "depends_on": ["s1"]},
        ],
    })


def _discharged_passed_candidate(goal: str = GOAL) -> str:
    """Goal-bound PASSED that DISCHARGES a real numeric obligation (complete case-cover mod 4).

    This is the elementary, obligation-grounded champion the fitness oracle should prefer: its
    discharged-content ratio lifts it strictly ABOVE the trivial PASSED candidate.
    """
    return json.dumps({
        "problem": "demo",
        "claim": goal,
        "steps": [
            {"id": "s1", "claim": "A hypothesis.", "justification": "given", "depends_on": []},
            {"id": "cs", "claim": "Split modulo 4 over a complete residue system.",
             "justification": "case_split", "depends_on": ["s1"],
             "obligations": {"case_cover": {"modulus": 4, "residues": [0, 1, 2, 3]}}},
            {"id": "s2", "claim": goal, "justification": "conclusion", "depends_on": ["cs"]},
        ],
    })


# --------------------------------------------------------------------------------------------------
# Preconditions: the builders land in the bands the loop tests rely on (pure, no evolution).
# --------------------------------------------------------------------------------------------------
def test_candidate_band_preconditions(toolkit):
    seed = score_ledger(_needs_review_seed(), toolkit, goal=GOAL)["combined_score"]
    off = score_ledger(_off_goal_candidate(), toolkit, goal=GOAL)["combined_score"]
    trivial = score_ledger(_trivial_passed_candidate(), toolkit, goal=GOAL)["combined_score"]
    discharged = score_ledger(_discharged_passed_candidate(), toolkit, goal=GOAL)["combined_score"]

    assert NEEDS_REVIEW_FLOOR <= seed < PASSED_FLOOR        # seed is a real LOW-fitness start
    assert off == 0.0                                       # off-goal is a HARD zero
    assert PASSED_FLOOR <= trivial < discharged <= 1.0      # discharge strictly out-ranks trivial PASSED


# --------------------------------------------------------------------------------------------------
# THE HEADLINE PROOF: a low-fitness seed evolves to a higher-fitness champion (real loop, real oracle).
# --------------------------------------------------------------------------------------------------
def test_population_evolves_low_seed_to_higher_champion(toolkit):
    """A low-fitness seed must EVOLVE to a strictly-higher-fitness champion over generations.

    The breadth model (mocked) proposes a better candidate; the REAL OpenEvolve controller mutates the
    population and the REAL fitness oracle selects. If the loop were a no-op (no iteration ever ran), the
    champion would still score the seed's NEEDS_REVIEW fitness. Reaching the PASSED band — strictly above
    the seed — proves the population genuinely evolved under real selection.
    """
    pytest.importorskip("openevolve", reason="pip install mathagent[evolve] to run the live loop")

    seed = _needs_review_seed()
    seed_score = score_ledger(seed, toolkit, goal=GOAL)["combined_score"]
    assert NEEDS_REVIEW_FLOOR <= seed_score < PASSED_FLOOR  # precondition: the seed is genuinely low

    # Breadth proposes the obligation-discharging PASSED ledger as its mutation.
    breadth = StubEvolveLLM([_discharged_passed_candidate()])
    champ_text, champ_fit, metrics = evolve_sketches(
        GOAL, toolkit, llm=breadth, iterations=6, seed_sketches=[seed], num_islands=3,
    )

    assert champ_fit >= PASSED_FLOOR, (
        f"the population did NOT evolve the seed (champion fitness={champ_fit}, seed={seed_score}); "
        "a no-op loop would leave the champion at the seed's NEEDS_REVIEW score")
    assert champ_fit > seed_score                            # strict improvement over generations
    assert metrics.get("obligations_discharged", 0.0) >= 1.0  # the champion discharges a real obligation
    led = parse_ledger(champ_text)
    assert binds_to_goal(led, GOAL)                          # and is goal-bound (cleared the HARD gate)


def test_fitness_oracle_selects_best_of_diverse_candidates(toolkit):
    """Breadth proposes DIVERSE candidates of varying quality; the oracle SELECTS the best (discharge).

    The mocked breadth model cycles three candidates: an off-goal HARD-ZERO, a trivial goal-bound
    PASSED, and an obligation-discharging goal-bound PASSED. Over multiple generations across islands the
    REAL fitness oracle must crown the obligation-discharging ledger — it neither settles for the
    off-goal candidate (zeroed by the HARD binding gate) nor the weaker trivial PASSED. This is the
    elementarity-aware breadth-selection the directive calls the antidote to grabbing a non-elementary
    shortcut.
    """
    pytest.importorskip("openevolve", reason="pip install mathagent[evolve] to run the live loop")

    discharged_score = score_ledger(_discharged_passed_candidate(), toolkit, goal=GOAL)["combined_score"]
    trivial_score = score_ledger(_trivial_passed_candidate(), toolkit, goal=GOAL)["combined_score"]

    breadth = StubEvolveLLM([
        _off_goal_candidate(),          # 0.0 — must be rejected by the oracle
        _trivial_passed_candidate(),    # PASSED but weaker
        _discharged_passed_candidate(), # PASSED + discharge — the elementary winner
    ])
    champ_text, champ_fit, metrics = evolve_sketches(
        GOAL, toolkit, llm=breadth, iterations=12, seed_sketches=[_needs_review_seed()], num_islands=3,
    )

    # The champion is the obligation-discharging candidate: top-band fitness, a discharged obligation,
    # and strictly above the trivial PASSED — i.e. the oracle SELECTED on elementary content, not luck.
    assert champ_fit == pytest.approx(discharged_score)
    assert champ_fit > trivial_score
    assert metrics.get("obligations_discharged", 0.0) >= 1.0
    assert "case_cover" in champ_text                       # the discharging candidate, not the trivial one


def test_off_goal_only_breadth_yields_no_winning_champion(toolkit):
    """If EVERY proposed candidate is off-goal, the loop cannot mint a goal-bound champion.

    The HARD goal-binding gate zeroes every off-goal candidate, so the seed (a goal-bound NEEDS_REVIEW)
    out-scores them all and remains the best the population can reach: ``evolve_prove`` reports the
    champion is NOT accepted. This proves the fitness gate is a real selection filter — a degenerate
    breadth model cannot smuggle an off-goal 'win' into the archive.
    """
    pytest.importorskip("openevolve", reason="pip install mathagent[evolve] to run the live loop")

    breadth = StubEvolveLLM([_off_goal_candidate()])
    champ = evolve_prove(
        GOAL, toolkit, llm=breadth, iterations=5, seed_sketches=[_needs_review_seed()], num_islands=2,
    )
    # No off-goal candidate can be accepted: it never clears the goal-bound PASSED bar.
    assert champ.accepted is False
    # The best the population holds is the goal-bound seed (NEEDS_REVIEW) — never an off-goal HARD zero.
    assert champ.fitness < PASSED_FLOOR
    assert champ.goal_bound is True                          # the seed itself binds; the off-goal mutants do not


def test_evolve_prove_accepts_goal_bound_passed_champion(toolkit):
    """FIRST-CLASS acceptance: a goal-bound PASSED champion (fitness < 1.0) is ACCEPTED.

    The HARD-gated band caps a genuine PASSED ledger well below 1.0 (here ~0.815). The old ``--evolve``
    gate required ``fitness == 1.0`` and so discarded EVERY real champion — a fallback-only no-op.
    ``evolve_prove`` accepts on goal-bound + PASSED instead, so the champion is handed to the DAG/Lean.
    """
    pytest.importorskip("openevolve", reason="pip install mathagent[evolve] to run the live loop")

    breadth = StubEvolveLLM([_discharged_passed_candidate()])
    champ = evolve_prove(
        GOAL, toolkit, llm=breadth, iterations=5, seed_sketches=[_needs_review_seed()], num_islands=2,
    )
    assert champ.goal_bound and champ.passed and champ.accepted
    assert PASSED_FLOOR <= champ.fitness < 1.0               # accepted DESPITE not reaching 1.0
    # The accepted ledger re-parses and binds to the goal — ready for DAG + Lean verification.
    assert binds_to_goal(parse_ledger(champ.ledger), GOAL)
