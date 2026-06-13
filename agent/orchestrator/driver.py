"""The Phase-1 flat sequential driver.

`plan -> prove -> deterministic gate -> (repair loop) -> adversarial judges -> verdict`, with hard
liveness caps. The driver depends only on small Protocols (Prover, Judge), so the real LLM-backed
implementations and the test stubs are interchangeable. The deterministic gate is authoritative for
REJECT; the judges (Layer 2) are soft and only block by triggering a repair, never silently pass.

Scope (PLAN.md Section 4): flat, single-target, no DAG / memo / goal cache (those are Phase 2).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

from agent.gates.gate import GateReport, Verdict, evaluate
from agent.gates.ledger import Ledger
from agent.gates.report import Severity
from agent.gates.toolkit import Toolkit, load_toolkit
from agent.orchestrator.state import Budget, NodeState
from agent.orchestrator.trace import RunTrace


@dataclass
class JudgeVerdict:
    """A Layer-2 adversarial review verdict."""

    judge: str
    elementary: bool
    no_gaps: bool
    notes: list[str] = field(default_factory=list)
    confidence: float = 1.0

    @property
    def passed(self) -> bool:
        return self.elementary and self.no_gaps


@runtime_checkable
class Prover(Protocol):
    def prove(self, problem: str, feedback: Optional[list[str]] = None) -> str:
        """Return a ledger (JSON text or fenced block). `feedback` carries prior gate/judge failures."""
        ...


@runtime_checkable
class Judge(Protocol):
    name: str

    def review(self, ledger: Ledger) -> JudgeVerdict:
        ...


@dataclass
class RunResult:
    problem: str
    state: NodeState
    gate_report: Optional[GateReport] = None
    judge_verdicts: list[JudgeVerdict] = field(default_factory=list)
    attempts: int = 0
    trace: Optional[RunTrace] = None

    @property
    def proven(self) -> bool:
        return self.state is NodeState.PROVEN

    @property
    def ledger(self) -> Optional[Ledger]:
        return self.gate_report.ledger if self.gate_report else None


def _classify_gate_failure(report: GateReport) -> NodeState:
    codes = {f.code for f in report.rejects()}
    if "bad_justification" in codes or "denylist_term" in codes:
        return NodeState.FAILED_ELEMENTARY
    return NodeState.FAILED_GAP


class FlatDriver:
    def __init__(
        self,
        prover: Prover,
        judges: Optional[list[Judge]] = None,
        toolkit: Optional[Toolkit] = None,
        budget: Optional[Budget] = None,
        trace: Optional[RunTrace] = None,
    ):
        self.prover = prover
        self.judges = judges or []
        self.toolkit = toolkit or load_toolkit()
        self.budget = budget or Budget()
        self.trace = trace or RunTrace(run_id="flat-run")

    def run(self, problem: str) -> RunResult:
        feedback: Optional[list[str]] = None
        report: Optional[GateReport] = None
        verdicts: list[JudgeVerdict] = []
        attempts = 0
        state = NodeState.OPEN

        while True:
            if not self.budget.can_call():
                state = NodeState.EXHAUSTED
                self.trace.emit("budget", reason="llm_calls", **self.budget.snapshot())
                break

            state = NodeState.IN_PROGRESS
            attempts += 1
            self.budget.spend_call()
            text = self.prover.prove(problem, feedback)
            self.trace.emit("prove", attempt=attempts, feedback=feedback or [])

            report = evaluate(text, self.toolkit)
            self.trace.emit(
                "gate", attempt=attempts, verdict=report.verdict.value,
                rejects=[f.code for f in report.rejects()],
                reviews=[f.code for f in report.reviews()],
            )

            # Deterministic REJECT -> repair (if budget allows).
            if report.rejected:
                if not self.budget.can_repair():
                    state = _classify_gate_failure(report)
                    break
                if not self.budget.can_call():
                    state = NodeState.EXHAUSTED
                    self.trace.emit("budget", reason="llm_calls", **self.budget.snapshot())
                    break
                self.budget.spend_repair()
                feedback = [str(f) for f in report.rejects()]
                continue

            # Deterministically admitted -> Layer-2 adversarial review.
            verdicts = []
            for judge in self.judges:
                if not self.budget.can_call():
                    break
                self.budget.spend_call()
                v = judge.review(report.ledger)
                verdicts.append(v)
                self.trace.emit("judge", judge=v.judge, passed=v.passed,
                                elementary=v.elementary, no_gaps=v.no_gaps)

            if self.judges and any(not v.passed for v in verdicts):
                non_elem = any(not v.elementary for v in verdicts)
                fail_state = NodeState.FAILED_ELEMENTARY if non_elem else NodeState.FAILED_GAP
                if not self.budget.can_repair():
                    state = fail_state
                    break
                if not self.budget.can_call():
                    state = NodeState.EXHAUSTED
                    self.trace.emit("budget", reason="llm_calls", **self.budget.snapshot())
                    break
                self.budget.spend_repair()
                notes: list[str] = []
                for v in verdicts:
                    if not v.passed:
                        tag = "non-elementary" if not v.elementary else "gap"
                        notes.append(f"{v.judge} ({tag}): " + "; ".join(v.notes))
                feedback = notes
                continue

            state = NodeState.PROVEN
            break

        self.trace.emit("final", state=state.value, attempts=attempts,
                        **self.budget.snapshot())
        return RunResult(
            problem=problem,
            state=state,
            gate_report=report,
            judge_verdicts=verdicts,
            attempts=attempts,
            trace=self.trace,
        )


# --------------------------------------------------------------------------------------------------
# Scripted stubs (for tests and offline demos) — interchangeable with real LLM-backed roles.
# --------------------------------------------------------------------------------------------------

class ScriptedProver:
    """Returns a fixed sequence of ledgers (one per prove() call). Repeats the last when exhausted."""

    def __init__(self, ledgers: list[str]):
        if not ledgers:
            raise ValueError("ScriptedProver needs at least one ledger")
        self._ledgers = ledgers
        self.calls = 0
        self.feedback_log: list[Optional[list[str]]] = []

    def prove(self, problem: str, feedback: Optional[list[str]] = None) -> str:
        self.feedback_log.append(feedback)
        idx = min(self.calls, len(self._ledgers) - 1)
        self.calls += 1
        return self._ledgers[idx]


class ScriptedJudge:
    """Returns a fixed sequence of verdicts (one per review() call). Repeats the last when exhausted."""

    def __init__(self, name: str, verdicts: list[JudgeVerdict]):
        self.name = name
        if not verdicts:
            raise ValueError("ScriptedJudge needs at least one verdict")
        self._verdicts = verdicts
        self.calls = 0

    def review(self, ledger: Ledger) -> JudgeVerdict:
        idx = min(self.calls, len(self._verdicts) - 1)
        self.calls += 1
        return self._verdicts[idx]
