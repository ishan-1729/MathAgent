"""Regression tests for the Ralph loop's crash-open-on-NEEDS_REVIEW defect (lane L4-ralph-faith).

A soft NEEDS_REVIEW verdict (an elastic justification like descent/vieta_jumping) with no judge
panel must NOT be admitted as a proven direct proof. Mirrors FlatDriver's `review_unhandled` guard.
"""
import json

import pytest

from agent.gates.gate import Verdict
from agent.gates.toolkit import load_toolkit
from agent.orchestrator import Budget, RunTrace, ScriptedProver, RalphLoop, JudgeVerdict, ScriptedJudge

TOOLKIT = load_toolkit()


def _descent_ledger():
    """A structurally-valid ledger whose elastic `descent` justification routes to Layer-2 review.

    The claim and conclusion restate the run() goal ("p") verbatim so the ledger is GOAL-BOUND —
    Ralph's per-episode binding check would otherwise fail the episode before review semantics
    are exercised (the point of these tests).
    """
    return json.dumps({
        "problem": "p", "claim": "p",
        "steps": [
            {"id": "s1", "claim": "descend", "justification": "descent", "depends_on": [],
             "obligations": {"descent": {"measure": "x", "strictly_decreases": True,
                                         "stays_in_domain": True}}},
            {"id": "s2", "claim": "p", "justification": "conclusion", "depends_on": ["s1"]},
        ],
    })


def _passing_ledger():
    """A goal-bound ledger that passes the deterministic gate cleanly (PASSED_DETERMINISTIC)."""
    return json.dumps({
        "problem": "p", "claim": "p",
        "steps": [
            {"id": "s1", "claim": "x", "justification": "given", "depends_on": []},
            {"id": "s2", "claim": "p", "justification": "conclusion", "depends_on": ["s1"]},
        ],
    })


def test_needs_review_without_judges_is_not_success():
    """The core defect: NEEDS_REVIEW + no judges must NOT return RalphResult(success=True)."""
    trace = RunTrace("t")
    res = RalphLoop(ScriptedProver([_descent_ledger()]), toolkit=TOOLKIT,
                    trace=trace, judges=None).run("p")
    assert res.success is False
    assert res.exhausted is True
    assert res.report is not None and res.report.verdict is Verdict.NEEDS_REVIEW
    # A 'review_unhandled' trace event must be emitted (mirrors FlatDriver).
    unhandled = trace.by_kind("review_unhandled")
    assert len(unhandled) == 1
    assert "elastic_justification" in unhandled[0].data["reviews"]


def test_passed_deterministic_without_judges_still_succeeds():
    """Guard must not regress the happy path: a clean PASSED_DETERMINISTIC ledger still succeeds."""
    res = RalphLoop(ScriptedProver([_passing_ledger()]), toolkit=TOOLKIT,
                    trace=RunTrace("t"), judges=None).run("p")
    assert res.success is True
    assert res.report is not None and res.report.verdict is Verdict.PASSED_DETERMINISTIC


def test_needs_review_with_passing_judge_succeeds():
    """A NEEDS_REVIEW ledger that a judge panel approves is still admitted (review resolved)."""
    judge = ScriptedJudge("J", [JudgeVerdict("J", elementary=True, no_gaps=True)])
    res = RalphLoop(ScriptedProver([_descent_ledger()]), toolkit=TOOLKIT,
                    trace=RunTrace("t"), judges=[judge]).run("p")
    assert res.success is True


def test_needs_review_with_failing_judge_retries_not_unhandled():
    """A failing judge feeds lessons back and re-runs; it must not hit the review_unhandled path."""
    judge = ScriptedJudge("J", [JudgeVerdict("J", elementary=False, no_gaps=False, notes=["gap"])])
    trace = RunTrace("t")
    res = RalphLoop(ScriptedProver([_descent_ledger()]), toolkit=TOOLKIT,
                    trace=trace, judges=[judge], max_episodes=2).run("p")
    assert res.success is False
    # Judges were present, so the review was handled (failed), not silently unhandled.
    assert trace.by_kind("review_unhandled") == []


# ---- Fix 1 (ordering): the per-episode goal-binding check runs BEFORE the NEEDS_REVIEW-no-judge
#      return, so an UNBOUND NEEDS_REVIEW ledger is a FAILED (feedback-repair) episode, not a
#      terminal review_unhandled exhaustion. Only a BOUND NEEDS_REVIEW ledger takes review_unhandled.

def _unbound_descent_ledger():
    """A structurally-valid NEEDS_REVIEW ledger (elastic `descent`) whose claim/conclusion is a
    PARAPHRASE, not the run() goal "p" — so it is gate-admitted NEEDS_REVIEW but NOT goal-bound."""
    return json.dumps({
        "problem": "p", "claim": "some paraphrase of p",
        "steps": [
            {"id": "s1", "claim": "descend", "justification": "descent", "depends_on": [],
             "obligations": {"descent": {"measure": "x", "strictly_decreases": True,
                                         "stays_in_domain": True}}},
            {"id": "s2", "claim": "some paraphrase of p", "justification": "conclusion",
             "depends_on": ["s1"]},
        ],
    })


def test_unbound_needs_review_is_feedback_repair_not_review_unhandled():
    """Fix 1 core: episode 1 returns an UNBOUND NEEDS_REVIEW ledger (gate says NEEDS_REVIEW, but the
    claim/conclusion paraphrases the goal). Because goal-binding is now checked FIRST, this is a FAILED
    episode carrying the verbatim-restatement lesson (NOT the terminal review_unhandled exhaustion that
    would route the driver to a 1-call FAILED_ELEMENTARY). Episode 2 returns a BOUND NEEDS_REVIEW ledger,
    which THEN takes the review_unhandled path (exhausted, no judge to resolve it)."""
    trace = RunTrace("t")
    prover = ScriptedProver([_unbound_descent_ledger(), _descent_ledger()])
    res = RalphLoop(prover, toolkit=TOOLKIT, trace=trace, judges=None, max_episodes=3).run("p")
    # Episode 1 unbound -> feedback repair (not review_unhandled). Episode 2 bound NEEDS_REVIEW,
    # no judge -> the review_unhandled exhaustion. Two episodes ran.
    assert res.success is False
    assert res.exhausted is True
    assert res.episodes == 2
    # The unbound episode was recorded as a goal-unbound feedback-repair, NOT a review_unhandled.
    unbound = trace.by_kind("ralph_goal_unbound")
    assert len(unbound) == 1
    # review_unhandled fires exactly ONCE, on the BOUND episode 2 (not the unbound episode 1).
    unhandled = trace.by_kind("review_unhandled")
    assert len(unhandled) == 1
    assert "elastic_justification" in unhandled[0].data["reviews"]
    # The verbatim-restatement lesson from episode 1 is carried into the loop's lessons.
    assert any("verbatim" in l for l in res.lessons)


def test_unbound_passed_ledger_also_feedback_repair_regardless_of_verdict():
    """Fix 1 semantics: an unbound ledger is a FAILED episode REGARDLESS of gate verdict. Here episode 1
    is an unbound PASSED_DETERMINISTIC ledger (no elastic step) — still feedback-repair, not success —
    and episode 2 binds and succeeds cleanly."""
    def _unbound_passed():
        return json.dumps({
            "problem": "p", "claim": "paraphrase",
            "steps": [
                {"id": "s1", "claim": "x", "justification": "given", "depends_on": []},
                {"id": "s2", "claim": "paraphrase", "justification": "conclusion",
                 "depends_on": ["s1"]},
            ],
        })
    trace = RunTrace("t")
    prover = ScriptedProver([_unbound_passed(), _passing_ledger()])
    res = RalphLoop(prover, toolkit=TOOLKIT, trace=trace, judges=None, max_episodes=3).run("p")
    assert res.success is True
    assert res.episodes == 2
    assert len(trace.by_kind("ralph_goal_unbound")) == 1
    assert trace.by_kind("review_unhandled") == []
