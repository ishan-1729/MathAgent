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


def test_partial_judge_panel_cannot_displace_incumbent():
    """A budget that could fund only the first favorable vote must not change the champion."""
    class CountingComparator:
        def __init__(self, scores):
            self.scores = scores
            self.calls = 0

        def compare(self, a, b):
            self.calls += 1
            return (self.scores[a.content] > self.scores[b.content]) - \
                (self.scores[a.content] < self.scores[b.content])

    pro_b = CountingComparator({"A": 0, "B": 1})
    pro_a = CountingComparator({"A": 1, "B": 0})
    # Critic+author+synth consume three calls. One call remains: enough for the old implementation to
    # apply the first pro-B vote, but not enough for the complete two-judge panel.
    budget = Budget(max_llm_calls=4)
    ctrl = RevisionController(
        ScriptedCritic([["flaw"]]), ScriptedAuthor(["B"]), ScriptedSynthesizer(["B"]),
        [pro_b, pro_a], budget=budget, max_passes=1, margin=1, seed=0,
    )
    res = ctrl.refine("goal", "A")
    assert res.content == "A"
    assert res.changed is False and res.displacements == 0
    assert pro_b.calls == pro_a.calls == 0          # panel preflight is atomic; no partial votes applied
    assert budget.calls_spent == 3
    assert res.history[0]["reason"] == "judge_panel_incomplete"


def test_exact_full_panel_budget_can_still_displace():
    # Control for the atomic preflight: exactly enough budget for critic+author+synth and both votes
    # completes the panel and admits the unanimously preferred challenger.
    judges = [KeyComparator(lambda c: {"A": 0, "B": 1}[c.content]) for _ in range(2)]
    budget = Budget(max_llm_calls=5)
    ctrl = RevisionController(
        ScriptedCritic([["flaw"]]), ScriptedAuthor(["B"]), ScriptedSynthesizer(["B"]),
        judges, budget=budget, max_passes=1, margin=1, seed=0,
    )
    res = ctrl.refine("goal", "A")
    assert res.content == "B" and res.displacements == 1
    assert budget.calls_spent == 5
    assert "reason" not in res.history[0]


def test_is_deterministic_under_seed():
    scores = {"A": 1.0, "B": 5.0, "AB": 3.0}
    a = _controller(scores, author=["B"], synth=["AB"], seed=0).refine("g", "A")
    b = _controller(scores, author=["B"], synth=["AB"], seed=0).refine("g", "A")
    assert (a.content, a.passes, a.displacements) == (b.content, b.passes, b.displacements)


def test_rejects_empty_judge_panel():
    with pytest.raises(ValueError):
        RevisionController(ScriptedCritic([[]]), ScriptedAuthor(["x"]),
                           ScriptedSynthesizer(["y"]), [])


# ---- (P3 / #6) AutoReason ONE-WAY POLISH: refined output never re-enters the evolve archive ----

def test_autoreason_refined_output_is_barred_from_evolve_archive():
    """The AutoReason (exploit) champion, once it displaces the incumbent, must NEVER seed evolve.

    AutoReason explores nothing — it polishes the single champion. Re-injecting that polished output
    into the diversity-maximising explore archive can propagate a 'better-sounding' synthesis. We assert
    the one-directional separation: a refined artifact registered with the explore/exploit barrier is
    rejected as an evolve seed (fail loud)."""
    pytest.importorskip("openevolve", reason="pip install mathagent[evolve]")
    from agent.tools.openevolve_bridge import explore_exploit_barrier, evolve_sketches, StubEvolveLLM
    from agent.gates.toolkit import load_toolkit

    # The RevisionController's displaced champion (the refined output the driver would store).
    scores = {"A": 1.0, "B": 5.0, "AB": 3.0}
    res = _controller(scores, author=["B"], synth=["AB"], k_stop=2, max_passes=6).refine("goal", "A")
    assert res.changed and res.content == "B"   # a real displacement -> "B" is the refined champion

    # Simulate the driver registering the refined champion (exploit side).
    explore_exploit_barrier().mark_refined(res.content)

    # The refined champion must NOT be ingestible as an evolve (explore) seed.
    with pytest.raises(AssertionError, match="explore/exploit separation"):
        evolve_sketches("goal", load_toolkit(), llm=StubEvolveLLM([res.content]),
                        iterations=1, seed_sketches=[res.content])


def test_driver_refine_registers_champion_with_barrier():
    """The DagDriver's _refine registers a CHANGED refined champion with the explore/exploit barrier,
    so a later evolve run cannot re-seed from it (the one-way explore->exploit guarantee, wired)."""
    pytest.importorskip("openevolve", reason="pip install mathagent[evolve]")
    import json
    from agent.orchestrator.dag_driver import DagDriver
    from agent.orchestrator.driver import JudgeVerdict, ScriptedJudge, ScriptedProver
    from agent.tools.openevolve_bridge import explore_exploit_barrier
    from agent.gates.toolkit import load_toolkit

    tk = load_toolkit()
    incumbent = json.dumps({"problem": "p", "claim": "G", "steps": [
        {"id": "s1", "claim": "setup", "justification": "given", "depends_on": []},
        {"id": "s2", "claim": "G", "justification": "conclusion", "depends_on": ["s1"]}]})
    refined = json.dumps({"problem": "p2", "claim": "G", "steps": [
        {"id": "s1", "claim": "alt setup", "justification": "given", "depends_on": []},
        {"id": "s2", "claim": "G", "justification": "conclusion", "depends_on": ["s1"]}]})

    class _Refiner:
        def refine(self, goal, ledger, is_admissible=None):
            from agent.orchestrator.tournament import RefineResult
            return RefineResult(content=refined, changed=True, passes=1, displacements=1)

    proof_judge = ScriptedJudge(
        "logic", [JudgeVerdict("logic", elementary=True, no_gaps=True)])
    driver = DagDriver(ScriptedProver([incumbent]), toolkit=tk, refiner=_Refiner(),
                       judges=[proof_judge])
    out = driver._refine("G", incumbent)
    assert out == refined
    assert explore_exploit_barrier().is_refined(refined)   # registered -> barred from the archive
