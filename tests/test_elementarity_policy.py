"""Offline tests for agent/orchestrator/elementarity_policy.py (W3).

Pure table lookup: no model/Lean/tool calls. Asserts each ElementarityLevel maps to the EXACT policy
row from the architecture contract's TABLE, and that each enforcing level's per-node bit tracks
lean.per_node.
"""
import pytest

from agent.orchestrator.elementarity_policy import ElementarityPolicy, policy_for
from agent.orchestrator.run_profile import ElementarityLevel


def test_none_row():
    p = policy_for(ElementarityLevel.none)
    assert p == ElementarityPolicy(False, False, False)


def test_soft_row():
    p = policy_for(ElementarityLevel.soft)
    assert p == ElementarityPolicy(True, False, False)


def test_soft_per_node_tracks_flag():
    on = policy_for(ElementarityLevel.soft, lean_per_node=True)
    off = policy_for(ElementarityLevel.soft, lean_per_node=False)
    assert on == ElementarityPolicy(True, False, True)
    assert off == ElementarityPolicy(True, False, False)


def test_authoritative_row_default_per_node_off():
    p = policy_for(ElementarityLevel.authoritative)
    assert p == ElementarityPolicy(True, True, False)


def test_authoritative_per_node_tracks_flag():
    on = policy_for(ElementarityLevel.authoritative, lean_per_node=True)
    off = policy_for(ElementarityLevel.authoritative, lean_per_node=False)
    assert on == ElementarityPolicy(True, True, True)
    assert off == ElementarityPolicy(True, True, False)


def test_lean_per_node_ignored_when_elementarity_is_none():
    # The supervisor rejects this contradictory profile; the pure policy also fails safely by
    # refusing to attach a Lean gate if it is consulted independently.
    assert policy_for(ElementarityLevel.none, lean_per_node=True).attach_per_node_lean is False


def test_policy_is_frozen():
    p = policy_for(ElementarityLevel.soft)
    with pytest.raises(Exception):
        p.enforce_elementarity = False  # type: ignore[misc]


def test_every_level_has_a_row():
    # Total over the closed enum (no level falls through to the fail-closed ValueError).
    for level in ElementarityLevel:
        assert isinstance(policy_for(level), ElementarityPolicy)
