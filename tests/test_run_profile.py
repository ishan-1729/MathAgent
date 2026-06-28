"""Offline tests for agent/orchestrator/run_profile.py (W1).

Pure data: no model/Lean/tool calls. Covers default validity, extra-key rejection,
frozen-ness, YAML round-trip, per-role default models, and profile_hash stability.
"""
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent.orchestrator.run_profile import (
    BudgetProfile,
    ElementarityLevel,
    LeanProfile,
    Mode,
    ProviderKey,
    Role,
    RoleSpec,
    RolesProfile,
    RunProfile,
    StageProfile,
)


# ---- defaults validate & carry the documented values ---- #
def test_default_profile_validates():
    p = RunProfile()
    assert p.name == "default"
    assert p.seed == 0
    assert p.notes == ""
    assert p.mode is Mode.dag
    assert p.elementarity is ElementarityLevel.soft
    assert isinstance(p.stages, StageProfile)
    assert isinstance(p.roles, RolesProfile)
    assert isinstance(p.budgets, BudgetProfile)
    assert isinstance(p.lean, LeanProfile)


def test_stage_defaults():
    s = StageProfile()
    assert s.decompose is True
    assert s.review is True
    assert s.population == 0
    assert s.evolve_fallback == 0
    assert s.refine is False
    assert s.h0_consistency is True


def test_lean_defaults_all_off():
    le = LeanProfile()
    assert (le.per_node, le.terminal, le.strict, le.server) == (False, False, False, False)


def test_budget_defaults():
    b = BudgetProfile()
    assert b.max_llm_calls == 60
    assert b.max_node_verify_calls is None
    assert b.max_depth == 3
    assert b.max_decomp_attempts == 2
    assert b.episodes == 3


def test_role_spec_defaults_to_claude():
    r = RoleSpec()
    assert r.provider is ProviderKey.claude
    assert r.model is None
    assert r.effort is None
    assert r.timeout_s is None
    assert r.fallback is None


def test_per_role_default_models():
    roles = RolesProfile()
    for role in (Role.prover, Role.refiner):
        spec = getattr(roles, role.value)
        assert spec.provider is ProviderKey.claude
        assert spec.model == "opus"
    for role in (Role.decomposer, Role.reviewer, Role.formalizer, Role.faithfulness):
        spec = getattr(roles, role.value)
        assert spec.provider is ProviderKey.claude
        assert spec.model == "sonnet"
    for role in (Role.comparator, Role.judge):
        spec = getattr(roles, role.value)
        assert spec.provider is ProviderKey.claude
        assert spec.model == "haiku"


def test_every_role_enum_has_a_field():
    roles = RolesProfile()
    for role in Role:
        assert isinstance(getattr(roles, role.value), RoleSpec)


# ---- extra/unknown keys fail closed ---- #
def test_extra_key_top_level_raises():
    with pytest.raises(ValidationError):
        RunProfile(unknown_field=1)


def test_extra_key_nested_raises():
    with pytest.raises(ValidationError):
        RunProfile.model_validate({"stages": {"decompose": True, "bogus": 7}})


def test_extra_key_rolespec_raises():
    with pytest.raises(ValidationError):
        RoleSpec(provider="claude", weird=True)


# ---- frozen: mutation raises ---- #
def test_frozen_top_level():
    p = RunProfile()
    with pytest.raises(ValidationError):
        p.name = "changed"


def test_frozen_nested():
    p = RunProfile()
    with pytest.raises(ValidationError):
        p.stages.population = 5


def test_frozen_rolespec():
    r = RoleSpec()
    with pytest.raises(ValidationError):
        r.model = "opus"


# ---- enums reject bad values ---- #
def test_bad_enum_value_raises():
    with pytest.raises(ValidationError):
        RunProfile(mode="parallel")
    with pytest.raises(ValidationError):
        RunProfile(elementarity="hard")
    with pytest.raises(ValidationError):
        RoleSpec(provider="gemini")


# ---- model_dump is JSON-serializable with enum values ---- #
def test_model_dump_json_values():
    p = RunProfile()
    dumped = p.model_dump(mode="json")
    assert dumped["mode"] == "dag"
    assert dumped["elementarity"] == "soft"
    assert dumped["roles"]["prover"]["provider"] == "claude"


# ---- from_yaml round-trips ---- #
def test_from_yaml_round_trip(tmp_path: Path):
    p = RunProfile(
        name="codex-run",
        seed=42,
        mode=Mode.direct,
        elementarity=ElementarityLevel.none,
        stages=StageProfile(decompose=False, population=4),
        lean=LeanProfile(server=True),
    )
    yaml_path = tmp_path / "profile.yaml"
    import yaml

    yaml_path.write_text(yaml.safe_dump(p.model_dump(mode="json")), encoding="utf-8")
    loaded = RunProfile.from_yaml(yaml_path)
    assert loaded == p
    assert loaded.profile_hash == p.profile_hash


def test_from_yaml_empty_file_is_default(tmp_path: Path):
    yaml_path = tmp_path / "empty.yaml"
    yaml_path.write_text("", encoding="utf-8")
    assert RunProfile.from_yaml(yaml_path) == RunProfile()


def test_from_yaml_non_mapping_raises(tmp_path: Path):
    yaml_path = tmp_path / "list.yaml"
    yaml_path.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ValueError):
        RunProfile.from_yaml(yaml_path)


def test_from_yaml_unknown_key_raises(tmp_path: Path):
    yaml_path = tmp_path / "bad.yaml"
    yaml_path.write_text("name: x\nbogus: 1\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        RunProfile.from_yaml(yaml_path)


# ---- profile_hash: stable + changes on any field change ---- #
def test_profile_hash_stable_across_instances():
    assert RunProfile().profile_hash == RunProfile().profile_hash


def test_profile_hash_is_hex_sha256():
    h = RunProfile().profile_hash
    assert len(h) == 64
    int(h, 16)  # valid hex


def test_profile_hash_changes_on_scalar():
    base = RunProfile()
    assert base.profile_hash != RunProfile(seed=1).profile_hash
    assert base.profile_hash != RunProfile(name="other").profile_hash
    assert base.profile_hash != RunProfile(mode=Mode.direct).profile_hash
    assert base.profile_hash != RunProfile(elementarity=ElementarityLevel.authoritative).profile_hash


def test_profile_hash_changes_on_nested():
    base = RunProfile()
    assert base.profile_hash != RunProfile(stages=StageProfile(population=1)).profile_hash
    assert base.profile_hash != RunProfile(lean=LeanProfile(per_node=True)).profile_hash
    assert base.profile_hash != RunProfile(budgets=BudgetProfile(max_llm_calls=61)).profile_hash
    assert (
        base.profile_hash
        != RunProfile(roles=RolesProfile(prover=RoleSpec(provider=ProviderKey.codex))).profile_hash
    )
