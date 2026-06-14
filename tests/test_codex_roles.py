"""Offline checks for the Codex-backed tournament roles (construction + protocol conformance).

No Codex is invoked — these assert the roles satisfy the tournament Protocols and that
`make_codex_refiner` wires a real RevisionController (so the harness is not stub-only)."""
from agent.tools.codex_prover import (
    CodexConfig, CodexCritic, CodexAuthor, CodexSynthesizer, CodexSolutionComparator,
    make_codex_refiner, _json_array, _extract_json, _extract_json_object, _last_balanced,
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


# --- Greedy-regex regression: stray braces/brackets in prose BEFORE the final JSON ---------------

def test_extract_json_object_ignores_brace_prose_before_final_json():
    # set notation {1,2,3} and a Lean term [a,b] precede the real verdict; greedy \{.*\} used to
    # span from the first '{' to the last '}' and fail json.loads, losing the verdict.
    raw = 'The roots are {1,2,3} and the term [a, b].\nVerdict:\n{"winner": "A"}'
    assert _extract_json_object(raw) == {"winner": "A"}


def test_json_array_ignores_bracket_prose_before_final_json():
    raw = 'See [12] and the set {x : x > 0}.\n["flaw one", "flaw two"]'
    assert _json_array(raw) == ["flaw one", "flaw two"]


def test_extract_prefers_fenced_block_even_with_brace_prose():
    raw = 'note {a,b,c}\n```json\n{"useful": true, "elementary": false, "notes": []}\n```'
    assert _extract_json_object(raw) == {"useful": True, "elementary": False, "notes": []}


def test_last_balanced_skips_braces_inside_strings():
    # A '}' inside a JSON string value must not be mistaken for the object's close.
    raw = 'prose\n{"winner": "tie", "note": "the set {1,2} here"}'
    assert _extract_json_object(raw) == {"winner": "tie", "note": "the set {1,2} here"}
    assert _last_balanced(raw, "{", "}") == '{"winner": "tie", "note": "the set {1,2} here"}'


def test_extract_json_fails_closed_on_genuine_garbage():
    assert _extract_json_object("no json at all {oops") is None
    assert _json_array("no array {nope}") == []
    assert _extract_json("totally unparseable") is None


def test_extract_json_picks_trailing_value_when_both_present():
    # An object earlier, an array last -> the trailing array is the real payload.
    assert _extract_json('{"x": 1}\n["a", "b"]') == ["a", "b"]
    # ...and vice versa.
    assert _extract_json('["a"]\n{"winner": "B"}') == {"winner": "B"}
