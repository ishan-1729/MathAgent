"""Split-keyed memo tests (T100 P1 item 4): (semantic_goal_hash, proof_context_hash).

A goal stays SHARED across branches under one context (a hit), but a toolkit/gate/denylist/lemma
change must INVALIDATE a stale PROVEN (a miss) — the same goal under a changed elementary ruleset is
not the same certified result.
"""
import copy
import json
from dataclasses import replace

import pytest

from agent.gates.toolkit import Justification, load_toolkit
from agent.orchestrator.dag import (
    ProofDAG, goal_hash, semantic_goal_hash, proof_context_hash,
)
from agent.orchestrator.dag_driver import DagDriver, ScriptedReviewer, ReviewVerdict
from agent.orchestrator.driver import ScriptedProver  # noqa: F401 (parity import)
from agent.orchestrator.state import Budget, NodeState
from agent.orchestrator.trace import RunTrace

TOOLKIT = load_toolkit()


def valid_ledger(goal: str) -> str:
    return json.dumps({"problem": "p", "claim": goal, "steps": [
        {"id": "s1", "claim": "setup", "justification": "given", "depends_on": []},
        {"id": "s2", "claim": goal, "justification": "conclusion", "depends_on": ["s1"]}]})


class DictProver:
    def __init__(self, mapping, default=None):
        self.mapping = mapping
        self.default = default or json.dumps({"problem": "p", "claim": "?", "steps": [
            {"id": "s1", "claim": "x", "justification": "class_field_theory", "depends_on": []},
            {"id": "s2", "claim": "?", "justification": "conclusion", "depends_on": ["s1"]}]})
        self.calls = 0

    def prove(self, goal, feedback=None):
        self.calls += 1
        return self.mapping.get(goal, self.default)


# ---- semantic_goal_hash is the same identity as goal_hash (statement identity → sharing) ----

def test_semantic_goal_hash_is_goal_hash():
    assert semantic_goal_hash is goal_hash
    assert semantic_goal_hash("a + b") == semantic_goal_hash("a  +  b")
    assert semantic_goal_hash("x") != semantic_goal_hash("X")


# ---- proof_context_hash is stable, order-insensitive, and changes when the ruleset changes ----

def test_proof_context_hash_is_stable_and_deterministic():
    a = proof_context_hash(TOOLKIT)
    b = proof_context_hash(load_toolkit())   # a fresh identical load
    assert a == b
    assert isinstance(a, str) and len(a) == 16


def test_proof_context_hash_ignores_key_reordering():
    # Reordering the justification dict (same content) must NOT change the context hash.
    tk = load_toolkit()
    reordered = dict(reversed(list(tk.justifications.items())))
    tk2 = replace(tk, justifications=reordered)
    assert proof_context_hash(tk) == proof_context_hash(tk2)


def test_proof_context_hash_changes_when_toolkit_vocabulary_changes():
    tk = load_toolkit()
    base = proof_context_hash(tk)
    # Add a new allowed justification -> the elementary vocabulary changed -> different context.
    extra = dict(tk.justifications)
    extra["brand_new_method"] = Justification(key="brand_new_method", obligation="none")
    tk2 = replace(tk, justifications=extra)
    assert proof_context_hash(tk2) != base


def test_proof_context_hash_changes_when_denylist_changes():
    tk = load_toolkit()
    base = proof_context_hash(tk)
    tk2 = replace(tk, prose_terms=tk.prose_terms + ["newly banned method"])
    assert proof_context_hash(tk2) != base


def test_proof_context_hash_changes_when_obligation_retagged():
    # Re-tagging a justification's obligation (e.g. demanding descent now) is a context change.
    tk = load_toolkit()
    base = proof_context_hash(tk)
    js = dict(tk.justifications)
    js["algebra"] = replace(js["algebra"], obligation="descent")
    tk2 = replace(tk, justifications=js)
    assert proof_context_hash(tk2) != base


def test_proof_context_hash_changes_when_axiom_whitelist_changes():
    tk = load_toolkit()
    base = proof_context_hash(tk)
    tk2 = replace(tk, lean_axiom_whitelist=tk.lean_axiom_whitelist + ["sorryAx"])
    assert proof_context_hash(tk2) != base


# ---- ProofDAG split-keyed memo: same context => share (hit); changed context => miss ----

def test_same_context_shares_proven_certificate():
    ctx = proof_context_hash(TOOLKIT)
    dag = ProofDAG(context=ctx)
    dag.mark_proven_direct("L", valid_ledger("L"))
    assert dag.is_proven("L")
    # A re-visit under the SAME context is a cache hit (the certificate is reused).
    node = dag.get_or_create("L")
    assert node.proven
    assert dag.cache_hits == 1
    assert dag.stale_invalidations == 0


def test_changed_context_invalidates_stale_proven():
    # A PROVEN minted under context A is NOT reused when the DAG's context is B.
    ctx_a = "aaaaaaaaaaaaaaaa"
    ctx_b = "bbbbbbbbbbbbbbbb"
    dag = ProofDAG(context=ctx_a)
    node = dag.mark_proven_direct("L", valid_ledger("L"))
    assert node.proof_context == ctx_a
    # Now the toolkit/gate context changes: simulate by retargeting the DAG's context to B.
    dag.context = ctx_b
    # The stale PROVEN is a MISS: is_proven reports False and get_or_create evicts it to OPEN.
    assert not dag.is_proven("L")
    reopened = dag.get_or_create("L")
    assert reopened.state is NodeState.OPEN          # evicted (re-attemptable under the new context)
    assert reopened.proof is None and reopened.proof_kind is None
    assert dag.stale_invalidations == 1
    assert dag.cache_hits == 0                        # the stale cert was NOT counted as a share


def test_assemble_and_stats_do_not_report_a_context_stale_proven():
    # R1-fix (memo read-through): the read accessors must respect context validity the SAME way
    # get_or_create / is_proven do. A PROVEN minted under context A, then read back after the DAG's
    # context flips to B, is a STALE certificate and must NOT be reported as proven through
    # assemble() / stats() / proof_bundle() (those used to read node.proven/node.state DIRECTLY,
    # leaking a certificate the new context never admitted).
    ctx_a = "aaaaaaaaaaaaaaaa"
    ctx_b = "bbbbbbbbbbbbbbbb"
    dag = ProofDAG(context=ctx_a)
    dag.mark_proven_direct("L", valid_ledger("L"))
    # Sanity: under the minting context everything reports PROVEN.
    assert dag.assemble("L")["state"] == NodeState.PROVEN.value
    assert dag.stats()["proven"] == 1
    assert "PROOF LEDGER" in dag.proof_bundle("L")

    # Now MUTATE the context (toolkit/gate change) WITHOUT touching the node.
    dag.context = ctx_b

    # assemble() must NOT render the stale certificate as proven.
    tree = dag.assemble("L")
    assert tree["state"] != NodeState.PROVEN.value
    assert tree["state"] == NodeState.OPEN.value          # reported as a re-attemptable miss
    # stats() must NOT count the stale certificate.
    assert dag.stats()["proven"] == 0
    # proof_bundle() must NOT serialize the stale certificate.
    assert dag.proof_bundle("L") == ""
    # The node object itself is untouched by a read (only get_or_create/_evict_if_stale mutate).
    assert dag.nodes[goal_hash("L")].state is NodeState.PROVEN


def test_context_none_is_context_agnostic_back_compat():
    # A context-agnostic DAG (legacy callers) shares by goal identity only, never invalidates.
    dag = ProofDAG()  # context=None
    dag.mark_proven_direct("L", valid_ledger("L"))
    assert dag.is_proven("L")
    dag.get_or_create("L")
    assert dag.cache_hits == 1
    assert dag.stale_invalidations == 0


# ---- end-to-end: the driver stamps its context onto every certificate it mints ----

def _driver(prover, **kw):
    return DagDriver(prover, toolkit=TOOLKIT, budget=Budget(), trace=RunTrace("t"), **kw)


def test_driver_stamps_context_on_proven_node():
    d = _driver(DictProver({"G": valid_ledger("G")}))
    res = d.run("G")
    assert res.proven
    node = res.dag.get(res.dag.get_or_create("G").key)
    assert node.proof_context == d.context_hash == proof_context_hash(TOOLKIT)


def test_driver_dag_context_matches_toolkit():
    d = _driver(DictProver({}))
    assert d.dag.context == proof_context_hash(TOOLKIT)


def test_two_drivers_same_toolkit_share_context():
    a = _driver(DictProver({}))
    b = _driver(DictProver({}))
    assert a.context_hash == b.context_hash       # identical toolkit -> identical, shareable context
