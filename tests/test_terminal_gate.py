"""Tests for the DAG terminal gate (Layer 4 as the terminal authoritative gate) + proof bundling."""
import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from agent.orchestrator.dag import ProofDAG
from agent.orchestrator.dag_driver import DagDriver
from agent.orchestrator.state import Budget
from agent.orchestrator.trace import RunTrace
from agent.gates.toolkit import load_toolkit

TOOLKIT = load_toolkit()


def _valid(goal):
    return json.dumps({"problem": "p", "claim": goal, "steps": [
        {"id": "s1", "claim": "setup", "justification": "given", "depends_on": []},
        {"id": "s2", "claim": goal, "justification": "conclusion", "depends_on": ["s1"]}]})


class DictProver:
    def __init__(self, mapping):
        self.mapping = mapping

    def prove(self, goal, feedback=None):
        return self.mapping.get(goal, "{}")


@dataclass
class FakeTerminal:
    authoritative: bool
    certification_trusted: bool = False
    called_with: tuple = ()


# ---- proof bundle ----

def test_proof_bundle_direct():
    dag = ProofDAG()
    dag.mark_proven_direct("G", "LEDGER_TEXT")
    bundle = dag.proof_bundle("G")
    assert "GOAL: G" in bundle and "PROOF LEDGER: LEDGER_TEXT" in bundle


def test_proof_bundle_decomposition():
    dag = ProofDAG()
    dag.commit_decomposition("G", "SKETCH", ["A"])
    dag.mark_proven_direct("A", "LA")
    dag.mark_proven_via_children("G")
    bundle = dag.proof_bundle("G")
    assert "DECOMPOSITION SKETCH" in bundle and "SUB-LEMMAS" in bundle
    assert "GOAL: A" in bundle and "PROOF LEDGER: LA" in bundle


# ---- terminal gate wiring ----

def test_terminal_gate_called_on_proof():
    captured = {}

    def gate(goal, proof_text):
        captured["goal"] = goal
        captured["proof_text"] = proof_text
        return FakeTerminal(authoritative=True, certification_trusted=True)

    res = DagDriver(DictProver({"G": _valid("G")}), toolkit=TOOLKIT, budget=Budget(),
                    trace=RunTrace("t"), terminal_gate=gate).run("G")
    assert res.proven
    assert captured["goal"] == "G"
    assert "G" in captured["proof_text"]               # the bundle includes the goal
    assert res.terminal is not None and res.authoritative_elementary
    assert res.trace.by_kind("terminal_gate")


def test_untrusted_duck_typed_terminal_cannot_mint_authority():
    """Only the trusted certification boundary may mint the public authoritative bit."""
    res = DagDriver(
        DictProver({"G": _valid("G")}), toolkit=TOOLKIT, budget=Budget(), trace=RunTrace("t"),
        terminal_gate=lambda _goal, _proof: FakeTerminal(authoritative=True),
    ).run("G")
    assert res.proven and not res.authoritative_elementary
    event = res.trace.by_kind("terminal_gate")[-1]
    assert event.data["authoritative"] is False
    assert event.data["certification_trusted"] is False


@pytest.mark.parametrize("malformed", ["false", 1, object()])
def test_trusted_terminal_requires_literal_true_authority(malformed):
    """A trusted boundary must not truthy-promote malformed provider output."""
    res = DagDriver(
        DictProver({"G": _valid("G")}), toolkit=TOOLKIT, budget=Budget(), trace=RunTrace("t"),
        terminal_gate=lambda _goal, _proof: SimpleNamespace(
            authoritative=malformed, certification_trusted=True),
    ).run("G")
    assert res.proven and not res.authoritative_elementary
    assert res.trace.by_kind("terminal_gate")[-1].data["authoritative"] is False


def test_terminal_gate_rejection_blocks_authoritative():
    res = DagDriver(DictProver({"G": _valid("G")}), toolkit=TOOLKIT, budget=Budget(),
                    trace=RunTrace("t"),
                    terminal_gate=lambda g, p: FakeTerminal(authoritative=False)).run("G")
    assert res.proven                       # informally proven...
    assert not res.authoritative_elementary  # ...but the terminal Layer-4 gate rejected it


def test_no_terminal_gate_means_not_authoritative():
    res = DagDriver(DictProver({"G": _valid("G")}), toolkit=TOOLKIT, budget=Budget(),
                    trace=RunTrace("t")).run("G")
    assert res.proven
    assert res.terminal is None and not res.authoritative_elementary


def test_dag_result_sixth_positional_argument_remains_terminal():
    """Backward compatibility: candidate metadata must not shift the exported positional API."""
    from agent.orchestrator.dag_driver import DagResult

    terminal = FakeTerminal(authoritative=False)
    result = DagResult("G", True, ProofDAG(), RunTrace("compat"), Budget(), terminal)
    assert result.terminal is terminal
    assert result.candidate is None


def test_terminal_gate_skipped_when_unproven():
    called = {"n": 0}

    def gate(goal, proof_text):
        called["n"] += 1
        return FakeTerminal(authoritative=True)

    res = DagDriver(DictProver({}), toolkit=TOOLKIT, budget=Budget(max_llm_calls=5),
                    trace=RunTrace("t"), terminal_gate=gate).run("G")
    assert not res.proven and called["n"] == 0  # terminal gate only runs on a proven root
