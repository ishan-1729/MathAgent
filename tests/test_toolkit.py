"""Tests for the toolkit/denylist loader and data integrity of the YAML/JSON files."""
import json
from pathlib import Path

import pytest

from agent.gates.toolkit import (
    Toolkit,
    load_toolkit,
    OBLIGATION_KINDS,
    _coerce_justifications,
)

REPO = Path(__file__).resolve().parents[1]


def test_loads_packaged_toolkit(toolkit):
    assert isinstance(toolkit, Toolkit)
    assert toolkit.justifications, "toolkit must be non-empty"


def test_conclusion_justification_present(toolkit):
    assert toolkit.is_allowed("conclusion")
    assert toolkit.obligation_for("conclusion") == "none"


def test_every_justification_has_valid_obligation(toolkit):
    for key, j in toolkit.justifications.items():
        assert j.obligation in OBLIGATION_KINDS, f"{key} has bad obligation {j.obligation}"


def test_core_justifications_exist(toolkit):
    for key in ["descent", "congruence", "gcd_coprimality", "case_split", "lte", "induction",
                "pigeonhole", "bounding", "vieta_jumping", "vierzahlensatz"]:
        assert toolkit.is_allowed(key), f"missing core justification {key}"


def test_obligation_mappings(toolkit):
    assert toolkit.obligation_for("case_split") == "case_cover"
    assert toolkit.obligation_for("descent") == "descent"
    assert toolkit.obligation_for("vieta_jumping") == "descent"
    assert toolkit.obligation_for("bounding") == "bounding"
    assert toolkit.obligation_for("euclid_splitting") == "split_coprimality"
    assert toolkit.obligation_for("congruence") == "none"


def test_unknown_justification(toolkit):
    assert not toolkit.is_allowed("class_field_theory")
    assert toolkit.obligation_for("nope") == "none"


def test_denylist_loaded(toolkit):
    assert "elliptic curve" in toolkit.prose_terms
    assert "class group" in toolkit.prose_terms
    # stored lowercased
    assert all(t == t.lower() for t in toolkit.prose_terms)
    assert toolkit.lean_denylist_decls
    assert "WellFounded.fix" in toolkit.lean_infrastructure_allowlist


def test_boundary_rulings(toolkit):
    assert toolkit.ruling("pell_fundamental_solution") == "allowed"
    assert toolkit.ruling("gauss_sum_or_analytic_qr") == "disallowed"
    assert toolkit.ruling("higher_reciprocity") == "allowed_per_problem_whitelist"


def test_coerce_rejects_bad_obligation():
    with pytest.raises(ValueError):
        _coerce_justifications({"foo": {"obligation": "teleport"}})


def test_load_missing_conclusion(tmp_path):
    tk = tmp_path / "tk.yaml"
    tk.write_text("justifications:\n  given: {obligation: none}\n", encoding="utf-8")
    dl = tmp_path / "dl.yaml"
    dl.write_text("prose_terms: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="conclusion"):
        load_toolkit(tk, dl)


def test_method_refs_resolve_on_disk(toolkit):
    """Every method_ref in the toolkit must point to a real file (post-reorg paths)."""
    for key, j in toolkit.justifications.items():
        if j.method_ref:
            assert (REPO / j.method_ref).is_file(), f"{key}: missing {j.method_ref}"


def test_schema_file_is_valid_draft7():
    import jsonschema
    schema = json.loads((REPO / "agent/gates/ledger.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft7Validator.check_schema(schema)
