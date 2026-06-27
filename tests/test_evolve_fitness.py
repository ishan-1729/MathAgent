"""Reward-hacking regression tests for the HARDENED OpenEvolve fitness (T101 P0+P1).

These pin the "don't optimise a gameable signal" invariant as EXECUTABLE tests rather than prose
(research/docs/openevolve_stacking_brief.md §9). They assert the load-bearing properties of
``agent.tools.openevolve_bridge.score_ledger`` and the numeric witness-spec checker:

  * HARD goal-binding gate ZEROES the fitness of an off-goal / claim-weakened / vacuous candidate
    (deterministic ``goal_hash`` equality on claim AND conclusion — never an LLM vote).
  * NEEDS_REVIEW (the 0.5 band) is NOT a win: it scores strictly below ANY goal-bound PASSED.
  * The §3 Ljunggren trap — relabel a genuinely-hard step under an ALLOWED method
    (``vierzahlensatz``) WITHOUT discharging its obligation — must NOT raise fitness above a real
    goal-bound, obligation-discharging ledger.
  * The evaluator NEVER executes a 'poison' claim string (it only READS + gates).
  * The numeric witness mode is scored ONLY by the exact-integer checker and never eval/exec's a spec.
"""
import json

import pytest

from agent.gates.toolkit import load_toolkit
from agent.tools.openevolve_bridge import (
    NEEDS_REVIEW_FLOOR,
    PASSED_FLOOR,
    binds_to_goal,
    gate_ledger_evaluator,
    is_vacuous,
    score_ledger,
    score_witness_spec,
)
from agent.gates.ledger import parse_ledger


GOAL = "The only integer solution of x^2 + 1 = 2 y^4 is (x, y) = (1, 1)."


@pytest.fixture(scope="module")
def tk():
    return load_toolkit()


def _score(led: dict, tk, goal=GOAL) -> float:
    return score_ledger(json.dumps(led), tk, goal=goal)["combined_score"]


# --------------------------------------------------------------------------------------------------
# Ledger builders: the canonical traps + the honest baseline.
# --------------------------------------------------------------------------------------------------
def _honest_obligation_ledger() -> dict:
    """Goal-bound, PASSED, and DISCHARGES a real numeric obligation (a complete case-cover mod 4)."""
    return {
        "problem": "ljunggren",
        "claim": GOAL,
        "steps": [
            {"id": "h", "claim": "x^2 + 1 = 2 y^4.", "justification": "given", "depends_on": []},
            {"id": "cs", "claim": "Split y modulo 4 over a complete residue system.",
             "justification": "case_split", "depends_on": ["h"],
             "obligations": {"case_cover": {"modulus": 4, "residues": [0, 1, 2, 3]}}},
            {"id": "c", "claim": GOAL, "justification": "conclusion", "depends_on": ["cs"]},
        ],
    }


def _ljunggren_relabel_trap() -> dict:
    """The §3 trap: the hard crux is relabeled under the ALLOWED 'vierzahlensatz' tag, undischarged.

    Goal-bound and gates PASSED_DETERMINISTIC (the soft gate is fooled by the relabel), but the crux
    asserts a genuinely-hard Pell/quartic fact with NO obligation backing -> assertion debt.
    """
    return {
        "problem": "ljunggren",
        "claim": GOAL,
        "steps": [
            {"id": "h", "claim": "x^2 + 1 = 2 y^4.", "justification": "given", "depends_on": []},
            {"id": "crux",
             "claim": "The only square in this Pell denominator sequence is the trivial one.",
             "justification": "vierzahlensatz", "depends_on": ["h"]},
            {"id": "c", "claim": GOAL, "justification": "conclusion", "depends_on": ["crux"]},
        ],
    }


def _claim_weakened_offgoal() -> dict:
    """Claim/conclusion drifted to a WEAKER, off-goal statement that still passes the structural gate."""
    weak = "x is an integer."
    return {
        "problem": "ljunggren",
        "claim": GOAL,  # advertises the goal...
        "steps": [
            {"id": "h", "claim": weak, "justification": "given", "depends_on": []},
            # ...but the terminal conclusion proves only the weak statement (off-goal).
            {"id": "c", "claim": weak, "justification": "conclusion", "depends_on": ["h"]},
        ],
    }


def _vacuous_restatement() -> dict:
    """The conclusion is a trivial restatement of a hypothesis (proves nothing new)."""
    h = "x^2 + 1 = 2 y^4 has a solution."
    return {
        "problem": "ljunggren",
        "claim": h,
        "steps": [
            {"id": "h", "claim": h, "justification": "given", "depends_on": []},
            {"id": "c", "claim": h, "justification": "conclusion", "depends_on": ["h"]},
        ],
    }


# --------------------------------------------------------------------------------------------------
# THE reward-hacking trap: relabel-the-hard-theorem must not out-score honest discharge.
# --------------------------------------------------------------------------------------------------
def test_ljunggren_relabel_trap_does_not_beat_honest_discharge(tk):
    honest = _score(_honest_obligation_ledger(), tk)
    trap = _score(_ljunggren_relabel_trap(), tk)
    # The honest obligation-discharging ledger is goal-bound + PASSED, in the PASSED band.
    assert honest >= PASSED_FLOOR
    # R5 (Fix 3): the relabel-the-hard-fact trap rests its conclusion on a bare ``vierzahlensatz``
    # crux that discharges NOTHING and is not re-checkable offline — a non-anchor un-grounded crux. The
    # structural cap routes it to the NEEDS_REVIEW band (a Layer-4 candidate, not a top-band winner).
    # This is the CORRECT vierzahlensatz-trap semantics: a relabel that discharges nothing is at most a
    # NEEDS_REVIEW candidate, NEVER a soft-PASSED win.
    assert trap < PASSED_FLOOR, f"relabel trap ({trap}) reached the PASSED band"
    # ...and a fortiori it does not out-score the honest obligation-discharging ledger.
    assert trap < honest, f"relabel trap ({trap}) out-scored honest discharge ({honest})"


def test_relabel_under_allowed_method_adds_obligation_debt(tk):
    metrics = score_ledger(json.dumps(_ljunggren_relabel_trap()), tk, goal=GOAL)
    # The un-grounded 'vierzahlensatz' crux is counted as unresolved obligation debt even though the
    # structural gate passes it (its tag is allowed and it requires no typed obligation key).
    assert metrics["obligation_debt"] >= 1.0
    assert metrics["obligations_discharged"] == 0.0


# --------------------------------------------------------------------------------------------------
# THE STRONGEST HOLE (R2 #1): a crux RELABELED under a generic obligation:none tag (algebra /
# divisibility / congruence / ...) must NOT evade the assertion-debt penalty. The old defense was an
# ALLOWLIST of named hard methods (vierzahlensatz/pell/...); relabeling under 'algebra' carried ZERO
# debt, passed binds_to_goal, was non-vacuous, and landed in the PASSED band WITH a subgoal-depth
# bonus — so a fake all-'algebra' chain BEAT honest discharge and DEEPER fakes scored HIGHER. The
# defense is now a STRUCTURAL un-grounded-crux detector, asserted by these tests.
# --------------------------------------------------------------------------------------------------
def _goal_bound_all_tag_chain(tag: str, depth: int) -> dict:
    """A goal-bound, PASSED chain of ``depth`` non-trivial ``tag`` cruxes, each descending only from a
    'given' premise (NO discharged obligation anywhere). Every crux is an un-grounded standalone claim."""
    steps = [{"id": "h", "claim": "x^2 + 1 = 2 y^4.", "justification": "given", "depends_on": []}]
    prev = "h"
    for i in range(depth):
        steps.append({"id": f"a{i}", "claim": f"Crux number {i} holds nontrivially.",
                      "justification": tag, "depends_on": [prev]})
        prev = f"a{i}"
    steps.append({"id": "c", "claim": GOAL, "justification": "conclusion", "depends_on": [prev]})
    return {"problem": "lj", "claim": GOAL, "steps": steps}


@pytest.mark.parametrize("tag", ["algebra", "divisibility", "congruence"])
def test_relabel_under_generic_obligation_none_tag_scores_below_honest(tk, tag):
    # A goal-bound all-<tag> chain (the relabel-under-algebra hole) must score STRICTLY BELOW an honest
    # obligation-discharging ledger. Without the structural detector this fake out-scored honest
    # discharge; with it, every un-grounded crux is assertion debt.
    honest = _score(_honest_obligation_ledger(), tk)
    for depth in (1, 3, 6):
        fake = _score(_goal_bound_all_tag_chain(tag, depth), tk)
        assert fake < honest, (
            f"all-{tag} depth={depth} fake ({fake}) did not score below honest discharge ({honest})")


@pytest.mark.parametrize("tag", ["algebra", "divisibility", "congruence"])
def test_deeper_fake_does_not_outscore_shallower_fake(tk, tag):
    # Padding the fake chain with MORE un-grounded cruxes must NOT raise its score: a deeper fake must
    # not out-score a shallower fake (the subgoal-depth bonus no longer rewards un-grounded padding,
    # and each extra crux only adds debt). Monotone NON-increasing in depth.
    scores = [_score(_goal_bound_all_tag_chain(tag, d), tk) for d in (1, 3, 6, 10)]
    for shallow, deep in zip(scores, scores[1:]):
        assert deep <= shallow, f"a deeper all-{tag} fake out-scored a shallower one: {scores}"


def test_relabel_chain_accrues_assertion_debt_per_crux(tk):
    # Each un-grounded obligation:none crux is charged assertion debt regardless of its (generic) tag.
    m = score_ledger(json.dumps(_goal_bound_all_tag_chain("algebra", 4)), tk, goal=GOAL)
    assert m["obligation_debt"] >= 4.0       # one per un-grounded crux
    assert m["obligations_discharged"] == 0.0


def test_grounded_elaboration_after_discharge_is_not_debt(tk):
    # An obligation:none step DOWNSTREAM of a discharged typed obligation is content-grounded (its
    # content leans on the re-checkable case_cover), so it is NOT charged assertion debt, and the
    # grounded ledger out-ranks the bare honest one (genuine elaboration is rewarded, not penalised).
    grounded = {
        "problem": "lj", "claim": GOAL,
        "steps": [
            {"id": "h", "claim": "x^2 + 1 = 2 y^4.", "justification": "given", "depends_on": []},
            {"id": "cs", "claim": "Split y modulo 4 over a complete residue system.",
             "justification": "case_split", "depends_on": ["h"],
             "obligations": {"case_cover": {"modulus": 4, "residues": [0, 1, 2, 3]}}},
            {"id": "a", "claim": "Each residue class forces y=1 by direct computation.",
             "justification": "algebra", "depends_on": ["cs"]},
            {"id": "c", "claim": GOAL, "justification": "conclusion", "depends_on": ["a"]},
        ],
    }
    m = score_ledger(json.dumps(grounded), tk, goal=GOAL)
    assert m["obligation_debt"] == 0.0
    assert m["obligations_discharged"] >= 1.0
    assert m["combined_score"] >= _score(_honest_obligation_ledger(), tk)


def test_obligation_hiding_lemma_is_debt_not_free(tk):
    # A ledger that hides the hard work behind an undischarged cited 'lemma' must not out-score one
    # that actually discharges a numeric obligation (a cited lemma is deferred = debt here).
    lemma_hider = {
        "problem": "ljunggren", "claim": GOAL,
        "steps": [
            {"id": "h", "claim": "x^2 + 1 = 2 y^4.", "justification": "given", "depends_on": []},
            {"id": "l", "claim": "The crux Pell fact holds.", "justification": "lemma",
             "depends_on": ["h"]},
            {"id": "c", "claim": GOAL, "justification": "conclusion", "depends_on": ["l"]},
        ],
    }
    assert _score(lemma_hider, tk) < _score(_honest_obligation_ledger(), tk)


# --------------------------------------------------------------------------------------------------
# R3 — THE CENTRAL ANTI-LAUNDERING INVARIANT (V6). The R2 grounding walk grounded a step whenever ANY
# discharged obligation appeared ANYWHERE in its TRANSITIVE closure, so a single trivially-true,
# GOAL-IRRELEVANT case_cover{mod2,[0,1]} LAUNDERED an arbitrarily long chain of fake 'algebra' cruxes
# to debt 0 — the laundered fake BEAT honest discharge and DEEPER fakes scored HIGHER. V6 makes
# grounding LOCAL + RELEVANT (a crux is grounded only by a NON-VACUOUS obligation it DIRECTLY depends
# on) and charges debt PER un-grounded crux, so no single anchor can zero them all. These pin the fix
# against the LIVE score_ledger, reproducing the auditor's numbers.
# --------------------------------------------------------------------------------------------------
def _laundered_chain(n_fakes: int) -> dict:
    """ONE trivial, goal-irrelevant case_cover{mod2,[0,1]} + a chain of ``n_fakes`` fake 'algebra'
    cruxes. The R2 defect grounded the WHOLE chain off the single cover (debt 0); V6 grounds none."""
    steps = [
        {"id": "h", "claim": "x^2 + 1 = 2 y^4.", "justification": "given", "depends_on": []},
        {"id": "cc", "claim": "Cases mod 2 are exhaustive.", "justification": "case_split",
         "depends_on": ["h"], "obligations": {"case_cover": {"modulus": 2, "residues": [0, 1]}}},
    ]
    prev = "cc"
    for i in range(n_fakes):
        steps.append({"id": f"f{i}", "claim": f"Fake algebra crux {i} asserting new content.",
                      "justification": "algebra", "depends_on": [prev]})
        prev = f"f{i}"
    steps.append({"id": "c", "claim": GOAL, "justification": "conclusion", "depends_on": [prev]})
    return {"problem": "lj", "claim": GOAL, "steps": steps}


def test_laundered_chain_scores_strictly_below_honest_discharge(tk):
    # The auditor's primary attack: 1 trivial case_cover + N fake 'algebra' cruxes. For EVERY N with at
    # least one fake (up to and beyond the depth cap) the laundered chain must score STRICTLY BELOW an
    # honest obligation-discharging ledger (R2: it scored ~0.83-0.865 > honest ~0.815 and rose with N).
    honest = _score(_honest_obligation_ledger(), tk)
    for n in range(1, 9):
        fake = _score(_laundered_chain(n), tk)
        assert fake < honest, f"laundered chain N={n} ({fake}) did not score below honest ({honest})"


def test_laundered_chain_charges_debt_per_fake_crux(tk):
    # Per-crux additive debt: the single (now vacuous) cover grounds NONE of the fakes, so N fakes accrue
    # ~N independent debt (R2 charged 0 debt for the whole laundered chain).
    for n in (1, 3, 6):
        m = score_ledger(json.dumps(_laundered_chain(n)), tk, goal=GOAL)
        assert m["obligation_debt"] >= float(n), f"N={n} laundered chain only charged {m['obligation_debt']} debt"


def test_deeper_laundered_chain_does_not_outscore_shallower(tk):
    # Monotone NON-increasing in laundered depth: a DEEPER fake chain must never out-score a shallower
    # one (R2 was strictly increasing in depth up to the cap). Each extra fake only adds debt.
    scores = [_score(_laundered_chain(n), tk) for n in (1, 2, 3, 4, 6, 8)]
    for shallow, deep in zip(scores, scores[1:]):
        assert deep <= shallow, f"a deeper laundered chain out-scored a shallower one: {scores}"


@pytest.mark.parametrize("tag", ["algebra", "divisibility", "congruence", "parity",
                                 "factorization", "well_ordering"])
def test_lone_ungrounded_crux_under_generic_tag_below_passed_floor(tk, tag):
    # Even a LONE un-grounded crux relabeled under a GENERIC obligation:none tag must score BELOW the
    # PASSED floor (R2: a lone generic crux reached ~0.7133 >= 0.60). Its support carries no content
    # anchor (no discharged obligation / named method / lemma), so it is demoted out of the PASSED band.
    led = {
        "problem": "lj", "claim": GOAL,
        "steps": [
            {"id": "h", "claim": "x^2 + 1 = 2 y^4.", "justification": "given", "depends_on": []},
            {"id": "x", "claim": "A lone crux asserting new content.", "justification": tag,
             "depends_on": ["h"]},
            {"id": "c", "claim": GOAL, "justification": "conclusion", "depends_on": ["x"]},
        ],
    }
    assert _score(led, tk) < PASSED_FLOOR, f"lone un-grounded {tag} crux reached the PASSED band"


def test_trivial_mod2_cover_grounds_nothing_downstream(tk):
    # The RELEVANCE/non-vacuity rule: a complete-but-trivial case_cover{mod2,[0,1]} is content-free, so a
    # crux directly downstream of it is STILL un-grounded (debt), whereas the same crux downstream of a
    # non-trivial mod-4 cover IS grounded (the grounded-elaboration test). This isolates the vacuity gate.
    trivial = {
        "problem": "lj", "claim": GOAL,
        "steps": [
            {"id": "h", "claim": "x^2 + 1 = 2 y^4.", "justification": "given", "depends_on": []},
            {"id": "cc", "claim": "Cases mod 2 are exhaustive.", "justification": "case_split",
             "depends_on": ["h"], "obligations": {"case_cover": {"modulus": 2, "residues": [0, 1]}}},
            {"id": "a", "claim": "Each case forces the result.", "justification": "algebra",
             "depends_on": ["cc"]},
            {"id": "c", "claim": GOAL, "justification": "conclusion", "depends_on": ["a"]},
        ],
    }
    m = score_ledger(json.dumps(trivial), tk, goal=GOAL)
    assert m["obligation_debt"] >= 1.0  # the algebra crux is NOT grounded by the trivial mod-2 cover


# --------------------------------------------------------------------------------------------------
# R5 — DEFINITIVELY bound the OpenEvolve reward-hack. The fitness kept approximating "is this crux
# grounded?" with GAMEABLE STRUCTURAL-PRESENCE proxies; each documented break is reproduced against the
# LIVE score_ledger and pinned closed. The KEY DELIVERABLE is the STRUCTURAL CAP (a provable invariant,
# not per-attack tuning): ANY ledger whose terminal conclusion's load-bearing support contains >= 1
# UN-GROUNDED crux is HARD-CAPPED strictly BELOW the PASSED floor (a NEEDS_REVIEW candidate for Layer-4,
# never a top-band winner). This single cap bounds ALL present and future relabel/launder variants.
# --------------------------------------------------------------------------------------------------
def _honest_score(tk) -> float:
    return _score(_honest_obligation_ledger(), tk)


def _mod3_discharged_pump(n: int) -> dict:
    """BREAK 1 — N INDEPENDENT complete mod-3 case_covers, each 1:1 'grounding' its own FALSE 'algebra'
    crux that the conclusion conjoins. The covers are non-vacuous (mod 3) and each discharges, so the
    OLD per-crux-local grounding grounded all N cruxes (debt 0) and the additive discharged-credit +
    grounded-depth PUMPED the score above honest, rising with N."""
    steps = [{"id": "h", "claim": "x^2 + 1 = 2 y^4.", "justification": "given", "depends_on": []}]
    crux_ids = []
    for i in range(n):
        steps.append({"id": f"cc{i}", "claim": f"Cases mod 3 are exhaustive ({i}).",
                      "justification": "case_split", "depends_on": ["h"],
                      "obligations": {"case_cover": {"modulus": 3, "residues": [0, 1, 2]}}})
        steps.append({"id": f"a{i}", "claim": f"False algebra crux {i} leaning on cover {i}.",
                      "justification": "algebra", "depends_on": [f"cc{i}"]})
        crux_ids.append(f"a{i}")
    steps.append({"id": "c", "claim": GOAL, "justification": "conclusion", "depends_on": crux_ids})
    return {"problem": "lj", "claim": GOAL, "steps": steps}


def test_mod3_discharged_pump_capped_below_honest_and_not_increasing(tk):
    # BREAK 1: the N-independent-cover discharged-PUMP. For every N >= 2 the assembled pump must score
    # STRICTLY BELOW honest discharge, and the score must be NON-INCREASING in N (a WIDER construction
    # can never out-score one honest discharge — saturating credit + the width-saturation structural cap).
    honest = _honest_score(tk)
    scores = []
    for n in (2, 3, 5, 8, 12, 20):
        s = _score(_mod3_discharged_pump(n), tk)
        scores.append(s)
        assert s < honest, f"mod-3 discharged-PUMP N={n} ({s}) did not score below honest ({honest})"
        assert s < PASSED_FLOOR, f"mod-3 discharged-PUMP N={n} ({s}) reached the PASSED band"
    for shallow, wide in zip(scores, scores[1:]):
        assert wide <= shallow, f"a WIDER discharged-PUMP out-scored a narrower one: {scores}"


def test_mod3_pump_charges_debt_for_surplus_parallel_cruxes(tk):
    # The width-saturation: only ONE parallel elaboration spine is honored; the surplus parallel cruxes
    # are re-classified un-grounded, so N covers' worth of FALSE cruxes accrue debt (N-1 surplus).
    for n in (3, 5, 8):
        m = score_ledger(json.dumps(_mod3_discharged_pump(n)), tk, goal=GOAL)
        assert m["obligation_debt"] >= float(n - 1), (
            f"N={n} pump charged only {m['obligation_debt']} debt for {n - 1} surplus parallel cruxes")


def _offrelevance_independent_chain(n: int) -> dict:
    """BREAK 2 — a complete cover present ONLY in conclusion.depends_on (a parallel branch the fake chain
    does NOT depend on). The OLD 'anchor present ANYWHERE in conclusion support' proxy let this irrelevant
    anchor block demotion for an arbitrarily long INDEPENDENT fake 'algebra' chain (n=30 still PASSED)."""
    steps = [{"id": "h", "claim": "x^2 + 1 = 2 y^4.", "justification": "given", "depends_on": []},
             {"id": "cov", "claim": "Cases mod 5 are exhaustive.", "justification": "case_split",
              "depends_on": ["h"], "obligations": {"case_cover": {"modulus": 5, "residues": [0, 1, 2, 3, 4]}}}]
    prev = "h"
    for i in range(n):
        steps.append({"id": f"f{i}", "claim": f"Independent fake crux {i} asserting new content.",
                      "justification": "algebra", "depends_on": [prev]})
        prev = f"f{i}"
    # the conclusion conjoins the INDEPENDENT fake chain tail AND the irrelevant cover (parallel branch)
    steps.append({"id": "c", "claim": GOAL, "justification": "conclusion", "depends_on": [prev, "cov"]})
    return {"problem": "lj", "claim": GOAL, "steps": steps}


def test_offrelevance_independent_chain_demoted_for_any_length(tk):
    # BREAK 2: the off-relevance anchor cannot launder an INDEPENDENT fake chain. The cover sits in a
    # PARALLEL branch of conclusion.depends_on that the fake chain does not depend on; the fake chain's
    # cruxes are still un-grounded and remain in the conclusion's load-bearing support, so the structural
    # cap demotes the ledger BELOW the PASSED floor for ANY chain length.
    for n in (1, 3, 5, 30, 50):
        s = _score(_offrelevance_independent_chain(n), tk)
        assert s < PASSED_FLOOR, (
            f"off-relevance anchor blocked demotion for an independent chain of length {n} ({s})")


@pytest.mark.parametrize("tag", ["vierzahlensatz", "pell", "cauchy_bezout", "euclid_set_extension",
                                 "invariant_substitution", "arithmetic_progression", "sum_to_product"])
def test_method_ref_lone_crux_is_needs_review_not_passed(tk, tag):
    # BREAK 3: a bare named-method (method_ref) step is NOT a deterministic content anchor — it is a
    # Layer-4 candidate, not re-checkable offline. A conclusion resting only on such a crux must be routed
    # to the NEEDS_REVIEW band (BELOW the PASSED floor): a relabel that discharges nothing is at most a
    # NEEDS_REVIEW candidate, never a soft-PASSED winner (the CORRECT vierzahlensatz-trap semantics).
    led = {
        "problem": "lj", "claim": GOAL,
        "steps": [
            {"id": "h", "claim": "x^2 + 1 = 2 y^4.", "justification": "given", "depends_on": []},
            {"id": "x", "claim": "A lone crux invoking a named method.", "justification": tag,
             "depends_on": ["h"]},
            {"id": "c", "claim": GOAL, "justification": "conclusion", "depends_on": ["x"]},
        ],
    }
    s = _score(led, tk)
    assert NEEDS_REVIEW_FLOOR <= s < PASSED_FLOOR, (
        f"lone bare {tag} method_ref crux scored {s} (must be in the NEEDS_REVIEW band, below PASSED)")


def _grounded_restatement_pump(n: int) -> dict:
    """BREAK 4 — restatements off a genuinely-grounded discharged step. Each restatement repeats the
    cover step's claim (goal_hash equality) and adds NO new content; the OLD grounded-depth bonus counted
    them as proof structure, so stacking restatements pumped the score at 0 debt."""
    steps = [
        {"id": "h", "claim": "x^2 + 1 = 2 y^4.", "justification": "given", "depends_on": []},
        {"id": "cs", "claim": "Split y modulo 4 over a complete residue system.",
         "justification": "case_split", "depends_on": ["h"],
         "obligations": {"case_cover": {"modulus": 4, "residues": [0, 1, 2, 3]}}},
    ]
    prev = "cs"
    for i in range(n):
        steps.append({"id": f"r{i}", "claim": "Split y modulo 4 over a complete residue system.",
                      "justification": "algebra", "depends_on": [prev]})
        prev = f"r{i}"
    steps.append({"id": "c", "claim": GOAL, "justification": "conclusion", "depends_on": [prev]})
    return {"problem": "lj", "claim": GOAL, "steps": steps}


def test_grounded_restatement_depth_pump_yields_no_score_gain(tk):
    # BREAK 4: stacking trivial restatements off a grounded step gives NO score gain — the grounded-depth
    # bonus no longer counts restatements as new proof structure. The score is constant in restatement
    # count, and never exceeds the bare honest discharge.
    base = _score(_grounded_restatement_pump(0), tk)
    for n in (1, 2, 4, 8):
        s = _score(_grounded_restatement_pump(n), tk)
        assert abs(s - base) < 1e-9, f"a grounded restatement pump (n={n}) changed the score: {base} -> {s}"


def test_depth_monotone_non_increasing_in_fake_depth(tk):
    # The depth-monotone invariant across the generic-tag fake chain: padding with more un-grounded cruxes
    # is NON-INCREASING in fake depth (each extra crux only adds debt; the depth bonus ignores it).
    for tag in ("algebra", "divisibility", "congruence"):
        scores = [_score(_goal_bound_all_tag_chain(tag, d), tk) for d in (1, 2, 4, 8, 16)]
        for shallow, deep in zip(scores, scores[1:]):
            assert deep <= shallow, f"a deeper all-{tag} fake out-scored a shallower one: {scores}"


# --------------------------------------------------------------------------------------------------
# THE STRUCTURAL CAP AS A GENERAL INVARIANT (the KEY DELIVERABLE). Over a BATTERY of assembled ledgers,
# NO ledger whose terminal conclusion's load-bearing support contains an un-grounded crux scores
# >= PASSED_FLOOR. This is the provable property that bounds every present/future relabel/launder/
# method_ref variant at once, rather than tuning per-attack.
# --------------------------------------------------------------------------------------------------
def test_structural_cap_general_property_over_battery(tk):
    import random
    from agent.tools.openevolve_bridge import (
        _ungrounded_crux_ids, _conclusion_support_ids,
    )
    from agent.gates.ledger import parse_ledger

    none_tags = ["algebra", "divisibility", "congruence", "parity", "factorization",
                 "well_ordering", "extremal", "induction"]
    method_tags = ["vierzahlensatz", "pell", "cauchy_bezout", "euclid_set_extension",
                   "invariant_substitution", "sum_to_product"]
    rng = random.Random(1234)
    checked = 0
    for _ in range(600):
        steps = [{"id": "h", "claim": "x^2 + 1 = 2 y^4.", "justification": "given", "depends_on": []}]
        ids = ["h"]
        # optionally inject one or two genuine non-vacuous covers (so anchors ARE present somewhere)
        for k in range(rng.randint(0, 2)):
            cid = f"cov{k}"
            m = rng.choice([3, 4, 5])
            steps.append({"id": cid, "claim": f"Cases mod {m} exhaustive {k}.",
                          "justification": "case_split", "depends_on": [rng.choice(ids)],
                          "obligations": {"case_cover": {"modulus": m, "residues": list(range(m))}}})
            ids.append(cid)
        for i in range(rng.randint(1, 6)):
            tag = rng.choice(none_tags + method_tags)
            steps.append({"id": f"s{i}", "claim": f"Assertion {i} of fresh content.",
                          "justification": tag, "depends_on": [rng.choice(ids)]})
            ids.append(f"s{i}")
        deps = rng.sample(ids, k=min(len(ids), rng.randint(1, 3)))
        steps.append({"id": "c", "claim": GOAL, "justification": "conclusion", "depends_on": deps})
        led = {"problem": "lj", "claim": GOAL, "steps": steps}
        try:
            L = parse_ledger(json.dumps(led))
        except Exception:
            continue
        s = _score(led, tk)
        support = _conclusion_support_ids(L)
        if any(x in _ungrounded_crux_ids(L, tk) for x in support):
            checked += 1
            assert s < PASSED_FLOOR, (
                f"INVARIANT VIOLATION: a ledger with an un-grounded crux in its terminal support scored "
                f"{s} >= PASSED_FLOOR ({PASSED_FLOOR})")
    assert checked >= 50, f"battery exercised too few capped ledgers ({checked}); not a real test"


def test_honest_discharge_score_unchanged_and_passed(tk):
    # The honest obligation-discharging ledger (a complete mod-4 case-cover) is UNCHANGED at the prior
    # band score and remains in the PASSED band — the reward-hack fixes do not perturb honest discharge.
    honest = _score(_honest_obligation_ledger(), tk)
    assert abs(honest - 0.815) < 1e-9, f"honest-discharge score moved from 0.815 to {honest}"
    assert honest >= PASSED_FLOOR


def test_honest_serial_multi_discharge_stays_passed(tk):
    # A genuine SERIAL proof that discharges TWO obligations along ONE spine (cover -> elaborate -> cover
    # -> elaborate -> conclusion) is NOT a parallel pump: each later elaboration transitively depends on
    # the earlier one, so the width-saturation keeps both grounded and the ledger stays PASSED.
    serial = {
        "problem": "lj", "claim": GOAL,
        "steps": [
            {"id": "h", "claim": "x^2 + 1 = 2 y^4.", "justification": "given", "depends_on": []},
            {"id": "cs1", "claim": "Split y modulo 4.", "justification": "case_split", "depends_on": ["h"],
             "obligations": {"case_cover": {"modulus": 4, "residues": [0, 1, 2, 3]}}},
            {"id": "e1", "claim": "In each class reduce to a coprime split.", "justification": "algebra",
             "depends_on": ["cs1"]},
            {"id": "cs2", "claim": "Split the cofactor modulo 8.", "justification": "case_split",
             "depends_on": ["e1"],
             "obligations": {"case_cover": {"modulus": 8, "residues": [0, 1, 2, 3, 4, 5, 6, 7]}}},
            {"id": "e2", "claim": "Conclude y=1 in each subcase.", "justification": "algebra",
             "depends_on": ["cs2"]},
            {"id": "c", "claim": GOAL, "justification": "conclusion", "depends_on": ["e2"]},
        ],
    }
    m = score_ledger(json.dumps(serial), tk, goal=GOAL)
    assert m["combined_score"] >= PASSED_FLOOR, "honest serial multi-discharge proof was demoted"
    assert m["obligation_debt"] == 0.0  # both discharges on one spine are grounded; no surplus debt


# --------------------------------------------------------------------------------------------------
# REMOVE-THE-FALSE-CAP regression (FINAL FITNESS FIX item 1). The framework's OWN mandated honest
# split-coprimality pattern — h(given) -> g(gcd_coprimality, obligation:none) -> s(euclid_splitting
# discharging split_coprimality with coprimality_from=g) -> c(conclusion=goal) — was being DEMOTED to
# ~0.365 (NEEDS_REVIEW) because the cited provenance step ``g`` was mis-classified as an un-grounded
# crux. A gcd_coprimality step that is the REQUIRED + GATE-VERIFIED provenance (``coprimality_from``,
# enforced as a typed ANCESTOR by agent/gates/obligations.py::_check_split_coprimality) of a DISCHARGED
# split_coprimality is legitimate GROUNDED bookkeeping, not a crux. It must score in the PASSED band.
# --------------------------------------------------------------------------------------------------
def _honest_split_coprimality_ledger() -> dict:
    """The framework's mandated honest provenance pattern: a gcd_coprimality step ``g`` CITED as the
    ``coprimality_from`` provenance of a DISCHARGED ``split_coprimality`` on ``s``. ``g`` is a real
    gcd_coprimality ancestor of ``s`` (so the gate's _check_split_coprimality passes), and the
    conclusion rests on the discharged split. Goal-bound + PASSED."""
    return {
        "problem": "ljunggren",
        "claim": GOAL,
        "steps": [
            {"id": "h", "claim": "x^2 + 1 = 2 y^4.", "justification": "given", "depends_on": []},
            {"id": "g", "claim": "The two factors of the coprime split are coprime.",
             "justification": "gcd_coprimality", "depends_on": ["h"]},
            {"id": "s", "claim": "Each coprime factor is therefore a perfect square.",
             "justification": "euclid_splitting", "depends_on": ["g"],
             "obligations": {"split_coprimality": {"coprimality_from": "g"}}},
            {"id": "c", "claim": GOAL, "justification": "conclusion", "depends_on": ["s"]},
        ],
    }


def test_honest_split_coprimality_pattern_scores_passed(tk):
    # POSITIVE REGRESSION (item 1 / item 4): the honest provenance pattern is NO LONGER false-capped.
    # Before the fix the cited gcd_coprimality provenance step ``g`` was treated as an un-grounded crux,
    # demoting the whole ledger to ~0.365 (NEEDS_REVIEW). The cited+required+gate-verified provenance of
    # a DISCHARGED split is now GROUNDED, so the ledger scores in the PASSED band (>= 0.60).
    m = score_ledger(json.dumps(_honest_split_coprimality_ledger()), tk, goal=GOAL)
    assert m["combined_score"] >= PASSED_FLOOR, (
        f"honest split-coprimality pattern scored {m['combined_score']} (must be PASSED >= {PASSED_FLOOR}; "
        f"was false-capped at ~0.365 before the fix)")
    # The split is discharged and there is NO assertion debt: ``g`` (provenance) and ``s`` (discharge)
    # are both grounded, so the conclusion rests on no un-grounded crux.
    assert m["obligations_discharged"] >= 1.0
    assert m["obligation_debt"] == 0.0


def test_honest_split_coprimality_provenance_step_is_grounded_not_a_crux(tk):
    # Pin the exact mechanism: the cited gcd_coprimality provenance step is in the GROUNDED set and NOT
    # in the un-grounded-crux set, and the conclusion does not rest on any un-grounded crux.
    from agent.tools.openevolve_bridge import (
        _content_grounded_ids, _ungrounded_crux_ids, _conclusion_rests_on_ungrounded_crux,
        _discharged_obligation_provenance_ids,
    )
    led = parse_ledger(json.dumps(_honest_split_coprimality_ledger()))
    assert "g" in _discharged_obligation_provenance_ids(led, tk)  # g is the cited provenance
    assert "g" in _content_grounded_ids(led, tk)                  # ...therefore grounded
    assert "g" not in _ungrounded_crux_ids(led, tk)               # ...not an un-grounded crux
    assert not _conclusion_rests_on_ungrounded_crux(led, tk)      # so the cap does not fire


def test_provenance_grounding_does_not_launder_independent_fake(tk):
    # CONSERVATIVE-GENERALIZATION guard (item 1): the provenance grounding is NARROW — it grounds ONLY
    # the cited gcd_coprimality step, NOT arbitrary downstream cruxes. A ledger with a legit cited
    # provenance split AND an INDEPENDENT un-grounded fake crux that the conclusion ALSO conjoins is
    # still capped below PASSED (the fake crux remains an un-grounded crux in the conclusion support).
    led = {
        "problem": "ljunggren", "claim": GOAL,
        "steps": [
            {"id": "h", "claim": "x^2 + 1 = 2 y^4.", "justification": "given", "depends_on": []},
            {"id": "g", "claim": "The two factors are coprime.", "justification": "gcd_coprimality",
             "depends_on": ["h"]},
            {"id": "s", "claim": "Each coprime factor is a square.", "justification": "euclid_splitting",
             "depends_on": ["g"], "obligations": {"split_coprimality": {"coprimality_from": "g"}}},
            {"id": "fake", "claim": "An independent hard Pell fact asserting new content.",
             "justification": "algebra", "depends_on": ["h"]},
            {"id": "c", "claim": GOAL, "justification": "conclusion", "depends_on": ["s", "fake"]},
        ],
    }
    assert _score(led, tk) < PASSED_FLOOR, "an independent fake crux was laundered by provenance grounding"


def test_provenance_grounding_requires_real_discharge_not_dangling_citation(tk):
    # The provenance grounding keys off a DISCHARGED split whose citation the gate ACCEPTS. A split that
    # cites a non-ancestor (the gate REJECTs it) is a HARD ZERO, never a laundered pass — confirming the
    # grounding cannot be abused to ground a gcd_coprimality crux the split does not actually rely on.
    laundered = {
        "problem": "ljunggren", "claim": GOAL,
        "steps": [
            {"id": "h", "claim": "x^2 + 1 = 2 y^4.", "justification": "given", "depends_on": []},
            {"id": "g", "claim": "A bare coprimality crux the split does NOT depend on.",
             "justification": "gcd_coprimality", "depends_on": ["h"]},
            {"id": "fake", "claim": "An unrelated hard fact.", "justification": "algebra",
             "depends_on": ["h"]},
            {"id": "s", "claim": "Split cites g but depends only on fake.",
             "justification": "euclid_splitting", "depends_on": ["fake"],
             "obligations": {"split_coprimality": {"coprimality_from": "g"}}},
            {"id": "c", "claim": GOAL, "justification": "conclusion", "depends_on": ["s"]},
        ],
    }
    # The gate REJECTs the non-ancestor citation (split_coprimality_unrelated) -> HARD ZERO.
    assert _score(laundered, tk) == 0.0


# --------------------------------------------------------------------------------------------------
# KNOWN LIMITATION (FINAL FITNESS FIX item 3+4) — ACCEPTED + DOCUMENTED per the maintainer's decision.
# The deterministic fitness grounds a crux STRUCTURALLY (it depends on a non-vacuous discharged typed
# obligation). A FAKE crux that merely CITES a trivially-true complete case-cover
# (case_cover{mod 3, [0,1,2]} — a tautology implying nothing about the goal) is thereby declared
# grounded and CAN reach the PASSED band (~0.817). Distinguishing this from a legitimate elaboration
# needs CONTENT-ENTAILMENT (does the obligation actually imply the crux's claim?), which is
# proof-checking and UNDECIDABLE offline — the same reason the project relies on Layer-4 Lean. This
# soft fitness is a SEARCH HEURISTIC, NOT a certifier; the spoof misleads the SEARCH but NEVER mints a
# false authoritative_elementary verdict, because Layer-4 (the Lean proof-term dependency closure +
# collectAxioms) is the only authoritative elementary filter and rejects a relabeled / fake / non-
# entailing step (it will not compile). This test TRACKS the accepted spoof so it is not silently
# forgotten; it is BY DESIGN that the spoof scores PASSED here (search-signal only, bounded by Layer-4).
# --------------------------------------------------------------------------------------------------
def _trivial_cover_citation_spoof() -> dict:
    """A FAKE 'algebra' crux that merely CITES (depends on) a trivially-true complete mod-3 cover. The
    cover is non-vacuous (mod 3) and complete, so the structural grounding heuristic declares the crux
    grounded — even though the tautological cover ENTAILS NOTHING about the goal. ACCEPTED spoof."""
    return {
        "problem": "ljunggren", "claim": GOAL,
        "steps": [
            {"id": "h", "claim": "x^2 + 1 = 2 y^4.", "justification": "given", "depends_on": []},
            {"id": "cc", "claim": "Every integer is 0, 1, or 2 mod 3.", "justification": "case_split",
             "depends_on": ["h"],
             "obligations": {"case_cover": {"modulus": 3, "residues": [0, 1, 2]}}},
            {"id": "crux", "claim": "The hard Pell quartic fact follows (leaning on the cover).",
             "justification": "algebra", "depends_on": ["cc"]},
            {"id": "c", "claim": GOAL, "justification": "conclusion", "depends_on": ["crux"]},
        ],
    }


def test_known_limitation_trivial_cover_citation_spoof_is_search_only(tk):
    # ACCEPTED LIMITATION, per the maintainer's decision "ACCEPT + DOCUMENT the limit". This asserts the
    # documented trivial-cover-citation spoof reaches the PASSED band TODAY. This is NOT a soundness bug
    # and is NOT something to "fix" here: the soft fitness is a SEARCH HEURISTIC, not a certifier, and a
    # structural (dependency-based) grounding test cannot decide CONTENT-ENTAILMENT offline (that is
    # proof-checking, undecidable). The spoof can mislead the SEARCH but NEVER mints a false
    # authoritative_elementary verdict — Layer-4 (Lean proof-term dependency closure + collectAxioms) is
    # the only authoritative elementary filter and rejects the relabeled / non-entailing step (it will
    # not compile). This test exists to TRACK the accepted behavior so it is not silently forgotten; if
    # the spoof ever stops reaching PASSED, that is a (welcome) IMPROVEMENT and this test should be
    # revisited deliberately, not a regression.
    m = score_ledger(json.dumps(_trivial_cover_citation_spoof()), tk, goal=GOAL)
    assert m["combined_score"] >= PASSED_FLOOR, (
        f"the documented trivial-cover-citation spoof scored {m['combined_score']}; it is ACCEPTED to "
        f"reach the PASSED band (search-signal only, bounded by Layer-4). If this changed, revisit "
        f"the KNOWN-LIMITATION docs at score_ledger / _conclusion_rests_on_ungrounded_crux.")
    # The crux is structurally grounded by the cited (tautological) cover, so the cap does not fire.
    from agent.tools.openevolve_bridge import _conclusion_rests_on_ungrounded_crux
    led = parse_ledger(json.dumps(_trivial_cover_citation_spoof()))
    assert not _conclusion_rests_on_ungrounded_crux(led, tk)  # the spoof is NOT capped (by design)


# --------------------------------------------------------------------------------------------------
# HARD goal-binding gate: off-goal / weakened / vacuous -> a HARD zero.
# --------------------------------------------------------------------------------------------------
def test_offgoal_weakened_claim_is_hard_zero(tk):
    metrics = score_ledger(json.dumps(_claim_weakened_offgoal()), tk, goal=GOAL)
    assert metrics["combined_score"] == 0.0
    assert metrics["goal_bound"] == 0.0


def test_vacuous_restatement_is_hard_zero(tk):
    # Goal = the (vacuous) hypothesis itself; the conclusion restates a 'given' -> vacuous -> 0.0.
    led = _vacuous_restatement()
    assert is_vacuous(parse_ledger(json.dumps(led)))
    assert score_ledger(json.dumps(led), tk, goal=led["claim"])["combined_score"] == 0.0


def test_goal_binding_is_deterministic_hash_not_text_identity(tk):
    # Binding is goal_hash equality, which folds notation/synonyms/spacing (e.g. "modulo" -> "mod" and
    # a trailing period) — so a restated-but-equivalent claim still binds — yet NEVER merges distinct
    # statements (it does not rename variables or reorder operands). An equivalent restatement binds...
    restated = {
        "problem": "demo", "claim": "n^2 is congruent to 0 or 1 mod 4",
        "steps": [
            {"id": "h", "claim": "n is an integer.", "justification": "given", "depends_on": []},
            {"id": "c", "claim": "n^2 is congruent to 0 or 1 modulo 4.",
             "justification": "conclusion", "depends_on": ["h"]},
        ],
    }
    led = parse_ledger(json.dumps(restated))
    assert binds_to_goal(led, "n^2 is congruent to 0 or 1 modulo 4.")
    # ...while an off-by-meaning goal (n^3, not n^2) does not.
    assert not binds_to_goal(led, "n^3 is congruent to 0 or 1 modulo 4.")


# --------------------------------------------------------------------------------------------------
# NEEDS_REVIEW (0.5) is NOT a win: strictly below any goal-bound PASSED.
# --------------------------------------------------------------------------------------------------
def _needs_review_goal_bound() -> dict:
    # A 'factorization' step routes to mandatory review (NEEDS_REVIEW), but the ledger is goal-bound.
    return {
        "problem": "demo", "claim": GOAL,
        "steps": [
            {"id": "h", "claim": "x^2 + 1 = 2 y^4.", "justification": "given", "depends_on": []},
            {"id": "f", "claim": "A factorization holds.", "justification": "factorization",
             "depends_on": ["h"]},
            {"id": "c", "claim": GOAL, "justification": "conclusion", "depends_on": ["f"]},
        ],
    }


def test_needs_review_strictly_below_any_goal_bound_passed(tk):
    nr = _score(_needs_review_goal_bound(), tk)
    passed = _score(_honest_obligation_ledger(), tk)
    assert NEEDS_REVIEW_FLOOR <= nr < PASSED_FLOOR     # NEEDS_REVIEW band
    assert passed >= PASSED_FLOOR                       # PASSED band
    assert nr < passed                                  # 0.5 is NOT a win


def test_needs_review_below_even_a_minimal_goal_bound_passed(tk):
    # Even the weakest goal-bound PASSED ledger out-ranks the richest goal-bound NEEDS_REVIEW.
    minimal_passed = {
        "problem": "demo", "claim": GOAL,
        "steps": [
            {"id": "h", "claim": "x^2 + 1 = 2 y^4.", "justification": "given", "depends_on": []},
            {"id": "c", "claim": GOAL, "justification": "conclusion", "depends_on": ["h"]},
        ],
    }
    assert _score(_needs_review_goal_bound(), tk) < _score(minimal_passed, tk)


# --------------------------------------------------------------------------------------------------
# SAFETY: the evaluator never executes a poison claim string (it only READS + gates).
# --------------------------------------------------------------------------------------------------
def test_evaluator_never_executes_poison_claim(tmp_path, tk):
    import os

    marker = str(tmp_path / "PWNED")
    poison_claim = f'__import__("os").system("type nul > {marker}")'
    led = {
        "problem": "demo", "claim": GOAL,
        "steps": [
            {"id": "h", "claim": poison_claim, "justification": "given", "depends_on": []},
            {"id": "c", "claim": GOAL, "justification": "conclusion", "depends_on": ["h"]},
        ],
    }
    p = tmp_path / "cand.txt"
    p.write_text(json.dumps(led), encoding="utf-8")

    # The serialised-style top-level evaluator must only READ + gate; no exec, no side effect.
    metrics = gate_ledger_evaluator(str(p))  # must not raise
    assert 0.0 <= metrics["combined_score"] <= 1.0
    assert not os.path.exists(marker)  # the claim string was NEVER executed


def test_evaluator_reads_baked_goal_from_env(tmp_path, tk, monkeypatch):
    # The serialised evaluator binds against the goal threaded via MATHAGENT_EVOLVE_GOAL: an off-goal
    # ledger is zeroed, an on-goal ledger is not.
    on_goal = _honest_obligation_ledger()
    p = tmp_path / "cand.txt"
    p.write_text(json.dumps(on_goal), encoding="utf-8")

    monkeypatch.setenv("MATHAGENT_EVOLVE_GOAL", GOAL)
    assert gate_ledger_evaluator(str(p))["combined_score"] >= PASSED_FLOOR

    monkeypatch.setenv("MATHAGENT_EVOLVE_GOAL", "A totally different goal statement.")
    assert gate_ledger_evaluator(str(p))["combined_score"] == 0.0


# --------------------------------------------------------------------------------------------------
# R2 #3: goal=None STRICT GUARD on the certification/evolution path. binds_to_goal is only enforced
# when goal is not None, so a caller that forgets to thread the goal (a DROPPED MATHAGENT_EVOLVE_GOAL)
# would silently DISABLE the soundness-critical binding gate and admit an un-bound candidate. The
# certification path now FAILS LOUD (zeroes); the explicit test-only no-goal back-compat path still works.
# --------------------------------------------------------------------------------------------------
def test_certification_path_with_missing_goal_is_hard_zero(tmp_path, tk, monkeypatch):
    # A would-be-PASSED ledger scored through the evaluator with NO goal resolvable (constant null +
    # env-var absent) must be a HARD ZERO, not a silent un-bound pass.
    monkeypatch.delenv("MATHAGENT_EVOLVE_GOAL", raising=False)
    honest = _honest_obligation_ledger()           # goal-bound + PASSED when a goal IS threaded
    p = tmp_path / "cand.txt"
    p.write_text(json.dumps(honest), encoding="utf-8")
    metrics = gate_ledger_evaluator(str(p))
    assert metrics["combined_score"] == 0.0        # missing goal => refuse to admit
    assert metrics["goal_bound"] == 0.0


def test_score_ledger_require_goal_zeroes_missing_goal_but_backcompat_path_scores(tk):
    # The strict-guard knob: require_goal=True + goal=None => HARD ZERO (the certification contract);
    # the default test-only back-compat path (require_goal omitted) still scores without the binding gate.
    honest = json.dumps(_honest_obligation_ledger())
    strict = score_ledger(honest, tk, goal=None, require_goal=True)
    assert strict["combined_score"] == 0.0 and strict["goal_bound"] == 0.0
    # Explicit no-goal back-compat path: still produces a graded score (binding gate intentionally off).
    backcompat = score_ledger(honest, tk)          # require_goal defaults False
    assert backcompat["combined_score"] >= PASSED_FLOOR
    # And when the goal IS supplied, require_goal is a no-op (the gate runs as usual).
    bound = score_ledger(honest, tk, goal=GOAL, require_goal=True)
    assert bound["combined_score"] >= PASSED_FLOOR and bound["goal_bound"] == 1.0


# --------------------------------------------------------------------------------------------------
# NUMERIC witness mode: scored ONLY by the exact-integer checker; a spec is never eval/exec'd.
# --------------------------------------------------------------------------------------------------
def test_witness_residue_cover_complete_scores_one():
    spec = json.dumps({"kind": "residue_cover", "modulus": 4, "residues": [0, 1, 2, 3]})
    assert score_witness_spec(spec)["combined_score"] == 1.0


def test_witness_residue_cover_incomplete_scores_zero():
    spec = json.dumps({"kind": "residue_cover", "modulus": 4, "residues": [0, 1, 2]})
    assert score_witness_spec(spec)["combined_score"] == 0.0


def test_witness_descent_strict_decrease_scores_one():
    spec = json.dumps({"kind": "descent", "measure_expr": "x", "next_expr": "x - 1",
                       "variables": ["x"], "bounds": {"x": [1, 100]}})
    assert score_witness_spec(spec)["combined_score"] == 1.0


def test_witness_descent_non_decrease_scores_zero():
    spec = json.dumps({"kind": "descent", "measure_expr": "x", "next_expr": "x",
                       "variables": ["x"], "bounds": {"x": [1, 100]}})
    assert score_witness_spec(spec)["combined_score"] == 0.0


def test_witness_solution_set_verified_scores_one():
    spec = json.dumps({"kind": "solution_set", "expression": "x**2 + 1 - y**3",
                       "variables": ["x", "y"], "bounds": {"x": [-50, 50], "y": [-10, 10]},
                       "claimed": [{"x": 0, "y": 1}]})
    assert score_witness_spec(spec)["combined_score"] == 1.0


def test_witness_spec_never_executes_injection(tmp_path):
    # A spec whose measure expression is a code-injection string is parsed ONLY by the restricted
    # no-eval integer AST: it HARD-fails (score 0.0) and never runs the payload.
    import os

    sentinel = tmp_path / "PWNED"
    payload = f'__import__("os").system("type nul > {sentinel}")'
    spec = json.dumps({"kind": "descent", "measure_expr": payload, "next_expr": "x",
                       "variables": ["x"], "bounds": {"x": [0, 1]}})
    assert score_witness_spec(spec)["combined_score"] == 0.0
    assert not os.path.exists(str(sentinel))


def test_witness_malformed_spec_is_hard_zero():
    assert score_witness_spec("not a json spec at all {{{")["combined_score"] == 0.0
    assert score_witness_spec(json.dumps({"kind": "unknown_kind"}))["combined_score"] == 0.0


# --------------------------------------------------------------------------------------------------
# Band ordering is TOTAL over every gate verdict (Python has no exhaustiveness checker, so enumerate
# the Verdict enum x a grid of (debt, discharged, depth) and assert the band invariants hold for ALL).
# --------------------------------------------------------------------------------------------------
def test_graded_score_band_ordering_total_over_verdicts():
    from itertools import product

    from agent.gates.gate import Verdict
    from agent.tools.openevolve_bridge import (
        _graded_score, HARD_ZERO, NEEDS_REVIEW_FLOOR as NRF, PASSED_FLOOR as PF,
    )

    # ENUM x feature-grid cartesian: every Verdict value, every plausible (debt, discharged, depth).
    grid = list(product(range(0, 7), range(0, 7), range(0, 9)))
    for verdict in Verdict:
        for debt, discharged, depth in grid:
            score = _graded_score(verdict.value, debt, discharged, depth)
            assert 0.0 <= score <= 1.0
            if verdict is Verdict.REJECTED:
                assert score == HARD_ZERO
            elif verdict is Verdict.PASSED_DETERMINISTIC:
                assert PF <= score <= 1.0          # PASSED band is ALWAYS >= PASSED_FLOOR
            else:  # NEEDS_REVIEW (and any future admitted-but-not-passed verdict)
                assert NRF <= score < PF           # review band is ALWAYS strictly below PASSED_FLOOR


def test_more_discharged_never_lowers_score_and_more_debt_never_raises_it():
    # Monotonicity of the graded terms: holding the band fixed, discharging more obligations cannot
    # lower the score, and accruing more debt cannot raise it. (Pins "debt is a penalty, discharge a
    # reward" so a future edit cannot silently invert the incentive an evolutionary search optimises.)
    from agent.tools.openevolve_bridge import _graded_score

    v = "passed_deterministic"
    assert _graded_score(v, 0, 2, 3) >= _graded_score(v, 0, 1, 3) >= _graded_score(v, 0, 0, 3)
    assert _graded_score(v, 0, 1, 3) >= _graded_score(v, 1, 1, 3) >= _graded_score(v, 2, 1, 3)
