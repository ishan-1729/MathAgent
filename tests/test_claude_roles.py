"""Offline checks for the first-class Claude roles (parsing + Protocol conformance).

No Claude CLI is invoked — ``_run_claude`` is monkeypatched to return canned fenced JSON, so these
assert each role (a) parses the CLI response into the right type and (b) satisfies the EXISTING
Protocol the DAG/tournament expect. Provider-agnostic by construction (the live CLI is never touched).
"""
import agent.tools.claude_roles as cr
from agent.tools.claude_cli import ClaudeConfig
from agent.tools.claude_roles import (
    ClaudeProver, ClaudeDecomposer, ClaudeReviewer, ClaudeComparator, ClaudeJudge,
    ClaudeFaithfulnessChecker, ClaudeCritic, ClaudeAuthor, ClaudeSynthesizer, make_claude_refiner,
)
from agent.orchestrator.driver import Prover
from agent.orchestrator.dag_driver import Decomposer, Reviewer, ReviewVerdict
from agent.orchestrator.population import Candidate, Comparator
from agent.orchestrator.tournament import (
    RevisionController, RevisionCritic, RevisionAuthor, Synthesizer,
)


class FakeToolkit:
    def allowed_keys(self):
        return {"algebra", "congruence", "descent", "bounding"}


def _patch(monkeypatch, response):
    """Make every _run_claude call (wherever referenced) return `response`, capturing prompts."""
    seen = {"prompts": [], "cfgs": []}

    def fake_run(prompt, cfg):
        seen["prompts"].append(prompt)
        seen["cfgs"].append(cfg)
        return response

    monkeypatch.setattr(cr, "_run_claude", fake_run)
    return seen


# --- Protocol conformance (structural; no CLI needed) -------------------------------------------

def test_roles_satisfy_their_protocols():
    tk = FakeToolkit()
    assert isinstance(ClaudeProver(tk), Prover)
    assert isinstance(ClaudeDecomposer(tk), Decomposer)
    assert isinstance(ClaudeReviewer(tk), Reviewer)
    assert isinstance(ClaudeComparator(), Comparator)
    assert isinstance(ClaudeJudge(), Comparator)
    assert isinstance(ClaudeCritic(), RevisionCritic)
    assert isinstance(ClaudeAuthor(), RevisionAuthor)
    assert isinstance(ClaudeSynthesizer(), Synthesizer)


def test_default_models_match_role_tiers():
    # prover/refiner=opus, decomposer/reviewer/faithfulness=sonnet, comparator/judge=sonnet
    # (roster rule: Haiku is not used anywhere, even as a bare-constructor default)
    assert ClaudeProver(FakeToolkit()).cfg.model == "opus"
    assert ClaudeDecomposer(FakeToolkit()).cfg.model == "sonnet"
    assert ClaudeReviewer(FakeToolkit()).cfg.model == "sonnet"
    assert ClaudeComparator().cfg.model == "sonnet"
    assert ClaudeJudge().cfg.model == "sonnet"
    assert ClaudeFaithfulnessChecker().cfg.model == "sonnet"


def test_spec_model_is_honored():
    # The registry pins model=spec.model; a custom ClaudeConfig must flow through unchanged.
    cfg = ClaudeConfig(model="opus")
    assert ClaudeComparator(cfg).cfg.model == "opus"


# --- Parsing (monkeypatched CLI) ----------------------------------------------------------------

def test_prover_returns_raw_ledger_text(monkeypatch):
    canned = '```json\n{"claim": "x", "steps": []}\n```'
    seen = _patch(monkeypatch, canned)
    out = ClaudeProver(FakeToolkit()).prove("prove x")
    assert out == canned                      # prover returns RAW text (gate parses downstream)
    assert "prove x" in seen["prompts"][0]
    assert seen["cfgs"][0].model == "opus"


def test_decomposer_extracts_children_from_lemma_steps(monkeypatch):
    sketch = (
        '```json\n{"problem": "p", "claim": "G", "steps": ['
        '{"id": "s1", "claim": "lemma A", "justification": "lemma", "depends_on": []},'
        '{"id": "s2", "claim": "lemma B", "justification": "lemma", "depends_on": []},'
        '{"id": "s3", "claim": "G", "justification": "conclusion", "depends_on": ["s1", "s2"]}'
        ']}\n```'
    )
    _patch(monkeypatch, sketch)
    text, children = ClaudeDecomposer(FakeToolkit()).decompose("prove G")
    assert text == sketch
    assert children == ["lemma A", "lemma B"]


def test_reviewer_parses_verdict(monkeypatch):
    _patch(monkeypatch, '```json\n{"useful": true, "elementary": false, "notes": ["uses Baker"]}\n```')
    v = ClaudeReviewer(FakeToolkit()).review("G", "sketch", ["c1"])
    assert isinstance(v, ReviewVerdict)
    assert v.useful is True and v.elementary is False and v.notes == ["uses Baker"]
    assert v.ok is False


def test_reviewer_fails_closed_on_unparseable(monkeypatch):
    _patch(monkeypatch, "no json here at all")
    v = ClaudeReviewer(FakeToolkit()).review("G", "sketch", [])
    assert v.useful is False and v.elementary is False and v.ok is False


def test_comparator_maps_winner_to_int(monkeypatch):
    a = Candidate(id="a", content="A", goal="G")
    b = Candidate(id="b", content="B", goal="G")
    _patch(monkeypatch, '{"winner": "A"}')
    assert ClaudeComparator().compare(a, b) == 1
    _patch(monkeypatch, '{"winner": "B"}')
    assert ClaudeComparator().compare(a, b) == -1
    _patch(monkeypatch, '{"winner": "tie"}')
    assert ClaudeComparator().compare(a, b) == 0


def test_comparator_fails_to_tie_on_unparseable(monkeypatch):
    _patch(monkeypatch, "garbage")
    assert ClaudeComparator().compare(Candidate(id="a", content="A"),
                                      Candidate(id="b", content="B")) == 0


def test_judge_maps_winner_to_int(monkeypatch):
    a = Candidate(id="a", content="A", goal="G")
    b = Candidate(id="b", content="B", goal="G")
    _patch(monkeypatch, '{"winner": "B"}')
    assert ClaudeJudge().compare(a, b) == -1


def test_critic_returns_flaw_list(monkeypatch):
    _patch(monkeypatch, '```json\n["flaw one", "flaw two"]\n```')
    flaws = ClaudeCritic().critique("G", "incumbent")
    assert flaws == ["flaw one", "flaw two"]


def test_critic_empty_when_flawless(monkeypatch):
    _patch(monkeypatch, "looks great []")
    assert ClaudeCritic().critique("G", "inc") == []


def test_author_and_synthesizer_return_text(monkeypatch):
    _patch(monkeypatch, "REVISED CANDIDATE")
    assert ClaudeAuthor().revise("G", "inc", ["fix it"]) == "REVISED CANDIDATE"
    assert ClaudeSynthesizer().synthesize("G", "a", "b") == "REVISED CANDIDATE"


def test_faithfulness_panel_all_lenses_faithful(monkeypatch):
    _patch(monkeypatch, '{"faithful": true, "issues": []}')
    v = ClaudeFaithfulnessChecker().check("claim", "theorem t : True", "t")
    assert v.faithful is True
    assert len(v.votes) == 4 and all(vote.faithful for vote in v.votes)


def test_faithfulness_panel_default_closed_on_unfaithful_lens(monkeypatch):
    _patch(monkeypatch, '{"faithful": false, "issues": ["wrong quantifier"]}')
    v = ClaudeFaithfulnessChecker().check("claim", "theorem t : False", "t")
    assert v.faithful is False
    assert v.n_unfaithful == 4


# --- FIX 1: model-returned STRING booleans must fail closed (bool("false") == True was fail-open) --

def test_reviewer_stringified_bools_fail_closed(monkeypatch):
    # A model emitting the STRINGS "true"/"false" (not JSON booleans) must NOT be trusted: any
    # non-True value maps to False. bool("false") == True previously admitted a rejected verdict.
    _patch(monkeypatch, '{"useful": "true", "elementary": "false", "notes": []}')
    v = ClaudeReviewer(FakeToolkit()).review("G", "sketch", [])
    assert v.useful is False and v.elementary is False and v.ok is False


def test_reviewer_genuine_bools_still_work(monkeypatch):
    _patch(monkeypatch, '{"useful": true, "elementary": true, "notes": []}')
    v = ClaudeReviewer(FakeToolkit()).review("G", "sketch", [])
    assert v.useful is True and v.elementary is True


def test_faithfulness_stringified_bool_fails_closed(monkeypatch):
    _patch(monkeypatch, '{"faithful": "true", "issues": []}')
    v = ClaudeFaithfulnessChecker().check("claim", "theorem t : True", "t")
    assert v.faithful is False and v.n_unfaithful == 4


# --- Refiner wiring (a real controller, not a stub) ---------------------------------------------

def test_make_claude_refiner_builds_a_real_controller():
    ref = make_claude_refiner(ClaudeConfig(model="opus"), n_judges=3, max_passes=2, k_stop=2)
    assert isinstance(ref, RevisionController)
    assert len(ref.judges) == 3
    assert all(isinstance(j, Comparator) for j in ref.judges)
    assert isinstance(ref.critic, ClaudeCritic)
    assert isinstance(ref.author, ClaudeAuthor)
    assert isinstance(ref.synthesizer, ClaudeSynthesizer)


def test_make_claude_refiner_clamps_judges_to_at_least_one():
    assert len(make_claude_refiner(n_judges=0).judges) == 1
