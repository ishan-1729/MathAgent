"""Tests for the adversarial statement-faithfulness panel (model-agnostic aggregation)."""
import pytest

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


# ---- judge-exception isolation: a crashing lens must default to UNFAITHFUL, not propagate ----

def _judge_one_raises(claim, src, name, lens):
    if lens == "quantifiers_domain":
        raise RuntimeError("network timeout")
    return SingleVerdict(lens=lens, faithful=True)


def test_raising_judge_counts_as_unfaithful_and_does_not_propagate():
    # No exception escapes; the crashing lens is recorded as an unfaithful vote (default-closed).
    v = adversarial_check("claim", "lean", "thm", _judge_one_raises)
    assert not v.faithful
    assert v.n_unfaithful == 1
    assert len(v.votes) == len(DEFAULT_LENSES)
    crashed = next(vote for vote in v.votes if vote.lens == "quantifiers_domain")
    assert not crashed.faithful
    assert any("RuntimeError" in i for i in crashed.issues)
    assert any("quantifiers_domain" in i for i in v.issues)


def test_raising_judge_through_panel_checker():
    chk = PanelFaithfulnessChecker(_judge_one_raises)
    assert not chk.check("c", "l", "t").faithful  # crash must not propagate or pass


def test_malformed_or_mislabeled_judge_vote_fails_closed():
    malformed = PanelFaithfulnessChecker(lambda *_args: {"faithful": True}, lenses=["strength"])
    verdict = malformed.check("claim", "source", "theorem")
    assert not verdict.faithful and "SingleVerdict" in verdict.issues[0]

    mislabeled = PanelFaithfulnessChecker(
        lambda *_args: SingleVerdict("vacuity", True), lenses=["strength"])
    verdict = mislabeled.check("claim", "source", "theorem")
    assert not verdict.faithful and "unexpected lens" in verdict.issues[0]

    truthy_string = PanelFaithfulnessChecker(
        lambda *_args: SingleVerdict("strength", "false"), lenses=["strength"])
    verdict = truthy_string.check("claim", "source", "theorem")
    assert not verdict.faithful and verdict.n_unfaithful == 1


@pytest.mark.parametrize("value", [-1, 1, 4, True, 0.5])
def test_panel_rejects_invalid_or_vacuously_permissive_tolerance(value):
    with pytest.raises(ValueError, match="max_unfaithful"):
        PanelFaithfulnessChecker(_judge_all_faithful, lenses=["only"], max_unfaithful=value)
