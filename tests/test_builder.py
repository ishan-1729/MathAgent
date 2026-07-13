"""Offline tests for agent/orchestrator/builder.py (W5).

No CLI, model, or real Lean process is ever invoked: profiles use SCRIPTED roles, and Lean wiring
tests replace server/gate construction with deterministic sentinels. We assert:

  * build_driver runs validate_profile FIRST (an inadmissible profile raises SupervisorError BEFORE any
    role is resolved / component constructed);
  * the DEFAULT profile yields a DagDriver whose injected components match the intended current
    wiring: enforce_elementarity True, decomposer+reviewer+proof judge present, no comparator/refiner/terminal/per-
    node gates, h0 on, budgets mapped;
  * each StageProfile knob maps to the right injected param (decompose=False -> decomposer=None, etc.);
  * elementarity policy threads enforcement and optional per-node Lean independently of terminal
    authority;
  * build_and_run constructs + runs end-to-end on scripted parts.
"""
import json

import pytest

from agent.orchestrator.builder import build_driver, build_and_run
from agent.orchestrator.dag_driver import DagDriver, DagResult
from agent.orchestrator.run_profile import (
    BudgetProfile,
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


def test_build_driver_cannot_forward_model_copy_disabled_h0(monkeypatch):
    called = {"resolve": 0}

    def spy(*_args, **_kwargs):
        called["resolve"] += 1
        raise AssertionError("registry must not run for an unchecked profile")

    monkeypatch.setattr(registry_mod, "resolve", spy)
    stages = StageProfile().model_copy(update={"h0_consistency": False})
    bad = _profile().model_copy(update={"stages": stages})
    with pytest.raises(SupervisorError, match="schema revalidation"):
        build_driver(bad)
    assert called["resolve"] == 0


def test_build_driver_cannot_forward_model_copy_invalid_budget(monkeypatch):
    called = {"resolve": 0}

    def spy(*_args, **_kwargs):
        called["resolve"] += 1
        raise AssertionError("registry must not run for an unchecked profile")

    monkeypatch.setattr(registry_mod, "resolve", spy)
    budgets = BudgetProfile().model_copy(update={"max_llm_calls": 0})
    bad = _profile().model_copy(update={"budgets": budgets})
    with pytest.raises(SupervisorError, match="schema revalidation"):
        build_driver(bad)
    assert called["resolve"] == 0


def test_build_driver_admissible_profile_constructs_a_driver():
    d = build_driver(_profile())
    assert isinstance(d, DagDriver)


# ---- the DEFAULT (soft) profile wiring ----------------------------------------------------------

def test_default_profile_wiring():
    """The default soft profile enables enforcement, decomposition review, and proof judging, with
    no population/refinement/Lean gates or warm server. (Only the scripted provider differs here.)"""
    d = build_driver(_profile())
    # elementarity=soft -> enforce ON, no Lean gates of any kind.
    assert d.enforce_elementarity is True
    assert d.terminal_gate is None
    assert d.node_verifier is None and d.sketch_verifier is None
    assert d.lean_server is None
    assert d.lean_strict is False
    # default stages: decompose + review ON; population/refine/evolve OFF.
    assert d.decomposer is not None and d.reviewer is not None and d.judges
    assert d.comparator is None and d.population_k == 0
    assert d.refiner is None
    assert d.evolve_fallback is None
    assert d.h0_consistency is True


def test_default_profile_budget_and_depth_mapping():
    """BudgetProfile -> the engine Budget + the DagDriver depth/attempt/episode params (the default
    BudgetProfile: max_llm_calls=60, max_depth=3, max_decomp_attempts=2, episodes=3)."""
    d = build_driver(_profile())
    assert d.budget.max_llm_calls == 60
    assert d.budget.max_replan_depth == 2
    assert d.budget.max_node_verify_calls is None    # default unlimited (byte-identical sub-cap)
    assert d.max_depth == 3
    assert d.max_decomp_attempts == 2
    assert d.ralph_episodes == 3


def test_replan_budget_is_independent_from_per_node_decomposition_attempts():
    d = build_driver(_profile(budgets=BudgetProfile(
        max_decomp_attempts=7, max_replan_depth=3)))
    assert d.max_decomp_attempts == 7
    assert d.budget.max_replan_depth == 3


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


def test_refiner_judge_count_comes_from_stage_profile():
    d = build_driver(_profile(stages=StageProfile(refine=True, judges=4)))
    assert len(d.refiner.judges) == 4


def test_h0_consistency_is_mandatory_and_cannot_be_disabled():
    assert build_driver(_profile(stages=StageProfile(h0_consistency=True))).h0_consistency is True
    with pytest.raises(ValueError, match="h0_consistency"):
        StageProfile(h0_consistency=False)


# ---- elementarity policy -> enforcement + optional offline-sentinel Lean wiring -----------------

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


def test_elementarity_soft_per_node_wires_both_node_gates_and_server(monkeypatch):
    """A validated soft+per-node profile must attach and execute its requested Lean gates.

    Keep the test offline by replacing validation/tool probes, server startup, and gate factories
    with sentinels; this verifies the profile -> policy -> builder integration rather than Lean.
    """
    import agent.orchestrator.builder as builder_mod
    from agent.orchestrator.run_profile import Role

    class _Server:
        def close(self):
            pass

    class _Prover:
        def prove(self, goal, feedback=None):
            return json.dumps({"problem": "p", "claim": goal, "steps": [
                {"id": "s1", "claim": "setup", "justification": "given", "depends_on": []},
                {"id": "s2", "claim": goal, "justification": "conclusion",
                 "depends_on": ["s1"]},
            ]})

    class _Verified:
        elementary_verified = True
        lean_unavailable = False
        compiled = True
        audit = None

    server = _Server()
    calls = {"warm": 0, "gates": 0, "node": 0, "sketch": 0}

    monkeypatch.setattr(builder_mod, "validate_profile", lambda _profile: None)
    monkeypatch.setitem(
        registry_mod.PROVIDERS,
        (Role.prover, ProviderKey.scripted),
        lambda _spec, _deps: _Prover(),
    )

    def _fake_warm():
        calls["warm"] += 1
        return server

    def node_gate(_goal, _ledger):
        calls["node"] += 1
        return _Verified()

    def sketch_gate(_goal, _sketch, _children):
        calls["sketch"] += 1
        return _Verified()

    def _fake_node_gates(profile, toolkit, actual_server, **_kwargs):
        calls["gates"] += 1
        assert actual_server is server
        return node_gate, sketch_gate

    monkeypatch.setattr(builder_mod, "_warm_lean_server", _fake_warm)
    monkeypatch.setattr(builder_mod, "_make_node_gates", _fake_node_gates)

    d = build_driver(_profile(
        elementarity=ElementarityLevel.soft,
        stages=StageProfile(review=False),
        lean={"per_node": True, "server": True},
    ))

    assert calls == {"warm": 1, "gates": 1, "node": 0, "sketch": 0}
    assert d.enforce_elementarity is True
    assert d.terminal_gate is None
    assert d.node_verifier is node_gate and d.sketch_verifier is sketch_gate
    assert d.lean_server is server

    result = d.run("G")
    assert result.proven
    assert calls == {"warm": 1, "gates": 1, "node": 1, "sketch": 0}
    root = result.dag.get(result.dag.get_or_create("G").key)
    assert root.lean_verified is True


def test_elementarity_none_with_a_lean_flag_is_rejected_by_supervisor():
    """The supervisor (S4) rejects elementarity=none with any Lean flag set, BEFORE the builder wires
    anything — the contradiction never reaches the driver."""
    bad = _profile(elementarity=ElementarityLevel.none,
                   lean={"terminal": True})
    with pytest.raises(SupervisorError):
        build_driver(bad)


def test_direct_mode_with_authoritative_wires_terminal_gate(monkeypatch):
    """Direct-only execution can still certify its completed root through the terminal Layer-4 gate."""
    import agent.orchestrator.builder as builder_mod
    # This is a wiring-only test using scripted components. Production validation correctly rejects
    # scripted certificate roles (pinned separately below), so bypass only that outer guard here.
    monkeypatch.setattr(builder_mod, "validate_profile", lambda _profile: None)
    profile = _profile(
        mode=Mode.direct,
        elementarity=ElementarityLevel.authoritative,
        stages=StageProfile(decompose=False, review=False),
        lean={"terminal": True},
    )
    d = build_driver(profile)
    assert d.decomposer is None
    assert d.terminal_gate is not None


# ---- lean.server wiring (item 3): warm the persistent server ONLY when lean.server=True ---------

def _authoritative_offline(**lean_kw):
    """A scripted authoritative fixture used only by wiring tests that bypass validation.

    Production validation rejects its scripted certificate roles; terminal=True merely keeps the
    policy internally coherent. Extra keywords override LeanProfile fields for a particular wiring.
    """
    lean = {"terminal": True}
    lean.update(lean_kw)
    return _profile(elementarity=ElementarityLevel.authoritative, lean=lean)


def test_authoritative_scripted_certificate_roles_are_rejected():
    with pytest.raises(SupervisorError, match="scripted"):
        build_driver(_authoritative_offline())


def test_terminal_gate_with_server_false_does_not_warm_lean_server(monkeypatch):
    """elementarity=authoritative attaches the terminal gate, but with lean.server=False the builder must
    NOT warm a persistent LeanServer (the gate is threaded server=None -> per-call lean fallback)."""
    import agent.orchestrator.builder as builder_mod
    calls = {"warm": 0}
    monkeypatch.setattr(builder_mod, "validate_profile", lambda _profile: None)
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
    monkeypatch.setattr(builder_mod, "validate_profile", lambda _profile: None)

    def _fake_warm():
        calls["warm"] += 1
        return sentinel

    monkeypatch.setattr(builder_mod, "_warm_lean_server", _fake_warm)
    d = build_driver(_authoritative_offline(server=True))
    assert calls["warm"] == 1
    assert d.terminal_gate is not None
    assert d.lean_server is sentinel


def test_required_server_returning_none_fails_instead_of_downgrading(monkeypatch):
    import agent.orchestrator.builder as builder_mod

    monkeypatch.setattr(builder_mod, "validate_profile", lambda _profile: None)
    monkeypatch.setattr(builder_mod, "_warm_lean_server", lambda: None)
    with pytest.raises(SupervisorError, match="refusing to downgrade"):
        build_driver(_authoritative_offline(server=True))


def test_warm_server_startup_exception_is_actionable_and_fail_closed(monkeypatch):
    import agent.gates.lean_server as lean_server_mod
    import agent.orchestrator.builder as builder_mod

    class _BrokenServer:
        def start(self):
            raise RuntimeError("REPL handshake failed")

    monkeypatch.setattr(lean_server_mod, "LeanServer", _BrokenServer)
    with pytest.raises(SupervisorError, match="could not be started") as ei:
        builder_mod._warm_lean_server()
    assert isinstance(ei.value.__cause__, RuntimeError)


def test_warm_server_is_closed_when_gate_construction_raises(monkeypatch):
    import agent.orchestrator.builder as builder_mod

    server = _StubServer()
    monkeypatch.setattr(builder_mod, "validate_profile", lambda _profile: None)
    monkeypatch.setattr(builder_mod, "_warm_lean_server", lambda: server)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("terminal gate construction failed")

    monkeypatch.setattr(builder_mod, "_make_terminal_gate", _boom)
    with pytest.raises(RuntimeError, match="terminal gate construction failed"):
        build_driver(_authoritative_offline(server=True))
    assert server.closed == 1


def _evolve_offline_profile(**kw):
    kw.setdefault("stages", StageProfile(evolve_fallback=2))
    return _profile(**kw)


def test_required_evolve_fallback_unavailable_fails_closed(monkeypatch):
    import agent.tools.openevolve_bridge as evolve_mod
    import agent.orchestrator.builder as builder_mod

    monkeypatch.setattr(
        evolve_mod.OpenEvolveBackend,
        "available",
        staticmethod(lambda: False),
    )
    with pytest.raises(SupervisorError, match="became unavailable"):
        builder_mod._resolve_evolve_fallback(_evolve_offline_profile(), object())


def test_required_evolve_fallback_constructor_failure_fails_closed(monkeypatch):
    import agent.tools.openevolve_bridge as evolve_mod
    import agent.orchestrator.builder as builder_mod

    class _BrokenBackend:
        @staticmethod
        def available():
            return True

        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("backend construction failed")

    monkeypatch.setattr(evolve_mod, "OpenEvolveBackend", _BrokenBackend)
    with pytest.raises(SupervisorError, match="could not be constructed") as ei:
        builder_mod._resolve_evolve_fallback(_evolve_offline_profile(), object())
    assert isinstance(ei.value.__cause__, RuntimeError)


def test_evolve_fallback_does_not_borrow_ordinary_decomposer_config(monkeypatch):
    import agent.tools.openevolve_bridge as evolve_mod
    import agent.orchestrator.builder as builder_mod

    seen = {}

    class _CapturingBackend:
        @staticmethod
        def available():
            return True

        def __init__(self, _toolkit, claude_cfg, **kwargs):
            seen["cfg"] = claude_cfg
            seen["kwargs"] = kwargs

    monkeypatch.setattr(evolve_mod, "OpenEvolveBackend", _CapturingBackend)
    roles = _scripted_roles().model_copy(update={
        "decomposer": RoleSpec(
            provider=ProviderKey.codex,
            model="gpt-5.5",
            timeout_s=17,
        ),
    })
    profile = _evolve_offline_profile(roles=roles)
    backend = builder_mod._resolve_evolve_fallback(profile, object())
    assert isinstance(backend, _CapturingBackend)
    assert seen["cfg"].model == "sonnet" and seen["cfg"].timeout_s == 600
    assert seen["kwargs"]["generations"] == 2


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
    assert res.resolved_roles["prover"]["provider"] == "scripted"
    assert len(res.policy_digest or "") == 64
    assert len(res.dag.context or "") == 64


def test_actual_fallback_selection_survives_on_result(monkeypatch):
    """A fixed declared profile hash can execute its fallback; the result records what ran."""
    import agent.orchestrator.builder as builder_mod
    from agent.orchestrator import supervisor
    from agent.orchestrator.run_profile import Mode, RoleSpec, RolesProfile

    monkeypatch.setattr(builder_mod, "validate_profile", lambda _profile: None)
    monkeypatch.setattr(
        supervisor, "_provider_available", lambda provider: provider is ProviderKey.codex,
    )
    spec = RoleSpec(provider=ProviderKey.claude, fallback=ProviderKey.codex)
    profile = _profile(
        mode=Mode.direct,
        roles=RolesProfile(
            prover=spec, decomposer=spec, reviewer=spec, comparator=spec, judge=spec,
            formalizer=spec, faithfulness=spec, refiner=spec,
        ),
        stages=StageProfile(decompose=False, review=False),
    )
    declared_hash = profile.profile_hash
    driver = build_driver(profile)
    driver.prover = _GoalBoundProver()
    result = driver.run("G")

    assert profile.profile_hash == declared_hash
    assert result.resolved_roles["prover"]["provider"] == "codex"
    assert result.resolved_roles["prover"]["fallback_selected"] is True


def test_build_driver_rejects_unconsumed_goal_dependent_presearch(monkeypatch):
    import agent.orchestrator.builder as builder_mod

    monkeypatch.setattr(builder_mod, "validate_profile", lambda _profile: None)
    with pytest.raises(SupervisorError, match="build_and_run"):
        build_driver(_profile(stages=StageProfile(evolve=1)))


def test_build_and_run_feeds_presearch_seed_through_ordinary_driver(monkeypatch):
    import agent.orchestrator.builder as builder_mod

    monkeypatch.setattr(builder_mod, "validate_profile", lambda _profile: None)

    def seed(_profile, goal, **_kwargs):
        return json.dumps({"problem": "p", "claim": goal, "steps": [
            {"id": "s1", "claim": "setup", "justification": "given", "depends_on": []},
            {"id": "s2", "claim": goal, "justification": "conclusion", "depends_on": ["s1"]},
        ]})

    monkeypatch.setattr(builder_mod, "_run_profile_presearch", seed)
    result = build_and_run(
        _profile(stages=StageProfile(evolve=1, review=False)), "G")
    assert result.proven
    assert result.dag.get_or_create("G").proof_kind == "direct"


def test_build_and_run_proves_with_a_goal_bound_scripted_prover(monkeypatch):
    """End-to-end: with a scripted prover that returns a goal-bound ledger, the constructed driver PROVES
    the goal directly through build_and_run — confirming the wired driver runs the real engine."""
    # Register a one-off scripted prover factory returning the goal-bound prover, restore after.
    from agent.orchestrator.run_profile import Role
    key = (Role.prover, ProviderKey.scripted)
    saved = registry_mod.PROVIDERS[key]
    registry_mod.PROVIDERS[key] = lambda spec, deps: _GoalBoundProver()
    try:
        # This test isolates the prover path; disabling review avoids the independent scripted judge
        # replacing that concern with its own canned verdict.
        res = build_and_run(_profile(stages=StageProfile(review=False)), "G")
    finally:
        registry_mod.PROVIDERS[key] = saved
    assert res.proven is True
    assert res.dag.get(res.dag.get_or_create("G").key).proof_kind == "direct"


class _StubServer:
    """A warm-LeanServer stand-in that records how many times it was closed (FIX 2 leak test)."""
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


def test_build_and_run_closes_warm_lean_server_once(monkeypatch):
    """FIX 2: build_and_run OWNS the one LeanServer it warmed and must close it exactly once (a
    try/finally driver.close()), else every build_and_run leaks an orphaned repl.exe holding Mathlib.
    We thread a stub server through the builder's warm path and assert it is closed once after run()."""
    import agent.orchestrator.builder as builder_mod
    stub = _StubServer()
    monkeypatch.setattr(builder_mod, "validate_profile", lambda _profile: None)
    monkeypatch.setattr(builder_mod, "_warm_lean_server", lambda: stub)
    res = build_and_run(_authoritative_offline(server=True), "G")
    assert isinstance(res, DagResult)
    assert stub.closed == 1                    # the warmed server was released exactly once


def test_build_and_run_closes_warm_lean_server_even_when_run_raises(monkeypatch):
    """FIX 2: the close() runs in a finally, so a RAISING run() still releases the warmed server (the
    exception propagates, but no repl.exe leaks)."""
    import agent.orchestrator.builder as builder_mod
    stub = _StubServer()
    monkeypatch.setattr(builder_mod, "validate_profile", lambda _profile: None)
    monkeypatch.setattr(builder_mod, "_warm_lean_server", lambda: stub)

    def _boom(self, goal, *, context=None):
        raise RuntimeError("run blew up")

    monkeypatch.setattr(DagDriver, "run", _boom)
    with pytest.raises(RuntimeError):
        build_and_run(_authoritative_offline(server=True), "G")
    assert stub.closed == 1                    # finally released the server despite run() raising


def test_driver_close_is_idempotent_and_never_raises(monkeypatch):
    """DagDriver.close() guards a None server, swallows a raising close(), and is safe to call twice."""
    class _RaisingServer:
        def __init__(self):
            self.closed = 0

        def close(self):
            self.closed += 1
            raise RuntimeError("close blew up")

    import agent.orchestrator.builder as builder_mod
    srv = _RaisingServer()
    monkeypatch.setattr(builder_mod, "validate_profile", lambda _profile: None)
    monkeypatch.setattr(builder_mod, "_warm_lean_server", lambda: srv)
    d = build_driver(_authoritative_offline(server=True))
    assert d.lean_server is srv
    d.close()                                  # a raising server.close() must NOT propagate
    assert srv.closed == 1 and d.lean_server is None
    d.close()                                  # second close is a no-op (server already None)
    assert srv.closed == 1


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

    def spy(profile, *, toolkit=None, trace=None, **kwargs):
        seen["toolkit"] = toolkit
        seen["trace"] = trace
        return real_build(profile, toolkit=toolkit, trace=trace, **kwargs)

    monkeypatch.setattr(builder_mod, "build_driver", spy)
    build_and_run(_profile(), "G", toolkit=tk, trace=trace)
    assert seen["toolkit"] is tk and seen["trace"] is trace
