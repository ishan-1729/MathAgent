"""The TOTAL per-node lifecycle state machine (T100 P1, forge_relevance_study §4 / §7).

`dag_driver._prove` used to advance a node through scattered inline branches; the confirmed
`NEEDS_REVIEW`-stuck bug (a direct-attempt exhaustion that should have decomposed but terminated
instead) was a *missing transition* hidden in that control flow. This module makes the lifecycle a
single explicit table:

    node_transition(state: NodeState, event: NodeEvent) -> (NodeState, Action)

enumerating EVERY (state x event) pair explicitly — NO catch-all / wildcard arm. Python has no
exhaustiveness checker, so totality is enforced operationally by an ENUM-CARTESIAN proptest
(tests/test_node_fsm.py) that iterates all NodeState x NodeEvent and asserts the table never raises
and is fully determined. The orchestrator (dag_driver) *decides* via this table and then *executes*
the returned Action; the bug-fix arm `(IN_PROGRESS, DirectNeedsReviewNoJudge) -> DECOMPOSE` is now a
visible, tested row instead of an implicit fall-through.

Effects-as-data discipline (Forge's `Effect`): a transition returns an `Action` describing what to do
and NEVER performs I/O or mutates the DAG. The driver is the only stateful shell.

Invariants the table guarantees (checked as proptests):
  - Terminal states (PROVEN / FAILED_GAP / FAILED_ELEMENTARY / EXHAUSTED) ABSORB every event: they
    transition to themselves with the no-op action NONE. No event resurrects a closed node — this is
    termination as a *property of the table* (a late child result or memo re-entry cannot reopen a
    decided node).
  - A NEEDS_REVIEW-no-judge node ALWAYS decomposes (DECOMPOSE) before it may EXHAUST; it never goes
    straight to a terminal failure when a producer (decomposer) could still be tried.
"""
from __future__ import annotations

import enum

from agent.orchestrator.state import NodeState


class NodeEvent(enum.Enum):
    """The outcomes the orchestrator can observe for a node (the FSM's input alphabet)."""

    DirectProven = "direct_proven"                       # direct attempt produced a goal-bound proof
    DirectRejected = "direct_rejected"                   # direct attempt failed (genuine gap, gate reject)
    DirectNeedsReviewNoJudge = "direct_needs_review_no_judge"  # exhausted only because review unavailable
    DirectExhaustedBudget = "direct_exhausted_budget"    # direct attempt ran out of LLM-call budget
    Decomposed = "decomposed"                            # a decomposition was committed; recurse on children
    DecompositionReviewerVetoed = "decomposition_reviewer_vetoed"  # reviewer/validation rejected every plan
    ChildrenAllProven = "children_all_proven"            # every child of the committed plan proved
    ChildFailed = "child_failed"                         # a child failed -> this plan does not close
    DepthLimit = "depth_limit"                           # recursion hit max_depth (limit-induced)
    CycleDetected = "cycle_detected"                     # decomposition would close a cycle
    GoalClaimMismatch = "goal_claim_mismatch"            # proof concludes a different statement than the goal
    ElementaryViolation = "elementary_violation"         # adversarial verifier refuted elementarity


class Action(enum.Enum):
    """What the orchestrator must DO for a (state, event). Effects-as-data: the driver enacts these."""

    ATTEMPT_DIRECT = "attempt_direct"                    # run the Ralph direct-proof loop
    ROUTE_REVIEW = "route_review"                        # route a NEEDS_REVIEW node to the adversarial verifier
    DECOMPOSE = "decompose"                              # ask the decomposer + recurse on children
    WAIT_CHILDREN = "wait_children"                      # children committed; block on their results
    ACCEPT_PROVEN = "accept_proven"                      # promote to PROVEN
    MARK_FAILED_GAP = "mark_failed_gap"                  # terminal: a real logical gap / not proven
    MARK_FAILED_ELEMENTARY = "mark_failed_elementary"    # terminal: a real proof, but not elementary
    EXHAUST_STARVED = "exhaust_starved"                  # non-terminal-ish: ran out of budget/depth, retryable
    NONE = "none"                                        # explicit no-op (terminal absorption / ignored pair)


class ReasonCode(enum.Enum):
    """Precise blocker classification (T100 P1 item 3). Attached to every failure/exhaust so no
    give-up is unclassified; emitted on the trace and stored on the node."""

    needs_review_no_reviewer = "needs_review_no_reviewer"
    decomposer_absent = "decomposer_absent"
    budget_starved = "budget_starved"
    depth_limit = "depth_limit"
    cyclic_decomposition = "cyclic_decomposition"
    elementary_violation = "elementary_violation"
    formalization_failed = "formalization_failed"
    gap_found = "gap_found"
    unknown_tool_error = "unknown_tool_error"


# Terminal states absorb every event (no resurrection). Mirrors NodeState.is_terminal but named here
# so the table reads explicitly.
_TERMINAL = (
    NodeState.PROVEN,
    NodeState.FAILED_ELEMENTARY,
    NodeState.FAILED_GAP,
    NodeState.EXHAUSTED,
)


def node_transition(state: NodeState, event: NodeEvent) -> tuple[NodeState, Action]:
    """The TOTAL transition table. Every (state x event) pair is handled explicitly; there is no
    wildcard. Ignored pairs are explicit named no-ops `(state, Action.NONE)`.

    Returns the next NodeState and the Action the orchestrator must enact. Raises ValueError ONLY on
    an unknown enum member (a programming error / new enum added without a row), never on a legitimate
    pair — the proptest proves totality over the real enums.
    """
    # ----- Terminal states ABSORB every event (no resurrection from a closed node) ---------------
    if state in _TERMINAL:
        return state, Action.NONE

    # ----- OPEN: a fresh node. The only meaningful first move is to attempt a direct proof. The
    #       driver always sends OPEN -> IN_PROGRESS via Direct*/Decompose events, so non-attempt
    #       events on a still-OPEN node are explicit no-ops (the driver has not acted yet). --------
    if state is NodeState.OPEN:
        if event in (
            NodeEvent.DirectProven,
            NodeEvent.DirectRejected,
            NodeEvent.DirectNeedsReviewNoJudge,
            NodeEvent.DirectExhaustedBudget,
            NodeEvent.Decomposed,
            NodeEvent.DecompositionReviewerVetoed,
            NodeEvent.ChildrenAllProven,
            NodeEvent.ChildFailed,
            NodeEvent.DepthLimit,
            NodeEvent.CycleDetected,
            NodeEvent.GoalClaimMismatch,
            NodeEvent.ElementaryViolation,
        ):
            # An OPEN node has not begun; the driver moves it to IN_PROGRESS before any outcome. Treat
            # any outcome observed while still OPEN as "begin work" by attempting the direct proof.
            return NodeState.IN_PROGRESS, Action.ATTEMPT_DIRECT
        raise ValueError(f"unhandled event {event!r} for OPEN")  # pragma: no cover - enum-guarded

    # ----- IN_PROGRESS: the node is being worked. Every outcome maps to an explicit decision. ----
    if state is NodeState.IN_PROGRESS:
        if event is NodeEvent.DirectProven:
            return NodeState.PROVEN, Action.ACCEPT_PROVEN
        if event is NodeEvent.DirectRejected:
            # Direct attempt genuinely failed -> try a producer (decomposition). The driver decides
            # terminal failure only when no producer/budget remains (DecompositionReviewerVetoed etc).
            return NodeState.IN_PROGRESS, Action.DECOMPOSE
        if event is NodeEvent.DirectNeedsReviewNoJudge:
            # THE BUG-FIX ARM (now visible & tested): an exhaustion caused purely by review being
            # unavailable is a *producible* state, NOT genuine starvation. Route to decomposition.
            return NodeState.IN_PROGRESS, Action.DECOMPOSE
        if event is NodeEvent.DirectExhaustedBudget:
            # Genuine starvation on the direct attempt: no budget left to even decompose.
            return NodeState.EXHAUSTED, Action.EXHAUST_STARVED
        if event is NodeEvent.Decomposed:
            # A plan committed; block on the children.
            return NodeState.IN_PROGRESS, Action.WAIT_CHILDREN
        if event is NodeEvent.DecompositionReviewerVetoed:
            # No admissible decomposition (reviewer veto / validation / no decomposer) -> real gap.
            return NodeState.FAILED_GAP, Action.MARK_FAILED_GAP
        if event is NodeEvent.ChildrenAllProven:
            return NodeState.PROVEN, Action.ACCEPT_PROVEN
        if event is NodeEvent.ChildFailed:
            # This plan does not close; the driver will re-plan or, when exhausted, mark the gap.
            return NodeState.IN_PROGRESS, Action.DECOMPOSE
        if event is NodeEvent.DepthLimit:
            # Limit-induced, retryable on a shallower branch -> non-terminal EXHAUSTED.
            return NodeState.EXHAUSTED, Action.EXHAUST_STARVED
        if event is NodeEvent.CycleDetected:
            # A cyclic decomposition is rejected; the node has no valid plan -> real gap.
            return NodeState.FAILED_GAP, Action.MARK_FAILED_GAP
        if event is NodeEvent.GoalClaimMismatch:
            return NodeState.FAILED_GAP, Action.MARK_FAILED_GAP
        if event is NodeEvent.ElementaryViolation:
            # The adversarial verifier refuted elementarity: downgrade-don't-discard.
            return NodeState.FAILED_ELEMENTARY, Action.MARK_FAILED_ELEMENTARY
        raise ValueError(f"unhandled event {event!r} for IN_PROGRESS")  # pragma: no cover

    raise ValueError(f"unhandled state {state!r}")  # pragma: no cover - enum-guarded


# All non-terminal states (for proptests / driver assertions).
NON_TERMINAL_STATES = (NodeState.OPEN, NodeState.IN_PROGRESS)
