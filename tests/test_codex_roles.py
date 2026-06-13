"""Offline checks for the Codex-backed tournament roles (construction + protocol conformance).

No Codex is invoked — these assert the roles satisfy the tournament Protocols and that
`make_codex_refiner` wires a real RevisionController (so the harness is not stub-only)."""
from agent.tools.codex_prover import (
    CodexConfig, CodexCritic, CodexAuthor, CodexSynthesizer, CodexSolutionComparator,
    make_codex_refiner, _json_array,
)
from agent.orchestrator.population import Comparator
from agent.orchestrator.tournament import (
    RevisionController, RevisionCritic, RevisionAuthor, Synthesizer,
)

CFG = CodexConfig(model="gpt-5.5", reasoning_effort="xhigh")


def test_codex_roles_satisfy_tournament_protocols():
    assert isinstance(CodexCritic(CFG), RevisionCritic)
    assert isinstance(CodexAuthor(CFG), RevisionAuthor)
    assert isinstance(CodexSynthesizer(CFG), Synthesizer)
    assert isinstance(CodexSolutionComparator(CFG), Comparator)


def test_make_codex_refiner_builds_a_real_controller():
    ref = make_codex_refiner(CFG, n_judges=3, max_passes=2, k_stop=2)
    assert isinstance(ref, RevisionController)
    assert len(ref.judges) == 3
    assert all(isinstance(j, Comparator) for j in ref.judges)
    # roles are the real Codex ones, not stubs
    assert isinstance(ref.critic, CodexCritic)
    assert isinstance(ref.author, CodexAuthor)
    assert isinstance(ref.synthesizer, CodexSynthesizer)


def test_make_codex_refiner_clamps_judges_to_at_least_one():
    assert len(make_codex_refiner(CFG, n_judges=0).judges) == 1


def test_json_array_parser():
    assert _json_array('prose ["a", "b"] tail') == ["a", "b"]
    assert _json_array("[]") == []
    assert _json_array("no array here") == []
    assert _json_array('{"not": "array"}') == []
