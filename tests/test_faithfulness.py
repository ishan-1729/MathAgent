"""Tests for the adversarial statement-faithfulness panel (model-agnostic aggregation)."""
from agent.orchestrator.faithfulness import (
    SingleVerdict, adversarial_check, PanelFaithfulnessChecker, ScriptedFaithfulnessChecker,
    DEFAULT_LENSES,
)


def _judge_all_faithful(claim, src, name, lens):
    return SingleVerdict(lens=lens, faithful=True)


def _judge_one_objection(claim, src, name, lens):
    bad = lens == "vacuity"
    return SingleVerdict(lens=lens, faithful=not bad, issues=["vacuously true"] if bad else [])


def test_unanimous_faithful():
    v = adversarial_check("claim", "lean", "thm", _judge_all_faithful)
    assert v.faithful and v.n_unfaithful == 0
    assert len(v.votes) == len(DEFAULT_LENSES)


def test_single_objection_fails_by_default():
    v = adversarial_check("claim", "lean", "thm", _judge_one_objection)
    assert not v.faithful            # max_unfaithful=0 -> any objection rejects
    assert v.n_unfaithful == 1
    assert any("vacuity" in i for i in v.issues)


def test_tolerance_allows_one_objection():
    v = adversarial_check("claim", "lean", "thm", _judge_one_objection, max_unfaithful=1)
    assert v.faithful


def test_panel_checker():
    chk = PanelFaithfulnessChecker(_judge_one_objection)
    assert not chk.check("c", "l", "t").faithful
    chk2 = PanelFaithfulnessChecker(_judge_all_faithful)
    assert chk2.check("c", "l", "t").faithful


def test_scripted_checker():
    assert ScriptedFaithfulnessChecker(True).check("c", "l", "t").faithful
    bad = ScriptedFaithfulnessChecker(False, issues=["wrong quantifier"]).check("c", "l", "t")
    assert not bad.faithful and "wrong quantifier" in bad.issues
