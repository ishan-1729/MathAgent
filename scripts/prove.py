"""CLI: drive the supervised MathAgent proof harness.

Examples:
    # Full profile-driven harness (the shipped default profile uses Claude roles):
    python scripts/prove.py --profile profiles/default.yaml \
        "For every integer n, n^2 is congruent to 0 or 1 modulo 4."

    # Legacy no-profile harness (Codex at xHigh):
    python scripts/prove.py "For every integer n, n^2 is congruent to 0 or 1 modulo 4."

    # Quick single-shot direct proof (one Ralph loop, no decomposition), faster effort:
    python scripts/prove.py --direct --effort low "For all integers n, n + 0 = n."

The deterministic and Lean gates retain authority; model providers only generate or softly review.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.gates.toolkit import load_toolkit, toolkit_policy_sha256
from agent.gates.ledger import parse_ledger, LedgerError
from agent.orchestrator import Budget, RunTrace, RalphLoop, DagDriver
from agent.orchestrator.dag import goal_hash
from agent.orchestrator.reporting import (
    ReportStatus, report_status, result_certification_state, result_has_candidate,
)
from agent.orchestrator.run_profile import (
    BudgetProfile, ElementarityLevel, EnsembleProfile, LeanProfile, Mode, ProviderKey, RoleSpec,
    RolesProfile, RunProfile, StageProfile,
)
from agent.tools.codex_prover import CodexConfig


def _direct_result_ledger(result: object, goal: str) -> Optional[str]:
    """Extract the raw winning ledger from either direct execution result type.

    Legacy ``--direct`` returns a ``RalphResult`` with a ``ledger`` attribute.  A profile whose
    ``mode`` is ``direct`` still runs through ``DagDriver`` and returns ``DagResult``; its raw ledger
    lives on the root DAG node.  Treating both as the former type caused every successful
    profile-driven direct run to crash after proving the goal.
    """
    direct = getattr(result, "ledger", None)
    if isinstance(direct, str) and direct.strip():
        return direct
    dag = getattr(result, "dag", None)
    nodes = getattr(dag, "nodes", None)
    if not isinstance(nodes, dict):
        return None
    node = nodes.get(goal_hash(goal))
    proof = getattr(node, "proof", None)
    if (getattr(node, "proof_kind", None) == "direct"
            and isinstance(proof, str) and proof.strip()):
        return proof
    return None


# --------------------------------------------------------------------------------------------------
# profile_from_args (W6): the THIN, total argparse -> RunProfile mapping. Every structural CLI flag
# maps to exactly one profile field, so the builder (build_driver) becomes the single DagDriver
# construction site. This CLI is the LEGACY Codex prover; with NO --profile it keeps Codex roles so the
# legacy provider choices remain Codex. The shipped profiles/default.yaml uses Claude (the maintainer
# default); pass --profile to swap providers/stages wholesale, and individual flags then OVERRIDE the
# loaded profile's structural fields (so e.g. `--profile codex.yaml --population 4` works).
# --------------------------------------------------------------------------------------------------
def _codex_roles(args) -> RolesProfile:
    """The legacy Codex role table for prove.py (every role -> codex, honoring --model/--effort).

    prove.py historically drove Codex for EVERY role, so the back-compat profile pins provider=codex
    with the CLI's --model/--effort/--timeout. The formalizer honors --formalizer-model (codex|claude)."""
    codex = RoleSpec(provider=ProviderKey.codex, model=args.model,
                     effort=args.effort, timeout_s=args.timeout)
    formalizer = (RoleSpec(provider=ProviderKey.claude, model="opus", timeout_s=args.timeout)
                  if getattr(args, "formalizer_model", "codex") == "claude" else codex)
    return RolesProfile(prover=codex, decomposer=codex, reviewer=codex, comparator=codex,
                        judge=codex, formalizer=formalizer, faithfulness=codex, refiner=codex)


def _elementarity_from_args(args) -> ElementarityLevel:
    """Map the certification flags to the elementarity level (the single enforcement knob).

    --terminal-gate or --formalize (Layer-4 authoritative gate) => authoritative; otherwise soft (the
    in-engine elementarity refutation still enforces). prove.py has no 'none' flag — solution-only
    runs go through profiles/solution-only.yaml via --profile."""
    return (ElementarityLevel.authoritative
            if (getattr(args, "terminal_gate", False) or getattr(args, "formalize", False))
            else ElementarityLevel.soft)


def _certifying(args, profile: "RunProfile") -> bool:
    """Whether the terminal Layer-4 gate ACTUALLY runs for this invocation: the explicit
    --terminal-gate/--formalize flags, OR an effective profile with elementarity=authoritative (whose
    builder attaches the terminal gate). The status ladder and the exit code key on this, so it must
    reflect the `--profile profiles/authoritative.yaml` path too, not only the flags — otherwise a
    proven-but-non-elementary authoritative run mislabels as `soft_proven` and exits 0."""
    return bool(args.terminal_gate or args.formalize
                or profile.elementarity is ElementarityLevel.authoritative)


class _SeededProver:
    """Offer one externally generated candidate, then delegate every repair/retry.

    An evolved champion is search output, not an admission decision. Feeding it through the ordinary
    prover protocol makes the existing Ralph/DAG deterministic gate, goal binding, proof judges,
    refinement routing, per-node Lean, terminal audit, and shared budget apply without a parallel
    short-circuit path.
    """

    def __init__(self, seed: str, delegate):
        self._seed = seed
        self._delegate = delegate
        self._used = False

    def prove(self, problem: str, feedback=None) -> str:
        if not self._used:
            self._used = True
            return self._seed
        return self._delegate.prove(problem, feedback=feedback)


# The structural flags profile_from_args reads.  The parser records which option strings appeared;
# comparing parsed values with defaults is insufficient because an explicit ``--population 0`` or
# ``--no-server`` must be able to override a non-default value loaded from a profile.
_FLAG_DEFAULTS = {
    "direct": False, "formalize": False, "terminal_gate": False,
    "lean_per_node": False, "lean_strict": False,
    "server": False, "refine": False, "population": 0, "evolve": 0,
    "evolve_witness": 0, "evolve_fallback": 0, "judges": 1,
    "budget": 60, "max_depth": 3, "max_decomp": 2, "max_replan": 2, "episodes": 3,
}
class _TrackingArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that records option destinations explicitly supplied by the user."""

    def parse_known_args(self, args=None, namespace=None):
        raw = list(sys.argv[1:] if args is None else args)
        parsed, extras = super().parse_known_args(raw, namespace)
        explicit: set[str] = set()
        for token in raw:
            if token == "--":
                break
            option = token.split("=", 1)[0]
            action = self._option_string_actions.get(option)
            if action is not None and action.dest is not argparse.SUPPRESS:
                explicit.add(action.dest)
        parsed._explicit_flags = explicit
        return parsed, extras


def _explicitly_set(args) -> set[str]:
    """Structural flags explicitly present on the command line.

    ``build_arg_parser`` attaches ``_explicit_flags``.  The value-comparison fallback keeps direct
    unit-test Namespaces and third-party callers compatible without weakening the real CLI path.
    """
    recorded = getattr(args, "_explicit_flags", None)
    if recorded is not None:
        return set(recorded) & set(_FLAG_DEFAULTS)
    return {k for k, default in _FLAG_DEFAULTS.items()
            if getattr(args, k, default) != default}


def profile_from_args(args, base: "RunProfile | None" = None) -> RunProfile:
    """Map the parsed CLI args to a RunProfile (the one control lever the builder consumes).

    With NO ``base`` this is the legacy Codex profile (Codex roles, every flag mapped), so a bare
    `prove.py GOAL` builds the legacy Codex provider wiring plus the current safety gates. With a
    ``base`` (from --profile) the base is
    the starting point — its roles/identity AND its structural fields are preserved — and ONLY the CLI
    flags the user EXPLICITLY set override it (see :func:`_explicitly_set`); loading authoritative.yaml
    therefore keeps its Lean wiring unless you pass the corresponding flag.

    The mapping is total over the structural flags: --direct->mode; --max-depth/--max-decomp/--episodes/
    --budget/--max-replan->budgets; --population/--refine/--judges/--evolve*->stages;
    --terminal-gate->elementarity
    (+lean.terminal); --lean-per-node/--lean-strict/--server->lean."""
    normalize_lean_flags(args)
    if base is None:
        # Legacy path: every structural flag maps into a complete Codex-backed profile.
        return _profile_all_flags(args, _codex_roles(args), name="prove-cli", seed=0, notes="")
    # --profile path: start from the base, then layer ONLY explicit flag overrides. Generic legacy
    # provider knobs have ambiguous cross-provider semantics (e.g. a Codex effort on a Claude role),
    # so reject them instead of silently ignoring them. ``--formalizer-model`` is intentionally a
    # provider switch and is applied only to the formalizer role.
    recorded = set(getattr(args, "_explicit_flags", ()))
    incompatible = recorded & {"model", "effort", "timeout"}
    if incompatible:
        flags = ", ".join(f"--{name.replace('_', '-')}" for name in sorted(incompatible))
        raise ValueError(
            f"{flags} cannot be combined with --profile because the profile pins per-role provider "
            "settings; edit the profile YAML instead"
        )
    explicit = _explicitly_set(args)
    formalizer_explicit = "formalizer_model" in recorded
    if not explicit and not formalizer_explicit:
        return base
    # Build a full flag-derived profile, then copy across only the explicitly-set fields.
    full = _profile_all_flags(args, base.roles, name=base.name, seed=base.seed, notes=base.notes)
    update: dict = {}
    if formalizer_explicit:
        current = base.roles.formalizer
        timeout_s = current.timeout_s if current.timeout_s is not None else int(args.timeout)
        if args.formalizer_model == "claude":
            formalizer = RoleSpec(
                provider=ProviderKey.claude, model="opus", timeout_s=timeout_s)
        else:
            formalizer = RoleSpec(
                provider=ProviderKey.codex, model="gpt-5.5", effort="xhigh",
                timeout_s=timeout_s)
        update["roles"] = base.roles.model_copy(update={"formalizer": formalizer})
    if {"direct"} & explicit:
        update["mode"] = full.mode
    if {"terminal_gate", "formalize"} & explicit:
        update["elementarity"] = full.elementarity
    stage_keys = {
        "direct": ("decompose",),
        "population": ("population",),
        "evolve": ("evolve",),
        "evolve_witness": ("evolve_witness",),
        "evolve_fallback": ("evolve_fallback",),
        "refine": ("refine",),
        "judges": ("judges",),
    }
    stage_update = {f: getattr(full.stages, f)
                    for flag, fields in stage_keys.items() if flag in explicit for f in fields}
    if stage_update:
        update["stages"] = base.stages.model_copy(update=stage_update)
    budget_keys = {"budget": "max_llm_calls", "max_depth": "max_depth",
                   "max_decomp": "max_decomp_attempts",
                   "max_replan": "max_replan_depth", "episodes": "episodes"}
    budget_update = {field: getattr(full.budgets, field)
                     for flag, field in budget_keys.items() if flag in explicit}
    if budget_update:
        update["budgets"] = base.budgets.model_copy(update=budget_update)
    lean_keys = {"lean_per_node": "per_node", "terminal_gate": "terminal",
                 "formalize": "terminal",
                 "lean_strict": "strict", "server": "server"}
    lean_update = {field: getattr(full.lean, field)
                   for flag, field in lean_keys.items() if flag in explicit}
    if lean_update:
        update["lean"] = base.lean.model_copy(update=lean_update)
    return base.model_copy(update=update)


def _profile_all_flags(args, roles: RolesProfile, *, name: str, seed: int, notes: str) -> RunProfile:
    """Build a RunProfile mapping EVERY structural flag (the total argparse->profile mapping)."""
    return RunProfile(
        name=name,
        seed=seed,
        notes=notes,
        mode=Mode.direct if args.direct else Mode.dag,
        elementarity=_elementarity_from_args(args),
        roles=roles,
        stages=StageProfile(
            decompose=not args.direct,
            # Review is orthogonal to decomposition: in direct mode it wires the independent proof
            # judge; in DAG mode it additionally wires the decomposition reviewer.
            review=True,
            population=int(args.population),
            evolve=int(args.evolve),
            evolve_witness=int(args.evolve_witness),
            evolve_fallback=int(args.evolve_fallback),
            refine=bool(args.refine),
            judges=int(args.judges),
            h0_consistency=True,
        ),
        budgets=BudgetProfile(
            max_llm_calls=int(args.budget),
            max_depth=int(args.max_depth),
            max_decomp_attempts=int(args.max_decomp),
            max_replan_depth=int(args.max_replan),
            episodes=int(args.episodes),
        ),
        lean=LeanProfile(
            per_node=bool(args.lean_per_node),
            terminal=bool(getattr(args, "terminal_gate", False)
                          or getattr(args, "formalize", False)),
            strict=bool(args.lean_strict),
            server=bool(args.server),
        ),
        ensemble=EnsembleProfile(timeout_s=int(args.timeout)),
    )


def build_lean_node_gates(args, toolkit, cfg, server, retriever=None, *, formalizer=None):
    """Build the PER-NODE Lean verifiers for the DAG driver from the parsed CLI args.

    Returns ``(node_verifier, sketch_verifier)``:
      * ``(None, None)`` when ``--lean-per-node`` is NOT set — the DAG driver gets no per-node Lean
        authority and stays on the soft verification path.
      * ``(make_node_gate(...), make_sketch_gate(...))`` when ``--lean-per-node`` IS set — a per-LEAF
        gate (every directly-proven leaf is Lean-verified) AND a per-AND-node composition gate (every
        decomposition sketch is Lean-checked), so a leaf reaching ``elementary_verified`` is promoted to
        the first-class LEAN_VERIFIED state. The same warm ``server`` is threaded into BOTH gates so
        every per-node compile reuses one Mathlib environment instead of reloading it per node.

    The formalizer is CodexFormalizer by default, or ClaudeFormalizer when ``--formalizer-model=claude``.
    Both gates defer per-leaf/composition faithfulness to the root terminal gate (see make_node_gate /
    make_sketch_gate), so this only wires the elementary-audit authority per node."""
    if not getattr(args, "lean_per_node", False):
        return None, None
    from agent.orchestrator.formalize_bridge import make_node_gate, make_sketch_gate
    if formalizer is None:
        # Compatibility path for direct unit callers. Production build_dag_driver supplies the
        # registry-resolved formalizer so provider fallback and provenance have one selection point.
        from agent.tools.formalizer import CodexFormalizer, ClaudeFormalizer
        if args.formalizer_model == "claude":
            from agent.tools.claude_cli import ClaudeConfig
            formalizer = ClaudeFormalizer(toolkit, ClaudeConfig(model="opus", timeout_s=args.timeout))
        else:
            formalizer = CodexFormalizer(toolkit, cfg)
    node_verifier = make_node_gate(formalizer, toolkit, server=server, retriever=retriever,
                                   repair_iters=args.repair, timeout_s=args.timeout)
    sketch_verifier = make_sketch_gate(formalizer, toolkit, server=server, retriever=retriever,
                                       repair_iters=args.repair, timeout_s=args.timeout)
    return node_verifier, sketch_verifier


def build_dag_driver(args, *, profile, prover, toolkit, cfg, budget, trace, server,
                     terminal_gate, comparator, refiner, evolve_fallback):
    """Construct the DAG-mode :class:`DagDriver` from the parsed CLI args + already-built dependencies.

    This is the SINGLE argparse->DagDriver construction site, factored out so the wiring is testable end
    to end. It builds the per-node Lean verifiers from ``--lean-per-node`` (via ``build_lean_node_gates``)
    and threads them — together with ``lean_strict`` (``--lean-strict``) and the warm ``lean_server`` —
    into the driver. WITHOUT ``--lean-per-node`` the driver is constructed with
    ``node_verifier=None``/``sketch_verifier=None``/``lean_strict=False``/``lean_server=None``.

    The decomposer and reviewer are RESOLVED via the registry from the SAME effective ``profile`` the
    supervisor validated (not hardwired), so the legacy path runs the provider the profile pins. For the
    legacy Codex profile (``_codex_roles`` pins provider=codex for every role) the resolved components
    are CodexDecomposer/CodexReviewer with the CLI's model/effort."""
    from agent.orchestrator.registry import Deps, resolve, resolution_of
    from agent.orchestrator.run_profile import Role
    deps = Deps(toolkit=toolkit, budget=budget, trace=trace,
                n_judges=profile.stages.judges, seed=profile.seed)
    decomposer = resolve(Role.decomposer, profile.roles.decomposer, deps)
    reviewer = (resolve(Role.reviewer, profile.roles.reviewer, deps)
                if profile.stages.review else None)
    judge = (resolve(Role.judge, profile.roles.judge, deps)
             if profile.stages.review else None)
    formalizer = (resolve(Role.formalizer, profile.roles.formalizer, deps)
                  if getattr(args, "lean_per_node", False) else None)
    node_verifier, sketch_verifier = build_lean_node_gates(
        args, toolkit, cfg, server, retriever=getattr(args, "_retriever", None),
        formalizer=formalizer)
    driver = DagDriver(
        prover,
        decomposer=decomposer,
        reviewer=reviewer,
        judges=([judge] if judge is not None else None),
        toolkit=toolkit, budget=budget, trace=trace,
        max_depth=profile.budgets.max_depth,
        max_decomp_attempts=profile.budgets.max_decomp_attempts,
        ralph_episodes=profile.budgets.episodes, terminal_gate=terminal_gate,
        comparator=comparator, population_k=profile.stages.population, refiner=refiner,
        evolve_fallback=evolve_fallback,
        node_verifier=node_verifier, sketch_verifier=sketch_verifier,
        lean_strict=bool(args.lean_strict),
        lean_server=(server if getattr(args, "lean_per_node", False) else None),
    )
    components = {
        Role.prover: prover,
        Role.decomposer: decomposer,
        Role.reviewer: reviewer,
        Role.judge: judge,
        Role.comparator: comparator,
        Role.refiner: refiner,
        Role.formalizer: formalizer,
    }
    driver.resolved_roles = {
        role.value: metadata
        for role, component in components.items()
        if (metadata := resolution_of(component)) is not None
    }
    driver.policy_digest = toolkit_policy_sha256(toolkit)
    trace.emit("role_resolution", roles=driver.resolved_roles)
    trace.emit("policy_resolution", toolkit_policy_sha256=driver.policy_digest)
    return driver


def build_arg_parser() -> argparse.ArgumentParser:
    """The REAL CLI parser (the single source of the flag definitions). Factored out of ``main`` so the
    flag wiring (e.g. ``--lean-per-node`` -> DagDriver) can be driven through the ACTUAL argparse path in
    tests, not a hand-rolled stub."""
    ap = _TrackingArgumentParser(
        description="Prove a number-theory goal with the configured MathAgent harness.",
        allow_abbrev=False,
    )
    ap.add_argument("goal", nargs="?", help="the goal/theorem statement to prove")
    ap.add_argument("--profile", type=Path, metavar="PATH",
                    help="load a RunProfile YAML as the base wiring (roles/stages/budget/lean); the "
                         "structural CLI flags then OVERRIDE its fields. The shipped profiles/ presets "
                         "(default.yaml=Claude, codex.yaml=legacy, solution-only.yaml, authoritative.yaml) "
                         "are good starting points.")
    ap.add_argument("--dump-profile", action="store_true",
                    help="print the EFFECTIVE RunProfile (after merging --profile + flags) as YAML and "
                         "exit WITHOUT running — for inspecting/diffing the exact wiring a run would use.")
    ap.add_argument("--model", default="gpt-5.5",
                    help="legacy no-profile Codex model (with --profile, edit the role in YAML)")
    ap.add_argument("--effort", default="xhigh", choices=["low", "medium", "high", "xhigh"],
                    help="legacy no-profile Codex effort (with --profile, edit the role in YAML)")
    ap.add_argument("--direct", action=argparse.BooleanOptionalAction, default=False,
                    help="single Ralph loop, no DAG decomposition")
    ap.add_argument("--max-depth", type=int, default=3)
    ap.add_argument("--max-decomp", type=int, default=2)
    ap.add_argument("--episodes", type=int, default=3, help="Ralph episodes per node")
    ap.add_argument("--budget", type=int, default=60,
                    help="max orchestrator search/review model calls (formalization and OpenEvolve "
                         "have separately bounded controls)")
    ap.add_argument("--timeout", type=int, default=1200,
                    help="legacy no-profile call timeout (with --profile, edit per-role YAML)")
    ap.add_argument(
        "--out", type=Path,
        help="publish a proof/certificate JSON and trace JSONL using this path prefix (no overwrite)",
    )
    ap.add_argument("--formalize", action="store_true",
                    help="in direct mode, attach the authoritative Layer-4 formalize/audit/faithfulness "
                         "gate (requires Lean and the configured certification roles)")
    ap.add_argument("--terminal-gate", action=argparse.BooleanOptionalAction, default=False,
                    help="(dag mode) run Layer 4 as the terminal authoritative gate on the proven root")
    faith_group = ap.add_mutually_exclusive_group()
    faith_group.add_argument("--faithfulness", action="store_true",
                             help="force the adversarial statement-faithfulness panel on (default ON "
                                  "for certification modes --terminal-gate / --formalize)")
    faith_group.add_argument("--no-faithfulness", action="store_true",
                             help="legacy compatibility flag; rejected in certification modes because "
                                  "an unchecked formalization cannot be authoritative")
    ap.add_argument("--server", action=argparse.BooleanOptionalAction, default=False,
                    help="use the persistent Lean server (loads Mathlib once) for audits")
    ap.add_argument("--lean-per-node", action=argparse.BooleanOptionalAction, default=False,
                    help="(dag) Lean-verify EVERY leaf and AND-composition per node (formalize -> "
                         "compile -> Layer-4 audit on a warm Lean server) so LEAN_VERIFIED becomes "
                         "reachable; implies --server. Errors out if Lean is unavailable.")
    ap.add_argument("--lean-strict", action=argparse.BooleanOptionalAction, default=False,
                    help="(dag) implies --lean-per-node and fails CLOSED on inability to formalize: a "
                         "leaf/sketch that cannot be compiled is NOT accepted as proven (no unverified "
                         "PROVEN is ever minted)")
    ap.add_argument("--formalizer-model", default="codex", choices=["codex", "claude"],
                    help="formalizer provider under --lean-per-node/terminal audit; when explicitly "
                         "combined with --profile, overrides only profile.roles.formalizer")
    ap.add_argument("--repair", type=int, default=0, metavar="N",
                    help="autoformalization Lean-error repair iterations (feed compile errors back)")
    ap.add_argument("--retrieval", action="store_true",
                    help="retrieve real Mathlib lemmas (Loogle + BM25) to guide repair")
    ap.add_argument("--neural", action="store_true",
                    help="add the neural bi-encoder retriever (needs mathagent[neural]); implies --retrieval")
    ap.add_argument("--rerank", action="store_true",
                    help="rerank neural candidates with a cross-encoder (needs mathagent[neural])")
    ap.add_argument("--population", type=int, default=0, metavar="K",
                    help="(dag) generate K candidate decompositions; rank by Codex Elo + Bradley-Terry; "
                         "expand PUCT-best-first")
    ap.add_argument("--judges", type=int, default=1, metavar="N",
                    help="independent judges in the optional refinement tournament")
    ap.add_argument("--refine", action=argparse.BooleanOptionalAction, default=False,
                    help="(dag) refine each directly-proven ledger with the Codex Autoreason tournament")
    ap.add_argument("--max-replan", type=int, default=2, metavar="D",
                    help="global re-plan budget (max_replan_depth)")
    ap.add_argument("--evolve", type=int, default=0, metavar="K",
                    help="FIRST-CLASS evolutionary proving: run the breadth-led OpenEvolve proof-sketch "
                         "search for K iterations to explore a goal-bound, obligation-discharged "
                         "CHAMPION ledger; an admitted champion becomes the first ordinary prover "
                         "candidate and still passes judges/DAG/Lean/budgets (needs mathagent[evolve])")
    ap.add_argument("--evolve-witness", type=int, default=0, metavar="K",
                    help="NUMERIC-GROUNDING evolution: evolve a witness/construction SPEC (residue "
                         "cover / descent measure / solution set) for K iterations, scored ONLY by the "
                         "exact-integer checker (numeric.py, no eval/exec). Reports the best confirmed "
                         "construction (needs mathagent[evolve])")
    ap.add_argument("--evolve-fallback", type=int, default=0, metavar="K",
                    help="(dag) wire the OpenEvolve backend as a FALLBACK decomposer that fires ONLY "
                         "on stuck nodes (K evolve iterations per fire); commits an evolved blueprint "
                         "only if it is goal-bound + obligation-discharged (needs mathagent[evolve])")
    return ap


def normalize_lean_flags(args) -> None:
    """In-place flag normalization (shared by main + tests): ``--lean-strict`` implies
    ``--lean-per-node`` which implies ``--server`` (one warm Lean env reused by every per-node compile),
    so the rest of the pipeline reads a single source of truth."""
    explicit = getattr(args, "_explicit_flags", None)
    if args.lean_strict:
        args.lean_per_node = True
        if explicit is not None and "lean_strict" in explicit:
            explicit.add("lean_per_node")
    if args.lean_per_node:
        args.server = True
        if explicit is not None and "lean_per_node" in explicit:
            explicit.add("server")
    # A cross-encoder only has neural candidates to rerank. Make the advertised implication explicit
    # rather than silently treating a standalone --rerank as a no-op.
    if getattr(args, "rerank", False):
        args.neural = True
        args.retrieval = True


def effective_profile(args) -> RunProfile:
    """The EFFECTIVE RunProfile for this invocation: the --profile base (if any) overridden by the
    structural CLI flags. The single source of truth shared by --dump-profile and the DAG build."""
    base = _load_profile_arg(args.profile) if getattr(args, "profile", None) else None
    return profile_from_args(args, base=base)


def _load_profile_arg(spec: Path) -> RunProfile:
    """Load a filesystem profile or a preset packaged with an installed wheel.

    ``profiles/default.yaml`` remains the documented spelling both in a checkout and after install;
    when that relative path does not exist in the current working directory, resolve it against the
    importable ``profiles`` resource package.  Absolute/missing arbitrary paths never fall through to
    a similarly named preset.
    """
    path = Path(spec)
    if path.is_file():
        return RunProfile.from_yaml(path)
    if path.is_absolute():
        raise FileNotFoundError(f"RunProfile does not exist: {path}")
    parts = list(path.parts)
    # Resource lookup is an explicit namespace, not a basename guess. A typo such as
    # ``default.yaml`` must not silently switch from an intended local file to the bundled preset.
    if not parts or parts[0].lower() != "profiles":
        raise FileNotFoundError(f"RunProfile does not exist: {path}")
    parts = parts[1:]
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise FileNotFoundError(f"invalid packaged RunProfile path: {path}")
    if not Path(parts[-1]).suffix:
        parts[-1] += ".yaml"
    from importlib import resources

    resource = resources.files("profiles").joinpath(*parts)
    if not resource.is_file():
        raise FileNotFoundError(f"RunProfile does not exist locally or as a packaged preset: {path}")
    with resources.as_file(resource) as resource_path:
        return RunProfile.from_yaml(resource_path)


def _role_chain_label(spec: RoleSpec) -> str:
    """Honest provider-chain label; it never claims which availability fallback was selected."""
    primary = f"{spec.provider.value}/{spec.model or '(default)'}"
    if spec.effort is not None:
        primary += f"@{spec.effort}"
    if spec.fallback is not None:
        primary += f" -> {spec.fallback.value}/(provider default)"
    return primary


def _resolution_label(metadata: object) -> str:
    """Compact label for the provider/model that the registry actually selected."""
    if not isinstance(metadata, dict):
        return "unrecorded"
    provider = metadata.get("provider")
    model = metadata.get("model") or "(provider default)"
    if not isinstance(provider, str) or not provider:
        return "unrecorded"
    label = f"{provider}/{model}"
    effort = metadata.get("effort")
    if isinstance(effort, str) and effort:
        label += f"@{effort}"
    if metadata.get("fallback_selected") is True:
        label += " [fallback]"
    return label


def _retriever_chain(retriever: object | None) -> list[str]:
    """Return the effective retriever topology in deterministic depth-first order.

    Retrieval is optional and can degrade at startup (for example when the neural extra or the
    Mathlib corpus is unavailable).  Recording only the requested flags would therefore overstate
    what a run actually used.  Keep the wrapper in the receipt as well as its children so a hybrid
    topology cannot be mistaken for a flat sequence.
    """
    if retriever is None:
        return []
    out = [f"{type(retriever).__module__}.{type(retriever).__qualname__}"]
    children = getattr(retriever, "retrievers", None)
    if isinstance(children, (list, tuple)):
        for child in children:
            out.extend(_retriever_chain(child))
    return out


def _proof_artifact(result: object, *, goal: str, profile: RunProfile,
                    status: ReportStatus, trace: RunTrace,
                    terminal_override: object | None = None,
                    resolved_roles_override: Optional[dict[str, dict[str, object]]] = None,
                    policy_digest_override: Optional[str] = None,
                    execution_controls: Optional[dict[str, object]] = None) -> dict[str, object]:
    """Build one mode-neutral, self-describing proof artifact for ``--out``."""
    from agent.orchestrator.reporting import result_proof_context, result_role_provenance
    from scripts import _benchmark_artifacts as artifacts

    tree = None
    proof_tree = getattr(result, "proof_tree", None)
    if callable(proof_tree):
        tree = proof_tree()
    bundle = None
    dag = getattr(result, "dag", None)
    proof_bundle = getattr(dag, "proof_bundle", None)
    if callable(proof_bundle):
        bundle = proof_bundle(goal)
    ledger = _direct_result_ledger(result, goal)
    terminal = (terminal_override if terminal_override is not None
                else getattr(result, "terminal", None))
    terminal_summary = None
    if terminal is not None:
        summary = getattr(terminal, "summary", None)
        terminal_summary = summary() if callable(summary) else str(terminal)
    resolved_roles = result_role_provenance(result)
    if resolved_roles_override:
        resolved_roles.update(resolved_roles_override)
    proven, audited, authoritative, audit_record = result_certification_state(
        result, terminal_override=terminal_override,
    )
    # Recompute the categorical status from the persisted receipt. ``status`` is retained as a
    # compatibility hint for callers, but it can never promote an incoherent certificate.
    status = report_status(
        proven=proven,
        has_candidate=result_has_candidate(result),
        audited=audited,
        authoritative_elementary=authoritative,
    )
    return {
        "schema_version": 1,
        "run_id": trace.run_id,
        "code_revision": artifacts.cached_code_revision(Path(__file__).resolve().parents[1]),
        "profile_name": profile.name,
        "profile_hash": profile.profile_hash,
        # A hash alone binds configuration but is not self-describing.  Preserve the validated,
        # effective snapshot so an artifact remains interpretable without the originating checkout.
        "effective_profile": profile.model_dump(mode="json"),
        "execution_controls": dict(execution_controls or {}),
        "goal": goal,
        "proven": proven,
        "reporting_status": status.label,
        "resolved_roles": resolved_roles,
        "toolkit_policy_sha256": (
            getattr(result, "policy_digest", None) or policy_digest_override
        ),
        "proof_context_sha256": result_proof_context(result),
        "direct_ledger": ledger,
        "proof_bundle": bundle,
        "proof_tree": tree,
        "terminal_summary": terminal_summary,
        "lean_audit": audit_record,
    }


def _publish_run_outputs(prefix: Path, *, result: object, goal: str, profile: RunProfile,
                         status: ReportStatus, trace: RunTrace,
                         terminal_override: object | None = None,
                         resolved_roles_override: Optional[dict[str, dict[str, object]]] = None,
                         policy_digest_override: Optional[str] = None,
                         execution_controls: Optional[dict[str, object]] = None) -> None:
    """Publish proof+trace as a no-clobber pair; the trace is the pair's commit point."""
    from scripts import _benchmark_artifacts as artifacts

    proof_final = Path(str(prefix) + ".proof.json")
    trace_final = Path(str(prefix) + ".trace.jsonl")
    token = uuid.uuid4().hex
    proof_checkpoint = Path(str(proof_final) + f".{token}.incomplete")
    trace_checkpoint = Path(str(trace_final) + f".{token}.incomplete")
    payload = json.dumps(
        _proof_artifact(
            result, goal=goal, profile=profile, status=status, trace=trace,
            terminal_override=terminal_override,
            resolved_roles_override=resolved_roles_override,
            policy_digest_override=policy_digest_override,
            execution_controls=execution_controls,
        ),
        indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False,
    ) + "\n"
    artifacts.prepare_text_checkpoint(proof_checkpoint, payload)
    try:
        artifacts.prepare_text_checkpoint(trace_checkpoint, trace.to_jsonl() + "\n")
        artifacts.publish_pair(
            summary_checkpoint=proof_checkpoint, summary_final=proof_final,
            data_checkpoint=trace_checkpoint, data_final=trace_final,
        )
    except BaseException:
        # Completed checkpoints remain recoverable. publish_pair rolls back a proof link if the trace
        # commit point could not be created.
        raise
    else:
        artifacts.finish_publication((proof_checkpoint, trace_checkpoint))


def _main(owned_legacy_servers: list[object]) -> int:
    # Windows consoles default to cp1252, which cannot print Unicode math (≡, ², √) that appears in
    # goals/ledgers — reconfigure to UTF-8 so a goal string can never crash a print (mirrors
    # scripts/run_benchmark.py).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_arg_parser().parse_args()
    normalize_lean_flags(args)

    # --dump-profile: print the effective profile (after merging --profile + flags) and exit. Pure data
    # — performs NO model/Lean call, so it works even with no goal and no backend installed.
    if args.dump_profile:
        import yaml as _yaml
        try:
            prof = effective_profile(args)
        except Exception as exc:
            print(f"ERROR: could not load profile: {exc}", file=sys.stderr)
            return 2
        print(_yaml.safe_dump(prof.model_dump(mode="json"), sort_keys=False), end="")
        return 0

    if not args.goal:
        print("ERROR: a goal is required (or use --dump-profile to inspect the profile).",
              file=sys.stderr)
        return 2

    if args.out:
        proof_path = Path(str(args.out) + ".proof.json")
        trace_path = Path(str(args.out) + ".trace.jsonl")
        if any(path.exists() or path.is_symlink() for path in (proof_path, trace_path)):
            print("ERROR: --out refuses to overwrite an existing proof or trace artifact.",
                  file=sys.stderr)
            return 2
        if not proof_path.parent.is_dir():
            print(f"ERROR: --out parent directory does not exist: {proof_path.parent}",
                  file=sys.stderr)
            return 2

    # SUPERVISOR = the SOLE pre-flight chokepoint. Compute the effective RunProfile and validate it
    # FAIL-CLOSED before ANY construction or model/Lean call, so EVERY CLI path (direct / legacy DAG /
    # --profile / evolve) is supervised. The supervisor's per-profile provider guards replace the old
    # blanket 'codex CLI required' check, so a Claude profile no longer depends on Codex being present.
    from agent.orchestrator.supervisor import validate_profile, SupervisorError
    from agent.orchestrator.registry import resolve, resolution_of, Deps
    from agent.orchestrator.run_profile import Role
    try:
        profile = effective_profile(args)
    except Exception as exc:
        print(f"ERROR: could not load profile: {exc}", file=sys.stderr)
        return 2
    if args.formalize and profile.mode is not Mode.direct:
        print("ERROR: --formalize requires direct mode (pass --direct or use a direct profile).",
              file=sys.stderr)
        return 2
    if not 0 <= args.repair <= 1000:
        print("ERROR: --repair must be between 0 and 1000.", file=sys.stderr)
        return 2
    if args.no_faithfulness and _certifying(args, profile):
        print("ERROR: certification requires the adversarial faithfulness panel; "
              "--no-faithfulness cannot be combined with --formalize, --terminal-gate, or an "
              "authoritative profile.", file=sys.stderr)
        return 2
    if args.faithfulness and not _certifying(args, profile):
        print("ERROR: --faithfulness is only meaningful with an authoritative terminal gate; "
              "add --terminal-gate/--formalize or use an authoritative profile.", file=sys.stderr)
        return 2
    try:
        validate_profile(profile)
    except SupervisorError as e:
        print(f"ERROR: profile rejected by supervisor: {e}", file=sys.stderr)
        return 2

    profile_driven_invocation = bool(getattr(args, "profile", None))

    toolkit = load_toolkit()
    cfg = CodexConfig(model=args.model, reasoning_effort=args.effort, timeout_s=args.timeout)
    budget = Budget(max_llm_calls=profile.budgets.max_llm_calls,
                    max_replan_depth=profile.budgets.max_replan_depth)
    # Provider-neutral, collision-resistant provenance. The declared profile content is bound into
    # every run id; a random suffix distinguishes repeated runs of the exact same configuration.
    trace = RunTrace(
        f"mathagent-prove-{profile.profile_hash[:12]}-{uuid.uuid4().hex[:12]}"
    )
    # The builder is the sole resolution boundary for profile-driven runs. Legacy/no-profile paths
    # still resolve here because they construct Ralph/DagDriver directly below.
    prover = None
    selected_prover = None
    if not profile_driven_invocation:
        prover = resolve(Role.prover, profile.roles.prover, Deps(toolkit=toolkit))
        selected_prover = resolution_of(prover)
    execution_roles: dict[str, dict[str, object]] = {}
    if selected_prover is not None:
        execution_roles[Role.prover.value] = selected_prover
    trace.emit(
        "run_config",
        profile_name=profile.name,
        profile_hash=profile.profile_hash,
        prover_chain=_role_chain_label(profile.roles.prover),
        prover_selected=selected_prover,
    )

    # Header: a role can declare a live availability fallback. Print the configured chain rather than
    # falsely claiming the primary was selected; registry resolution is the actual selection boundary.
    mode = profile.mode.value
    if getattr(args, "profile", None):
        pspec = profile.roles.prover
        print(f"# Proving (prover-chain={_role_chain_label(pspec)}, mode={mode}):")
    else:
        print(f"# Proving (model={args.model}, effort={args.effort}, mode={mode}):")
    print(f"  {args.goal}\n")

    # Build the retriever EARLY (before --evolve) so the evolutionary search can retrieval-seed its
    # islands with elementary Mathlib exemplars matching the goal. None unless --retrieval/--neural.
    retriever = None
    neural_active = False
    if args.retrieval or args.neural:
        from agent.tools.retrieval import LoogleRetriever
        from agent.tools.semantic_retrieval import SemanticRetriever, HybridRetriever
        rs = [LoogleRetriever()]                  # Loogle: exact names from compile errors
        if args.neural:                           # neural bi-encoder: semantic (closes abbrev gap)
            from agent.tools.neural_retrieval import (NeuralRetriever, SentenceTransformerEmbedder,
                                                      CrossEncoderReranker)
            rer = CrossEncoderReranker() if args.rerank else None
            neu = NeuralRetriever(SentenceTransformerEmbedder(), reranker=rer)
            if neu.available():
                rs.append(neu)
                neural_active = True
            else:
                print("# (--neural requested but sentence-transformers/Mathlib unavailable; skipping)")
        sem = SemanticRetriever()
        if sem.available():
            rs.append(sem)                        # BM25: relevance from the claim's meaning words
        retriever = HybridRetriever(rs) if len(rs) > 1 else rs[0]

    execution_controls: dict[str, object] = {
        "formalization_repair_iterations": int(args.repair),
        "retrieval": {
            "retrieval_requested": bool(args.retrieval),
            "neural_requested": bool(args.neural),
            "rerank_requested": bool(args.rerank),
            "retrieval_effective": retriever is not None,
            "neural_active": neural_active,
            "neural_degraded": bool(args.neural and not neural_active),
            "rerank_active": bool(args.rerank and neural_active),
            "retriever_chain": _retriever_chain(retriever),
        },
    }
    trace.emit("execution_controls", **execution_controls)

    # FIRST-CLASS evolutionary proving. When --evolve K is set we run the breadth-led OpenEvolve
    # exploration loop (Sonnet samples MANY diverse candidate ledgers per generation; MAP-Elites + the
    # island database evolve them; the HARD-gated, goal-bound, obligation-debt-graded fitness SELECTS
    # across generations) for K iterations to produce a CHAMPION ledger. The supervisor requires the
    # backend up front, and a later availability race fails closed. The champion is handed to the DAG +
    # Lean for verification — iff it is a GOAL-BOUND ledger that cleared the PASSED band; we do NOT
    # require the unreachable fitness == 1.0 (the HARD-gated band caps a genuine PASSED ledger well below
    # 1.0, so an == 1.0 gate would discard every real champion and degrade --evolve to a no-op).
    evolved_ledger = None
    evolve_iterations = profile.stages.evolve
    if evolve_iterations:
        from agent.tools.openevolve_bridge import OpenEvolveBackend, evolve_prove, PASSED_FLOOR
        if not OpenEvolveBackend.available():
            print("ERROR: the supervised evolutionary stage became unavailable before startup; "
                  "install mathagent[evolve] and retry.", file=sys.stderr)
            return 2
        else:
            ens = profile.ensemble
            from agent.tools.claude_cli import ClaudeConfig
            print(f"# evolving proof-sketch ledgers ({evolve_iterations} iterations, "
                  f"{ens.breadth_model}-breadth + {ens.depth_model}-depth ensemble)...")
            champ = evolve_prove(
                args.goal,
                toolkit,
                iterations=evolve_iterations,
                retriever=retriever,
                claude_cfg=ClaudeConfig(timeout_s=ens.timeout_s),
                breadth_model=ens.breadth_model,
                depth_model=ens.depth_model,
                breadth_weight=ens.breadth_weight,
                depth_weight=ens.depth_weight,
            )
            print(f"# evolve: best gate fitness = {champ.fitness:.2f} "
                  f"(goal_bound={champ.goal_bound}, passed={champ.passed}, "
                  f"PASSED_FLOOR={PASSED_FLOOR})")
            # Accept the champion as a proof CANDIDATE for THIS goal iff it is goal-bound AND cleared
            # the PASSED band (champ.accepted). champ.accepted is only the SEARCH signal — a gameable
            # soft fitness (its own docstrings concede documented spoofs reach the PASSED band), NOT a
            # verdict. The candidate is verified below by the SAME deterministic gate every proof passes
            # (+ the terminal Layer-4 audit in certifying modes); the search fitness never by itself
            # mints PROVEN or a certificate.
            if champ.accepted:
                evolved_ledger = champ.ledger
                print("# evolve: champion is goal-bound + cleared the search fitness — verifying below\n")
            elif champ.goal_bound:
                evolved_ledger = champ.ledger
                print("# evolve: best champion binds to the goal but is below the PASSED band; "
                      "using it only as a seed for the prover\n")
            else:
                print("# evolve: no goal-bound champion; falling through to the prover\n")

    # NUMERIC-GROUNDING witness evolution (opt-in). Evolve a witness/construction SPEC scored ONLY by
    # the exact-integer checker (numeric.py) — a non-gameable signal: non-elementary objects are
    # literally unrepresentable in the integer-only AST. This grounds a CONSTRUCTION (e.g. a complete
    # residue cover) rather than the claim; it reports the best confirmed spec and never short-circuits
    # the prover. Wires the previously-unreachable evolve_witnesses entrypoint.
    witness_iterations = profile.stages.evolve_witness
    if witness_iterations:
        from agent.tools.openevolve_bridge import OpenEvolveBackend, evolve_witnesses, score_witness_spec
        if not OpenEvolveBackend.available():
            print("ERROR: the supervised witness-evolution stage became unavailable before startup; "
                  "install mathagent[evolve] and retry.", file=sys.stderr)
            return 2
        else:
            ens = profile.ensemble
            from agent.tools.claude_cli import ClaudeConfig
            print(f"# evolving numeric witness/construction specs ({witness_iterations} iterations)...")
            best_spec, wfit, _wm = evolve_witnesses(
                args.goal,
                iterations=witness_iterations,
                claude_cfg=ClaudeConfig(timeout_s=ens.timeout_s),
                breadth_model=ens.breadth_model,
                depth_model=ens.depth_model,
                breadth_weight=ens.breadth_weight,
                depth_weight=ens.depth_weight,
            )
            confirmed = score_witness_spec(best_spec)["combined_score"] >= 1.0
            print(f"# witness: best exact-integer fitness = {wfit:.2f} "
                  f"(confirmed={confirmed})")
            if confirmed:
                print("# witness: construction CONFIRMED by the exact-integer checker\n"
                      "--- witness spec ---\n" + best_spec + "\n")
            else:
                print("# witness: no confirmed construction evolved\n")

    # NOTE: an accepted evolved champion is NOT reported here off the search fitness. It is verified
    # below (after the faithfulness/server/certifying setup) by the SAME deterministic gate — and, under
    # --terminal-gate/--formalize, the terminal Layer-4 audit — that every other proof passes. This
    # closes the bypass where `champ.accepted` alone printed `result: PROVEN` and exited 0.

    server = None
    # A profile-driven DagDriver owns the server built by builder; starting another one here would
    # load Mathlib twice and leave ownership ambiguous. Legacy paths still own their single server.
    if args.server and not profile_driven_invocation:
        from agent.gates.lean_server import LeanServer
        if LeanServer.available():
            print("# starting persistent Lean server (loads Mathlib once)...")
            try:
                server = LeanServer().start()
                if server is None:
                    raise RuntimeError("LeanServer.start() returned no server")
                owned_legacy_servers.append(server)
            except Exception as exc:
                print(f"ERROR: the required Lean server failed to start: {exc}", file=sys.stderr)
                return 2
        else:
            print("ERROR: --server requires the Mathlib Lean REPL. Run `lake build repl` in the "
                  "Mathlib project, then retry.", file=sys.stderr)
            return 2
    # Faithfulness FAILS CLOSED: a Layer-4 result can only be "authoritative" if a faithfulness panel
    # actually ran and passed. Certification modes (--terminal-gate / --formalize, or an authoritative
    # profile) therefore require the panel; --no-faithfulness is rejected before any backend call.
    # `certifying` reflects whether the terminal Layer-4 gate ACTUALLY runs (flags OR an authoritative
    # profile) — see _certifying. This fixes the `--profile profiles/authoritative.yaml` path, where the
    # audit ran but flag-only `certifying` was False, mislabeling a non-elementary result `soft_proven`.
    certifying = _certifying(args, profile)
    want_faith = certifying
    faith = None
    if want_faith and not profile_driven_invocation:
        # Resolve from the effective profile just like the formalizer.  Hard-coding Codex here made a
        # Claude authoritative profile silently borrow an undeclared backend and defeated fallback.
        faith = resolve(Role.faithfulness, profile.roles.faithfulness, Deps(toolkit=toolkit))
        metadata = resolution_of(faith)
        if metadata is not None:
            execution_roles[Role.faithfulness.value] = metadata
    # (retriever was built above, before the --evolve block, so evolve can retrieval-seed its islands.)

    cert_authoritative = None   # set in certifying modes once a Layer-4 certification actually runs
    audit_completed = False     # True only after Lean compiled and returned an actual audit result
    artifact_terminal = None    # legacy post-hoc certification receipt for --out

    # A goal-bound champion enters as the FIRST prover candidate; it never bypasses the configured
    # proof judge, DAG/refinement routing, per-node Lean, terminal gate, or retry budget.
    if evolved_ledger is not None and not profile_driven_invocation:
        prover = _SeededProver(evolved_ledger, prover)

    legacy_direct = bool(args.direct and not getattr(args, "profile", None))
    if legacy_direct:
        judge = (resolve(Role.judge, profile.roles.judge, Deps(toolkit=toolkit))
                 if profile.stages.review else None)
        judge_metadata = resolution_of(judge)
        if judge_metadata is not None:
            execution_roles[Role.judge.value] = judge_metadata
        res = RalphLoop(prover, toolkit=toolkit, budget=budget, trace=trace,
                        max_episodes=args.episodes,
                        judges=[judge] if judge is not None else None).run(args.goal)
        ok = res.success
        # Goal<->claim binding: a prover may return a clean, self-consistent ledger that proves a
        # DIFFERENT statement than the one requested. Bind BOTH the top-level claim AND the terminal
        # conclusion step to args.goal (mirroring DagDriver), so a ledger whose claim==goal but whose
        # conclusion proves a fresh unrelated statement is not reported PROVEN for this goal.
        # NOTE: RalphLoop now enforces goal-binding as a PER-EPISODE acceptance criterion (a res.success
        # ledger is already goal-bound), so this post-hoc check is a REDUNDANT backstop — kept for
        # defense in depth (it should no longer be the only guard).
        if ok and res.ledger:
            try:
                led = parse_ledger(res.ledger)
                gh = goal_hash(args.goal)
                concl = next((s for s in led.steps if s.justification == "conclusion"), None)
                proved = led.claim if (concl is None) else concl.claim
                if goal_hash(led.claim) != gh or concl is None or goal_hash(concl.claim) != gh:
                    ok = False
                    print(f"# rejected: the ledger proves {proved!r}, not the requested goal")
            except LedgerError:
                pass
        print(f"result: {'PROVEN' if ok else 'NOT PROVEN'}  (episodes={res.episodes})")
        if res.report:
            print(f"gate: {res.report.summary()}")
        if ok and res.ledger:
            print("\n--- ledger ---\n" + res.ledger)
    else:
        if getattr(args, "profile", None):
            # PROFILE-DRIVEN path (W6): delegate construction to the builder — the single
            # RunProfile -> supervisor -> registry -> DagDriver site. The supervisor fails CLOSED
            # before any model/Lean call if the loaded profile is inadmissible.
            from agent.orchestrator.builder import build_driver
            # Reuse the immutable snapshot already loaded and supervisor-validated above. Re-reading a
            # mutable YAML path here would create a profile TOCTOU: certification setup/provenance could
            # describe one policy while build_driver executes another.
            prof = profile
            print(f"# profile: {args.profile} (hash {prof.profile_hash[:12]}, "
                  f"elementarity={prof.elementarity.value})")
            driver = build_driver(
                prof, toolkit=toolkit, trace=trace,
                retriever=retriever, repair_iters=args.repair,
                _presearch_consumed=True)
            selected = getattr(driver, "resolved_roles", {})
            print("# selected profile roles: " + ", ".join(
                f"{role}={_resolution_label(metadata)}"
                for role, metadata in sorted(selected.items())
            ))
            if evolved_ledger is not None:
                # build_driver resolves the profile's prover internally; preserve that as the retry
                # delegate while placing the evolved candidate at episode one.
                driver.prover = _SeededProver(evolved_ledger, driver.prover)
        else:
            terminal_gate = None
            if args.terminal_gate:
                from agent.orchestrator.formalize_bridge import make_terminal_gate
                from agent.orchestrator.registry import Deps, resolve
                from agent.orchestrator.run_profile import Role
                # RESOLVE the terminal-gate formalizer via the registry from the SAME effective profile
                # (so --formalizer-model=claude, which maps to the profile's formalizer RoleSpec in
                # _codex_roles, is honored: it resolves ClaudeFormalizer). The default legacy Codex
                # profile resolves CodexFormalizer(toolkit, codex-cfg).
                formalizer = resolve(Role.formalizer, profile.roles.formalizer, Deps(toolkit=toolkit))
                formalizer_metadata = resolution_of(formalizer)
                if formalizer_metadata is not None:
                    execution_roles[Role.formalizer.value] = formalizer_metadata
                terminal_gate = make_terminal_gate(formalizer, toolkit,
                                                   faithfulness_checker=faith, server=server,
                                                   retriever=retriever, repair_iters=args.repair)
            # Resolve optional population/refinement roles through the same registry boundary as every
            # other live role so fallback selection and actual model provenance remain coherent.
            role_deps = Deps(
                toolkit=toolkit, budget=budget, trace=trace,
                n_judges=profile.stages.judges, max_passes=2, k_stop=2, margin=1,
                seed=profile.seed,
            )
            comparator = (resolve(Role.comparator, profile.roles.comparator, role_deps)
                          if args.population else None)
            refiner = (resolve(Role.refiner, profile.roles.refiner, role_deps)
                       if args.refine else None)
            # OpenEvolve fallback decomposer (fires ONLY on stuck nodes; commits only goal-bound,
            # obligation-discharged blueprints). Availability races fail closed after supervision.
            evolve_fallback = None
            if profile.stages.evolve_fallback:
                from agent.tools.openevolve_bridge import OpenEvolveBackend
                from agent.tools.claude_cli import ClaudeConfig
                if OpenEvolveBackend.available():
                    ens = profile.ensemble
                    evolve_fallback = OpenEvolveBackend(
                        toolkit, ClaudeConfig(timeout_s=ens.timeout_s),
                        generations=profile.stages.evolve_fallback,
                        breadth_model=ens.breadth_model,
                        depth_model=ens.depth_model,
                        breadth_weight=ens.breadth_weight,
                        depth_weight=ens.depth_weight,
                        retriever=retriever)
                else:
                    print("ERROR: the supervised evolve fallback became unavailable before "
                          "construction; install mathagent[evolve] and retry.", file=sys.stderr)
                    return 2
            # Single argparse->DagDriver construction site (factored into build_dag_driver so the wiring
            # is testable). It builds the per-node Lean verifiers from --lean-per-node and threads
            # lean_strict + the warm Lean server in; without --lean-per-node those gates stay absent.
            args._retriever = retriever
            driver = build_dag_driver(
                args, profile=profile, prover=prover, toolkit=toolkit, cfg=cfg, budget=budget,
                trace=trace, server=server, terminal_gate=terminal_gate, comparator=comparator,
                refiner=refiner, evolve_fallback=evolve_fallback,
            )
        # LeanServer ownership: on the --profile path build_driver warms a LeanServer for lean.server
        # profiles and stores it on driver.lean_server (mirroring build_and_run); this branch owns it for
        # the single run, so it must close() it — otherwise a lean.server --profile run leaks an orphaned
        # repl.exe holding Mathlib. try/finally so close() runs on BOTH success and a raising run()
        # (DagDriver.close() is idempotent and never raises). The NON-profile path manages its OWN
        # --server server, closed at the end of main(), so it does not close here.
        profile_driven = bool(getattr(args, "profile", None))
        if profile.lean.per_node:
            formalizer_label = (
                _resolution_label(getattr(driver, "resolved_roles", {}).get("formalizer"))
                if profile_driven else _role_chain_label(profile.roles.formalizer)
            )
            print("# per-node Lean: verifying every leaf + AND-composition "
                  f"(formalizer={formalizer_label}, "
                  f"strict={profile.lean.strict})")
        try:
            res = driver.run(args.goal)
            # In the --profile path the builder constructed its OWN budget from the profile; point the
            # final search/review-call report at the budget the driver actually spent (not main's unused
            # one). Formalization and faithfulness model calls are reported by their own audit result.
            if profile_driven:
                budget = res.budget
            ok = res.proven
            print(f"result: {'PROVEN' if ok else 'NOT PROVEN'}")
            print(f"dag: {res.dag.stats()}")
            import json as _json
            print("\n--- proof tree ---\n" + _json.dumps(res.proof_tree(), indent=2))
            # Report per-node Lean states: PROVEN (soft) vs the first-class LEAN_VERIFIED hard-success state.
            if profile.lean.per_node:
                from agent.orchestrator.state import NodeState as _NodeState
                lean_nodes = sum(1 for n in res.dag.nodes.values()
                                 if n.state is _NodeState.LEAN_VERIFIED)
                soft_nodes = sum(1 for n in res.dag.nodes.values()
                                 if n.state is _NodeState.PROVEN)
                print(f"node states: proven={soft_nodes} lean_verified={lean_nodes}")
            if res.terminal is not None:
                print("\nterminal Layer-4 gate:", res.terminal.summary())
                print("authoritative_elementary:", res.authoritative_elementary)
                if args.formalize and getattr(res.terminal, "lean_source", None):
                    print("\n--- formalized Lean ---\n" + res.terminal.lean_source)
                _proven, audit_completed, coherent_authority, _audit = (
                    result_certification_state(res)
                )
                cert_authoritative = coherent_authority
        finally:
            if profile_driven:
                driver.close()

    # Optional: close the loop — formalize the proven ledger to Lean and run the Layer-4 audit.
    winning_ledger = (_direct_result_ledger(res, args.goal)
                      if profile.mode is Mode.direct and ok else None)
    # Only the legacy Ralph-only direct path lacks an attached terminal gate. Profile-driven direct
    # mode already audited inside DagDriver; running again would spend twice and could overwrite the
    # authoritative result with a second, inconsistent verdict.
    posthoc_audit = bool(legacy_direct and (args.formalize or args.terminal_gate))
    if posthoc_audit and winning_ledger:
        from agent.orchestrator.formalize_bridge import formalize_and_audit
        print("\n# Formalizing the ledger to Lean and running the Layer-4 audit...")
        # Resolve the formalizer from the EFFECTIVE profile (honors --formalizer-model and a pinned
        # --profile formalizer), not a hardcoded CodexFormalizer, matching the --terminal-gate path.
        _formalizer = resolve(Role.formalizer, profile.roles.formalizer, Deps(toolkit=toolkit))
        formalizer_metadata = resolution_of(_formalizer)
        if formalizer_metadata is not None:
            execution_roles[Role.formalizer.value] = formalizer_metadata
        fa = formalize_and_audit(winning_ledger, _formalizer, toolkit=toolkit,
                                 informal_claim=args.goal,
                                 faithfulness_checker=faith, server=server,
                                 retriever=retriever, repair_iters=args.repair)
        print("formalize + audit:", fa.summary())
        print("authoritative_elementary:", fa.authoritative)
        _proven, audit_completed, coherent_authority, _audit = result_certification_state(
            res, terminal_override=fa,
        )
        cert_authoritative = coherent_authority
        artifact_terminal = fa
        if fa.lean_source:
            print("\n--- formalized Lean ---\n" + fa.lean_source)
    elif posthoc_audit:
        print("\n(--formalize is supported in --direct mode on a proven ledger)")

    print(f"\norchestrator search/review calls spent: "
          f"{budget.calls_spent}/{budget.max_llm_calls}")
    # CATEGORICAL user-facing status (P3): the graded search fitness stays INTERNAL; the user sees only
    # a category from the certification ladder, so a search score never leaks into certification language.
    status = report_status(
        proven=ok,
        has_candidate=result_has_candidate(res),
        audited=audit_completed,
        authoritative_elementary=cert_authoritative is True,
    )
    print(f"status: {status.label}")
    if args.out:
        try:
            _publish_run_outputs(
                args.out, result=res, goal=args.goal, profile=profile, status=status, trace=trace,
                terminal_override=artifact_terminal,
                resolved_roles_override=execution_roles,
                policy_digest_override=toolkit_policy_sha256(toolkit),
                execution_controls=execution_controls,
            )
        except Exception as exc:
            print(f"ERROR: could not publish --out artifacts without overwriting evidence: {exc}",
                  file=sys.stderr)
            return 2
    print("trace events: " + ", ".join(f"{k}={len(trace.by_kind(k))}"
          for k in ["ralph_episode", "decompose", "review", "prove_node", "cache_hit", "final"]))
    # In certifying modes the exit code reflects CERTIFICATION (authoritative_elementary), not just the
    # informal PROVEN verdict, so automation keyed on the exit code is not misled.
    success = ok
    if certifying:
        # FAIL CLOSED: in certifying mode the exit code reflects CERTIFICATION, not the informal PROVEN
        # verdict. Only cert_authoritative is True is success. A None verdict means the terminal gate
        # CRASHED (subprocess timeout / toolchain failure — DagDriver.run catches it and returns
        # terminal=None) or never ran; that must exit non-zero exactly like a completed REJECT, so
        # automation keyed on the exit code is never told a crashed certification succeeded.
        success = ok and (cert_authoritative is True)
        if ok and cert_authoritative is not True:
            reason = ("NOT authoritative_elementary" if cert_authoritative is False
                      else "certification did not complete (terminal gate produced no verdict)")
            print(f"\n# NOTE: informally PROVEN but {reason}; exiting non-zero (certifying mode). "
                  "Inspect the terminal audit/faithfulness diagnostics, or run a non-authoritative "
                  "soft profile when certification is not required.")
    return 0 if success else 1


def main() -> int:
    """Run the CLI and deterministically release every legacy server on all exits/exceptions."""
    owned_legacy_servers: list[object] = []
    try:
        return _main(owned_legacy_servers)
    finally:
        for server in reversed(owned_legacy_servers):
            try:
                server.close()
            except BaseException:
                # Cleanup must never replace the mathematical/result exception.  LeanServer.close is
                # specified idempotent and non-raising; this guards injected test/custom servers too.
                pass


if __name__ == "__main__":
    raise SystemExit(main())
