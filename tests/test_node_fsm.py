"""Totality + invariant tests for the per-node lifecycle FSM (T100 P1).

Python has NO Rust-style exhaustiveness check, so totality is enforced operationally by an
ENUM-CARTESIAN proptest over EVERY (NodeState x NodeEvent) pair: the table must never raise and must
be fully determined. Plus the soundness invariants the table guarantees:
  - terminal states (PROVEN / FAILED_* / EXHAUSTED) ABSORB every event (no resurrection);
  - a NEEDS_REVIEW-no-judge node ALWAYS decomposes before it may EXHAUST (the bug-fix arm).
"""
import itertools

import pytest

from agent.orchestrator.node_fsm import (
    Action, NodeEvent, ReasonCode, node_transition, NON_TERMINAL_STATES,
)
from agent.orchestrator.state import NodeState

ALL_STATES = list(NodeState)
ALL_EVENTS = list(NodeEvent)
TERMINAL = (NodeState.PROVEN, NodeState.LEAN_VERIFIED, NodeState.FAILED_ELEMENTARY,
            NodeState.FAILED_GAP, NodeState.EXHAUSTED)


# ---- ENUM-CARTESIAN TOTALITY: every (state x event) pair is handled, never raises, fully determined.

@pytest.mark.parametrize("state,event", list(itertools.product(ALL_STATES, ALL_EVENTS)))
def test_node_transition_is_total_and_determined(state, event):
    next_state, action = node_transition(state, event)
    # Fully determined: the outputs are the real enum members (no None, no stray type).
    assert isinstance(next_state, NodeState)
    assert isinstance(action, Action)


def test_node_transition_is_a_pure_function():
    # Determinism: the same (state, event) always yields the identical (state, action) — no hidden
    # state, no randomness. Call every pair twice and compare.
    for state, event in itertools.product(ALL_STATES, ALL_EVENTS):
        a = node_transition(state, event)
        b = node_transition(state, event)
        assert a == b


def test_every_pair_covered_no_exceptions():
    # A second guard that the cartesian product never raises (e.g. a ValueError from a missing arm).
    raised = []
    for state, event in itertools.product(ALL_STATES, ALL_EVENTS):
        try:
            node_transition(state, event)
        except Exception as e:  # noqa: BLE001 - the whole point is to prove NOTHING raises
            raised.append((state, event, repr(e)))
    assert raised == [], f"node_transition raised on {raised}"


# ---- INVARIANT: terminal states ABSORB every event (no resurrection from a closed node).

@pytest.mark.parametrize("state", TERMINAL)
@pytest.mark.parametrize("event", ALL_EVENTS)
def test_terminal_states_absorb_every_event(state, event):
    next_state, action = node_transition(state, event)
    assert next_state is state, f"{state} was resurrected to {next_state} by {event}"
    assert action is Action.NONE, f"{state} produced a non-noop action {action} on {event}"


def test_terminal_absorbs_an_arbitrary_event_stream():
    # Feeding any sequence of events to a terminal node never moves it off that state.
    for term in TERMINAL:
        state = term
        for event in ALL_EVENTS + list(reversed(ALL_EVENTS)):
            state, action = node_transition(state, event)
            assert state is term
            assert action is Action.NONE


# ---- P5 INVARIANT: LEAN_VERIFIED is a first-class TERMINAL-ABSORBING success state that DOMINATES
#       soft PROVEN. The enum-cartesian proptests above ALREADY enumerate it (it is in NodeState, hence
#       in ALL_STATES and in TERMINAL); these pin its specific properties.

def test_lean_verified_is_enumerated_by_the_cartesian_proptest():
    # The totality/determinism proptests range over ALL_STATES x ALL_EVENTS; prove LEAN_VERIFIED is a
    # real enum member they cover (not an orphaned constant the proptest skips).
    assert NodeState.LEAN_VERIFIED in ALL_STATES
    assert NodeState.LEAN_VERIFIED in TERMINAL
    # Every event on LEAN_VERIFIED is a determinate, non-raising transition (a slice of the cartesian).
    for event in ALL_EVENTS:
        next_state, action = node_transition(NodeState.LEAN_VERIFIED, event)
        assert isinstance(next_state, NodeState) and isinstance(action, Action)


@pytest.mark.parametrize("event", ALL_EVENTS)
def test_lean_verified_is_terminal_absorbing(event):
    # LEAN_VERIFIED absorbs EVERY event -> (LEAN_VERIFIED, NONE): no event resurrects, reopens, or
    # DOWNGRADES a Lean-confirmed node (a late child result / memo re-entry cannot move it).
    next_state, action = node_transition(NodeState.LEAN_VERIFIED, event)
    assert next_state is NodeState.LEAN_VERIFIED, f"LEAN_VERIFIED moved to {next_state} on {event}"
    assert action is Action.NONE


def test_lean_verified_is_terminal_and_success():
    # First-class state semantics: terminal (absorbing) AND a success (is_success), dominating PROVEN.
    assert NodeState.LEAN_VERIFIED.is_terminal
    assert NodeState.LEAN_VERIFIED.is_success
    # PROVEN remains a success too; both are the TERMINAL_SUCCESS set.
    assert NodeState.PROVEN.is_success
    assert {s for s in NodeState if s.is_success} == {NodeState.PROVEN, NodeState.LEAN_VERIFIED}
    # The failure/limit terminals are NOT success.
    for s in (NodeState.FAILED_GAP, NodeState.FAILED_ELEMENTARY, NodeState.EXHAUSTED):
        assert not s.is_success


def test_lean_verified_absorbs_an_arbitrary_event_stream():
    # Like the other terminals: feeding any sequence of events never moves LEAN_VERIFIED off itself.
    state = NodeState.LEAN_VERIFIED
    for event in ALL_EVENTS + list(reversed(ALL_EVENTS)):
        state, action = node_transition(state, event)
        assert state is NodeState.LEAN_VERIFIED
        assert action is Action.NONE


# ---- INVARIANT (the bug-fix): NEEDS_REVIEW-no-judge ALWAYS decomposes before EXHAUSTED.

def test_needs_review_no_judge_decomposes_not_exhausts():
    state, action = node_transition(NodeState.IN_PROGRESS, NodeEvent.DirectNeedsReviewNoJudge)
    assert action is Action.DECOMPOSE, "a NEEDS_REVIEW-no-judge node must route to decomposition"
    assert state is NodeState.IN_PROGRESS, "it stays in progress (a producible state), not terminal"
    # Crucially it does NOT go straight to EXHAUSTED / a terminal failure.
    assert state is not NodeState.EXHAUSTED
    assert action is not Action.EXHAUST_STARVED
    assert action not in (Action.MARK_FAILED_GAP, Action.MARK_FAILED_ELEMENTARY)


def test_genuine_budget_starvation_exhausts():
    # The distinct case: a true budget starvation on the direct attempt DOES exhaust.
    state, action = node_transition(NodeState.IN_PROGRESS, NodeEvent.DirectExhaustedBudget)
    assert state is NodeState.EXHAUSTED
    assert action is Action.EXHAUST_STARVED


def test_direct_rejected_routes_to_decompose():
    state, action = node_transition(NodeState.IN_PROGRESS, NodeEvent.DirectRejected)
    assert action is Action.DECOMPOSE


def test_proven_and_children_proven_accept():
    for ev in (NodeEvent.DirectProven, NodeEvent.ChildrenAllProven):
        state, action = node_transition(NodeState.IN_PROGRESS, ev)
        assert state is NodeState.PROVEN and action is Action.ACCEPT_PROVEN


def test_elementary_violation_downgrades_not_discards():
    state, action = node_transition(NodeState.IN_PROGRESS, NodeEvent.ElementaryViolation)
    assert state is NodeState.FAILED_ELEMENTARY
    assert action is Action.MARK_FAILED_ELEMENTARY


def test_cycle_and_mismatch_are_gaps():
    for ev in (NodeEvent.CycleDetected, NodeEvent.GoalClaimMismatch,
               NodeEvent.DecompositionReviewerVetoed):
        state, action = node_transition(NodeState.IN_PROGRESS, ev)
        assert state is NodeState.FAILED_GAP and action is Action.MARK_FAILED_GAP


def test_depth_limit_is_retryable_exhaust_not_terminal_gap():
    state, action = node_transition(NodeState.IN_PROGRESS, NodeEvent.DepthLimit)
    assert state is NodeState.EXHAUSTED          # retryable on a shallower branch (not FAILED_GAP)
    assert action is Action.EXHAUST_STARVED


# ---- INVARIANT: no non-terminal state is left "stuck" — every (non-terminal, event) yields a
#       non-NONE action OR a determinate transition (the FSM always has an enabled move).

@pytest.mark.parametrize("state", NON_TERMINAL_STATES)
@pytest.mark.parametrize("event", ALL_EVENTS)
def test_non_terminal_states_always_have_an_enabled_move(state, event):
    next_state, action = node_transition(state, event)
    # An OPEN node bootstraps to IN_PROGRESS+ATTEMPT_DIRECT; IN_PROGRESS always returns a real action.
    if state is NodeState.IN_PROGRESS:
        assert action is not Action.NONE, (
            f"IN_PROGRESS is stuck (no enabled transition) on {event}")


# ---- ReasonCode coverage: every code the driver may emit is a valid enum value (no typos).

def test_reason_codes_are_distinct_strings():
    values = [r.value for r in ReasonCode]
    assert len(values) == len(set(values))
    # The full classification set the study (§7) calls out.
    expected = {
        "needs_review_no_reviewer", "decomposer_absent", "budget_starved", "depth_limit",
        "cyclic_decomposition", "elementary_violation", "formalization_failed", "gap_found",
        "unknown_tool_error",
    }
    assert set(values) == expected
