"""Shared fixtures + ledger builders for the test suite."""
import copy
import json
from pathlib import Path

import pytest

from agent.gates.toolkit import load_toolkit

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "agent" / "gates" / "examples" / "squares_mod4.json"


@pytest.fixture(scope="session")
def toolkit():
    return load_toolkit()


@pytest.fixture
def example_ledger_dict():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def minimal_ledger(**overrides):
    """A tiny structurally-valid ledger: one given + one conclusion."""
    base = {
        "problem": "demo",
        "claim": "A demo theorem.",
        "steps": [
            {"id": "s1", "claim": "A hypothesis.", "justification": "given", "depends_on": []},
            {"id": "s2", "claim": "A demo theorem.", "justification": "conclusion",
             "depends_on": ["s1"]},
        ],
    }
    base.update(overrides)
    return base


def clone(d):
    return copy.deepcopy(d)
