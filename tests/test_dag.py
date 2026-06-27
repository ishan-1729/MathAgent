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


# ==================================================================================================
# P5 — FIRST-CLASS NodeState.LEAN_VERIFIED at the DAG layer (dominates soft PROVEN; counts as proven
# EVERYWHERE — cache-hit, is_proven, assemble, proof_bundle, stats, AND-composition).
# ==================================================================================================

def test_mark_proven_direct_default_is_soft_proven():
    # BYTE-IDENTICAL default: with no lean_verified flag a direct proof is soft PROVEN (not the new
    # state), exactly as before this feature — the offline path never mints LEAN_VERIFIED.
    dag = ProofDAG()
    n = dag.mark_proven_direct("L", "ledger")
    assert n.state is NodeState.PROVEN
    assert n.lean_verified is False


def test_mark_proven_direct_lean_verified_sets_first_class_state():
    # A Lean-confirmed leaf is the first-class LEAN_VERIFIED state (dominates PROVEN) AND carries the
    # annotation in lockstep. It is a success/terminal state and reads as proven.
    dag = ProofDAG()
    n = dag.mark_proven_direct("L", "ledger", lean_verified=True)
    assert n.state is NodeState.LEAN_VERIFIED
    assert n.lean_verified is True
    assert n.state.is_success and n.state.is_terminal
    assert n.proven                                   # `.proven` is is_success -> True for LEAN_VERIFIED
    assert dag.is_proven("L")
    assert dag.stats()["proven"] == 1                 # counted as proven


def test_lean_verified_node_is_reused_via_memo_cache_hit_like_proven():
    # A LEAN_VERIFIED node short-circuits the memo exactly like a PROVEN one: revisiting it counts a
    # cache hit (the `.proven` chokepoint accepts LEAN_VERIFIED).
    dag = ProofDAG()
    dag.mark_proven_direct("L", "ledger", lean_verified=True)
    assert dag.is_proven("L")
    n = dag.get_or_create("L")                        # revisit
    assert n.state is NodeState.LEAN_VERIFIED
    assert dag.cache_hits == 1


def test_lean_verified_node_renders_through_assemble_and_bundle():
    # assemble()/proof_bundle() treat a LEAN_VERIFIED leaf as proven (rendered, not skipped).
    dag = ProofDAG()
    dag.mark_proven_direct("L", "the ledger", lean_verified=True)
    tree = dag.assemble("L")
    assert tree["state"] == "lean_verified"           # the real first-class state is reported
    bundle = dag.proof_bundle("L")
    assert "the ledger" in bundle                     # a LEAN_VERIFIED node is serialized as proven


def test_and_composition_lean_verified_iff_sketch_and_all_children_lean_verified():
    # AND-node: parent is LEAN_VERIFIED iff sketch_lean_verified AND every child LEAN_VERIFIED.
    dag = ProofDAG()
    dag.commit_decomposition("G", "sketch", ["A", "B"])
    dag.mark_proven_direct("A", "la", lean_verified=True)
    dag.mark_proven_direct("B", "lb", lean_verified=True)
    g = dag.get_or_create("G")
    g.sketch_lean_verified = True                     # the composition compiled (driver stamps this)
    node = dag.mark_proven_via_children("G")
    assert node.state is NodeState.LEAN_VERIFIED
    assert node.lean_verified is True


def test_and_composition_one_soft_child_keeps_parent_soft_proven():
    # A single soft-PROVEN child -> parent stays soft PROVEN even though the sketch compiled and the
    # other child is LEAN_VERIFIED (the LEAP rule needs EVERY child Lean-discharged).
    dag = ProofDAG()
    dag.commit_decomposition("G", "sketch", ["A", "B"])
    dag.mark_proven_direct("A", "la", lean_verified=True)
    dag.mark_proven_direct("B", "lb")                 # soft PROVEN child
    g = dag.get_or_create("G")
    g.sketch_lean_verified = True
    node = dag.mark_proven_via_children("G")          # all children proven (A LEAN_VERIFIED, B PROVEN)
    assert node.state is NodeState.PROVEN             # NOT LEAN_VERIFIED
    assert node.lean_verified is False


def test_and_composition_no_sketch_verifier_keeps_parent_soft_proven():
    # The byte-identical default: sketch_lean_verified=False (no sketch verifier) -> parent soft PROVEN
    # even with all children LEAN_VERIFIED.
    dag = ProofDAG()
    dag.commit_decomposition("G", "sketch", ["A", "B"])
    dag.mark_proven_direct("A", "la", lean_verified=True)
    dag.mark_proven_direct("B", "lb", lean_verified=True)
    node = dag.mark_proven_via_children("G")          # sketch_lean_verified left False
    assert node.state is NodeState.PROVEN
    assert node.lean_verified is False


def test_stale_lean_verified_certificate_is_evicted_on_context_change():
    # A LEAN_VERIFIED certificate minted under one context is STALE (and re-attemptable) under another:
    # the same eviction that resets a stale PROVEN clears the LEAN_VERIFIED state + its Lean stamps.
    dag = ProofDAG(context="ctxA")
    dag.mark_proven_direct("L", "ledger", lean_verified=True)
    assert dag.get("L" and goal_hash("L")) is not None
    dag.context = "ctxB"                              # the context changed (toolkit/gate/denylist edit)
    n = dag.get_or_create("L")                        # read-through eviction
    assert n.state is NodeState.OPEN                  # stale -> reset to OPEN (a cache MISS)
    assert n.lean_verified is False                   # the stale Lean stamp is cleared
    assert dag.stale_invalidations == 1
