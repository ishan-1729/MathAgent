"""Tests for the Autoreason incumbent-tournament revision controller (offline, deterministic)."""
import pytest

from agent.orchestrator.population import KeyComparator
from agent.orchestrator.state import Budget
from agent.orchestrator.tournament import (
    RevisionController, ScriptedCritic, ScriptedAuthor, ScriptedSynthesizer,
)


def _controller(scores, *, author, synth, judges=1, **kw):
    """A controller whose judge(s) prefer the candidate with the higher score in `scores`."""
    judge_list = [KeyComparator(lambda c: scores.get(c.content, 0.0)) for _ in range(judges)]
    kw.setdefault("seed", 0)
    return RevisionController(
        ScriptedCritic([["flaw"]]),
        ScriptedAuthor(author),
        ScriptedSynthesizer(synth),
        judge_list,
        **kw,
    )


def test_do_nothing_wins_when_no_challenger_is_better():
    # Incumbent A outscores every challenger → it must never be displaced.
    scores = {"A": 10.0, "B": 1.0, "AB": 1.0}
    ctrl = _controller(scores, author=["B"], synth=["AB"], k_stop=2, max_passes=8)
    res = ctrl.refine("goal", "A")
    assert res.content == "A"
    assert res.changed is False
    assert res.displacements == 0
    assert res.passes == 2          # k_stop consecutive incumbent wins → converged


def test_clearly_better_challenger_displaces_incumbent():
    scores = {"A": 1.0, "B": 5.0, "AB": 3.0}
    ctrl = _controller(scores, author=["B"], synth=["AB"], k_stop=2, max_passes=6)
    res = ctrl.refine("goal", "A")
    assert res.content == "B"
    assert res.changed is True
    assert res.displacements >= 1


def test_non_admissible_challenger_cannot_displace_even_if_judges_love_it():
    # The judges adore "BAD", but it fails the elementary admissibility gate, so it never enters.
    scores = {"A": 1.0, "BAD": 100.0}
    ctrl = _controller(scores, author=["BAD"], synth=["BAD"], k_stop=2, max_passes=6)
    res = ctrl.refine("goal", "A", is_admissible=lambda c: c != "BAD")
    assert res.content == "A"
    assert res.changed is False
    assert res.displacements == 0


def test_margin_blocks_a_marginal_challenger_with_a_panel():
    # 3 judges, margin 3: a challenger preferred by only 2 of 3 cannot clear the margin.
    # Judge keys: two prefer B, one prefers A — net head-to-head for B is +2 < margin 3.
    j_pro_b1 = KeyComparator(lambda c: {"A": 1.0, "B": 2.0, "AB": 0.0}.get(c.content, 0.0))
    j_pro_b2 = KeyComparator(lambda c: {"A": 1.0, "B": 2.0, "AB": 0.0}.get(c.content, 0.0))
    j_pro_a = KeyComparator(lambda c: {"A": 9.0, "B": 2.0, "AB": 0.0}.get(c.content, 0.0))
    ctrl = RevisionController(
        ScriptedCritic([["flaw"]]), ScriptedAuthor(["B"]), ScriptedSynthesizer(["AB"]),
        [j_pro_b1, j_pro_b2, j_pro_a], margin=3, k_stop=2, max_passes=4, seed=1,
    )
    res = ctrl.refine("goal", "A")
    assert res.content == "A"        # +2 net < margin 3 → incumbent holds


def test_respects_budget():
    # A pass needs critic+author+synth (3 calls) before judging; with only 2 calls no pass completes.
    budget = Budget(max_llm_calls=2)
    ctrl = _controller({"A": 1.0, "B": 5.0}, author=["B"], synth=["AB"], budget=budget)
    res = ctrl.refine("goal", "A")
    assert res.content == "A"
    assert res.passes == 0
    assert budget.calls_spent <= 2


def test_is_deterministic_under_seed():
    scores = {"A": 1.0, "B": 5.0, "AB": 3.0}
    a = _controller(scores, author=["B"], synth=["AB"], seed=0).refine("g", "A")
    b = _controller(scores, author=["B"], synth=["AB"], seed=0).refine("g", "A")
    assert (a.content, a.passes, a.displacements) == (b.content, b.passes, b.displacements)


def test_rejects_empty_judge_panel():
    with pytest.raises(ValueError):
        RevisionController(ScriptedCritic([[]]), ScriptedAuthor(["x"]),
                           ScriptedSynthesizer(["y"]), [])
