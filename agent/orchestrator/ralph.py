"""The Ralph loop (AlphaProof_Nexus): a per-goal, multi-episode attempt loop.

Each episode asks the focused prover for a ledger, "recompiles" it with the deterministic gate, and —
if rejected — feeds the gate's findings back as **lessons learned** for the next episode (the paper's
"write a lessons-learned comment and resume"). Optionally an adversarial judge panel (Layer 2) reviews
a deterministically-admitted ledger. Every model call is drawn from a shared Budget so the loop always
terminates.

This is the engine the DAG driver runs at each node's *direct-proof* attempt; the injected prover may
be Claude, Codex, scripted, or any other supervised implementation of the protocol.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from agent.gates.gate import GateReport, Verdict, evaluate
from agent.gates.toolkit import Toolkit, load_toolkit
from agent.orchestrator.driver import Prover, Judge, _validated_judge_verdict
from agent.orchestrator.goal_binding import proves_goal as _proves_goal
from agent.orchestrator.state import Budget
from agent.orchestrator.trace import RunTrace

_MAX_LESSONS = 12


def _binding_feedback(goal: str) -> str:
    """The lessons-learned note fed into the next episode when a gate-passing ledger fails goal-binding.

    Tells the prover the structural gate passed but the claim/conclusion did not RESTATE the requested
    goal verbatim, and to set both the top-level claim and the conclusion step's claim to EXACTLY the
    goal string. This is the feedback-repair for the nondeterministic paraphrase the goal-binding
    check rejects."""
    return ("Your ledger passed the structural gate but its claim/conclusion does not restate the "
            "requested goal verbatim. Set both the top-level `claim` and the conclusion step's "
            f"`claim` to exactly: {goal}")


@dataclass
class RalphResult:
    success: bool
    ledger: Optional[str] = None
    report: Optional[GateReport] = None
    episodes: int = 0
    lessons: list[str] = field(default_factory=list)
    exhausted: bool = False  # stopped because the budget ran out (vs genuinely failing)


class RalphLoop:
    def __init__(
        self,
        prover: Prover,
        toolkit: Optional[Toolkit] = None,
        budget: Optional[Budget] = None,
        trace: Optional[RunTrace] = None,
        max_episodes: int = 4,
        judges: Optional[list[Judge]] = None,
        enforce_elementarity: bool = True,
    ):
        self.prover = prover
        self.toolkit = toolkit or load_toolkit()
        self.budget = budget or Budget()
        self.trace = trace or RunTrace("ralph")
        self.max_episodes = max_episodes
        self.judges = judges or []
        # Judges report two independent dimensions. Solution-only runs disable ONLY the
        # elementarity veto; logical gaps remain rejecting. Default True preserves standalone Ralph
        # callers and the pre-profile behavior.
        self.enforce_elementarity = bool(enforce_elementarity)

    def run(self, goal: str, *, context: Optional[str] = None) -> RalphResult:
        """Run the multi-episode attempt loop for ``goal``.

        ``context`` (per-RUN seed context, e.g. a problem's per-problem citable-inputs clause) is a
        STANDING lesson prepended to the lessons/feedback handed to ``prover.prove`` on EVERY episode —
        it is never "consumed" like an ordinary feedback note. It is prover-facing prompt context ONLY:
        goal-binding continues to check the PURE ``goal`` string, so the context clause never pollutes
        goal identity. The clause applies to the ROOT problem, but it is per-RUN context (not per-node),
        so the DagDriver threads the SAME context into child RalphLoop invocations too (a child sub-lemma
        may legitimately need the same citable inputs, e.g. quartic-residue permission on a Ljunggren
        sub-lemma)."""
        lessons: list[str] = []
        report: Optional[GateReport] = None
        text: Optional[str] = None
        episodes = 0
        # The standing seed context is prepended to the feedback on EVERY episode (a standing lesson,
        # not consumed). None => byte-identical to the pre-feature feedback the prover received.
        context_prefix: list[str] = [context] if context else []

        for _ep in range(self.max_episodes):
            if not self.budget.can_call():
                return RalphResult(False, text, report, episodes, lessons, exhausted=True)
            self.budget.spend_call()
            episodes += 1
            # ROBUSTNESS (Thread 2): a live prover call (Codex/Claude CLI) can RAISE — a subprocess
            # timeout (ClaudeError/CodexError), a non-zero exit, empty output, etc. Such a raise must
            # NOT crash the loop: treat it exactly like an unusable/rejected ledger. The call already
            # cost a budget unit (spent above), so we record a lessons-learned note describing the
            # failure and continue to the next episode. The loop stays bounded by max_episodes/budget,
            # so it terminates cleanly and exhausts as not-proven if every episode raises. The
            # exception is surfaced on the trace (never silently swallowed).
            try:
                # The seed context (if any) is prepended as a STANDING lesson every episode; the
                # per-episode lessons follow it. Goal-binding below still checks the PURE goal string.
                feedback = (context_prefix + lessons) or None
                text = self.prover.prove(goal, feedback=feedback)
            except Exception as e:
                lesson = f"prover call failed ({type(e).__name__}): {str(e)[:160]}"
                self.trace.emit("ralph_prover_error", goal=goal[:80], episode=episodes,
                                error_type=type(e).__name__, detail=str(e)[:160])
                lessons = ([lesson] + lessons)[:_MAX_LESSONS]
                report = None
                continue
            report = evaluate(text, self.toolkit)
            self.trace.emit("ralph_episode", goal=goal[:80], episode=episodes,
                            verdict=report.verdict.value,
                            rejects=[f.code for f in report.rejects()])

            if report.rejected:
                lessons = [str(f) for f in report.rejects()][:_MAX_LESSONS]
                continue

            # PER-EPISODE GOAL-BINDING (liveness fix) — CHECKED FIRST, BEFORE the NEEDS_REVIEW-no-judge
            # return below. A ledger that passes the structural gate but whose claim/conclusion does not
            # restate the requested goal VERBATIM is a FAILED episode, not a success — REGARDLESS of the
            # gate verdict (NEEDS_REVIEW or PASSED_DETERMINISTIC). This ORDERING is the liveness fix: if
            # the NEEDS_REVIEW-no-judge return ran first, an UNBOUND NEEDS_REVIEW ledger would return
            # exhausted -> the driver routes DirectNeedsReviewNoJudge -> _verify_or_downgrade ->
            # refute_elementary REFUTES on goal_binding -> terminal FAILED_ELEMENTARY at 1 call with the
            # retry budget unspent. Catching the unbinding HERE turns it into a normal feedback-repair
            # episode: record the verbatim-restatement lesson and continue. Only a BOUND ledger falls
            # through to the review return / judge panel below. If NO episode ever binds, the loop
            # returns success=False -> DirectRejected -> decomposition fallback. Ralph and the DAG
            # driver import the same shared `_proves_goal` helper.
            if not _proves_goal(text, goal):
                self.trace.emit("ralph_goal_unbound", goal=goal[:80], episode=episodes,
                                verdict=report.verdict.value)
                lessons = ([_binding_feedback(goal)] + lessons)[:_MAX_LESSONS]
                continue

            # Admitted by the deterministic gate AND goal-bound. A soft NEEDS_REVIEW (e.g. an elastic
            # justification like descent/vieta_jumping) with no judge to resolve it must NOT be
            # returned as a proven direct proof -- the scanner exists precisely to force that
            # review. Mirror FlatDriver's guard and fail closed. (Only reached once the ledger is
            # goal-bound; an unbound NEEDS_REVIEW was already turned into a feedback-repair episode.)
            if report.verdict is Verdict.NEEDS_REVIEW and not self.judges:
                self.trace.emit("review_unhandled", reviews=[f.code for f in report.reviews()])
                return RalphResult(False, text, report, episodes, lessons, exhausted=True)

            # Deterministically admitted -> optional Layer-2 adversarial review.
            judge_notes: list[str] = []
            incomplete = False
            for judge in self.judges:
                if not self.budget.can_call():
                    incomplete = True
                    break
                self.budget.spend_call()
                # ROBUSTNESS (Thread 2): a live adversarial-judge call (Codex/Claude CLI) can RAISE
                # exactly like the prover above (subprocess timeout, non-zero exit, malformed output).
                # It must NOT crash RalphLoop.run()/DagDriver.run(). Fail CLOSED: treat an un-runnable
                # judge as a non-passing 'gap' note so the ledger is NOT cleanly admitted, surface it
                # on the trace, and continue to the next judge. The call already cost a budget unit.
                try:
                    raw_verdict = judge.review(report.ledger)
                    v = _validated_judge_verdict(
                        raw_verdict, getattr(judge, "name", "unknown"))
                except Exception as e:
                    self.trace.emit("ralph_judge_error", goal=goal[:80], episode=episodes,
                                    error_type=type(e).__name__, detail=str(e)[:160])
                    judge_notes.append(f"judge call failed ({type(e).__name__}): {str(e)[:160]}")
                    continue
                # A JudgeVerdict contains two orthogonal dimensions. ``elementarity=none`` admits a
                # sound non-elementary proof, but must NEVER admit a logical gap. Do not use the
                # verdict's enforcement-agnostic ``passed`` convenience property here.
                effective_passed = (
                    v.no_gaps is True
                    and (v.elementary is True or not self.enforce_elementarity)
                )
                self.trace.emit(
                    "ralph_judge",
                    judge=v.judge,
                    passed=effective_passed,
                    raw_passed=v.passed is True,
                    elementary=v.elementary is True,
                    no_gaps=v.no_gaps is True,
                    elementarity_enforced=self.enforce_elementarity,
                )
                if not effective_passed:
                    # Preserve the legacy classification when enforcement is on. When it is off,
                    # any remaining rejection is necessarily the soundness-bearing gap dimension.
                    tag = ("non-elementary"
                           if self.enforce_elementarity and v.elementary is not True else "gap")
                    judge_notes.append(f"{v.judge} ({tag}): " + "; ".join(v.notes))
            if incomplete:
                return RalphResult(False, text, report, episodes, lessons, exhausted=True)
            if judge_notes:
                lessons = (judge_notes + lessons)[:_MAX_LESSONS]
                continue

            return RalphResult(True, text, report, episodes, lessons)

        return RalphResult(False, text, report, episodes, lessons)
