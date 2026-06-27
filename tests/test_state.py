"""Tests for the Budget caps in agent/orchestrator/state.py.

Focus (P3): the OPTIONAL per-node Lean VERIFY sub-cap (max_node_verify_calls) is SEPARATE from the main
max_llm_calls pool, defaults to None (UNLIMITED -> byte-identical), and accounts via
can_verify_node()/spend_verify_node() exactly mirroring the existing call accounting.
"""
import pytest

from agent.orchestrator.state import Budget, BudgetExceeded


# ---- default (None) sub-cap is UNLIMITED and byte-identical ----

def test_node_verify_subcap_defaults_unlimited():
    """The default Budget has no node-verify sub-cap: can_verify_node() is ALWAYS True and the snapshot
    is byte-identical to the pre-feature snapshot (no node_verify keys leak onto trace events)."""
    b = Budget()
    assert b.max_node_verify_calls is None
    assert b.node_verify_spent == 0
    # Unlimited: arbitrarily many verifies stay admissible.
    for _ in range(1000):
        assert b.can_verify_node() is True
        b.spend_verify_node()
    assert b.can_verify_node() is True            # never exhausts when unlimited
    # The snapshot must NOT carry the sub-cap keys when unset (byte-identical default).
    snap = b.snapshot()
    assert "node_verify_spent" not in snap
    assert "max_node_verify_calls" not in snap
    assert set(snap) == {"calls_spent", "repairs_spent", "replans_spent",
                         "max_llm_calls", "max_repair_iters"}


def test_node_verify_subcap_independent_of_llm_calls():
    """The sub-cap pool is SEPARATE from max_llm_calls: spending node-verify never moves calls_spent,
    and spending llm calls never moves node_verify_spent. A per-node Lean compile cannot starve the
    prover/decomposer search and vice versa."""
    b = Budget(max_llm_calls=3, max_node_verify_calls=1)
    b.spend_verify_node()
    assert b.calls_spent == 0 and b.node_verify_spent == 1
    assert not b.can_verify_node()                # node-verify pool exhausted...
    assert b.can_call()                           # ...but the LLM-call pool is untouched
    b.spend_call()
    assert b.calls_spent == 1 and b.node_verify_spent == 1


# ---- a SET sub-cap bounds the per-node verifies ----

def test_node_verify_subcap_blocks_after_cap():
    b = Budget(max_node_verify_calls=2)
    assert b.can_verify_node()
    b.spend_verify_node()
    assert b.can_verify_node()
    b.spend_verify_node()
    assert not b.can_verify_node()                # cap reached -> caller must fall back
    assert b.node_verify_spent == 2


def test_spend_verify_node_overshoot_raises_when_capped():
    """spend_verify_node mirrors spend_call: overshooting a SET cap raises BudgetExceeded (a defensive
    guard; normal control flow gates on can_verify_node() first)."""
    b = Budget(max_node_verify_calls=1)
    b.spend_verify_node()
    with pytest.raises(BudgetExceeded):
        b.spend_verify_node()


def test_spend_verify_node_unlimited_never_raises_and_tracks():
    """With no cap (None) spend_verify_node never raises and still increments the counter for
    observability."""
    b = Budget()                                  # max_node_verify_calls is None
    for i in range(1, 6):
        b.spend_verify_node()
        assert b.node_verify_spent == i


# ---- snapshot surfaces the sub-cap ONLY when configured ----

def test_snapshot_surfaces_subcap_only_when_set():
    b = Budget(max_node_verify_calls=4)
    b.spend_verify_node()
    snap = b.snapshot()
    assert snap["node_verify_spent"] == 1
    assert snap["max_node_verify_calls"] == 4
