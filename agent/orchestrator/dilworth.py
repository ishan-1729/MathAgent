"""Dilworth width + Mirsky/critical-path `upward_rank` over a proof-DAG poset (P2, §7).

A *faithful, dependency-free stdlib port* of Forge's `scripts/orchestrator/scheduler.py`
(`dilworth_width` + iterative Hopcroft-Karp + bitmask transitive closure) and its
`compute_upward_ranks` (`scripts/tv-feature-graph.py`). It exists to give the AND-node fan-out a
*principled* parallel width instead of a guessed cap.

.. note::

    **ASPIRATIONAL / NOT A LOAD-BEARING THROUGHPUT WIN (be honest).** The width this module
    computes caps a *safe-parallel fan-out that does not exist yet*: there is **no concurrent
    executor** in the harness — `dag_driver._prove_and_children` proves an AND-node's children
    **serially** (no asyncio / threads / processes anywhere). So the cap never speeds anything up
    today; it only bounds an as-yet-unbuilt parallel scheduler. Worse, for the common case of a
    *flat* AND-decomposition the committed children are precedence-independent, so **width ==
    child_count** and the Dilworth cap is the trivial answer — it rarely binds against the
    `min(width, budget, fanout_cap)` it feeds. `upward_rank` is computed but **decorative**: it is
    used only as a *third* tie-break sort key (behind `_critical_path_depths` + leverage), and for a
    flat plan every rank is 0, so it does not change ordering — which is in any case
    outcome-irrelevant (results are order-independent by the DAG's commit invariant). Treat both as
    *correct theorems doing no real work yet*, kept for when a concurrent executor lands, not as a
    realized parallel-throughput mechanism.

The two order-theory facts this module computes:

  * **Dilworth's theorem** — the largest *antichain* (the most nodes that are mutually
    precedence-independent, i.e. can be proven fully in parallel) equals the **minimum chain (path)
    cover** of the poset. For a DAG, the minimum *path* cover is `n - max_bipartite_matching` over the
    bipartite "reachability" graph (König): an edge `u_left -> v_right` exists iff `v` is strictly
    reachable from `u` in the **transitive closure**. Using full reachability (not just direct edges)
    makes the path cover a true *chain* cover, so the result is the exact Dilworth number.
        - A **deep chain** (a totally-ordered set) has width 1 — every node is reachable from the
          first, so the matching saturates all but one node: `n - (n-1) = 1`. (Don't over-spawn.)
        - A **wide antichain** (no edges) has width n — no node reaches any other, matching is 0,
          width `n - 0 = n`.
        - **Dilworth is undefined off a poset**, so a cyclic input RAISES `CyclicPosetError` rather
          than silently returning a number (which would mask the schedule fault).

  * **Mirsky's theorem (the dual)** — `upward_rank(u)` = the length of the longest chain *below* u =
    the longest path from u to a leaf (a node with no remaining dependents that depend on it). This is
    the `(max,+)` critical-path depth: a leaf has rank 0; otherwise `rank(u) = 1 + max(rank(child))`.
    The deepest critical chain leads scheduling so the irreducible-sequential part starts first.

Conventions (edge direction). An edge `(u, v)` means **u must be proven before v** — u is a
*prerequisite* of v (u → v, "u precedes v"). For an AND-node's committed children this relation is
normally *empty* (the children are precedence-independent by construction; the acyclicity guard
ensures it), in which case the width is exactly the number of children and ranks are all 0. The
module nonetheless accepts arbitrary intra-set precedence edges so it is correct if a caller ever
passes a partially-ordered open set.

Everything here is pure stdlib, iterative (NON-recursive — a long chain cannot blow Python's
recursion limit), and side-effect-free.
"""
from __future__ import annotations

from collections import deque
from typing import Hashable, Iterable, Optional

Node = Hashable
Edge = tuple[Node, Node]


class CyclicPosetError(ValueError):
    """Raised when a width/closure is requested over a CYCLIC input. Dilworth/Mirsky are statements
    about partial orders; a cycle makes the antichain width undefined, so we fail loud rather than
    return a misleading number (Forge: a silent number 'masks exactly the schedule fault')."""


# --------------------------------------------------------------------------------------------------
# Adjacency / topo helpers (edge (u, v) == "u precedes v" == u -> v).
# --------------------------------------------------------------------------------------------------
def _build_adj(nodes: Iterable[Node], edges: Iterable[Edge]) -> dict[Node, list[Node]]:
    """Adjacency `u -> [successors]` over exactly `nodes`. Edges touching a node outside `nodes` are
    dropped (the relation is restricted to the given set). Parallel/duplicate edges are de-duplicated
    so reachability and matching are not double-counted."""
    adj: dict[Node, list[Node]] = {u: [] for u in nodes}
    seen: set[Edge] = set()
    for u, v in edges:
        if u in adj and v in adj and u != v and (u, v) not in seen:
            seen.add((u, v))
            adj[u].append(v)
    return adj


def _topo_order(adj: dict[Node, list[Node]]) -> Optional[list[Node]]:
    """Kahn topological order of `adj`; None when the graph is CYCLIC. Iterative (no recursion)."""
    indeg: dict[Node, int] = {u: 0 for u in adj}
    for u in adj:
        for v in adj[u]:
            indeg[v] += 1
    queue = [u for u, d in indeg.items() if d == 0]
    order: list[Node] = []
    while queue:
        u = queue.pop()
        order.append(u)
        for v in adj[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                queue.append(v)
    return order if len(order) == len(adj) else None


def _transitive_closure(adj: dict[Node, list[Node]], order: list[Node]) -> dict[Node, set[Node]]:
    """Reachability `u -> {all v strictly reachable from u}` (excludes u). Single-pass bitmask DP in
    REVERSE topological order: `reach(u) = OR over successors c of (bit(c) | reach(c))`. The memo is
    only sound on a DAG and only in that order (a child's mask is final before its parent is read).
    Ported from Forge's `_transitive_closure`."""
    index = {u: i for i, u in enumerate(order)}
    names = order
    masks: dict[Node, int] = {u: 0 for u in adj}
    for u in reversed(order):           # reverse topo: every successor's mask is already final
        m = 0
        for v in adj[u]:
            m |= (1 << index[v]) | masks[v]
        masks[u] = m
    reach: dict[Node, set[Node]] = {}
    for u in adj:
        m = masks[u]
        out: set[Node] = set()
        while m:
            low = m & -m
            out.add(names[low.bit_length() - 1])
            m ^= low
        reach[u] = out
    return reach


# --------------------------------------------------------------------------------------------------
# Iterative Hopcroft-Karp maximum bipartite matching (NON-recursive).
# --------------------------------------------------------------------------------------------------
def _max_bipartite_matching(adj_right: dict[Node, list[Node]]) -> int:
    """Hopcroft-Karp maximum bipartite matching — fully ITERATIVE. `adj_right[u]` lists the right-side
    vertices the left vertex `u` may match. Left and right are disjoint copies of the node set (the
    standard min-path-cover encoding). Returns the matching size.

    Faithful port of Forge's `_max_bipartite_matching`: BFS layers the free left vertices, an
    explicit-stack DFS augments along vertex-disjoint shortest paths. O(E*sqrt(V)); no recursion, so a
    long augmenting path (closure paths reach O(V)) cannot blow Python's ~1000-frame limit."""
    INF = float("inf")
    match_l: dict[Node, Optional[Node]] = {u: None for u in adj_right}
    match_r: dict[Node, Optional[Node]] = {}
    for vs in adj_right.values():
        for v in vs:
            if v not in match_r:
                match_r[v] = None
    dist: dict[Node, float] = {}

    def bfs() -> bool:
        """Layer the free left vertices; True iff some augmenting path exists."""
        dist.clear()
        queue: deque[Node] = deque()
        for u in adj_right:
            if match_l[u] is None:
                dist[u] = 0
                queue.append(u)
        found_free = False
        while queue:
            u = queue.popleft()
            for v in adj_right[u]:
                w = match_r[v]
                if w is None:
                    found_free = True
                elif w not in dist:
                    dist[w] = dist[u] + 1
                    queue.append(w)
        return found_free

    def augment(root: Node) -> bool:
        """Iterative layered DFS — the explicit-stack translation of the recursive augmenting search.
        Frame: [u, edge-iterator, pending right-vertex awaiting child result]."""
        frames: list[list] = [[root, iter(adj_right[root]), None]]
        success = False
        while frames:
            frame = frames[-1]
            u = frame[0]
            if frame[2] is not None:
                v = frame[2]
                frame[2] = None
                if success:
                    match_r[v] = u
                    match_l[u] = v
                    frames.pop()
                    continue          # success propagates to the parent frame
            advanced = False
            for v in frame[1]:
                w = match_r[v]
                if w is None:
                    match_r[v] = u
                    match_l[u] = v
                    success = True
                    frames.pop()
                    advanced = True
                    break
                if dist.get(w, INF) == dist.get(u, INF) + 1:
                    frame[2] = v
                    success = False
                    frames.append([w, iter(adj_right[w]), None])
                    advanced = True
                    break
            if advanced:
                continue
            dist[u] = INF             # dead end: prune u from this phase's layered graph
            success = False
            frames.pop()
        return success

    matching = 0
    while bfs():
        for u in adj_right:
            if match_l[u] is None and augment(u):
                matching += 1
    return matching


# --------------------------------------------------------------------------------------------------
# Public API.
# --------------------------------------------------------------------------------------------------
def dilworth_width(nodes: Iterable[Node], edges: Iterable[Edge]) -> int:
    """The maximum-antichain width of the poset (`nodes`, `edges`), where edge `(u, v)` means
    "u precedes v" (u -> v). By Dilworth's theorem this is the minimum chain cover, computed as
    `n - max_bipartite_matching` over the TRANSITIVE CLOSURE (König).

    Returns 0 for an empty node set; otherwise >= 1.

    RAISES `CyclicPosetError` on a cyclic input (Dilworth is undefined off a poset)."""
    node_list = list(nodes)
    adj = _build_adj(node_list, edges)
    n = len(adj)
    if n == 0:
        return 0
    order = _topo_order(adj)
    if order is None:
        raise CyclicPosetError(
            "dilworth_width: input relation is CYCLIC — the antichain width is undefined off a poset")
    reach = _transitive_closure(adj, order)
    # Bipartite edges: u (left) -> v (right) iff v is strictly reachable from u.
    bip = {u: list(reach[u]) for u in adj}
    matching = _max_bipartite_matching(bip)
    return n - matching


def upward_rank(nodes: Iterable[Node], edges: Iterable[Edge]) -> dict[Node, int]:
    """The Mirsky / critical-path depth of every node: `rank(u)` = the longest chain *below* u = the
    longest path from u to a leaf. Edge `(u, v)` = "u precedes v" (u -> v), so v is *below* u in the
    precedence order; a node with no outgoing precedence edge (a leaf / sink of the relation) has rank
    0, otherwise `rank(u) = 1 + max(rank(v) for v a successor)`.

    Computed ITERATIVELY in reverse Kahn order (faithful port of Forge's `compute_upward_ranks`): no
    recursion, so a long chain cannot blow the recursion limit. Nodes trapped on a cycle (an input that
    is not a poset) keep rank 0 rather than crashing — callers that need a poset guarantee should call
    `dilworth_width` (which RAISES on a cycle) first.

    Returns a `{node: rank}` map covering exactly `nodes`."""
    node_list = list(nodes)
    adj = _build_adj(node_list, edges)             # u -> [successors v that u precedes]
    # parents[v] = nodes u whose rank depends on v (u precedes v, so v is below u).
    parents: dict[Node, list[Node]] = {u: [] for u in adj}
    pending: dict[Node, int] = {u: len(adj[u]) for u in adj}
    for u in adj:
        for v in adj[u]:
            parents[v].append(u)
    rank: dict[Node, int] = {u: 0 for u in adj}
    queue = [u for u, n in pending.items() if n == 0]  # leaves (no successors below them)
    while queue:
        u = queue.pop()
        for p in parents[u]:
            if rank[u] + 1 > rank[p]:
                rank[p] = rank[u] + 1
            pending[p] -= 1
            if pending[p] == 0:
                queue.append(p)
    return rank
