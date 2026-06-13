"""Offline tests for the persistent Lean server's parsing/discovery (live REPL test is opt-in)."""
from agent.gates import lean_server
from agent.gates.lean_server import LeanServer, report_from_response, response_errors


def test_report_from_response_extracts_json():
    resp = {"messages": [
        {"severity": "info", "data": "some preamble"},
        {"severity": "info", "data": 'MATHAGENT_AUDIT_JSON {"theorem":"t","axioms":[],"constants":[]}'},
    ]}
    assert report_from_response(resp) == '{"theorem":"t","axioms":[],"constants":[]}'


def test_report_from_response_none_on_errors():
    resp = {"messages": [{"severity": "error", "data": "unknown identifier 'foo'"}]}
    assert report_from_response(resp) is None


def test_response_errors():
    assert "unknown" in response_errors({"messages": [{"severity": "error", "data": "unknown id"}]})
    assert "sorries" in response_errors({"sorries": [{"goal": "x"}]})
    assert response_errors({"messages": []}) == "no audit output"


def test_available_is_bool():
    assert isinstance(LeanServer.available(), bool)
