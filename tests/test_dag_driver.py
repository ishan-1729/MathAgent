"""Tests for the DAG orchestrator: direct proof, decomposition, memoization, cycles, budgets.

All offline: scripted prover/decomposer/reviewer stand in for the Codex focused prover.
"""
import json

import pytest

from agent.orchestrator.dag_driver import (
    DagDriver, ReviewVerdict, ScriptedDecomposer, ScriptedReviewer,
)
from agent.orchestrator.state import Budget, NodeState
from agent.orchestrator.trace import RunTrace
from agent.gates.toolkit import load_toolkit

TOOLKIT = load_toolkit()
OK_REVIEW = ReviewVerdict(useful=True, elementary=True)


def valid_ledger(goal: str) -> str:
    return json.dumps({"problem": "p", "claim": goal, "steps": [
        {"id": "s1", "claim": "setup", "justification": "given", "depends_on": []},
        {"id": "s2", "claim": goal, "justification": "conclusion", "depends_on": ["s1"]}]})


def sketch(goal: str, children: list[str]) -> str:
    steps = [{"id": f"L{i}", "claim": c, "justification": "lemma", "depends_on": []}
             for i, c in enumerate(children)]
    steps.append({"id": "c", "claim": goal, "justification": "conclusion",
                  "depends_on": [f"L{i}" for i in range(len(children))]})
    return json.dumps({"problem": "p", "claim": goal, "steps": steps})


BAD = json.dumps({"problem": "p", "claim": "?", "steps": [
    {"id": "s1", "claim": "heavy", "justification": "class_field_theory", "depends_on": []},
    {"id": "s2", "claim": "?", "justification": "conclusion", "depends_on": ["s1"]}]})


class DictProver:
    """prove(goal) -> mapping[goal] or a rejected default."""
    def __init__(self, mapping: dict[str, str], default: str = BAD):
        self.mapping = mapping
        self.default = default
        self.calls = 0

    def prove(self, goal: str, feedback=None) -> str:
        self.calls += 1
        return self.mapping.get(goal, self.default)


class DictDecomposer:
    def __init__(self, mapping: dict[str, tuple[str, list[str]]]):
        self.mapping = mapping

    def decompose(self, goal: str, feedback=None):
        return self.mapping.get(goal, ("", []))


def _driver(prover, decomposer=None, reviewer=None, budget=None, **kw):
    return DagDriver(prover, decomposer=decomposer, reviewer=reviewer, toolkit=TOOLKIT,
                     budget=budget or Budget(), trace=RunTrace("t"), **kw)


# ---- direct proof ----

def test_direct_proof():
    res = _driver(DictProver({"G": valid_ledger("G")})).run("G")
    assert res.proven
    assert res.dag.get(res.dag.get_or_create("G").key).proof_kind == "direct"
    assert res.dag.stats()["proven"] == 1


# ---- decomposition ----

def test_decomposition_then_children():
    prover = DictProver({"A": valid_ledger("A"), "B": valid_ledger("B")})  # G fails direct
    decomp = ScriptedDecomposer([(sketch("G", ["A", "B"]), ["A", "B"])])
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW])).run("G")
    assert res.proven
    tree = res.proof_tree()
    assert tree["kind"] == "decomposition"
    assert {c["goal"] for c in tree["children"]} == {"A", "B"}


# ---- memoization: a shared sub-lemma is proven once and reused ----

def test_shared_lemma_memoized():
    prover = DictProver({"C": valid_ledger("C")})  # A, B, G fail direct; C proves directly
    decomp = DictDecomposer({
        "G": (sketch("G", ["A", "B"]), ["A", "B"]),
        "A": (sketch("A", ["C"]), ["C"]),
        "B": (sketch("B", ["C"]), ["C"]),
    })
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW])).run("G")
    assert res.proven
    assert res.dag.cache_hits >= 1           # C reused on the second branch
    # C exists once as a node and is proven.
    c_node = res.dag.get(res.dag.get_or_create("C").key)
    assert c_node.proven and c_node.proof_kind == "direct"


# ---- acyclicity: a self-referential decomposition cannot loop forever ----

def test_cycle_is_refused_and_terminates():
    prover = DictProver({})  # everything fails direct
    decomp = DictDecomposer({
        "G": (sketch("G", ["A"]), ["A"]),
        "A": (sketch("A", ["G"]), ["G"]),  # would close a cycle back to G
    })
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW])).run("G")
    assert not res.proven  # and crucially, it returned (no infinite recursion)


# ---- reviewer veto ----

def test_reviewer_rejection_blocks_commit():
    prover = DictProver({"A": valid_ledger("A")})
    decomp = ScriptedDecomposer([(sketch("G", ["A"]), ["A"])])
    bad_review = ScriptedReviewer([ReviewVerdict(useful=False, elementary=True, notes=["trivial"])])
    res = _driver(prover, decomposer=decomp, reviewer=bad_review,
                  max_decomp_attempts=2).run("G")
    assert not res.proven


def test_reviewer_non_elementary_blocks_commit():
    prover = DictProver({"A": valid_ledger("A")})
    decomp = ScriptedDecomposer([(sketch("G", ["A"]), ["A"])])
    rev = ScriptedReviewer([ReviewVerdict(useful=True, elementary=False, notes=["uses ANT"])])
    assert not _driver(prover, decomposer=decomp, reviewer=rev, max_decomp_attempts=2).run("G").proven


# ---- sketch/children consistency ----

def test_sketch_lemma_mismatch_rejected():
    prover = DictProver({"A": valid_ledger("A")})
    # sketch cites lemma "X" but declares child "A" -> mismatch.
    decomp = ScriptedDecomposer([(sketch("G", ["X"]), ["A"])])
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                  max_decomp_attempts=2).run("G")
    assert not res.proven


# ---- no decomposer: direct failure is terminal ----

def test_no_decomposer_direct_failure():
    res = _driver(DictProver({})).run("G")
    assert not res.proven
    assert res.dag.get(res.dag.get_or_create("G").key).state in (
        NodeState.FAILED_GAP, NodeState.FAILED_ELEMENTARY)


# ---- depth limit ----

def test_depth_limit():
    prover = DictProver({"A": valid_ledger("A")})
    decomp = ScriptedDecomposer([(sketch("G", ["A"]), ["A"])])
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                  max_depth=0).run("G")
    assert not res.proven  # children live at depth 1 > max_depth 0


# ---- budget always terminates ----

def test_tiny_budget_terminates():
    prover = DictProver({})
    decomp = ScriptedDecomposer([(sketch("G", ["A"]), ["A"])])
    budget = Budget(max_llm_calls=2)
    res = _driver(prover, decomposer=decomp, reviewer=ScriptedReviewer([OK_REVIEW]),
                  budget=budget).run("G")
    assert not res.proven
    assert budget.calls_spent <= 2


def test_trace_has_final_event():
    res = _driver(DictProver({"G": valid_ledger("G")})).run("G")
    assert res.trace.by_kind("final")
    assert res.trace.by_kind("final")[0].data["proven"] is True
