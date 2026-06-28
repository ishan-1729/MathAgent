"""Offline tests for the W6 CLI<->profile delegation in scripts/prove.py.

No CLI/model/Lean is invoked: profiles are pure data, the supervisor's availability probes are mocked
available, and the only build exercised uses the SCRIPTED provider. We pin:

  * ``profile_from_args(default args)`` maps the default CLI flags to the expected RunProfile fields
    (legacy Codex roles, soft elementarity, decompose+review on, depth/budget/episodes mapped);
  * every structural flag maps to exactly one profile field (direct/terminal-gate/lean-strict/population/
    refine/evolve-fallback/budgets);
  * ``--dump-profile`` prints the effective profile and exits 0 WITHOUT a goal and without any backend;
  * ``--profile`` loads a base profile whose structural fields the CLI flags then OVERRIDE;
  * each shipped profiles/*.yaml loads + passes the supervisor (with probes mocked available);
  * the profile-driven DAG build goes through the builder (build_driver) end to end on scripted parts.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

from agent.orchestrator import supervisor as sup
from agent.orchestrator.run_profile import (
    ElementarityLevel,
    Mode,
    ProviderKey,
    RoleSpec,
    RolesProfile,
    RunProfile,
)

# scripts/ is not a package; load prove.py by path (mirrors the other prove.py tests).
_REPO = Path(__file__).resolve().parents[1]
_PROVE_PATH = _REPO / "scripts" / "prove.py"
_spec = importlib.util.spec_from_file_location("prove_cli", _PROVE_PATH)
prove_cli = importlib.util.module_from_spec(_spec)
sys.modules["prove_cli"] = prove_cli
_spec.loader.exec_module(prove_cli)

_PROFILES_DIR = _REPO / "profiles"
_SHIPPED = sorted(_PROFILES_DIR.glob("**/*.yaml"))


def _parse(argv):
    return prove_cli.build_arg_parser().parse_args(argv)


@pytest.fixture
def all_probes_available(monkeypatch):
    """Mock every supervisor capability probe to 'available' so shipped profiles can be vetted offline."""
    for name in ("_claude_available", "_codex_available", "_lean_available", "_openevolve_available"):
        monkeypatch.setattr(sup, name, lambda: True)
    monkeypatch.setattr(sup, "_PROVIDER_PROBE",
                        {k: (lambda: True) for k in sup._PROVIDER_PROBE})


# ---- profile_from_args: the default flags map to the expected (legacy Codex) profile --------------

def test_profile_from_args_default_maps_legacy_codex_wiring():
    p = prove_cli.profile_from_args(_parse(["a goal"]))
    assert isinstance(p, RunProfile)
    assert p.mode is Mode.dag
    assert p.elementarity is ElementarityLevel.soft
    # Legacy CLI default: every role -> codex (the back-compat baseline).
    for role in ("prover", "decomposer", "reviewer", "comparator", "judge", "faithfulness", "refiner"):
        assert getattr(p.roles, role).provider is ProviderKey.codex
    # default stages: decompose + review ON; population/refine/evolve OFF; h0 on.
    assert p.stages.decompose is True and p.stages.review is True
    assert p.stages.population == 0 and p.stages.evolve_fallback == 0
    assert p.stages.refine is False and p.stages.h0_consistency is True
    # default budgets mapped from the CLI defaults.
    assert p.budgets.max_llm_calls == 60
    assert p.budgets.max_depth == 3
    assert p.budgets.max_decomp_attempts == 2
    assert p.budgets.episodes == 3
    # default lean: all off.
    assert (p.lean.per_node, p.lean.terminal, p.lean.strict, p.lean.server) == \
        (False, False, False, False)


def test_profile_from_args_formalizer_model_claude_overrides_only_formalizer():
    p = prove_cli.profile_from_args(_parse(["g", "--formalizer-model", "claude"]))
    assert p.roles.formalizer.provider is ProviderKey.claude
    assert p.roles.prover.provider is ProviderKey.codex  # the rest stay Codex


# ---- every structural flag maps to exactly one profile field -------------------------------------

def test_direct_flag_maps_to_direct_mode_and_disables_decompose_review():
    p = prove_cli.profile_from_args(_parse(["g", "--direct"]))
    assert p.mode is Mode.direct
    assert p.stages.decompose is False and p.stages.review is False


def test_terminal_gate_maps_to_authoritative_and_lean_terminal():
    p = prove_cli.profile_from_args(_parse(["g", "--terminal-gate"]))
    assert p.elementarity is ElementarityLevel.authoritative
    assert p.lean.terminal is True


def test_lean_strict_implies_per_node_and_server_in_profile():
    p = prove_cli.profile_from_args(_parse(["g", "--lean-strict"]))
    assert p.lean.strict is True and p.lean.per_node is True and p.lean.server is True


def test_lean_per_node_maps_and_implies_server():
    p = prove_cli.profile_from_args(_parse(["g", "--lean-per-node"]))
    assert p.lean.per_node is True and p.lean.server is True
    assert p.lean.strict is False


def test_population_refine_evolve_budgets_map_through():
    p = prove_cli.profile_from_args(_parse([
        "g", "--population", "4", "--refine", "--evolve-fallback", "2",
        "--budget", "99", "--max-depth", "5", "--max-decomp", "3", "--episodes", "7",
    ]))
    assert p.stages.population == 4
    assert p.stages.refine is True
    assert p.stages.evolve_fallback == 2
    assert p.budgets.max_llm_calls == 99
    assert p.budgets.max_depth == 5
    assert p.budgets.max_decomp_attempts == 3
    assert p.budgets.episodes == 7


# ---- --profile base: loaded then OVERRIDDEN by structural flags ----------------------------------

def test_profile_base_roles_preserved_flags_override_structure(tmp_path):
    """A loaded --profile supplies roles/name/seed/notes; the CLI flags override mode/stages/lean."""
    base = RunProfile(name="my-base", seed=7,
                      roles=RolesProfile(prover=RoleSpec(provider=ProviderKey.scripted)))
    yaml_path = tmp_path / "base.yaml"
    import yaml
    yaml_path.write_text(yaml.safe_dump(base.model_dump(mode="json")), encoding="utf-8")

    args = _parse(["g", "--profile", str(yaml_path), "--population", "3"])
    p = prove_cli.profile_from_args(args, base=RunProfile.from_yaml(args.profile))
    # roles + identity come from the base.
    assert p.roles.prover.provider is ProviderKey.scripted
    assert p.name == "my-base" and p.seed == 7
    # the structural flag overrides the base.
    assert p.stages.population == 3


def test_profile_base_unset_flags_do_not_clobber_base():
    """Loading a profile whose lean/elementarity are ON must NOT be silently downgraded by the
    default-False CLI flags — only EXPLICITLY-set flags override the base."""
    base = RunProfile.from_yaml(_PROFILES_DIR / "authoritative.yaml")
    p = prove_cli.profile_from_args(_parse(["g"]), base=base)
    # No flags set -> the base is returned untouched (still authoritative + per-node Lean).
    assert p.elementarity is ElementarityLevel.authoritative
    assert p.lean.per_node is True and p.lean.terminal is True


def test_profile_base_explicit_flag_overrides_only_that_field():
    base = RunProfile.from_yaml(_PROFILES_DIR / "codex.yaml")
    p = prove_cli.profile_from_args(_parse(["g", "--population", "5"]), base=base)
    assert p.stages.population == 5            # explicit override applied
    assert p.stages.decompose is True          # untouched base stage preserved
    assert p.roles.prover.provider is ProviderKey.codex  # base roles preserved
    assert p.elementarity is ElementarityLevel.soft       # base elementarity preserved


def test_effective_profile_uses_profile_file(tmp_path):
    base = RunProfile(name="loaded", roles=RolesProfile(prover=RoleSpec(provider=ProviderKey.scripted)))
    yaml_path = tmp_path / "p.yaml"
    import yaml
    yaml_path.write_text(yaml.safe_dump(base.model_dump(mode="json")), encoding="utf-8")
    args = _parse(["g", "--profile", str(yaml_path)])
    eff = prove_cli.effective_profile(args)
    assert eff.name == "loaded"
    assert eff.roles.prover.provider is ProviderKey.scripted


# ---- --dump-profile: prints effective profile, exits 0, needs no goal/backend --------------------

def test_dump_profile_prints_yaml_and_exits_zero_without_goal(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["prove.py", "--dump-profile"])
    rc = prove_cli.main()
    assert rc == 0
    out = capsys.readouterr().out
    # It is valid YAML round-tripping to a RunProfile.
    import yaml
    data = yaml.safe_load(out)
    assert RunProfile.model_validate(data).mode is Mode.dag


def test_dump_profile_reflects_flags(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["prove.py", "--dump-profile", "--direct", "--population", "2", "g"])
    rc = prove_cli.main()
    assert rc == 0
    import yaml
    data = yaml.safe_load(capsys.readouterr().out)
    p = RunProfile.model_validate(data)
    assert p.mode is Mode.direct
    assert p.stages.population == 2


def test_main_requires_a_goal_when_not_dumping(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["prove.py"])
    rc = prove_cli.main()
    assert rc == 2
    assert "goal is required" in capsys.readouterr().err


# ---- every shipped profile loads + passes the supervisor (probes mocked available) ---------------

def test_shipped_profiles_exist():
    names = {p.name for p in _SHIPPED}
    assert {"default.yaml", "codex.yaml", "solution-only.yaml", "authoritative.yaml"} <= names
    ablation = {p.name for p in (_PROFILES_DIR / "ablation").glob("*.yaml")}
    assert {"no-decompose.yaml", "no-review.yaml", "no-h0.yaml", "no-refine.yaml"} <= ablation


@pytest.mark.parametrize("path", _SHIPPED, ids=lambda p: p.name)
def test_shipped_profile_loads_and_passes_supervisor(path, all_probes_available):
    p = RunProfile.from_yaml(path)
    # No raise == admitted. (Probes are mocked available so the offline test can vet authoritative.)
    sup.validate_profile(p)


def test_default_profile_is_claude_roles():
    p = RunProfile.from_yaml(_PROFILES_DIR / "default.yaml")
    assert p.roles.prover.provider is ProviderKey.claude
    assert p.elementarity is ElementarityLevel.soft


def test_codex_profile_is_all_codex():
    p = RunProfile.from_yaml(_PROFILES_DIR / "codex.yaml")
    for role in ("prover", "decomposer", "reviewer", "formalizer"):
        assert getattr(p.roles, role).provider is ProviderKey.codex


def test_solution_only_profile_is_elementarity_none():
    p = RunProfile.from_yaml(_PROFILES_DIR / "solution-only.yaml")
    assert p.elementarity is ElementarityLevel.none
    assert not p.lean.per_node and not p.lean.terminal and not p.lean.strict


def test_authoritative_profile_wires_terminal_and_per_node_lean():
    p = RunProfile.from_yaml(_PROFILES_DIR / "authoritative.yaml")
    assert p.elementarity is ElementarityLevel.authoritative
    assert p.lean.terminal is True and p.lean.per_node is True


# ---- the profile-driven DAG build goes through the builder end to end (scripted) -----------------

def _scripted_offline_profile() -> RunProfile:
    S = RoleSpec(provider=ProviderKey.scripted)
    return RunProfile(
        name="offline-test",
        roles=RolesProfile(prover=S, decomposer=S, reviewer=S, comparator=S, judge=S,
                           formalizer=S, faithfulness=S, refiner=S),
    )


def test_build_driver_from_a_loaded_scripted_profile(tmp_path):
    """A scripted profile written to YAML loads + builds a real DagDriver via the builder (no backend)."""
    from agent.orchestrator.builder import build_driver
    from agent.orchestrator.dag_driver import DagDriver

    prof = _scripted_offline_profile()
    yaml_path = tmp_path / "scripted.yaml"
    import yaml
    yaml_path.write_text(yaml.safe_dump(prof.model_dump(mode="json")), encoding="utf-8")

    loaded = RunProfile.from_yaml(yaml_path)
    driver = build_driver(loaded)
    assert isinstance(driver, DagDriver)
    # default-stage wiring (byte-identical pre-feature): enforce on, no Lean gates.
    assert driver.enforce_elementarity is True
    assert driver.decomposer is not None and driver.reviewer is not None
    assert driver.terminal_gate is None and driver.node_verifier is None


def test_supervisor_is_sole_chokepoint_in_main(tmp_path, monkeypatch):
    """POINT 3: main() validates the EFFECTIVE profile via the supervisor BEFORE constructing any
    prover/driver, for EVERY path. An incoherent profile (elementarity=none but lean.per_node=true,
    supervisor guard S4) makes main() return 2 fail-closed, and no role is ever resolved/constructed —
    proving the supervisor is the sole pre-flight chokepoint, not the per-branch construction code."""
    import sys
    import scripts.prove as prove
    import agent.orchestrator.registry as reg
    bad = tmp_path / "incoherent.yaml"
    bad.write_text("elementarity: none\nlean:\n  per_node: true\n", encoding="utf-8")
    called = {"resolve": False}
    def boom(*a, **k):
        called["resolve"] = True
        raise AssertionError("resolve() ran before the supervisor validated the profile (chokepoint bypassed)")
    monkeypatch.setattr(reg, "resolve", boom)             # main() imports resolve lazily from this module
    monkeypatch.setattr(sys, "argv", ["prove", "some goal", "--profile", str(bad)])
    rc = prove.main()
    assert rc == 2, "incoherent profile must be rejected fail-closed (return 2)"
    assert called["resolve"] is False, "no prover/role may be constructed before the supervisor validates"
