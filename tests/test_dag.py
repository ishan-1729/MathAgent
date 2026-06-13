"""Tests for the AND-OR proof DAG: deep-hash memoization, acyclicity, assembly."""
import pytest

from agent.orchestrator.dag import ProofDAG, goal_hash, canonical_form, CycleError
from agent.orchestrator.state import NodeState


def test_goal_hash_normalizes_whitespace():
    assert goal_hash("a  +   b") == goal_hash("a + b")
    assert goal_hash(" x = y\n") == goal_hash("x = y")


def test_goal_hash_is_case_sensitive():
    # x and X are different variables — must not collide.
    assert goal_hash("x^2") != goal_hash("X^2")


# ---- semantic canonicalization: meaning-preserving surface differences MUST collapse ----

@pytest.mark.parametrize("a,b", [
    ("n² − n is even", "n^2 - n is even"),                 # superscript + unicode minus
    ("∀ n, P n", "for all n, P n"),                        # quantifier word vs symbol
    ("a ≤ b", "a <= b"),                                   # relation symbol
    ("a ∣ b", "a divides b"),                              # divisibility word vs symbol
    ("x ≠ y", "x != y"),
    ("a → b", "a implies b"),
    ("P if and only if Q", "P iff Q"),
    ("Prove that x = y", "x = y"),                         # leading imperative stripped
    ("$a + b$", "a + b"),                                  # math delimiters dropped
    ("f ( x , y )", "f(x,y)"),                             # operator/paren spacing
    ("x ∈ ℤ", "x in Int"),                                 # set membership + domain
    ("x₁ + x₂", "x_1 + x_2"),                              # subscripts
])
def test_canonical_form_folds_surface_differences(a, b):
    assert goal_hash(a) == goal_hash(b)


# ---- soundness: DISTINCT goals must NEVER collide (a false hit reuses the wrong proof) ----

@pytest.mark.parametrize("a,b", [
    ("x | y", "y | x"),                                    # divisibility is not symmetric
    ("a < b", "a > b"),
    ("x = y", "x ≠ y"),
    ("a - b", "b - a"),
    ("p divides n", "p divides m"),                        # different free variable
    ("x^2", "x^3"),
    ("forall n, P n", "exists n, P n"),
])
def test_canonical_form_never_merges_distinct_goals(a, b):
    assert goal_hash(a) != goal_hash(b)


def test_canonical_form_is_idempotent():
    once = canonical_form("∀ n, n² − n ≡ 0 (mod 2)")
    assert canonical_form(once) == once


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
