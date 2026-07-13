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
from agent.orchestrator.goal_binding import goal_binding_mismatches
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
        """Fail closed when an injected/provider verdict contains malformed booleans."""
        return self.elementary is True and self.no_gaps is True


def _validated_judge_verdict(value: object, fallback_name: str) -> JudgeVerdict:
    """Validate the authority-bearing provider boundary and synthesize a closed verdict on drift."""
    if (isinstance(value, JudgeVerdict)
            and isinstance(value.judge, str)
            and type(value.elementary) is bool
            and type(value.no_gaps) is bool
            and isinstance(value.notes, list)
            and all(isinstance(note, str) for note in value.notes)):
        return value
    return JudgeVerdict(
        judge=fallback_name if isinstance(fallback_name, str) else "unknown",
        elementary=True,
        no_gaps=False,
        notes=["judge returned a malformed verdict; rejected fail-closed"],
        confidence=0.0,
    )


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
    if "bad_justification" in codes:
        return NodeState.FAILED_ELEMENTARY
    return NodeState.FAILED_GAP


def _goal_binding_mismatches(ledger: Ledger, requested_goal: str) -> list[str]:
    """Compatibility wrapper around the shared claim + operative-conclusion contract."""
    return goal_binding_mismatches(ledger, requested_goal)


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
            try:
                text = self.prover.prove(problem, feedback)
            except Exception as exc:
                self.trace.emit(
                    "prover_error", attempt=attempts,
                    error_type=type(exc).__name__, detail=str(exc)[:160],
                )
                if not self.budget.can_repair() or not self.budget.can_call():
                    state = NodeState.EXHAUSTED
                    break
                self.budget.spend_repair()
                feedback = [
                    f"prover call failed ({type(exc).__name__}): {str(exc)[:160]}"
                ]
                continue
            self.trace.emit("prove", attempt=attempts, feedback=feedback or [])

            report = evaluate(text, self.toolkit)
            self.trace.emit(
                "gate", attempt=attempts, verdict=report.verdict.value,
                rejects=[f.code for f in report.rejects()],
                reviews=[f.code for f in report.reviews()],
            )

            # Goal binding is a logical precondition, independent of the elementarity gate. Compute it
            # even when another deterministic reject is present so a wrong-goal ledger can never be
            # mislabeled FAILED_ELEMENTARY merely because it also used a banned justification.
            binding_mismatches = (
                _goal_binding_mismatches(report.ledger, problem) if report.ledger is not None
                else ["ledger"]
            )
            if binding_mismatches:
                self.trace.emit("goal_claim_mismatch", attempt=attempts,
                                fields=binding_mismatches)

            # Deterministic REJECT -> repair (if budget allows).
            if report.rejected:
                if not self.budget.can_repair():
                    state = (NodeState.FAILED_GAP if binding_mismatches
                             else _classify_gate_failure(report))
                    break
                if not self.budget.can_call():
                    state = NodeState.EXHAUSTED
                    self.trace.emit("budget", reason="llm_calls", **self.budget.snapshot())
                    break
                self.budget.spend_repair()
                feedback = [str(f) for f in report.rejects()]
                if binding_mismatches:
                    feedback.append(
                        "the proof is not fully bound to the requested goal: "
                        f"asked to prove {problem!r}, but these fields differ: "
                        + ", ".join(binding_mismatches)
                    )
                continue

            # Goal binding is soundness-critical: the stated claim and operative terminal conclusion
            # must both bind to the requested goal. ``problem`` remains the schema's dataset identifier.
            # Route any mismatch through repair like a logical gap; a different theorem is never PROVEN.
            if binding_mismatches:
                if not self.budget.can_repair():
                    state = NodeState.FAILED_GAP
                    break
                if not self.budget.can_call():
                    state = NodeState.EXHAUSTED
                    self.trace.emit("budget", reason="llm_calls", **self.budget.snapshot())
                    break
                self.budget.spend_repair()
                feedback = [
                    "the proof is not fully bound to the requested goal: "
                    f"asked to prove {problem!r}, but these fields differ: "
                    + ", ".join(binding_mismatches)
                ]
                continue

            # Admitted by the deterministic gate. A soft NEEDS_REVIEW with no judge to resolve it
            # must NOT be marked PROVEN — the scanner exists precisely to force that review.
            if report.verdict is Verdict.NEEDS_REVIEW and not self.judges:
                state = NodeState.EXHAUSTED
                self.trace.emit("review_unhandled", reviews=[f.code for f in report.reviews()])
                break

            # Deterministically admitted -> Layer-2 adversarial review.
            verdicts = []
            judge_call_failed = False
            for judge in self.judges:
                if not self.budget.can_call():
                    break
                self.budget.spend_call()
                try:
                    candidate_name = getattr(judge, "name", "unknown")
                    judge_name = candidate_name if isinstance(candidate_name, str) else "unknown"
                except Exception:
                    judge_name = "unknown"
                try:
                    raw_verdict = judge.review(report.ledger)
                    v = _validated_judge_verdict(raw_verdict, judge_name)
                except Exception as exc:
                    judge_call_failed = True
                    self.trace.emit(
                        "judge_error", judge=judge_name,
                        error_type=type(exc).__name__, detail=str(exc)[:160],
                    )
                    v = JudgeVerdict(
                        judge=judge_name,
                        elementary=True,
                        no_gaps=False,
                        notes=[f"judge call failed ({type(exc).__name__}): {str(exc)[:160]}"],
                        confidence=0.0,
                    )
                verdicts.append(v)
                self.trace.emit("judge", judge=v.judge, passed=v.passed is True,
                                elementary=v.elementary is True, no_gaps=v.no_gaps is True)

            # A proof that was not fully reviewed (panel truncated by budget) is not PROVEN.
            if self.judges and len(verdicts) < len(self.judges):
                state = NodeState.EXHAUSTED
                self.trace.emit("budget", reason="judges_truncated", **self.budget.snapshot())
                break

            if self.judges and any(v.passed is not True for v in verdicts):
                non_elem = any(v.elementary is not True for v in verdicts)
                fail_state = NodeState.FAILED_ELEMENTARY if non_elem else NodeState.FAILED_GAP
                if not self.budget.can_repair():
                    state = NodeState.EXHAUSTED if judge_call_failed else fail_state
                    break
                if not self.budget.can_call():
                    state = NodeState.EXHAUSTED
                    self.trace.emit("budget", reason="llm_calls", **self.budget.snapshot())
                    break
                self.budget.spend_repair()
                notes: list[str] = []
                for v in verdicts:
                    if v.passed is not True:
                        tag = "non-elementary" if v.elementary is not True else "gap"
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
