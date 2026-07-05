"""Layer-4 sync guards (build_status §7 fix).

These lock three contracts that otherwise drift silently:

  1. The JSON SCHEMA emitted by `agent/gates/lean/Audit.lean` (`#audit`) must match what the Python
     auditor `DependencyReport.from_dict` consumes — keys *and* the `ConstantInfo` kind strings.
  2. The denylist / allowlist / axiom-whitelist YAML the auditor reads must be well-formed and the
     committed real fixture must still audit PASS under it.
  3. `gate.evaluate` (Layers 1–3) must NOT invoke Layer 4 — the chaining lives in
     formalize_bridge / prove.py by design (PLAN §5). This test pins that separation.
"""
import json
import re
from pathlib import Path

from agent.gates.gate import evaluate
from agent.gates.lean_audit import DependencyReport, audit_report, audit_file, LeanVerdict
from agent.gates.report import LAYER_LEAN
from agent.gates.toolkit import load_toolkit

ROOT = Path(__file__).resolve().parents[1]
AUDIT_LEAN = ROOT / "agent" / "gates" / "lean" / "Audit.lean"
FIXTURE = ROOT / "agent" / "gates" / "lean" / "examples" / "add_zero_report.json"
GATE_PY = ROOT / "agent" / "gates" / "gate.py"

# The exact top-level keys + constant sub-keys the Python parser reads (lean_audit.DependencyReport).
_PARSER_TOP_KEYS = {"theorem", "toolchain", "axioms", "constants"}
_PARSER_CONST_KEYS = {"name", "kind", "module"}


def _emitted_json_keys() -> set[str]:
    src = AUDIT_LEAN.read_text(encoding="utf-8")
    return set(re.findall(r'\("([a-zA-Z]+)",\s*Json\.', src))


def _emitted_const_kinds() -> set[str]:
    src = AUDIT_LEAN.read_text(encoding="utf-8")
    # constKind maps each ConstantInfo constructor to a `=> "<kind>"` string literal.
    return set(re.findall(r'=>\s*"([a-z]+)"', src))


# ---- 1. JSON schema: Lean emitter ↔ Python parser ----

def test_audit_lean_emits_exactly_the_keys_python_parses():
    emitted = _emitted_json_keys()
    assert _PARSER_TOP_KEYS <= emitted, f"Audit.lean stopped emitting {_PARSER_TOP_KEYS - emitted}"
    assert _PARSER_CONST_KEYS <= emitted, f"constant objects miss {_PARSER_CONST_KEYS - emitted}"
    # No extra top-level keys the parser would ignore (catches a renamed/added field).
    top_level = emitted - _PARSER_CONST_KEYS
    assert top_level == _PARSER_TOP_KEYS, f"unparsed Audit.lean keys: {top_level ^ _PARSER_TOP_KEYS}"


def test_round_trip_every_emitted_kind_parses():
    kinds = _emitted_const_kinds()
    assert kinds, "could not extract ConstantInfo kinds from Audit.lean::constKind"
    # Build a report in the EXACT shape Audit.lean emits, exercising every kind, and parse it.
    payload = {
        "theorem": "demo",
        "toolchain": "leanprover/lean4:vX",
        "axioms": [],
        "constants": [{"name": f"C{i}", "kind": k} for i, k in enumerate(sorted(kinds))],
    }
    rep = DependencyReport.from_json(json.dumps(payload))
    assert rep.theorem == "demo"
    assert {c.kind for c in rep.constants} == kinds       # every emitted kind survives parsing
    assert audit_report(rep).passed                       # benign constants → PASS


# ---- 2. denylist / allowlist YAML the auditor reads ----

def test_denylist_yaml_is_well_formed():
    tk = load_toolkit()
    for field in ("lean_denylist_decls", "lean_infrastructure_allowlist",
                  "lean_elementary_by_fiat", "lean_axiom_whitelist"):
        vals = getattr(tk, field)
        assert isinstance(vals, list) and vals, f"{field} must be a non-empty list"
        for v in vals:
            assert isinstance(v, str) and v and " " not in v, f"{field} has a bad entry {v!r}"


def test_fixture_audits_pass_and_kinds_are_known():
    res = audit_file(FIXTURE)
    assert res.verdict is LeanVerdict.PASS
    assert res.report.toolchain and res.report.toolchain.startswith("leanprover/lean4:")
    known = _emitted_const_kinds()
    fixture_kinds = {c.kind for c in res.report.constants}
    assert fixture_kinds <= known, f"fixture has kinds Audit.lean can't emit: {fixture_kinds - known}"


def test_sorry_axiom_is_rejected_by_name_the_auditor_special_cases():
    rep = DependencyReport.from_dict(
        {"theorem": "t", "axioms": ["sorryAx"], "constants": [{"name": "t", "kind": "theorem"}]})
    res = audit_report(rep)
    assert not res.passed
    assert any(f.code == "sorry_axiom" for f in res.rejects())


def test_denylisted_dependency_is_rejected_unless_allowlisted():
    tk = load_toolkit()
    deny = tk.lean_denylist_decls[0]
    rep = DependencyReport.from_dict(
        {"theorem": "t", "axioms": [], "constants": [{"name": deny, "kind": "theorem"}]})
    assert not audit_report(rep).passed


# ---- 3. gate.evaluate must NOT invoke Layer 4 (intentional separation) ----

def test_gate_does_not_import_or_invoke_layer4():
    # static: the deterministic gate module does not import the Lean auditor.
    src = GATE_PY.read_text(encoding="utf-8")
    assert "lean_audit" not in src and "lean_bridge" not in src
    # dynamic: a normal gate run produces no Layer-4 findings.
    ledger = json.dumps({"problem": "p", "claim": "G", "steps": [
        {"id": "s1", "claim": "setup", "justification": "given", "depends_on": []},
        {"id": "s2", "claim": "G", "justification": "conclusion", "depends_on": ["s1"]}]})
    report = evaluate(ledger)
    assert all(f.layer != LAYER_LEAN for f in report.findings)
