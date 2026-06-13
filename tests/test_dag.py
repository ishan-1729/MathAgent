"""Tests for the AND-OR proof DAG: deep-hash memoization, acyclicity, assembly."""
import pytest

from agent.orchestrator.dag import ProofDAG, goal_hash, CycleError
from agent.orchestrator.state import NodeState


def test_goal_hash_normalizes_whitespace():
    assert goal_hash("a  +   b") == goal_hash("a + b")
    assert goal_hash(" x = y\n") == goal_hash("x = y")


def test_goal_hash_is_case_sensitive():
    # x and X are different variables — must not collide.
    assert goal_hash("x^2") != goal_hash("X^2")


def test_get_or_create_memoizes():
    dag = ProofDAG()
    a = dag.get_or_create("goal one")
    b = dag.get_or_create("goal   one")  # same after normalization
    assert a is b
    assert len(dag.nodes) == 1


def test_proven_lookup_counts_cache_hits():
    dag = ProofDAG()
    dag.mark_proven_direct("L", "ledger")
    assert dag.is_proven("L")
    dag.get_or_create("L")  # revisiting a proven node
    assert dag.cache_hits == 1


def test_cycle_child_equals_goal():
    dag = ProofDAG()
    assert dag.would_create_cycle("G", ["G"])


def test_cycle_child_in_ancestors():
    dag = ProofDAG()
    assert dag.would_create_cycle("G", ["A"], ancestors={goal_hash("A")})


def test_cycle_via_committed_edges():
    dag = ProofDAG()
    # A decomposes to B (committed). Now decomposing B into [A] would close a cycle.
    dag.commit_decomposition("A", "sketchA", ["B"])
    assert dag.would_create_cycle("B", ["A"])


def test_commit_decomposition_rejects_cycle():
    dag = ProofDAG()
    with pytest.raises(CycleError):
        dag.commit_decomposition("G", "sketch", ["G"])


def test_mark_proven_via_children_requires_all_proven():
    dag = ProofDAG()
    dag.commit_decomposition("G", "sketch", ["A", "B"])
    dag.mark_proven_direct("A", "la")
    with pytest.raises(ValueError):
        dag.mark_proven_via_children("G")  # B not proven yet
    dag.mark_proven_direct("B", "lb")
    node = dag.mark_proven_via_children("G")
    assert node.state is NodeState.PROVEN


def test_assemble_marks_shared_nodes():
    dag = ProofDAG()
    # G -> [A, B]; A -> [C]; B -> [C]; C proven directly (shared).
    dag.commit_decomposition("G", "sg", ["A", "B"])
    dag.commit_decomposition("A", "sa", ["C"])
    dag.commit_decomposition("B", "sb", ["C"])
    dag.mark_proven_direct("C", "lc")
    for g in ["A", "B"]:
        dag.mark_proven_via_children(g)
    dag.mark_proven_via_children("G")
    tree = dag.assemble("G")
    assert tree["goal"] == "G" and tree["state"] == "proven"
    # C appears under both A and B; the second expansion is marked shared.
    flat = repr(tree)
    assert "shared" in flat


def test_stats():
    dag = ProofDAG()
    dag.mark_proven_direct("A", "la")
    dag.get_or_create("B")
    s = dag.stats()
    assert s["nodes"] == 2 and s["proven"] == 1
