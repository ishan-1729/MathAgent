"""Offline tests for the fail-CLOSED supervisor (Ramadge-Wonham SCT controller).

Every test monkeypatches the lazy capability probes in
``agent.orchestrator.supervisor`` so NO live model/Lean/openevolve call happens.

For each guard S1..S11 we assert two things:

* it FIRES on its offending profile, with a clear, field-naming message; and
* (minimal restrictiveness) a coherent profile with that capability present PASSES,
  so the supervisor never rejects a safe configuration.
"""
import time

import pytest

from agent.orchestrator import supervisor as sup
from agent.orchestrator.supervisor import SupervisorError, validate_profile
from agent.orchestrator.run_profile import (
    BudgetProfile,
    ElementarityLevel,
    EnsembleProfile,
    LeanProfile,
    Mode,
    ProviderKey,
    Role,
    RoleSpec,
    RolesProfile,
    RunProfile,
    StageProfile,
)


# --------------------------------------------------------------------------- #
# Fixtures: by default ALL capabilities are present, so only the guard under   #
# test can fire. Individual tests flip a single probe off.                     #
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def all_capabilities(monkeypatch):
    """Default world: claude/codex CLIs found, Lean reachable, openevolve installed."""
    monkeypatch.setattr(sup, "_claude_available", lambda: True)
    monkeypatch.setattr(sup, "_codex_available", lambda: True)
    monkeypatch.setattr(sup, "_lean_compiler_available", lambda: True)
    monkeypatch.setattr(sup, "_lean_repl_available", lambda: True)
    monkeypatch.setattr(sup, "_openevolve_available", lambda: True)
    # _provider_available / _PROVIDER_PROBE close over the module-level names above
    # at call time via the table, so refresh the table to the patched callables.
    monkeypatch.setattr(
        sup,
        "_PROVIDER_PROBE",
        {ProviderKey.claude: sup._claude_available, ProviderKey.codex: sup._codex_available},
    )


def _profile(**kw) -> RunProfile:
    """A coherent default profile, overridable by keyword."""
    return RunProfile(**kw)


def _roles_with(role: Role, spec: RoleSpec) -> RolesProfile:
    """A default RolesProfile with one role overridden."""
    base = RolesProfile()
    return base.model_copy(update={role.value: spec})


# --------------------------------------------------------------------------- #
# Baseline: a plain default profile passes (the most important safe config).   #
# --------------------------------------------------------------------------- #
def test_default_profile_passes():
    assert validate_profile(_profile()) is None


def test_unchecked_model_copy_cannot_disable_h0():
    stages = StageProfile().model_copy(update={"h0_consistency": False})
    prof = _profile().model_copy(update={"stages": stages})
    with pytest.raises(SupervisorError, match="schema revalidation"):
        validate_profile(prof)


def test_unchecked_model_copy_cannot_bypass_budget_bounds():
    budgets = BudgetProfile().model_copy(update={"max_llm_calls": 0})
    prof = _profile().model_copy(update={"budgets": budgets})
    with pytest.raises(SupervisorError, match="schema revalidation"):
        validate_profile(prof)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_unchecked_model_copy_cannot_inject_nonfinite_ensemble_weight(value):
    from agent.orchestrator.run_profile import EnsembleProfile

    ensemble = EnsembleProfile().model_copy(update={"breadth_weight": value})
    prof = _profile().model_copy(update={"ensemble": ensemble})
    with pytest.raises(SupervisorError):
        validate_profile(prof)


def test_validate_is_fast(monkeypatch):
    """Even with the real (lazy) probes, validation returns quickly. <1s budget."""
    # Use the offline-safe default but DON'T patch probes off — exercise real ones,
    # which on a bare machine just return False quickly. We pick a profile that needs
    # no capability so it passes regardless of environment.
    prof = _profile(name="offline-fast", elementarity=ElementarityLevel.none)
    t0 = time.perf_counter()
    validate_profile(prof)
    assert time.perf_counter() - t0 < 1.0


# --------------------------------------------------------------------------- #
# S1: authoritative => formalizer resolvable AND Lean reachable.              #
# --------------------------------------------------------------------------- #
# NOTE: an authoritative profile must ALSO set lean.terminal=True (S9: terminal is derived from
# elementarity), so these S1 profiles carry the consistent LeanProfile(terminal=True).
def test_s1_authoritative_requires_lean(monkeypatch):
    monkeypatch.setattr(sup, "_lean_compiler_available", lambda: False)
    prof = _profile(elementarity=ElementarityLevel.authoritative,
                    lean=LeanProfile(terminal=True))
    with pytest.raises(SupervisorError) as ei:
        validate_profile(prof)
    assert "authoritative" in str(ei.value).lower()
    assert "lean" in str(ei.value).lower()


def test_s1_one_shot_terminal_needs_compiler_not_repl(monkeypatch):
    """server=False uses per-call Lake/Lean and must not require the optional built REPL."""
    monkeypatch.setattr(sup, "_lean_compiler_available", lambda: True)
    monkeypatch.setattr(sup, "_lean_repl_available", lambda: False)
    prof = _profile(
        elementarity=ElementarityLevel.authoritative,
        lean=LeanProfile(terminal=True, server=False),
    )
    assert validate_profile(prof) is None


def test_s1_requested_server_requires_repl_even_when_compiler_exists(monkeypatch):
    monkeypatch.setattr(sup, "_lean_compiler_available", lambda: True)
    monkeypatch.setattr(sup, "_lean_repl_available", lambda: False)
    prof = _profile(
        elementarity=ElementarityLevel.authoritative,
        lean=LeanProfile(terminal=True, server=True),
    )
    with pytest.raises(SupervisorError) as ei:
        validate_profile(prof)
    msg = str(ei.value).lower()
    assert "server" in msg and "repl" in msg


def test_s1_requested_server_does_not_require_separate_one_shot_probe(monkeypatch):
    monkeypatch.setattr(sup, "_lean_compiler_available", lambda: False)
    monkeypatch.setattr(sup, "_lean_repl_available", lambda: True)
    prof = _profile(
        elementarity=ElementarityLevel.authoritative,
        lean=LeanProfile(terminal=True, server=True),
    )
    assert validate_profile(prof) is None


def test_yaml_one_shot_terminal_has_same_capability_contract_as_cli(tmp_path, monkeypatch):
    """A profile-file terminal audit with server off is the CLI's non-``--server`` one-shot path."""
    profile_path = tmp_path / "one-shot.yaml"
    profile_path.write_text(
        "elementarity: authoritative\nlean:\n  terminal: true\n  server: false\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sup, "_lean_compiler_available", lambda: True)
    monkeypatch.setattr(sup, "_lean_repl_available", lambda: False)
    assert validate_profile(RunProfile.from_yaml(profile_path)) is None


def test_s1_authoritative_requires_formalizer(monkeypatch):
    # Formalizer on codex, but codex CLI absent => formalizer unresolvable.
    monkeypatch.setattr(sup, "_codex_available", lambda: False)
    monkeypatch.setattr(
        sup,
        "_PROVIDER_PROBE",
        {ProviderKey.claude: sup._claude_available, ProviderKey.codex: sup._codex_available},
    )
    prof = _profile(
        elementarity=ElementarityLevel.authoritative,
        lean=LeanProfile(terminal=True),
        roles=_roles_with(Role.formalizer, RoleSpec(provider=ProviderKey.codex)),
    )
    with pytest.raises(SupervisorError) as ei:
        validate_profile(prof)
    # codex absence is caught at S6 OR S1 (both name the formalizer/provider) — either
    # way it must mention the formalizer or its provider.
    msg = str(ei.value).lower()
    assert "formalizer" in msg or "codex" in msg


def test_s1_authoritative_passes_when_available():
    prof = _profile(elementarity=ElementarityLevel.authoritative,
                    lean=LeanProfile(terminal=True))
    assert validate_profile(prof) is None


# --------------------------------------------------------------------------- #
# S2: lean.per_node => formalizer resolvable.                                 #
# --------------------------------------------------------------------------- #
def test_s2_per_node_requires_formalizer(monkeypatch):
    monkeypatch.setattr(sup, "_codex_available", lambda: False)
    monkeypatch.setattr(
        sup,
        "_PROVIDER_PROBE",
        {ProviderKey.claude: sup._claude_available, ProviderKey.codex: sup._codex_available},
    )
    # per_node also requires server=True (S10), so carry the consistent LeanProfile.
    prof = _profile(
        elementarity=ElementarityLevel.soft,
        lean=LeanProfile(per_node=True, server=True),
        roles=_roles_with(Role.formalizer, RoleSpec(provider=ProviderKey.codex)),
    )
    with pytest.raises(SupervisorError) as ei:
        validate_profile(prof)
    msg = str(ei.value).lower()
    assert "formalizer" in msg or "codex" in msg


def test_s2_per_node_passes_with_claude_formalizer():
    prof = _profile(elementarity=ElementarityLevel.soft,
                    lean=LeanProfile(per_node=True, server=True))
    assert validate_profile(prof) is None


def test_s2_soft_per_node_requires_repl(monkeypatch):
    """Per-node verification requests a warm server even when terminal authority is not enabled."""
    monkeypatch.setattr(sup, "_lean_compiler_available", lambda: True)
    monkeypatch.setattr(sup, "_lean_repl_available", lambda: False)
    prof = _profile(
        elementarity=ElementarityLevel.soft,
        lean=LeanProfile(per_node=True, server=True),
    )
    with pytest.raises(SupervisorError) as ei:
        validate_profile(prof)
    msg = str(ei.value).lower()
    assert "per_node" in msg and "repl" in msg


def test_s2_soft_per_node_uses_repl_not_one_shot_probe(monkeypatch):
    monkeypatch.setattr(sup, "_lean_compiler_available", lambda: False)
    monkeypatch.setattr(sup, "_lean_repl_available", lambda: True)
    prof = _profile(
        elementarity=ElementarityLevel.soft,
        lean=LeanProfile(per_node=True, server=True),
    )
    assert validate_profile(prof) is None


# --------------------------------------------------------------------------- #
# S3: evolve_fallback > 0 => openevolve installed.                            #
# --------------------------------------------------------------------------- #
def test_s3_evolve_requires_openevolve(monkeypatch):
    monkeypatch.setattr(sup, "_openevolve_available", lambda: False)
    prof = _profile(stages=StageProfile(evolve_fallback=4))
    with pytest.raises(SupervisorError) as ei:
        validate_profile(prof)
    msg = str(ei.value).lower()
    assert "openevolve" in msg and "evolve_fallback" in msg


def test_s3_evolve_passes_when_installed():
    prof = _profile(stages=StageProfile(evolve_fallback=4))
    assert validate_profile(prof) is None


def test_s3_zero_evolve_needs_nothing(monkeypatch):
    monkeypatch.setattr(sup, "_openevolve_available", lambda: False)
    assert validate_profile(_profile(stages=StageProfile(evolve_fallback=0))) is None


# --------------------------------------------------------------------------- #
# S4: elementarity=none => no Lean flags.                                     #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("flag", ["per_node", "terminal", "strict"])
def test_s4_none_forbids_lean_flag(flag):
    prof = _profile(
        elementarity=ElementarityLevel.none,
        lean=LeanProfile(**{flag: True}),
    )
    with pytest.raises(SupervisorError) as ei:
        validate_profile(prof)
    msg = str(ei.value).lower()
    assert "none" in msg and flag in msg


def test_s4_none_with_no_lean_passes():
    prof = _profile(elementarity=ElementarityLevel.none, lean=LeanProfile())
    assert validate_profile(prof) is None


def test_inert_lean_server_flag_is_rejected():
    # A transport with no terminal/per-node consumer would be silently ignored by the profile builder.
    prof = _profile(elementarity=ElementarityLevel.none, lean=LeanProfile(server=True))
    with pytest.raises(SupervisorError, match="server=True is inert"):
        validate_profile(prof)


# --------------------------------------------------------------------------- #
# S5: mode=direct => DAG-only stages off; terminal authority remains valid.   #
# --------------------------------------------------------------------------- #
def test_s5_direct_authoritative_passes_with_terminal_and_no_dag_stages():
    stages = StageProfile(decompose=False, population=0, refine=False, evolve_fallback=0)
    prof = _profile(
        mode=Mode.direct,
        elementarity=ElementarityLevel.authoritative,
        stages=stages,
        lean=LeanProfile(terminal=True),
    )
    assert validate_profile(prof) is None


def test_s5_direct_soft_passes():
    # A COHERENT direct profile (stages.decompose/review off — see S5b) with soft elementarity passes.
    direct_stages = StageProfile(decompose=False, review=False)
    assert validate_profile(_profile(mode=Mode.direct, elementarity=ElementarityLevel.soft,
                                     stages=direct_stages)) is None


def test_s5b_direct_with_decompose_on_rejected():
    # mode=direct with stages still decomposing is the contradiction the builder would silently run as
    # a decomposing DAG under a 'direct' label (the builder ignores mode). Default StageProfile has
    # decompose/review ON, so a bare mode=direct profile must now be rejected by S5b.
    with pytest.raises(SupervisorError) as ei:
        validate_profile(_profile(mode=Mode.direct, elementarity=ElementarityLevel.soft))
    msg = str(ei.value).lower()
    assert "direct" in msg and ("decompose" in msg or "stages" in msg)


# S5b siblings: population / refine / evolve_fallback are DAG-only stages the builder wires too, so a
# mode=direct profile that leaves ANY of them on (even with decompose/review off) must be rejected —
# else the builder runs best-of-K population / the AutoReason refiner / the evolve fallback under a
# 'direct' label. Each test isolates ONE sibling (all other DAG stages off) so ONLY S5b can trip.

def test_s5b_direct_with_population_rejected():
    stages = StageProfile(decompose=False, review=False, population=3)
    with pytest.raises(SupervisorError) as ei:
        validate_profile(_profile(mode=Mode.direct, elementarity=ElementarityLevel.soft, stages=stages))
    msg = str(ei.value).lower()
    assert "direct" in msg and "population" in msg


def test_s5b_direct_with_refine_rejected():
    stages = StageProfile(decompose=False, review=False, refine=True)
    with pytest.raises(SupervisorError) as ei:
        validate_profile(_profile(mode=Mode.direct, elementarity=ElementarityLevel.soft, stages=stages))
    msg = str(ei.value).lower()
    assert "direct" in msg and "refine" in msg


def test_s5b_direct_with_evolve_fallback_rejected():
    # openevolve is mocked available (autouse fixture) so S3 passes and ONLY S5b trips on the sibling.
    stages = StageProfile(decompose=False, review=False, evolve_fallback=20)
    with pytest.raises(SupervisorError) as ei:
        validate_profile(_profile(mode=Mode.direct, elementarity=ElementarityLevel.soft, stages=stages))
    msg = str(ei.value).lower()
    assert "direct" in msg and "evolve_fallback" in msg


def test_s5b_direct_all_dag_stages_off_passes():
    # REGRESSION: a coherent direct profile with EVERY DAG-only stage off (h0_consistency is not a
    # DAG-only stage, so it may stay on) must PASS — S5b must not over-reject a clean direct profile.
    stages = StageProfile(decompose=False, review=False, population=0, refine=False, evolve_fallback=0)
    assert validate_profile(
        _profile(mode=Mode.direct, elementarity=ElementarityLevel.soft, stages=stages)) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [("population", 3), ("evolve_fallback", 4)],
)
def test_s14_dag_decomposition_dependent_stage_cannot_be_inert(field, value):
    stages = StageProfile(decompose=False, **{field: value})
    with pytest.raises(SupervisorError) as ei:
        validate_profile(_profile(mode=Mode.dag, stages=stages))
    msg = str(ei.value).lower()
    assert "decompose" in msg and field in msg


def test_s14_dag_decompose_false_still_allows_direct_refinement():
    """Refinement can improve a directly proved ledger and is not decomposition-dependent."""
    stages = StageProfile(decompose=False, refine=True, population=0, evolve_fallback=0)
    assert validate_profile(_profile(mode=Mode.dag, stages=stages)) is None


# --------------------------------------------------------------------------- #
# S6: any codex role => codex CLI present.                                    #
# --------------------------------------------------------------------------- #
def test_s6_codex_role_requires_cli(monkeypatch):
    monkeypatch.setattr(sup, "_codex_available", lambda: False)
    monkeypatch.setattr(
        sup,
        "_PROVIDER_PROBE",
        {ProviderKey.claude: sup._claude_available, ProviderKey.codex: sup._codex_available},
    )
    prof = _profile(
        name="codex-run",
        roles=_roles_with(Role.prover, RoleSpec(provider=ProviderKey.codex)),
    )
    with pytest.raises(SupervisorError) as ei:
        validate_profile(prof)
    msg = str(ei.value).lower()
    assert "codex" in msg and "prover" in msg


def test_s6_codex_role_passes_when_cli_present():
    prof = _profile(
        name="codex-run",
        roles=_roles_with(Role.prover, RoleSpec(provider=ProviderKey.codex)),
    )
    assert validate_profile(prof) is None


# --------------------------------------------------------------------------- #
# S7: any claude role => claude CLI present.                                  #
# --------------------------------------------------------------------------- #
def test_s7_claude_role_requires_cli(monkeypatch):
    monkeypatch.setattr(sup, "_claude_available", lambda: False)
    monkeypatch.setattr(
        sup,
        "_PROVIDER_PROBE",
        {ProviderKey.claude: sup._claude_available, ProviderKey.codex: sup._codex_available},
    )
    # default profile is all-claude; first role checked is the prover.
    prof = _profile()
    with pytest.raises(SupervisorError) as ei:
        validate_profile(prof)
    assert "claude" in str(ei.value).lower()


def test_s7_claude_role_passes_when_cli_present():
    assert validate_profile(_profile()) is None


# --------------------------------------------------------------------------- #
# S8: scripted only in an explicit test/offline profile.                     #
# --------------------------------------------------------------------------- #
def test_s8_scripted_rejected_in_real_profile():
    prof = _profile(
        name="prod",
        roles=_roles_with(Role.prover, RoleSpec(provider=ProviderKey.scripted)),
    )
    with pytest.raises(SupervisorError) as ei:
        validate_profile(prof)
    msg = str(ei.value).lower()
    assert "scripted" in msg and "prover" in msg


@pytest.mark.parametrize("token", ["test", "offline", "Integration TEST suite", "OFFLINE smoke"])
def test_s8_scripted_allowed_in_test_profile(token):
    prof = _profile(
        name=token,
        roles=_roles_with(Role.prover, RoleSpec(provider=ProviderKey.scripted)),
    )
    assert validate_profile(prof) is None


def test_s8_scripted_via_notes_token():
    prof = _profile(
        name="x",
        notes="this is an offline fixture",
        roles=_roles_with(Role.prover, RoleSpec(provider=ProviderKey.scripted)),
    )
    assert validate_profile(prof) is None


def test_s8_token_not_substring_fastest_rejected():
    # The test/offline signal is a WHOLE-WORD token: an innocent name like 'fastest' (which merely
    # CONTAINS 'test' as a substring) must NOT admit scripted roles into a live profile.
    prof = _profile(
        name="fastest_tuning",
        roles=_roles_with(Role.prover, RoleSpec(provider=ProviderKey.scripted)),
    )
    with pytest.raises(SupervisorError) as ei:
        validate_profile(prof)
    assert "scripted" in str(ei.value).lower()


def test_s8_scripted_fallback_rejected_even_when_live_primary_is_reachable(monkeypatch):
    calls = {"claude": 0}

    def alternating_primary():
        calls["claude"] += 1
        return calls["claude"] % 2 == 1

    monkeypatch.setattr(
        sup,
        "_PROVIDER_PROBE",
        {ProviderKey.claude: alternating_primary, ProviderKey.codex: lambda: True},
    )
    prof = _profile(
        name="production",
        roles=_roles_with(
            Role.prover,
            RoleSpec(provider=ProviderKey.claude, fallback=ProviderKey.scripted),
        ),
    )
    with pytest.raises(SupervisorError) as ei:
        validate_profile(prof)
    msg = str(ei.value).lower()
    assert "fallback" in msg and "scripted" in msg
    assert calls["claude"] == 0, "fallback trust must be checked before reachability"


def test_s8_scripted_fallback_on_inactive_role_is_still_illegal():
    roles = _roles_with(
        Role.comparator,
        RoleSpec(provider=ProviderKey.claude, fallback=ProviderKey.scripted),
    )
    with pytest.raises(SupervisorError, match="fallback='scripted'"):
        validate_profile(_profile(name="production", roles=roles))


@pytest.mark.parametrize("role", [Role.formalizer, Role.faithfulness])
def test_s12_authoritative_certificate_rejects_scripted_fallback(role):
    roles = _roles_with(
        role,
        RoleSpec(provider=ProviderKey.claude, fallback=ProviderKey.scripted),
    )
    prof = _profile(
        name="offline test",
        elementarity=ElementarityLevel.authoritative,
        lean=LeanProfile(terminal=True),
        roles=roles,
    )
    with pytest.raises(SupervisorError) as ei:
        validate_profile(prof)
    msg = str(ei.value).lower()
    assert role.value in msg and "fallback" in msg and "trusted" in msg


def test_provider_probe_snapshot_prevents_alternating_selection(monkeypatch):
    """A provider is probed once per validation, not once per role/capability guard."""
    calls = {"claude": 0}

    def alternating_primary():
        calls["claude"] += 1
        return calls["claude"] % 2 == 1

    monkeypatch.setattr(
        sup,
        "_PROVIDER_PROBE",
        {ProviderKey.claude: alternating_primary, ProviderKey.codex: lambda: True},
    )
    roles = _roles_with(
        Role.prover,
        RoleSpec(provider=ProviderKey.claude, fallback=ProviderKey.scripted),
    )
    assert validate_profile(_profile(name="offline test", roles=roles)) is None
    assert calls["claude"] == 1


@pytest.mark.parametrize("provider_field", [{"model": "opus"}, {"effort": "high"}])
def test_cross_provider_fallback_rejects_primary_specific_config(provider_field):
    roles = _roles_with(
        Role.prover,
        RoleSpec(
            provider=ProviderKey.claude,
            fallback=ProviderKey.codex,
            **provider_field,
        ),
    )
    with pytest.raises(SupervisorError) as ei:
        validate_profile(_profile(roles=roles))
    msg = str(ei.value).lower()
    assert "cross-provider fallback" in msg
    assert next(iter(provider_field)) in msg


def test_cross_provider_fallback_with_provider_defaults_is_admissible():
    roles = _roles_with(
        Role.prover,
        RoleSpec(provider=ProviderKey.claude, fallback=ProviderKey.codex),
    )
    assert validate_profile(_profile(roles=roles)) is None


# --------------------------------------------------------------------------- #
# S9: lean.terminal must equal (elementarity == authoritative).               #
# --------------------------------------------------------------------------- #
def test_s9_authoritative_without_terminal_rejected():
    # authoritative but lean.terminal=False (the default) contradicts the derivation.
    prof = _profile(elementarity=ElementarityLevel.authoritative, lean=LeanProfile(terminal=False))
    with pytest.raises(SupervisorError) as ei:
        validate_profile(prof)
    msg = str(ei.value).lower()
    assert "terminal" in msg and "authoritative" in msg


def test_s9_soft_with_terminal_rejected():
    # soft (non-authoritative) but lean.terminal=True likewise contradicts the derivation.
    prof = _profile(elementarity=ElementarityLevel.soft, lean=LeanProfile(terminal=True))
    with pytest.raises(SupervisorError) as ei:
        validate_profile(prof)
    assert "terminal" in str(ei.value).lower()


def test_s9_authoritative_with_terminal_passes():
    prof = _profile(elementarity=ElementarityLevel.authoritative, lean=LeanProfile(terminal=True))
    assert validate_profile(prof) is None


def test_s9_soft_without_terminal_passes():
    prof = _profile(elementarity=ElementarityLevel.soft, lean=LeanProfile(terminal=False))
    assert validate_profile(prof) is None


# --------------------------------------------------------------------------- #
# S10: lean.per_node => lean.server.                                          #
# --------------------------------------------------------------------------- #
def test_s10_per_node_requires_server():
    prof = _profile(elementarity=ElementarityLevel.soft,
                    lean=LeanProfile(per_node=True, server=False))
    with pytest.raises(SupervisorError) as ei:
        validate_profile(prof)
    msg = str(ei.value).lower()
    assert "per_node" in msg and "server" in msg


def test_s10_per_node_with_server_passes():
    prof = _profile(elementarity=ElementarityLevel.soft,
                    lean=LeanProfile(per_node=True, server=True))
    assert validate_profile(prof) is None


def test_s10_strict_requires_per_node():
    prof = _profile(elementarity=ElementarityLevel.soft,
                    lean=LeanProfile(strict=True))
    with pytest.raises(SupervisorError) as ei:
        validate_profile(prof)
    msg = str(ei.value).lower()
    assert "strict" in msg and "per_node" in msg


def test_s10_strict_with_per_node_and_server_passes():
    prof = _profile(elementarity=ElementarityLevel.soft,
                    lean=LeanProfile(strict=True, per_node=True, server=True))
    assert validate_profile(prof) is None


def test_nondefault_judge_panel_is_rejected_when_refiner_is_off():
    with pytest.raises(SupervisorError, match="judges=3 is inert"):
        validate_profile(_profile(stages=StageProfile(judges=3)))


def test_nondefault_judge_panel_is_active_with_refiner():
    assert validate_profile(_profile(stages=StageProfile(refine=True, judges=3))) is None


def test_refinement_requires_independent_proof_review():
    with pytest.raises(SupervisorError, match="refine=True requires stages.review=True"):
        validate_profile(_profile(stages=StageProfile(refine=True, review=False)))


# --------------------------------------------------------------------------- #
# S11: ensemble weights non-negative with positive sum; model names non-empty. #
# --------------------------------------------------------------------------- #
def test_s11_negative_weight_rejected():
    from agent.orchestrator.run_profile import EnsembleProfile
    prof = _profile(ensemble=EnsembleProfile(breadth_weight=-0.1))
    with pytest.raises(SupervisorError) as ei:
        validate_profile(prof)
    assert "non-negative" in str(ei.value).lower()


def test_s11_all_zero_weights_rejected():
    from agent.orchestrator.run_profile import EnsembleProfile
    prof = _profile(ensemble=EnsembleProfile(breadth_weight=0.0, depth_weight=0.0))
    with pytest.raises(SupervisorError) as ei:
        validate_profile(prof)
    assert "positive finite sum" in str(ei.value).lower()


def test_s11_finite_weights_with_overflowing_sum_rejected():
    from agent.orchestrator.run_profile import EnsembleProfile

    prof = _profile(ensemble=EnsembleProfile(breadth_weight=1e308, depth_weight=1e308))
    with pytest.raises(SupervisorError) as ei:
        validate_profile(prof)
    assert "finite sum" in str(ei.value).lower()


def test_s11_empty_model_name_rejected():
    from agent.orchestrator.run_profile import EnsembleProfile
    prof = _profile(ensemble=EnsembleProfile(breadth_model="  "))
    with pytest.raises(SupervisorError) as ei:
        validate_profile(prof)
    assert "model" in str(ei.value).lower()


def test_s11_custom_ensemble_passes():
    prof = _profile(ensemble=EnsembleProfile(breadth_model="haiku-test", depth_model="opus",
                                             breadth_weight=0.5, depth_weight=0.5))
    assert validate_profile(prof) is None


@pytest.mark.parametrize("stage", [
    StageProfile(evolve=2),
    StageProfile(evolve_witness=2),
    StageProfile(evolve_fallback=2),
])
def test_every_evolution_mode_requires_openevolve(monkeypatch, stage):
    monkeypatch.setattr(sup, "_openevolve_available", lambda: False)
    with pytest.raises(SupervisorError, match="openevolve"):
        validate_profile(_profile(stages=stage))


def test_first_class_evolution_validates_ensemble_provider():
    profile = _profile(
        stages=StageProfile(evolve=2),
        ensemble=EnsembleProfile(provider=ProviderKey.codex),
    )
    with pytest.raises(SupervisorError, match="ensemble.provider='claude'"):
        validate_profile(profile)


def test_role_model_must_be_nonblank_when_declared():
    roles = _roles_with(Role.prover, RoleSpec(model="   "))
    with pytest.raises(SupervisorError) as ei:
        validate_profile(_profile(roles=roles))
    assert "prover" in str(ei.value).lower() and "model" in str(ei.value).lower()


def test_effort_is_rejected_for_provider_that_ignores_it():
    roles = _roles_with(Role.prover, RoleSpec(provider=ProviderKey.claude, effort="high"))
    with pytest.raises(SupervisorError, match="Codex-only"):
        validate_profile(_profile(roles=roles))


@pytest.mark.parametrize("timeout", [0, -1, 86_401])
def test_role_timeout_must_be_positive(timeout):
    roles = _roles_with(Role.prover, RoleSpec(timeout_s=timeout))
    with pytest.raises(SupervisorError) as ei:
        validate_profile(_profile(roles=roles))
    assert "prover" in str(ei.value).lower() and "timeout" in str(ei.value).lower()


# --------------------------------------------------------------------------- #
# Probes never raise: a probe that throws is treated as "unavailable".        #
# --------------------------------------------------------------------------- #
def test_probes_swallow_exceptions(monkeypatch):
    """A throwing underlying probe must be swallowed -> 'unavailable', never a crash."""
    import agent.tools.openevolve_bridge as oe

    # Undo the autouse fixture's stub so we exercise the REAL probe body's try/except.
    monkeypatch.undo()

    def _boom():
        raise RuntimeError("meta-path is on fire")

    monkeypatch.setattr(oe, "available", _boom)
    assert sup._openevolve_available() is False


def test_provider_probe_exception_rejects_as_unavailable(monkeypatch):
    def _boom():
        raise RuntimeError("launcher probe failed")

    monkeypatch.setattr(
        sup,
        "_PROVIDER_PROBE",
        {ProviderKey.claude: _boom, ProviderKey.codex: lambda: True},
    )
    with pytest.raises(SupervisorError, match="no reachable provider"):
        validate_profile(_profile())


def test_split_lean_probes_follow_the_actual_launch_paths(monkeypatch):
    monkeypatch.undo()  # exercise the real probe bodies, not the autouse capability stubs
    from agent.gates import lean_bridge
    from agent.gates import lean_server

    monkeypatch.setattr(lean_bridge, "find_mathlib_project", lambda: "/mathlib-project")
    monkeypatch.setattr(lean_bridge, "find_lake", lambda: "/bin/lake")
    monkeypatch.setattr(lean_bridge, "available", lambda: False)
    monkeypatch.setattr(lean_server.LeanServer, "available", classmethod(lambda cls: False))
    assert sup._lean_compiler_available() is True
    assert sup._lean_repl_available() is False


def test_split_lean_probes_fail_closed_on_exceptions(monkeypatch):
    monkeypatch.undo()  # exercise the real probe bodies, not the autouse capability stubs
    from agent.gates import lean_bridge
    from agent.gates import lean_server

    def _boom(*_args, **_kwargs):
        raise RuntimeError("probe failed")

    monkeypatch.setattr(lean_bridge, "find_mathlib_project", _boom)
    monkeypatch.setattr(lean_server.LeanServer, "available", classmethod(_boom))
    assert sup._lean_compiler_available() is False
    assert sup._lean_repl_available() is False


# --------------------------------------------------------------------------- #
# Minimal restrictiveness: a rich-but-coherent profile passes untouched.      #
# --------------------------------------------------------------------------- #
def test_rich_coherent_profile_passes():
    prof = RunProfile(
        name="full-authoritative",
        mode=Mode.dag,
        elementarity=ElementarityLevel.authoritative,
        stages=StageProfile(decompose=True, review=True, population=4, evolve_fallback=2, refine=True),
        lean=LeanProfile(per_node=True, terminal=True, strict=True, server=True),
        budgets=BudgetProfile(max_llm_calls=120),
    )
    assert validate_profile(prof) is None
