"""The Ralph loop (AlphaProof_Nexus): a per-goal, multi-episode attempt loop.

Each episode asks the focused prover for a ledger, "recompiles" it with the deterministic gate, and —
if rejected — feeds the gate's findings back as **lessons learned** for the next episode (the paper's
"write a lessons-learned comment and resume"). Optionally an adversarial judge panel (Layer 2) reviews
a deterministically-admitted ledger. Every model call is drawn from a shared Budget so the loop always
terminates.

This is the engine the DAG driver runs at each node's *direct-proof* attempt; the prover here is the
GPT-5.5-xHigh / Codex focused prover that substitutes for AlphaProof.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from agent.gates.gate import GateReport, Verdict, evaluate
from agent.gates.toolkit import Toolkit, load_toolkit
from agent.orchestrator.driver import Prover, Judge
from agent.orchestrator.state import Budget
from agent.orchestrator.trace import RunTrace

_MAX_LESSONS = 12


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
    ):
        self.prover = prover
        self.toolkit = toolkit or load_toolkit()
        self.budget = budget or Budget()
        self.trace = trace or RunTrace("ralph")
        self.max_episodes = max_episodes
        self.judges = judges or []

    def run(self, goal: str) -> RalphResult:
        lessons: list[str] = []
        report: Optional[GateReport] = None
        text: Optional[str] = None
        episodes = 0

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
                text = self.prover.prove(goal, feedback=lessons or None)
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

            # Admitted by the deterministic gate. A soft NEEDS_REVIEW (e.g. an elastic
            # justification like descent/vieta_jumping) with no judge to resolve it must NOT be
            # returned as a proven direct proof -- the scanner exists precisely to force that
            # review. Mirror FlatDriver's guard and fail closed.
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
                    v = judge.review(report.ledger)
                except Exception as e:
                    self.trace.emit("ralph_judge_error", goal=goal[:80], episode=episodes,
                                    error_type=type(e).__name__, detail=str(e)[:160])
                    judge_notes.append(f"judge call failed ({type(e).__name__}): {str(e)[:160]}")
                    continue
                self.trace.emit("ralph_judge", judge=v.judge, passed=v.passed)
                if not v.passed:
                    tag = "non-elementary" if not v.elementary else "gap"
                    judge_notes.append(f"{v.judge} ({tag}): " + "; ".join(v.notes))
            if incomplete:
                return RalphResult(False, text, report, episodes, lessons, exhausted=True)
            if judge_notes:
                lessons = (judge_notes + lessons)[:_MAX_LESSONS]
                continue

            return RalphResult(True, text, report, episodes, lessons)

        return RalphResult(False, text, report, episodes, lessons)
