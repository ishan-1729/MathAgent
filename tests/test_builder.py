"""Offline tests for agent/orchestrator/builder.py (W5).

No CLI / model / Lean is ever invoked: every profile here is an explicit test/offline profile whose
roles use the SCRIPTED provider (deterministic stubs), and elementarity stays soft/none so NO Lean gate
is ever wired. We assert:

  * build_driver runs validate_profile FIRST (an inadmissible profile raises SupervisorError BEFORE any
    role is resolved / component constructed);
  * the DEFAULT profile yields a DagDriver whose injected components match the intended (byte-identical)
    wiring: enforce_elementarity True, decomposer+reviewer present, no comparator/refiner/terminal/per-
    node gates, h0 on, budgets mapped;
  * each StageProfile knob maps to the right injected param (decompose=False -> decomposer=None, etc.);
  * elementarity policy threads enforce_elementarity (none -> False) without wiring Lean offline;
  * build_and_run constructs + runs end-to-end on scripted parts.
"""
import json

import pytest

from agent.orchestrator.builder import build_driver, build_and_run
from agent.orchestrator.dag_driver import DagDriver, DagResult
from agent.orchestrator.run_profile import (
    ElementarityLevel,
    Mode,
    ProviderKey,
    RoleSpec,
    RolesProfile,
    RunProfile,
    StageProfile,
)
from agent.orchestrator.supervisor import SupervisorError
from agent.orchestrator import registry as registry_mod


# A scripted RoleSpec for every role -> a fully offline-resolvable profile.
_S = RoleSpec(provider=ProviderKey.scripted)


def _scripted_roles() -> RolesProfile:
    return RolesProfile(prover=_S, decomposer=_S, reviewer=_S, comparator=_S, judge=_S,
                        formalizer=_S, faithfulness=_S, refiner=_S)


def _profile(**kw) -> RunProfile:
    """A baseline OFFLINE/test profile (the name carries the 'offline' opt-in the supervisor requires
    for the scripted provider). Keyword overrides flow straight through to RunProfile."""
    kw.setdefault("name", "offline-test")
    kw.setdefault("roles", _scripted_roles())
    return RunProfile(**kw)


# ---- supervisor runs FIRST: an inadmissible profile never constructs a component ----------------

def test_build_driver_validates_before_resolving_any_role(monkeypatch):
    """validate_profile is called FIRST: an inadmissible profile raises SupervisorError BEFORE a single
    role is resolved. We spy on registry.resolve and assert it is NEVER reached on a bad profile."""
    called = {"resolve": 0}
    real_resolve = registry_mod.resolve

    def spy(*a, **k):
        called["resolve"] += 1
        return real_resolve(*a, **k)

    # The builder imports resolve LAZILY from the registry inside build_driver (after the supervisor),
    # so patch it at the SOURCE module — the lazy `from registry import resolve` then binds the spy.
    monkeypatch.setattr(registry_mod, "resolve", spy)

    # scripted provider in a NON-test profile (no 'test'/'offline' token) is inadmissible (S8).
    bad = RunProfile(name="production", roles=_scripted_roles())
    with pytest.raises(SupervisorError):
        build_driver(bad)
    assert called["resolve"] == 0, "no role may be resolved before the supervisor admits the profile"


def test_build_driver_admissible_profile_constructs_a_driver():
    d = build_driver(_profile())
    assert isinstance(d, DagDriver)


# ---- the DEFAULT (soft) profile: BYTE-IDENTICAL pre-feature wiring ------------------------------

def test_default_profile_wiring_is_byte_identical():
    """A default (soft-elementarity) offline profile yields a DagDriver wired exactly like the
    pre-feature default: enforcement ON, decomposer+reviewer present, NO comparator/refiner/terminal/
    per-node gates, NO warm Lean server, h0 on. (Only the scripted provider differs from a live run.)"""
    d = build_driver(_profile())
    # elementarity=soft -> enforce ON, no Lean gates of any kind.
    assert d.enforce_elementarity is True
    assert d.terminal_gate is None
    assert d.node_verifier is None and d.sketch_verifier is None
    assert d.lean_server is None
    assert d.lean_strict is False
    # default stages: decompose + review ON; population/refine/evolve OFF.
    assert d.decomposer is not None and d.reviewer is not None
    assert d.comparator is None and d.population_k == 0
    assert d.refiner is None
    assert d.evolve_fallback is None
    assert d.h0_consistency is True


def test_default_profile_budget_and_depth_mapping():
    """BudgetProfile -> the engine Budget + the DagDriver depth/attempt/episode params (the default
    BudgetProfile: max_llm_calls=60, max_depth=3, max_decomp_attempts=2, episodes=3)."""
    d = build_driver(_profile())
    assert d.budget.max_llm_calls == 60
    assert d.budget.max_replan_depth == 2            # mapped from max_decomp_attempts
    assert d.budget.max_node_verify_calls is None    # default unlimited (byte-identical sub-cap)
    assert d.max_depth == 3
    assert d.max_decomp_attempts == 2
    assert d.ralph_episodes == 3


def test_resolved_components_conform_to_role_protocols():
    """The injected components are behind the EXISTING Protocols the DagDriver expects for each role."""
    from agent.orchestrator.driver import Prover
    from agent.orchestrator.dag_driver import Decomposer, Reviewer
    d = build_driver(_profile())
    assert isinstance(d.prover, Prover)
    assert isinstance(d.decomposer, Decomposer)
    assert isinstance(d.reviewer, Reviewer)


# ---- StageProfile knob -> injected-param mapping -----------------------------------------------

def test_decompose_false_yields_no_decomposer_direct_only():
    """stages.decompose=False -> decomposer=None: the driver is direct-only (never asks for a blueprint)."""
    d = build_driver(_profile(stages=StageProfile(decompose=False)))
    assert d.decomposer is None


def test_review_false_yields_no_reviewer():
    d = build_driver(_profile(stages=StageProfile(review=False)))
    assert d.reviewer is None


def test_population_zero_yields_no_comparator():
    d = build_driver(_profile(stages=StageProfile(population=0)))
    assert d.comparator is None and d.population_k == 0


def test_population_positive_wires_comparator_and_population_k():
    from agent.orchestrator.population import Comparator
    d = build_driver(_profile(stages=StageProfile(population=3)))
    assert d.comparator is not None and isinstance(d.comparator, Comparator)
    assert d.population_k == 3


def test_refine_false_yields_no_refiner():
    d = build_driver(_profile(stages=StageProfile(refine=False)))
    assert d.refiner is None


def test_refine_true_wires_a_revision_controller():
    from agent.orchestrator.tournament import RevisionController
    d = build_driver(_profile(stages=StageProfile(refine=True)))
    assert isinstance(d.refiner, RevisionController)


def test_h0_consistency_flag_threads_through():
    assert build_driver(_profile(stages=StageProfile(h0_consistency=True))).h0_consistency is True
    assert build_driver(_profile(stages=StageProfile(h0_consistency=False))).h0_consistency is False


# ---- elementarity policy -> enforce_elementarity (no Lean wired offline) ------------------------

def test_elementarity_none_disables_enforcement_no_lean_offline():
    """elementarity=none -> enforce_elementarity=False AND no Lean gate of any kind (the policy table's
    none row: enforce/terminal/per_node all False). Stays fully offline (no warm server)."""
    d = build_driver(_profile(elementarity=ElementarityLevel.none))
    assert d.enforce_elementarity is False
    assert d.terminal_gate is None
    assert d.node_verifier is None and d.sketch_verifier is None
    assert d.lean_server is None


def test_elementarity_soft_enforces_without_lean():
    d = build_driver(_profile(elementarity=ElementarityLevel.soft))
    assert d.enforce_elementarity is True
    assert d.terminal_gate is None and d.node_verifier is None


def test_elementarity_none_with_a_lean_flag_is_rejected_by_supervisor():
    """The supervisor (S4) rejects elementarity=none with any Lean flag set, BEFORE the builder wires
    anything — the contradiction never reaches the driver."""
    bad = _profile(elementarity=ElementarityLevel.none,
                   lean={"terminal": True})
    with pytest.raises(SupervisorError):
        build_driver(bad)


def test_direct_mode_with_authoritative_is_rejected_by_supervisor():
    """The supervisor (S5) rejects mode=direct + elementarity=authoritative before construction."""
    bad = _profile(mode=Mode.direct, elementarity=ElementarityLevel.authoritative)
    with pytest.raises(SupervisorError):
        build_driver(bad)


# ---- lean.server wiring (item 3): warm the persistent server ONLY when lean.server=True ---------

def _authoritative_offline(**lean_kw):
    """An offline AUTHORITATIVE profile (scripted roles) with a consistent LeanProfile: terminal=True
    (S9 requires terminal == authoritative). The scripted formalizer keeps the terminal gate buildable
    offline. Extra lean_kw override the LeanProfile fields (e.g. server=True/False)."""
    lean = {"terminal": True}
    lean.update(lean_kw)
    return _profile(elementarity=ElementarityLevel.authoritative, lean=lean)


def test_terminal_gate_with_server_false_does_not_warm_lean_server(monkeypatch):
    """elementarity=authoritative attaches the terminal gate, but with lean.server=False the builder must
    NOT warm a persistent LeanServer (the gate is threaded server=None -> per-call lean fallback)."""
    import agent.orchestrator.builder as builder_mod
    calls = {"warm": 0}
    monkeypatch.setattr(builder_mod, "_warm_lean_server",
                        lambda: calls.__setitem__("warm", calls["warm"] + 1) or None)
    d = build_driver(_authoritative_offline(server=False))
    assert calls["warm"] == 0                 # server=False -> never warmed
    assert d.terminal_gate is not None        # the terminal gate still attaches
    assert d.lean_server is None              # threaded server=None (per-call lean)


def test_terminal_gate_with_server_true_warms_lean_server(monkeypatch):
    """With lean.server=True the builder DOES call _warm_lean_server and threads its result in."""
    import agent.orchestrator.builder as builder_mod
    sentinel = object()
    calls = {"warm": 0}

    def _fake_warm():
        calls["warm"] += 1
        return sentinel

    monkeypatch.setattr(builder_mod, "_warm_lean_server", _fake_warm)
    d = build_driver(_authoritative_offline(server=True))
    assert calls["warm"] == 1
    assert d.terminal_gate is not None
    assert d.lean_server is sentinel


# ---- memo toggle (item 5): StageProfile.memo threads into the driver ----------------------------

def test_memo_default_true_threads_into_driver():
    d = build_driver(_profile())
    assert d.memo is True


def test_memo_false_threads_into_driver():
    d = build_driver(_profile(stages=StageProfile(memo=False)))
    assert d.memo is False


# ---- toolkit sharing + trace naming ------------------------------------------------------------

def test_build_driver_shares_one_toolkit_across_roles():
    """The builder threads ONE toolkit instance into the driver (the same Deps.toolkit every role got)."""
    from agent.gates.toolkit import load_toolkit
    tk = load_toolkit()
    d = build_driver(_profile(), toolkit=tk)
    assert d.toolkit is tk


def test_build_driver_uses_profile_name_for_default_trace():
    d = build_driver(_profile(name="offline-my-run"))
    assert d.trace.run_id == "offline-my-run"


# ---- build_and_run: construct + run end to end on scripted parts -------------------------------

class _GoalBoundProver:
    """A scripted prover returning a goal-bound, gate-passing ledger for any goal (so a default profile
    actually PROVES end to end through build_and_run, exercising the constructed driver's run())."""
    def prove(self, goal: str, feedback=None) -> str:
        return json.dumps({"problem": "p", "claim": goal, "steps": [
            {"id": "s1", "claim": "setup", "justification": "given", "depends_on": []},
            {"id": "s2", "claim": goal, "justification": "conclusion", "depends_on": ["s1"]}]})


def test_build_and_run_returns_a_dag_result():
    """build_and_run validates + builds + runs, returning a DagResult. With the default scripted prover
    (an empty ledger) the goal does not prove, but the call completes cleanly (no crash, real result)."""
    res = build_and_run(_profile(), "G")
    assert isinstance(res, DagResult)
    assert res.goal == "G"


def test_build_and_run_proves_with_a_goal_bound_scripted_prover(monkeypatch):
    """End-to-end: with a scripted prover that returns a goal-bound ledger, the constructed driver PROVES
    the goal directly through build_and_run — confirming the wired driver runs the real engine."""
    # Register a one-off scripted prover factory returning the goal-bound prover, restore after.
    from agent.orchestrator.run_profile import Role
    key = (Role.prover, ProviderKey.scripted)
    saved = registry_mod.PROVIDERS[key]
    registry_mod.PROVIDERS[key] = lambda spec, deps: _GoalBoundProver()
    try:
        res = build_and_run(_profile(), "G")
    finally:
        registry_mod.PROVIDERS[key] = saved
    assert res.proven is True
    assert res.dag.get(res.dag.get_or_create("G").key).proof_kind == "direct"


def test_build_and_run_forwards_toolkit_and_trace(monkeypatch):
    """item 7: build_and_run forwards the optional toolkit/trace to build_driver (previously dropped),
    so a caller can share one toolkit + trace across the build_and_run call."""
    from agent.gates.toolkit import load_toolkit
    from agent.orchestrator.trace import RunTrace
    tk = load_toolkit()
    trace = RunTrace("shared-trace")
    seen = {}
    import agent.orchestrator.builder as builder_mod
    real_build = builder_mod.build_driver

    def spy(profile, *, toolkit=None, trace=None):
        seen["toolkit"] = toolkit
        seen["trace"] = trace
        return real_build(profile, toolkit=toolkit, trace=trace)

    monkeypatch.setattr(builder_mod, "build_driver", spy)
    build_and_run(_profile(), "G", toolkit=tk, trace=trace)
    assert seen["toolkit"] is tk and seen["trace"] is trace
