"""Tests for the Dilworth-width + upward_rank order-theory module (P2, forge_relevance_study §7).

Edge convention throughout: an edge ``(u, v)`` means "u precedes v" (u must be proven before v),
i.e. u -> v with v BELOW u in the precedence order.
"""
import pytest

from agent.orchestrator.dilworth import (
    dilworth_width, upward_rank, CyclicPosetError,
)


# ==================================================================================================
# dilworth_width: antichain = minimum path cover
# ==================================================================================================

def test_empty_graph_has_width_zero():
    assert dilworth_width([], []) == 0


def test_singleton_has_width_one():
    assert dilworth_width(["a"], []) == 1


def test_wide_antichain_width_equals_n():
    # No edges -> every node is mutually independent -> the whole set is one antichain of size n.
    nodes = ["a", "b", "c", "d", "e"]
    assert dilworth_width(nodes, []) == len(nodes)


def test_deep_chain_has_width_one():
    # a -> b -> c -> d -> e is a totally ordered chain: no two are independent -> width 1.
    nodes = ["a", "b", "c", "d", "e"]
    edges = [("a", "b"), ("b", "c"), ("c", "d"), ("d", "e")]
    assert dilworth_width(nodes, edges) == 1


def test_chain_width_one_even_with_only_direct_edges():
    # The closure (not just direct edges) is what makes a path cover a true CHAIN cover: a -> b -> c
    # has a transitively-reachable a->c, so all three lie on one chain -> width 1.
    assert dilworth_width(["a", "b", "c"], [("a", "b"), ("b", "c")]) == 1


def test_hand_poset_antichain_equals_min_path_cover():
    # A classic diamond: r precedes both x and y; both x and y precede s.
    #        r
    #       / \
    #      x   y      (x, y incomparable -> the maximum antichain is {x, y}, width 2)
    #       \ /
    #        s
    # Min chain cover = 2 (e.g. r->x->s and y), and Dilworth width = max antichain = 2.
    nodes = ["r", "x", "y", "s"]
    edges = [("r", "x"), ("r", "y"), ("x", "s"), ("y", "s")]
    assert dilworth_width(nodes, edges) == 2


def test_two_disjoint_chains_width_two():
    # Two independent chains a->b->c and d->e->f: the widest antichain picks one node per chain = 2.
    nodes = ["a", "b", "c", "d", "e", "f"]
    edges = [("a", "b"), ("b", "c"), ("d", "e"), ("e", "f")]
    assert dilworth_width(nodes, edges) == 2


def test_three_parallel_singletons_plus_a_chain():
    # {p, q, r} mutually independent, plus a chain s->t->u independent of them.
    # Max antichain = {p, q, r} ∪ one of {s,t,u} = 4.
    nodes = ["p", "q", "r", "s", "t", "u"]
    edges = [("s", "t"), ("t", "u")]
    assert dilworth_width(nodes, edges) == 4


def test_fan_out_one_root_to_k_leaves():
    # root precedes k mutually-independent leaves: the k leaves form the maximum antichain (k), since
    # root is comparable to every leaf so it cannot extend the antichain.
    leaves = [f"L{i}" for i in range(5)]
    edges = [("root", l) for l in leaves]
    assert dilworth_width(["root", *leaves], edges) == 5


def test_edges_to_nodes_outside_set_are_ignored():
    # An edge mentioning a node not in `nodes` is dropped; the two listed nodes stay independent.
    assert dilworth_width(["a", "b"], [("a", "z"), ("z", "b")]) == 2


def test_duplicate_edges_do_not_change_width():
    nodes = ["a", "b", "c"]
    edges = [("a", "b"), ("a", "b"), ("b", "c"), ("b", "c")]
    assert dilworth_width(nodes, edges) == 1


def test_cyclic_input_raises():
    # Dilworth is undefined off a poset: a cycle must RAISE, not silently return a number.
    with pytest.raises(CyclicPosetError):
        dilworth_width(["a", "b", "c"], [("a", "b"), ("b", "c"), ("c", "a")])


def test_self_loop_is_dropped_not_cyclic():
    # A self-edge (u, u) is not a precedence relation; it is dropped, leaving an antichain.
    assert dilworth_width(["a", "b"], [("a", "a")]) == 2


def test_long_chain_does_not_blow_recursion_limit():
    # 3000-node chain: the iterative Hopcroft-Karp / closure must not hit Python's recursion limit.
    n = 3000
    nodes = [f"n{i}" for i in range(n)]
    edges = [(f"n{i}", f"n{i+1}") for i in range(n - 1)]
    assert dilworth_width(nodes, edges) == 1


# ==================================================================================================
# upward_rank: Mirsky / critical-path depth (longest path to a leaf)
# ==================================================================================================

def test_rank_singleton_is_zero():
    assert upward_rank(["a"], []) == {"a": 0}


def test_rank_antichain_all_zero():
    nodes = ["a", "b", "c"]
    assert upward_rank(nodes, []) == {"a": 0, "b": 0, "c": 0}


def test_rank_chain_is_distance_to_leaf():
    # a -> b -> c -> d: d is the leaf (rank 0), c=1, b=2, a=3 (the critical-chain depth).
    nodes = ["a", "b", "c", "d"]
    edges = [("a", "b"), ("b", "c"), ("c", "d")]
    assert upward_rank(nodes, edges) == {"a": 3, "b": 2, "c": 1, "d": 0}


def test_rank_takes_the_longest_path():
    # r precedes x and y; x precedes a longer tail than y. rank(r) follows the LONGEST chain below it.
    #   r -> x -> y2 -> y3   (len 3)
    #   r -> z              (len 1)
    nodes = ["r", "x", "y2", "y3", "z"]
    edges = [("r", "x"), ("x", "y2"), ("y2", "y3"), ("r", "z")]
    ranks = upward_rank(nodes, edges)
    assert ranks["y3"] == 0
    assert ranks["z"] == 0
    assert ranks["y2"] == 1
    assert ranks["x"] == 2
    assert ranks["r"] == 3            # the long chain, not the short one


def test_rank_diamond():
    # r -> x -> s and r -> y -> s. Both branches length 2, so rank(r)=2, rank(x)=rank(y)=1, rank(s)=0.
    nodes = ["r", "x", "y", "s"]
    edges = [("r", "x"), ("r", "y"), ("x", "s"), ("y", "s")]
    assert upward_rank(nodes, edges) == {"r": 2, "x": 1, "y": 1, "s": 0}


def test_rank_on_long_chain_iterative():
    # 3000-deep chain must not recurse; the head node's rank equals the chain length.
    n = 3000
    nodes = [f"n{i}" for i in range(n)]
    edges = [(f"n{i}", f"n{i+1}") for i in range(n - 1)]
    ranks = upward_rank(nodes, edges)
    assert ranks["n0"] == n - 1
    assert ranks[f"n{n-1}"] == 0


def test_rank_covers_exactly_the_node_set():
    nodes = ["a", "b", "c"]
    ranks = upward_rank(nodes, [("a", "b")])
    assert set(ranks) == set(nodes)


def test_rank_does_not_crash_on_cycle():
    # upward_rank does not promise a poset (dilworth_width does); a cyclic input keeps trapped nodes
    # at rank 0 rather than raising/recursing forever.
    ranks = upward_rank(["a", "b", "c"], [("a", "b"), ("b", "c"), ("c", "a")])
    assert ranks == {"a": 0, "b": 0, "c": 0}
