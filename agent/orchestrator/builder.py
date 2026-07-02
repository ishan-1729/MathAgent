"""The profile -> DagDriver builder (W5): the one place a validated RunProfile becomes a live driver.

Flow (the architecture's control lever, end to end)::

    RunProfile
      -> supervisor.validate_profile   (fail-CLOSED, BEFORE any model/Lean call)
      -> registry.resolve(role, spec)  (role -> provider -> component, behind each Protocol)
      -> StageProfile -> DagDriver injected params (decompose=False -> decomposer=None, ...)
      -> elementarity_policy.policy_for -> enforce_elementarity + terminal/per-node Lean gates
      -> the EXISTING DagDriver (no new FSM; the config drives the existing machine)

Two entry points:

* :func:`build_driver(profile, *, toolkit=None, trace=None) -> DagDriver` — construct (do not run).
* :func:`build_and_run(profile, goal) -> DagResult` — construct + ``.run(goal)``.

Invariants:

* ``validate_profile`` is ALWAYS called first; an inadmissible profile raises ``SupervisorError``
  before a single component is constructed (no model/Lean call ever leaks past a bad profile).
* The mapping is mechanical and total: every ``StageProfile`` / ``LeanProfile`` knob has exactly one
  destination ``DagDriver`` ctor param, and ``elementarity_policy`` is the single source of truth for
  the three Lean-wiring decisions (enforce / terminal / per-node).
* The DEFAULT profile yields a driver that runs the BYTE-IDENTICAL pre-feature path: enforcement on,
  no terminal/per-node Lean gates (soft elementarity wires none), so nothing here changes the default
  search behavior — it only re-expresses the existing ctor wiring as data.

SAFETY: nothing here exec/eval/imports model output; it only constructs objects + (when Lean is
requested) warms ONE shared LeanServer. The deterministic gate stays authoritative downstream.
"""
from __future__ import annotations

from typing import Optional

from agent.gates.toolkit import Toolkit, load_toolkit
from agent.orchestrator.dag_driver import DagDriver, DagResult
from agent.orchestrator.elementarity_policy import policy_for
from agent.orchestrator.run_profile import Role, RunProfile
from agent.orchestrator.state import Budget
from agent.orchestrator.supervisor import validate_profile
from agent.orchestrator.trace import RunTrace

# NOTE: the registry (which transitively imports the heavy agent.tools.formalizer / codex_prover
# wrappers at its module top) is imported LAZILY inside the functions below. Importing this module —
# and therefore the agent.orchestrator package __init__ that re-exports build_driver — must NOT eagerly
# pull the live tool wrappers (it would create an import cycle with agent.tools.* and defeats the cheap,
# side-effect-free package import the architecture promises). Each function that needs the registry
# imports Deps/resolve at call time.


def _spec(profile: RunProfile, role: Role):
    """The RoleSpec for ``role`` off the profile's RolesProfile (declaration-order safe)."""
    return getattr(profile.roles, role.value)


def _budget_from(profile: RunProfile) -> Budget:
    """Map BudgetProfile -> the engine's Budget (the cap object the DagDriver / Ralph loop spend).

    max_llm_calls / max_replan_depth(=max_decomp_attempts) / max_node_verify_calls map directly; the
    DagDriver's max_depth / max_decomp_attempts / ralph_episodes are SEPARATE ctor params (set in
    build_driver), so only the spend caps live on the Budget here."""
    b = profile.budgets
    return Budget(
        max_llm_calls=b.max_llm_calls,
        max_replan_depth=b.max_decomp_attempts,
        max_node_verify_calls=b.max_node_verify_calls,
    )


def _warm_lean_server():
    """Start ONE shared warm LeanServer if a built REPL is reachable, else None (never raises).

    Mirrors scripts/prove.py: a single persistent server (Mathlib + #audit loaded once) is threaded
    into BOTH per-node gates so every compile reuses the warm base environment. The supervisor has
    already verified Lean reachability for authoritative/per-node profiles, but we still probe + guard
    here so a transient toolchain failure degrades gracefully to no warm server rather than crashing."""
    try:
        from agent.gates.lean_server import LeanServer

        if not LeanServer.available():
            return None
        return LeanServer().start()
    except Exception:
        return None


def _make_terminal_gate(profile: RunProfile, toolkit: Toolkit, server):
    """Build the terminal Layer-4 gate (formalize -> Lean audit -> faithfulness) from the resolved
    formalizer + faithfulness roles. Returns the gate callable, or None if it cannot be built."""
    from agent.orchestrator.formalize_bridge import make_terminal_gate
    from agent.orchestrator.registry import Deps, resolve

    formalizer = resolve(Role.formalizer, _spec(profile, Role.formalizer),
                         Deps(toolkit=toolkit))
    faith = resolve(Role.faithfulness, _spec(profile, Role.faithfulness),
                    Deps(toolkit=toolkit))
    return make_terminal_gate(formalizer, toolkit, faithfulness_checker=faith, server=server)


def _make_node_gates(profile: RunProfile, toolkit: Toolkit, server):
    """Build the (node_verifier, sketch_verifier) per-node Lean gates from the resolved formalizer role.
    The SAME warm ``server`` is threaded into both so per-node compiles share one Mathlib environment."""
    from agent.orchestrator.formalize_bridge import make_node_gate, make_sketch_gate
    from agent.orchestrator.registry import Deps, resolve

    formalizer = resolve(Role.formalizer, _spec(profile, Role.formalizer),
                         Deps(toolkit=toolkit))
    node_verifier = make_node_gate(formalizer, toolkit, server=server)
    sketch_verifier = make_sketch_gate(formalizer, toolkit, server=server)
    return node_verifier, sketch_verifier


def build_driver(profile: RunProfile, *, toolkit: Optional[Toolkit] = None,
                 trace: Optional[RunTrace] = None) -> DagDriver:
    """Validate ``profile`` then construct the EXISTING DagDriver wired per the profile.

    Order (the contract): validate_profile FIRST -> resolve each role via the registry -> map
    StageProfile to the DagDriver's injected params -> apply elementarity_policy (enforce_elementarity
    + terminal/per-node Lean gates, warming one shared Lean server when Lean is on) -> construct.

    StageProfile -> injected param mapping (illegal states made absent, not flagged):
      * decompose=False -> decomposer=None  (direct-only: the driver never asks for a blueprint)
      * review=False    -> reviewer=None
      * population==0   -> comparator=None (else the comparator role + population_k=population)
      * refine=False    -> refiner=None
      * evolve_fallback==0 -> evolve_fallback=None (else the resolved fallback decomposer)
      * h0_consistency  -> the driver flag verbatim

    Raises SupervisorError (before any component is constructed) for an inadmissible profile."""
    # 1. FAIL-CLOSED supervisor FIRST — before resolving a single role or touching the toolkit/Lean.
    validate_profile(profile)

    # Lazy registry import (see module note): only after the supervisor admits the profile do we pull
    # the registry (and thus the live tool wrappers it imports). Keeps package import side-effect free.
    from agent.orchestrator.registry import Deps, resolve

    toolkit = toolkit or load_toolkit()
    trace = trace or RunTrace(profile.name)
    budget = _budget_from(profile)
    stages = profile.stages

    # One shared Deps so every role gets the SAME toolkit; refiner knobs feed the refiner factory.
    deps = Deps(toolkit=toolkit, budget=budget, trace=trace,
                n_judges=1, max_passes=2, k_stop=2, margin=1, seed=profile.seed)

    # 2. Resolve roles via the registry, mapping the StageProfile knobs to inject/omit each component.
    prover = resolve(Role.prover, _spec(profile, Role.prover), deps)
    decomposer = (resolve(Role.decomposer, _spec(profile, Role.decomposer), deps)
                  if stages.decompose else None)
    reviewer = (resolve(Role.reviewer, _spec(profile, Role.reviewer), deps)
                if stages.review else None)
    # population: a comparator only when population>0 (the population_k breadth knob).
    comparator = (resolve(Role.comparator, _spec(profile, Role.comparator), deps)
                  if stages.population > 0 else None)
    population_k = stages.population if stages.population > 0 else 0
    # refiner: the AutoReason incumbent tournament, only when refine is on.
    refiner = (resolve(Role.refiner, _spec(profile, Role.refiner), deps)
               if stages.refine else None)
    # evolve fallback: a last-resort decomposer on stuck nodes, only when evolve_fallback>0.
    evolve_fallback = (_resolve_evolve_fallback(profile, toolkit)
                       if stages.evolve_fallback > 0 else None)

    # 3. elementarity_policy: the SINGLE source of truth for the three Lean-wiring decisions.
    policy = policy_for(profile.elementarity, lean_per_node=profile.lean.per_node)

    server = None
    terminal_gate = None
    node_verifier = None
    sketch_verifier = None
    if (policy.attach_terminal_gate or policy.attach_per_node_lean) and profile.lean.server:
        # Warm ONE shared Lean server reused by the terminal + per-node gates (Mathlib loaded once) —
        # ONLY when lean.server is True. With lean.server=False a gate still attaches but is threaded
        # server=None, so each compile uses the per-call lean fallback the gates already support (no
        # persistent REPL). The supervisor (S10) has already ensured per_node => server, so a per-node
        # gate never reaches this branch with lean.server=False; the terminal gate may (server-less
        # terminal audit via per-call lean).
        server = _warm_lean_server()
    if policy.attach_terminal_gate:
        terminal_gate = _make_terminal_gate(profile, toolkit, server)
    if policy.attach_per_node_lean:
        node_verifier, sketch_verifier = _make_node_gates(profile, toolkit, server)

    # 4. Construct the EXISTING DagDriver. With the default (soft) profile this is the BYTE-IDENTICAL
    #    pre-feature wiring: enforce_elementarity=True, no terminal/per-node gates, no warm server.
    return DagDriver(
        prover,
        decomposer=decomposer,
        reviewer=reviewer,
        toolkit=toolkit,
        budget=budget,
        trace=trace,
        max_depth=profile.budgets.max_depth,
        max_decomp_attempts=profile.budgets.max_decomp_attempts,
        ralph_episodes=profile.budgets.episodes,
        comparator=comparator,
        population_k=population_k,
        refiner=refiner,
        terminal_gate=terminal_gate,
        h0_consistency=stages.h0_consistency,
        evolve_fallback=evolve_fallback,
        node_verifier=node_verifier,
        sketch_verifier=sketch_verifier,
        lean_strict=profile.lean.strict,
        lean_server=server,
        enforce_elementarity=policy.enforce_elementarity,
        memo=stages.memo,
    )


def _resolve_evolve_fallback(profile: RunProfile, toolkit: Toolkit):
    """Build the OpenEvolve fallback decomposer for ``stages.evolve_fallback`` generations.

    The supervisor (S3) has already verified ``openevolve`` is installed when evolve_fallback>0; this
    constructs the backend with the decomposer role's provider config. Returns None on any construction
    failure so a transient backend issue degrades to "no fallback" (a graceful no-op) rather than a
    crash — the driver treats a None / unavailable fallback as a no-op chokepoint."""
    try:
        from agent.tools.openevolve_bridge import OpenEvolveBackend

        if not OpenEvolveBackend.available():
            return None
        from agent.tools.claude_cli import ClaudeConfig

        spec = _spec(profile, Role.decomposer)
        cfg_kwargs: dict = {}
        if spec.model is not None:
            cfg_kwargs["model"] = spec.model
        if spec.timeout_s is not None:
            cfg_kwargs["timeout_s"] = spec.timeout_s
        # Thread the profile-addressable breadth/depth ensemble (model names + weights) into the backend.
        ens = profile.ensemble
        return OpenEvolveBackend(toolkit, ClaudeConfig(**cfg_kwargs),
                                 generations=profile.stages.evolve_fallback,
                                 breadth_model=ens.breadth_model, depth_model=ens.depth_model,
                                 breadth_weight=ens.breadth_weight, depth_weight=ens.depth_weight)
    except Exception:
        return None


def build_and_run(profile: RunProfile, goal: str, *, toolkit: Optional[Toolkit] = None,
                  trace: Optional[RunTrace] = None) -> DagResult:
    """Validate + build (via :func:`build_driver`) then ``.run(goal)``. The one-call entry point.

    Forwards the optional ``toolkit``/``trace`` to :func:`build_driver` (previously dropped) so a caller
    can share one toolkit/trace across a build_and_run just as with build_driver."""
    driver = build_driver(profile, toolkit=toolkit, trace=trace)
    return driver.run(goal)


__all__ = ["build_driver", "build_and_run"]
