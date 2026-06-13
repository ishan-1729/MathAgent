"""Tests for the flat driver: repair loop, judge integration, liveness/budget caps, trace."""
import json

import pytest

from agent.orchestrator import (
    FlatDriver, Budget, RunTrace, NodeState, JudgeVerdict, ScriptedProver, ScriptedJudge,
)
from agent.gates.toolkit import load_toolkit

TOOLKIT = load_toolkit()

GOOD = json.dumps({
    "problem": "p", "claim": "c",
    "steps": [
        {"id": "s1", "claim": "Let n be an integer.", "justification": "given", "depends_on": []},
        {"id": "s2", "claim": "Therefore the claim holds.", "justification": "conclusion",
         "depends_on": ["s1"]},
    ],
})

BAD = json.dumps({
    "problem": "p", "claim": "c",
    "steps": [
        {"id": "s1", "claim": "Invoke the class group.", "justification": "class_field_theory",
         "depends_on": []},
        {"id": "s2", "claim": "done", "justification": "conclusion", "depends_on": ["s1"]},
    ],
})

PASS = JudgeVerdict(judge="J", elementary=True, no_gaps=True)
NOT_ELEM = JudgeVerdict(judge="J", elementary=False, no_gaps=True, notes=["uses heavy machinery"])
HAS_GAP = JudgeVerdict(judge="J", elementary=True, no_gaps=False, notes=["step 3 unjustified"])


def _driver(prover, judges=None, budget=None):
    return FlatDriver(prover, judges=judges, toolkit=TOOLKIT,
                      budget=budget or Budget(), trace=RunTrace("t"))


def test_good_proof_no_judges_proven():
    res = _driver(ScriptedProver([GOOD])).run("p")
    assert res.proven and res.state is NodeState.PROVEN
    assert res.attempts == 1
    assert res.ledger is not None


def test_good_proof_passing_judge():
    res = _driver(ScriptedProver([GOOD]), judges=[ScriptedJudge("J", [PASS])]).run("p")
    assert res.proven
    assert len(res.judge_verdicts) == 1 and res.judge_verdicts[0].passed


def test_repair_then_success():
    prover = ScriptedProver([BAD, GOOD])  # first rejected, then fixed
    res = _driver(prover).run("p")
    assert res.proven
    assert res.attempts == 2
    # Feedback from the gate must have been passed into the second prove() call.
    assert prover.feedback_log[0] is None
    assert prover.feedback_log[1] and any("bad_justification" in f for f in prover.feedback_log[1])


def test_persistent_rejection_fails_elementary():
    budget = Budget(max_repair_iters=3, max_llm_calls=100)
    res = _driver(ScriptedProver([BAD]), budget=budget).run("p")
    assert res.state is NodeState.FAILED_ELEMENTARY
    assert res.attempts == budget.max_repair_iters + 1
    assert budget.repairs_spent == budget.max_repair_iters


def test_gap_failure_classification():
    # Gate-clean proof, but judge reports a logical gap -> FAILED_GAP.
    budget = Budget(max_repair_iters=2, max_llm_calls=100)
    res = _driver(ScriptedProver([GOOD]), judges=[ScriptedJudge("J", [HAS_GAP])],
                  budget=budget).run("p")
    assert res.state is NodeState.FAILED_GAP


def test_non_elementary_judge_failure_classification():
    budget = Budget(max_repair_iters=2, max_llm_calls=100)
    res = _driver(ScriptedProver([GOOD]), judges=[ScriptedJudge("J", [NOT_ELEM])],
                  budget=budget).run("p")
    assert res.state is NodeState.FAILED_ELEMENTARY


def test_judge_repair_then_pass():
    # Judge fails once, passes after a repair; prover keeps emitting GOOD.
    judge = ScriptedJudge("J", [HAS_GAP, PASS])
    res = _driver(ScriptedProver([GOOD]), judges=[judge]).run("p")
    assert res.proven
    assert res.attempts == 2


def test_budget_exhaustion_zero_calls():
    res = _driver(ScriptedProver([GOOD]), budget=Budget(max_llm_calls=0)).run("p")
    assert res.state is NodeState.EXHAUSTED
    assert res.attempts == 0


def test_budget_exhaustion_midway():
    # 3 calls, always-bad, generous repair iters -> runs out of CALLS, not repairs.
    budget = Budget(max_llm_calls=3, max_repair_iters=100)
    res = _driver(ScriptedProver([BAD]), budget=budget).run("p")
    assert res.state is NodeState.EXHAUSTED
    assert budget.calls_spent == 3


def test_calls_never_exceed_budget():
    budget = Budget(max_llm_calls=5, max_repair_iters=100)
    _driver(ScriptedProver([BAD]), budget=budget).run("p")
    assert budget.calls_spent <= 5


def test_trace_records_events():
    res = _driver(ScriptedProver([BAD, GOOD]), judges=[ScriptedJudge("J", [PASS])]).run("p")
    tr = res.trace
    assert len(tr.by_kind("prove")) == 2
    assert len(tr.by_kind("gate")) == 2
    assert len(tr.by_kind("judge")) == 1
    assert len(tr.by_kind("final")) == 1
    assert tr.by_kind("final")[0].data["state"] == "proven"


def test_scripted_prover_requires_ledgers():
    with pytest.raises(ValueError):
        ScriptedProver([])


def test_terminal_state_flags():
    assert NodeState.PROVEN.is_success
    assert NodeState.PROVEN.is_terminal
    assert not NodeState.OPEN.is_terminal
    assert not NodeState.FAILED_GAP.is_success
