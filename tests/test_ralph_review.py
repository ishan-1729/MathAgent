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

    The conclusion step's claim equals the ledger's stated goal so the deterministic gate admits it
    (NEEDS_REVIEW), rather than rejecting on goal/claim mismatch.
    """
    return json.dumps({
        "problem": "p", "claim": "done",
        "steps": [
            {"id": "s1", "claim": "descend", "justification": "descent", "depends_on": [],
             "obligations": {"descent": {"measure": "x", "strictly_decreases": True,
                                         "stays_in_domain": True}}},
            {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]},
        ],
    })


def _passing_ledger():
    """A ledger that passes the deterministic gate cleanly (PASSED_DETERMINISTIC, no review flags)."""
    return json.dumps({
        "problem": "p", "claim": "done",
        "steps": [
            {"id": "s1", "claim": "x", "justification": "given", "depends_on": []},
            {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]},
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
