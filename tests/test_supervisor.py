"""Offline tests for the fail-CLOSED supervisor (Ramadge-Wonham SCT controller).

Every test monkeypatches the lazy capability probes in
``agent.orchestrator.supervisor`` so NO live model/Lean/openevolve call happens.

For each guard S1..S8 we assert two things:

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
    monkeypatch.setattr(sup, "_lean_available", lambda: True)
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
def test_s1_authoritative_requires_lean(monkeypatch):
    monkeypatch.setattr(sup, "_lean_available", lambda: False)
    prof = _profile(elementarity=ElementarityLevel.authoritative)
    with pytest.raises(SupervisorError) as ei:
        validate_profile(prof)
    assert "authoritative" in str(ei.value).lower()
    assert "lean" in str(ei.value).lower()


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
        roles=_roles_with(Role.formalizer, RoleSpec(provider=ProviderKey.codex)),
    )
    with pytest.raises(SupervisorError) as ei:
        validate_profile(prof)
    # codex absence is caught at S6 OR S1 (both name the formalizer/provider) — either
    # way it must mention the formalizer or its provider.
    msg = str(ei.value).lower()
    assert "formalizer" in msg or "codex" in msg


def test_s1_authoritative_passes_when_available():
    prof = _profile(elementarity=ElementarityLevel.authoritative)
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
    prof = _profile(
        elementarity=ElementarityLevel.soft,
        lean=LeanProfile(per_node=True),
        roles=_roles_with(Role.formalizer, RoleSpec(provider=ProviderKey.codex)),
    )
    with pytest.raises(SupervisorError) as ei:
        validate_profile(prof)
    msg = str(ei.value).lower()
    assert "formalizer" in msg or "codex" in msg


def test_s2_per_node_passes_with_claude_formalizer():
    prof = _profile(elementarity=ElementarityLevel.soft, lean=LeanProfile(per_node=True))
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


def test_s4_none_allows_lean_server_flag():
    # lean.server is a transport detail, NOT a verification gate; none must allow it.
    prof = _profile(elementarity=ElementarityLevel.none, lean=LeanProfile(server=True))
    assert validate_profile(prof) is None


# --------------------------------------------------------------------------- #
# S5: mode=direct => not authoritative.                                       #
# --------------------------------------------------------------------------- #
def test_s5_direct_forbids_authoritative():
    prof = _profile(mode=Mode.direct, elementarity=ElementarityLevel.authoritative)
    with pytest.raises(SupervisorError) as ei:
        validate_profile(prof)
    msg = str(ei.value).lower()
    assert "direct" in msg and "authoritative" in msg


def test_s5_direct_soft_passes():
    assert validate_profile(_profile(mode=Mode.direct, elementarity=ElementarityLevel.soft)) is None


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
