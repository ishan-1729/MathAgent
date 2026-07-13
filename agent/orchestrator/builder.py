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
* The mapping is mechanical and total: driver-stage knobs map to one ``DagDriver`` ctor param;
  goal-dependent ``evolve``/``evolve_witness`` are consumed by ``build_and_run`` (and rejected by a
  bare ``build_driver`` unless a run front-end already consumed them). ``elementarity_policy`` is the
  single source of truth for the three Lean-wiring decisions (enforce / terminal / per-node).
* The DEFAULT profile enables deterministic enforcement plus the configured decomposition and
  proof-review roles, with no terminal/per-node Lean gates (its ``lean.per_node`` flag is false).

SAFETY: nothing here exec/eval/imports model output; it only constructs objects + (when Lean is
requested) warms ONE shared LeanServer. The deterministic gate stays authoritative downstream.
"""
from __future__ import annotations

from typing import Optional

from agent.gates.toolkit import Toolkit, load_toolkit, toolkit_policy_sha256
from agent.orchestrator.dag_driver import DagDriver, DagResult
from agent.orchestrator.elementarity_policy import policy_for
from agent.orchestrator.run_profile import Mode, ProviderKey, Role, RunProfile
from agent.orchestrator.state import Budget
from agent.orchestrator.supervisor import SupervisorError, validate_profile
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

    max_llm_calls / max_replan_depth / max_node_verify_calls map directly; the
    DagDriver's max_depth / max_decomp_attempts / ralph_episodes are SEPARATE ctor params (set in
    build_driver), so only the spend caps live on the Budget here."""
    b = profile.budgets
    return Budget(
        max_llm_calls=b.max_llm_calls,
        max_replan_depth=b.max_replan_depth,
        max_node_verify_calls=b.max_node_verify_calls,
    )


def _warm_lean_server():
    """Start and return ONE shared warm LeanServer, failing closed if startup is impossible.

    Mirrors scripts/prove.py: a single persistent server (Mathlib + #audit loaded once) is threaded
    into BOTH per-node gates so every compile reuses the warm base environment.  Supervision is a
    preflight snapshot, not a startup guarantee: the REPL can disappear or fail initialization between
    validation and construction.  Because ``lean.server=True`` is an explicit required capability,
    silently returning ``None`` here would change the requested profile to one-shot compilation.
    """
    try:
        from agent.gates.lean_server import LeanServer

        return LeanServer().start()
    except Exception as exc:
        raise SupervisorError(
            "lean.server=True was validated, but the persistent Lean REPL could not be started; "
            "the requested warm-server profile will not be silently downgraded. Rebuild the REPL "
            "with `lake build repl` and retry."
        ) from exc


_USE_PROFILE = object()


def _make_terminal_gate(profile: RunProfile, toolkit: Toolkit, server, *,
                        formalizer=None, faithfulness_checker=_USE_PROFILE, retriever=None,
                        repair_iters: int = 0):
    """Build the terminal Layer-4 gate (formalize -> Lean audit -> faithfulness) from the resolved
    formalizer + faithfulness roles. Returns the gate callable, or None if it cannot be built."""
    from agent.orchestrator.formalize_bridge import make_terminal_gate
    from agent.orchestrator.registry import Deps, resolve

    if formalizer is None:
        formalizer = resolve(Role.formalizer, _spec(profile, Role.formalizer),
                             Deps(toolkit=toolkit))
    faith = faithfulness_checker
    if faith is _USE_PROFILE:
        faith = resolve(Role.faithfulness, _spec(profile, Role.faithfulness),
                        Deps(toolkit=toolkit))
    return make_terminal_gate(formalizer, toolkit, faithfulness_checker=faith, server=server,
                              retriever=retriever, repair_iters=repair_iters)


def _make_node_gates(profile: RunProfile, toolkit: Toolkit, server, *, formalizer=None,
                     retriever=None, repair_iters: int = 0):
    """Build the (node_verifier, sketch_verifier) per-node Lean gates from the resolved formalizer role.
    The SAME warm ``server`` is threaded into both so per-node compiles share one Mathlib environment."""
    from agent.orchestrator.formalize_bridge import make_node_gate, make_sketch_gate
    from agent.orchestrator.registry import Deps, resolve

    if formalizer is None:
        formalizer = resolve(Role.formalizer, _spec(profile, Role.formalizer),
                             Deps(toolkit=toolkit))
    node_verifier = make_node_gate(formalizer, toolkit, server=server, retriever=retriever,
                                   repair_iters=repair_iters)
    sketch_verifier = make_sketch_gate(formalizer, toolkit, server=server, retriever=retriever,
                                       repair_iters=repair_iters)
    return node_verifier, sketch_verifier


def build_driver(profile: RunProfile, *, toolkit: Optional[Toolkit] = None,
                 trace: Optional[RunTrace] = None, faithfulness_checker=_USE_PROFILE,
                 retriever=None, repair_iters: int = 0,
                 _presearch_consumed: bool = False) -> DagDriver:
    """Validate ``profile`` then construct the EXISTING DagDriver wired per the profile.

    Order (the contract): validate_profile FIRST -> resolve each role via the registry -> map
    StageProfile to the DagDriver's injected params -> apply elementarity_policy (enforce_elementarity
    + terminal/per-node Lean gates, warming one shared Lean server when Lean is on) -> construct.

    StageProfile -> injected param mapping (illegal states made absent, not flagged):
      * decompose=False -> decomposer=None  (direct-only: the driver never asks for a blueprint)
      * review=False    -> reviewer=None and judge=None
      * population==0   -> comparator=None (else the comparator role + population_k=population)
      * refine=False    -> refiner=None
      * evolve_fallback==0 -> evolve_fallback=None (else the resolved fallback decomposer)
      * h0_consistency  -> the driver flag verbatim

    ``stages.evolve``/``evolve_witness`` require a goal and are therefore pre-driver controls:
    :func:`build_and_run` consumes them. A bare call with either enabled fails loudly unless an
    orchestration front-end explicitly marks them consumed.

    Raises SupervisorError (before any component is constructed) for an inadmissible profile."""
    # 1. FAIL-CLOSED supervisor FIRST — before resolving a single role or touching the toolkit/Lean.
    validate_profile(profile)
    if ((profile.stages.evolve > 0 or profile.stages.evolve_witness > 0)
            and not _presearch_consumed):
        raise SupervisorError(
            "stages.evolve/evolve_witness are goal-dependent pre-search controls and cannot be "
            "silently ignored by build_driver(). Use build_and_run(profile, goal), or have a run "
            "front-end consume them before requesting the driver."
        )
    if (isinstance(repair_iters, bool) or not isinstance(repair_iters, int)
            or not 0 <= repair_iters <= 1000):
        raise ValueError("repair_iters must be an integer in [0, 1000]")

    # Lazy registry import (see module note): only after the supervisor admits the profile do we pull
    # the registry (and thus the live tool wrappers it imports). Keeps package import side-effect free.
    from agent.orchestrator.registry import Deps, resolve, resolution_of

    toolkit = toolkit or load_toolkit()
    trace = trace or RunTrace(profile.name)
    budget = _budget_from(profile)
    stages = profile.stages

    # One shared Deps so every role gets the SAME toolkit; refiner knobs feed the refiner factory.
    deps = Deps(toolkit=toolkit, budget=budget, trace=trace,
                n_judges=stages.judges, max_passes=2, k_stop=2, margin=1,
                seed=profile.seed)

    resolved_roles: dict[str, dict[str, object]] = {}

    def resolve_role(role: Role):
        component = resolve(role, _spec(profile, role), deps)
        metadata = resolution_of(component)
        if metadata is None:  # registry resolution is required to be provenance-bearing
            raise SupervisorError(f"role '{role.value}' resolved without provider provenance")
        resolved_roles[role.value] = metadata
        return component

    # 2. Resolve roles via the registry, mapping the StageProfile knobs to inject/omit each component.
    prover = resolve_role(Role.prover)
    dag_mode = profile.mode is Mode.dag
    decomposer = (resolve_role(Role.decomposer)
                  if dag_mode and stages.decompose else None)
    reviewer = (resolve_role(Role.reviewer)
                if dag_mode and stages.decompose and stages.review else None)
    # Direct proofs and leaf proofs use an independent Layer-2 judge panel.  The role existed in the
    # profile schema but was previously never resolved, silently disabling adversarial review.
    judge = (resolve_role(Role.judge)
             if stages.review else None)
    # population: a comparator only when population>0 (the population_k breadth knob).
    comparator = (resolve_role(Role.comparator)
                  if dag_mode and stages.population > 0 else None)
    population_k = stages.population if dag_mode and stages.population > 0 else 0
    # refiner: the AutoReason incumbent tournament, only when refine is on.
    refiner = (resolve_role(Role.refiner)
               if dag_mode and stages.refine else None)
    # evolve fallback: a last-resort decomposer on stuck nodes, only when evolve_fallback>0.
    evolve_fallback = (_resolve_evolve_fallback(profile, toolkit, retriever=retriever)
                       if dag_mode and stages.evolve_fallback > 0 else None)

    # 3. elementarity_policy: the SINGLE source of truth for the three Lean-wiring decisions.
    policy = policy_for(profile.elementarity, lean_per_node=profile.lean.per_node)

    # Resolve certification roles exactly once. A single formalizer instance is shared by the
    # terminal gate and both per-node gates, so availability fallback cannot drift within one run.
    formalizer = (resolve_role(Role.formalizer)
                  if policy.attach_terminal_gate or policy.attach_per_node_lean else None)
    resolved_faithfulness = faithfulness_checker
    if policy.attach_terminal_gate:
        if resolved_faithfulness is _USE_PROFILE:
            resolved_faithfulness = resolve_role(Role.faithfulness)
        else:
            metadata = resolution_of(resolved_faithfulness)
            if metadata is not None:
                resolved_roles[Role.faithfulness.value] = metadata

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
        if server is None:
            # Belt-and-braces for injected/custom warmers: the production helper raises, but an
            # implementation returning None must not silently change the validated server contract.
            raise SupervisorError(
                "lean.server=True requires a warm persistent Lean REPL, but server startup returned "
                "no server; refusing to downgrade to one-shot Lean."
            )
    try:
        if policy.attach_terminal_gate:
            terminal_gate = _make_terminal_gate(
                profile, toolkit, server, formalizer=formalizer,
                faithfulness_checker=resolved_faithfulness,
                retriever=retriever, repair_iters=repair_iters)
        if policy.attach_per_node_lean:
            node_verifier, sketch_verifier = _make_node_gates(
                profile, toolkit, server, formalizer=formalizer,
                retriever=retriever, repair_iters=repair_iters)

        # 4. Construct the EXISTING DagDriver. The default soft profile enforces the informal gates and
        #    proof judge while leaving terminal/per-node Lean and the persistent server off.
        driver = DagDriver(
            prover,
            decomposer=decomposer,
            reviewer=reviewer,
            judges=[judge] if judge is not None else None,
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
        driver.resolved_roles = dict(resolved_roles)
        driver.policy_digest = toolkit_policy_sha256(toolkit)
        trace.emit("role_resolution", roles=driver.resolved_roles)
        trace.emit("policy_resolution", toolkit_policy_sha256=driver.policy_digest)
        return driver
    except BaseException:
        # Until DagDriver is successfully constructed there is no owner for a warmed REPL.  Close it
        # on every construction/cancellation failure and preserve the original exception even if close
        # itself has a cleanup error.
        if server is not None:
            try:
                server.close()
            except BaseException:
                pass
        raise


def _resolve_evolve_fallback(profile: RunProfile, toolkit: Toolkit, *, retriever=None):
    """Build the OpenEvolve fallback decomposer for ``stages.evolve_fallback`` generations.

    The supervisor (S3) has already verified ``openevolve`` is installed when evolve_fallback>0.  The
    builder checks again at construction time and fails closed if that explicitly requested stage can
    no longer be built; silently returning ``None`` would mislabel the executed profile.  The ensemble
    owns its model names/weights.  Its Claude transport uses the backend default timeout rather than
    borrowing provider-specific settings from the independent ordinary decomposer role.
    """
    try:
        from agent.tools.openevolve_bridge import OpenEvolveBackend

        if not OpenEvolveBackend.available():
            raise SupervisorError(
                "stages.evolve_fallback is enabled, but OpenEvolve became unavailable during builder "
                "construction. Install mathagent[evolve] or disable the stage."
            )
        from agent.tools.claude_cli import ClaudeConfig

        if profile.ensemble.provider is not ProviderKey.claude:
            raise SupervisorError(
                "stages.evolve_fallback currently requires ensemble.provider='claude'."
            )
        # Thread the profile-addressable breadth/depth ensemble (model names + weights) into the backend.
        ens = profile.ensemble
        return OpenEvolveBackend(toolkit, ClaudeConfig(timeout_s=ens.timeout_s),
                                 generations=profile.stages.evolve_fallback,
                                 breadth_model=ens.breadth_model, depth_model=ens.depth_model,
                                 breadth_weight=ens.breadth_weight, depth_weight=ens.depth_weight,
                                 retriever=retriever)
    except SupervisorError:
        raise
    except Exception as exc:
        raise SupervisorError(
            "stages.evolve_fallback is enabled, but its OpenEvolve backend could not be constructed; "
            "the requested stage will not be silently disabled."
        ) from exc


def build_and_run(profile: RunProfile, goal: str, *, context: Optional[str] = None,
                  toolkit: Optional[Toolkit] = None,
                  trace: Optional[RunTrace] = None,
                  faithfulness_checker=_USE_PROFILE, retriever=None,
                  repair_iters: int = 0) -> DagResult:
    """Validate + build (via :func:`build_driver`) then ``.run(goal)``. The one-call entry point.

    Forwards the optional ``toolkit``/``trace`` to :func:`build_driver` (previously dropped) so a caller
    can share one toolkit/trace across a build_and_run just as with build_driver.

    ``context`` (Fix 2) is optional per-RUN seed context — a prover-facing clause (e.g. a problem's
    per-problem citable-inputs whitelist) forwarded to ``DagDriver.run(goal, context=...)``. It is
    threaded as a standing lesson into every RalphLoop invocation and the decomposer feedback; it NEVER
    changes goal identity (the goal string handed here stays the PURE statement). The param is
    keyword-only with a None default, so every existing positional caller (e.g. ``builder(profile,
    goal)`` in ablate.py / tests) is unchanged."""
    # Goal-dependent evolutionary pre-search cannot run inside build_driver (which intentionally has
    # no goal). Consume it here before construction; direct build_driver callers receive an actionable
    # rejection instead of a silently incomplete execution.
    validate_profile(profile)
    toolkit = toolkit or load_toolkit()
    trace = trace or RunTrace(profile.name)
    seed_ledger = _run_profile_presearch(
        profile, goal, toolkit=toolkit, trace=trace, retriever=retriever)
    driver = build_driver(profile, toolkit=toolkit, trace=trace,
                          faithfulness_checker=faithfulness_checker,
                          retriever=retriever, repair_iters=repair_iters,
                          _presearch_consumed=True)
    if seed_ledger is not None:
        driver.prover = _SeededProver(seed_ledger, driver.prover)
    # ONE-SHOT ownership: build_and_run warmed (via build_driver) exactly one LeanServer and owns it for
    # this single run, so it must close it — otherwise every build_and_run leaks an orphaned repl.exe
    # holding Mathlib. close() runs on BOTH success and a raising run() (try/finally) and never raises;
    # the multi-goal build_driver path keeps its warm server (it does not go through here).
    try:
        return driver.run(goal, context=context)
    finally:
        driver.close()


class _SeededProver:
    """Offer a supervised pre-search candidate once, then delegate all retries normally."""

    def __init__(self, seed: str, delegate):
        self._seed = seed
        self._delegate = delegate
        self._used = False

    def prove(self, problem: str, feedback=None) -> str:
        if not self._used:
            self._used = True
            return self._seed
        return self._delegate.prove(problem, feedback=feedback)


def _run_profile_presearch(profile: RunProfile, goal: str, *, toolkit: Toolkit,
                           trace: RunTrace, retriever=None) -> Optional[str]:
    """Consume first-class profile evolution for :func:`build_and_run`.

    Search fitness never decides proof success: a goal-bound champion is only the first candidate fed
    into the ordinary deterministic/judge/DAG/Lean pipeline. Witness evolution is auxiliary and is
    recorded on the trace; it cannot seed or prove the theorem.
    """
    stages = profile.stages
    if not (stages.evolve or stages.evolve_witness):
        return None

    from agent.tools.claude_cli import ClaudeConfig
    from agent.tools.openevolve_bridge import (
        OpenEvolveBackend, evolve_prove, evolve_witnesses, score_witness_spec,
    )

    if not OpenEvolveBackend.available():
        raise SupervisorError(
            "an evolutionary stage was validated but OpenEvolve became unavailable before pre-search; "
            "install mathagent[evolve] and retry."
        )
    ens = profile.ensemble
    cfg = ClaudeConfig(timeout_s=ens.timeout_s)
    seed: Optional[str] = None
    if stages.evolve:
        champion = evolve_prove(
            goal, toolkit, iterations=stages.evolve, retriever=retriever, claude_cfg=cfg,
            breadth_model=ens.breadth_model, depth_model=ens.depth_model,
            breadth_weight=ens.breadth_weight, depth_weight=ens.depth_weight,
        )
        trace.emit(
            "evolve_presearch", goal=goal[:80], iterations=stages.evolve,
            fitness=float(champion.fitness), goal_bound=bool(champion.goal_bound),
            passed=bool(champion.passed),
        )
        if champion.goal_bound:
            seed = champion.ledger
    if stages.evolve_witness:
        spec, fitness, _metrics = evolve_witnesses(
            goal, iterations=stages.evolve_witness, claude_cfg=cfg,
            breadth_model=ens.breadth_model, depth_model=ens.depth_model,
            breadth_weight=ens.breadth_weight, depth_weight=ens.depth_weight,
        )
        confirmed = score_witness_spec(spec)["combined_score"] >= 1.0
        trace.emit(
            "evolve_witness", goal=goal[:80], iterations=stages.evolve_witness,
            fitness=float(fitness), confirmed=bool(confirmed),
        )
    return seed


__all__ = ["build_driver", "build_and_run"]
