"""Robustness tests for the Ralph loop (Thread 2): a RAISING prover must NOT crash run().

A live focused prover (Codex / Claude CLI) can raise on a subprocess timeout
(ClaudeError / CodexError), a non-zero exit, or empty output. That raise used to propagate out of
`RalphLoop.run()` -> `DagDriver._prove` -> `DagDriver.run()` and crash the whole episode. The fix
treats a raised `prove()` like an unusable/rejected ledger: the episode records a lessons-learned note
and the loop continues, respecting the budget (no infinite loop) and exhausting cleanly if every
episode raises.

These tests exercise the REAL code path (RalphLoop.run with a stub prover that raises) and FAIL
against the pre-change code (which let the exception propagate out of run()).
"""
import json

import pytest

from agent.gates.gate import Verdict
from agent.gates.toolkit import load_toolkit
from agent.orchestrator import Budget, RunTrace, RalphLoop, JudgeVerdict, ScriptedJudge

TOOLKIT = load_toolkit()


class BoomError(RuntimeError):
    """Stand-in for a live prover failure (ClaudeError / CodexError / subprocess timeout)."""


class RaisingProver:
    """Raises on every prove() call, recording how many times it was invoked (budget-bounded)."""

    def __init__(self, exc: Exception | None = None):
        self._exc = exc or BoomError("codex exec timed out after 1200s")
        self.calls = 0

    def prove(self, problem: str, feedback=None) -> str:
        self.calls += 1
        raise self._exc


class RaiseThenProve:
    """Raises on the first prove() call, then returns a clean PASSED_DETERMINISTIC ledger.

    Proves the loop RECOVERS: a transient prover crash does not poison the whole run; a later episode
    that succeeds is admitted normally.
    """

    def __init__(self, ledger: str, exc: Exception | None = None):
        self._ledger = ledger
        self._exc = exc or BoomError("transient timeout")
        self.calls = 0

    def prove(self, problem: str, feedback=None) -> str:
        self.calls += 1
        if self.calls == 1:
            raise self._exc
        return self._ledger


def _passing_ledger(goal: str = "done") -> str:
    return json.dumps({
        "problem": "p", "claim": goal,
        "steps": [
            {"id": "s1", "claim": "x", "justification": "given", "depends_on": []},
            {"id": "s2", "claim": goal, "justification": "conclusion", "depends_on": ["s1"]},
        ],
    })


def test_raising_prover_does_not_crash_run():
    """The core Thread-2 defect: a prover that RAISES must yield a not-proven RalphResult, NOT a crash.

    Pre-change, `text = self.prover.prove(...)` let BoomError propagate out of run(); this asserts
    run() returns normally with success=False.
    """
    trace = RunTrace("t")
    prover = RaisingProver()
    res = RalphLoop(prover, toolkit=TOOLKIT, trace=trace, max_episodes=3).run("p")
    assert res.success is False
    # The prover error was surfaced on the trace (not silently swallowed), once per episode attempted.
    errs = trace.by_kind("ralph_prover_error")
    assert len(errs) == 3
    assert errs[0].data["error_type"] == "BoomError"
    # A lessons-learned note describing the failure was recorded for the next episode.
    assert any("prover call failed" in l for l in res.lessons)


def test_raising_prover_respects_budget_no_infinite_loop():
    """Every raising episode still spends a budget unit, so the loop is bounded by the call budget
    (it can never spin forever). With a 2-call budget and 5 max episodes, exactly 2 calls happen."""
    budget = Budget(max_llm_calls=2)
    prover = RaisingProver()
    res = RalphLoop(prover, toolkit=TOOLKIT, budget=budget, trace=RunTrace("t"),
                    max_episodes=5).run("p")
    assert res.success is False
    assert prover.calls == 2                 # capped by budget, not max_episodes
    assert budget.calls_spent == 2


def test_raising_prover_exhausts_cleanly_when_all_episodes_fail():
    """All episodes raise -> the loop exhausts cleanly as not-proven (episodes counted, no crash)."""
    res = RalphLoop(RaisingProver(), toolkit=TOOLKIT, trace=RunTrace("t"),
                    max_episodes=2).run("p")
    assert res.success is False
    assert res.episodes == 2                 # both episodes were attempted and counted


def test_loop_recovers_after_a_transient_prover_crash():
    """A first-episode crash followed by a clean ledger still SUCCEEDS — a transient failure is not
    terminal. The recorded lesson is fed forward; the second episode proves the goal."""
    prover = RaiseThenProve(_passing_ledger("p"))     # goal-bound to "p" so the retry episode binds
    trace = RunTrace("t")
    res = RalphLoop(prover, toolkit=TOOLKIT, trace=trace, max_episodes=3).run("p")
    assert res.success is True
    assert res.report is not None and res.report.verdict is Verdict.PASSED_DETERMINISTIC
    assert len(trace.by_kind("ralph_prover_error")) == 1   # exactly one episode crashed
    assert prover.calls == 2                               # crash, then a successful retry


def test_raising_prover_with_judges_does_not_crash():
    """The presence of a judge panel must not change the robustness: a raising prover still yields a
    not-proven result without ever reaching the (never-run) judges."""
    judge = ScriptedJudge("J", [JudgeVerdict("J", elementary=True, no_gaps=True)])
    res = RalphLoop(RaisingProver(), toolkit=TOOLKIT, trace=RunTrace("t"),
                    judges=[judge], max_episodes=2).run("p")
    assert res.success is False


class RaisingJudge:
    """A Layer-2 adversarial judge whose review() RAISES (a live judge subprocess timeout)."""

    def __init__(self, exc: Exception | None = None):
        self._exc = exc or BoomError("judge exec timed out after 1200s")
        self.calls = 0

    def review(self, ledger):
        self.calls += 1
        raise self._exc


class OkProver:
    """Returns a clean PASSED_DETERMINISTIC ledger every episode (so the judge loop is reached)."""

    def __init__(self, ledger: str):
        self._ledger = ledger
        self.calls = 0

    def prove(self, problem: str, feedback=None) -> str:
        self.calls += 1
        return self._ledger


# ---- Per-episode goal-binding (liveness fix): a gate-passing but paraphrased ledger is a FAILED
#      episode carrying verbatim-restatement feedback; if nothing ever binds, success=False. ----

def _paraphrased_ledger(paraphrase: str) -> str:
    """A gate-passing ledger whose claim/conclusion is `paraphrase` (NOT the requested goal string).
    Structurally valid and internally consistent, so the deterministic gate ADMITS it — only the
    per-episode goal-binding should reject it for the real goal."""
    return json.dumps({
        "problem": "p", "claim": paraphrase,
        "steps": [
            {"id": "s1", "claim": "x", "justification": "given", "depends_on": []},
            {"id": "s2", "claim": paraphrase, "justification": "conclusion", "depends_on": ["s1"]},
        ],
    })


class ParaphraseThenBind:
    """Episode 1 returns a gate-passing but PARAPHRASED ledger (fails goal-binding); episode 2 returns
    a verbatim goal-bound ledger. Records the feedback each episode was called with so a test can
    assert the verbatim-restatement lesson was carried forward."""

    def __init__(self, goal: str, paraphrase: str):
        self._goal = goal
        self._paraphrase = paraphrase
        self.calls = 0
        self.feedbacks: list = []

    def prove(self, problem: str, feedback=None) -> str:
        self.calls += 1
        self.feedbacks.append(list(feedback) if feedback else None)
        if self.calls == 1:
            return _paraphrased_ledger(self._paraphrase)
        return _passing_ledger(self._goal)


class AlwaysParaphrase:
    """Every episode returns a gate-passing but paraphrased (non-goal-bound) ledger -> nothing ever
    binds, so the loop must return success=False after exhausting max_episodes."""

    def __init__(self, paraphrase: str):
        self._paraphrase = paraphrase
        self.calls = 0

    def prove(self, problem: str, feedback=None) -> str:
        self.calls += 1
        return _paraphrased_ledger(self._paraphrase)


def test_paraphrase_then_bind_succeeds_with_carried_feedback():
    """Episode 1 paraphrases (gate passes, goal-binding fails) -> FAILED episode with a verbatim
    lesson; episode 2 restates the goal verbatim -> the loop SUCCEEDS in 2 episodes. This is exactly
    the nondeterministic-paraphrase case feedback repair exists for (the live liveness bug)."""
    goal = "For every integer n, n^2 is congruent to 0 or 1 modulo 4."
    prover = ParaphraseThenBind(goal, paraphrase="n squared mod 4 is 0 or 1")
    trace = RunTrace("t")
    res = RalphLoop(prover, toolkit=TOOLKIT, trace=trace, max_episodes=3).run(goal)
    assert res.success is True
    assert res.episodes == 2
    assert prover.calls == 2
    # The unbound episode was surfaced on the trace, not silently swallowed.
    assert len(trace.by_kind("ralph_goal_unbound")) == 1
    # Episode 2 received the verbatim-restatement feedback captured from episode 1's binding failure.
    ep2_feedback = prover.feedbacks[1]
    assert ep2_feedback is not None
    joined = " ".join(ep2_feedback)
    assert "verbatim" in joined
    assert goal in joined                         # the feedback names the exact goal string to use


def test_always_paraphrase_never_binds_returns_failure():
    """A prover that ALWAYS paraphrases (gate passes, binding never does) -> success=False after
    max_episodes, so the DagDriver flows to DirectRejected -> decomposition (not a spurious success)."""
    res = RalphLoop(AlwaysParaphrase("paraphrased claim"), toolkit=TOOLKIT,
                    trace=RunTrace("t"), max_episodes=3).run("real goal")
    assert res.success is False
    assert res.exhausted is False                 # gate PASSED every episode; it is unbound, not starved
    assert res.episodes == 3


def test_goal_bound_ledger_still_succeeds():
    """Soundness regression guard: a verbatim goal-bound gate-passing ledger is STILL accepted in one
    episode (the per-episode binding filter does not reject a correctly-bound proof)."""
    goal = "some goal G"
    res = RalphLoop(OkProver(_passing_ledger(goal)), toolkit=TOOLKIT,
                    trace=RunTrace("t"), max_episodes=2).run(goal)
    assert res.success is True
    assert res.episodes == 1


# ---- Fix 2 (context threading): the per-RUN seed context is a STANDING lesson prepended to the
#      feedback on EVERY episode (incl. after a failure), while goal-binding checks the PURE goal. ----

class _SpyProver:
    """Records the (goal, feedback) it was handed each episode. Episode 1 paraphrases (fails binding),
    episode 2 binds — so we can assert the context clause arrives BOTH before and after the failure."""

    def __init__(self, goal: str, paraphrase: str):
        self._goal = goal
        self._paraphrase = paraphrase
        self.calls = 0
        self.goals: list[str] = []
        self.feedbacks: list = []

    def prove(self, problem: str, feedback=None) -> str:
        self.calls += 1
        self.goals.append(problem)
        self.feedbacks.append(list(feedback) if feedback else [])
        if self.calls == 1:
            return _paraphrased_ledger(self._paraphrase)
        return _passing_ledger(self._goal)


def _paraphrased_ledger(paraphrase: str) -> str:
    """A gate-passing ledger whose claim/conclusion is `paraphrase`, not the goal (fails binding)."""
    return json.dumps({
        "problem": "p", "claim": paraphrase,
        "steps": [
            {"id": "s1", "claim": "x", "justification": "given", "depends_on": []},
            {"id": "s2", "claim": paraphrase, "justification": "conclusion", "depends_on": ["s1"]},
        ],
    })


def test_context_is_standing_lesson_on_every_episode_and_not_in_goal():
    """Fix 2 (test spec b): a per-RUN context clause passed to RalphLoop.run(goal, context=...) arrives
    in the prover feedback on EVERY episode — including episode 2, AFTER episode 1's binding failure —
    as a STANDING lesson (never consumed). Meanwhile the GOAL string handed to the prover (and to
    goal-binding) is the PURE goal and never contains the clause."""
    goal = "For every integer n, n^2 is 0 or 1 mod 4."
    clause = "Per-problem citable inputs: quartic-residue whitelist for this problem only."
    prover = _SpyProver(goal, paraphrase="n squared mod 4 is 0 or 1")
    res = RalphLoop(prover, toolkit=TOOLKIT, trace=RunTrace("t"), max_episodes=3).run(
        goal, context=clause)
    assert res.success is True
    assert prover.calls == 2                       # ep1 paraphrase (fails binding), ep2 binds
    # The context clause is the FIRST (standing) feedback item on BOTH episodes.
    assert prover.feedbacks[0][0] == clause        # episode 1: standing context, no prior lessons
    assert prover.feedbacks[1][0] == clause        # episode 2: standing context STILL first, after fail
    # Episode 2 also carries the verbatim-restatement lesson from episode 1 (context did NOT displace it).
    assert any("verbatim" in item for item in prover.feedbacks[1])
    # The GOAL handed to the prover is the PURE goal on every episode — the clause is NOT in it.
    assert all(g == goal for g in prover.goals)
    assert all(clause not in g for g in prover.goals)


def test_no_context_is_byte_identical_feedback():
    """With context=None (the default), the feedback the prover receives is exactly the pre-feature
    feedback (no standing prefix injected)."""
    goal = "some goal G"
    prover = _SpyProver(goal, paraphrase="paraphrase")
    RalphLoop(prover, toolkit=TOOLKIT, trace=RunTrace("t"), max_episodes=3).run(goal)
    # Episode 1: no feedback at all (empty). Episode 2: only the verbatim-restatement lesson, no prefix.
    assert prover.feedbacks[0] == []
    assert all("Per-problem" not in item for item in prover.feedbacks[1])
    assert any("verbatim" in item for item in prover.feedbacks[1])


def test_raising_judge_does_not_crash_run():
    """A live adversarial-judge call that RAISES must NOT crash run(): fail CLOSED (an un-runnable judge
    leaves the ledger not cleanly admitted) and surface the error on the trace. Pre-change,
    `v = judge.review(report.ledger)` (ralph.py:105) let the raise propagate out of RalphLoop.run() ->
    DagDriver.run() and crash the whole run."""
    trace = RunTrace("t")
    judge = RaisingJudge()
    res = RalphLoop(OkProver(_passing_ledger("p")), toolkit=TOOLKIT, trace=trace,
                    judges=[judge], max_episodes=2).run("p")
    assert res.success is False                      # un-runnable judge -> not admitted (fail closed)
    assert judge.calls == 2                          # the judge was attempted each episode
    errs = trace.by_kind("ralph_judge_error")
    assert len(errs) == 2
    assert errs[0].data["error_type"] == "BoomError"
    assert any("judge call failed" in l for l in res.lessons)
