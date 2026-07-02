"""CLI: drive the DAG harness with Codex (GPT-5.5-xHigh) as the focused prover.

Examples:
    # Full AND-OR DAG harness (direct -> decompose -> review -> recurse), Codex at xHigh:
    python scripts/prove.py "For every integer n, n^2 is congruent to 0 or 1 modulo 4."

    # Quick single-shot direct proof (one Ralph loop, no decomposition), faster effort:
    python scripts/prove.py --direct --effort low "For all integers n, n + 0 = n."

The deterministic gate (agent/gates) is authoritative; Codex is only the generator/soft-reviewer.
"""
from __future__ import annotations

import argparse
import sys
from enum import Enum
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.gates.toolkit import load_toolkit
from agent.gates.ledger import parse_ledger, LedgerError
from agent.orchestrator import Budget, RunTrace, RalphLoop, DagDriver
from agent.orchestrator.dag import goal_hash
from agent.orchestrator.run_profile import (
    BudgetProfile, ElementarityLevel, LeanProfile, Mode, ProviderKey, RoleSpec, RolesProfile,
    RunProfile, StageProfile,
)
from agent.tools.codex_prover import (CodexProver, CodexDecomposer, CodexReviewer, CodexComparator,
                                      CodexConfig, make_codex_refiner)


# --------------------------------------------------------------------------------------------------
# SEARCH-FITNESS vs REPORTING-STATUS separation (P3 / openevolve_stacking_brief §9):
# the graded search fitness (the evolutionary combined_score, Elo ratings, etc.) is INTERNAL and must
# NEVER leak into the user-facing certification language. The user-facing status is a CATEGORICAL enum
# whose ladder runs strictly: rejected < candidate_incomplete < soft_proven < formalized_not_elementary
# < authoritative_elementary. A search SCORE (a float) is never a status — only these categories are.
# --------------------------------------------------------------------------------------------------
class ReportStatus(Enum):
    REJECTED = "rejected"                                  # no admissible proof found
    CANDIDATE_INCOMPLETE = "candidate_incomplete"          # a candidate exists but is not soft-proven
    SOFT_PROVEN = "soft_proven"                            # PROVEN (soft gate) — NOT a certificate
    FORMALIZED_NOT_ELEMENTARY = "formalized_not_elementary"  # formalized + audited but NOT elementary
    AUTHORITATIVE_ELEMENTARY = "authoritative_elementary"   # the ONLY real certificate

    @property
    def label(self) -> str:
        return self.value


def report_status(*, proven: bool, has_candidate: bool = False,
                  formalized: bool = False,
                  authoritative_elementary: bool = False) -> ReportStatus:
    """Map an outcome to the CATEGORICAL user-facing status (never a search score).

    Precedence (highest first): an ``authoritative_elementary`` certificate dominates everything; a
    ``formalized`` result that is NOT authoritative is ``formalized_not_elementary`` (audited but not
    certified elementary — never reported as a certificate); a ``proven`` (soft-gate) result is
    ``soft_proven``; a result with only a non-proven candidate is ``candidate_incomplete``; otherwise
    ``rejected``. The graded search fitness is INTENTIONALLY not an input here — a high search score
    never promotes the status."""
    if authoritative_elementary:
        return ReportStatus.AUTHORITATIVE_ELEMENTARY
    if formalized:
        # Formalized + audited but the audit did not certify elementary.
        return ReportStatus.FORMALIZED_NOT_ELEMENTARY
    if proven:
        return ReportStatus.SOFT_PROVEN
    if has_candidate:
        return ReportStatus.CANDIDATE_INCOMPLETE
    return ReportStatus.REJECTED


# --------------------------------------------------------------------------------------------------
# profile_from_args (W6): the THIN, total argparse -> RunProfile mapping. Every structural CLI flag
# maps to exactly one profile field, so the builder (build_driver) becomes the single DagDriver
# construction site. This CLI is the LEGACY Codex prover; with NO --profile it keeps Codex roles so the
# live output is byte-identical to before. The shipped profiles/default.yaml uses Claude (the maintainer
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

    --terminal-gate (Layer-4 terminal authoritative gate) => authoritative; otherwise soft (the
    in-engine elementarity refutation still enforces). prove.py has no 'none' flag — solution-only
    runs go through profiles/solution-only.yaml via --profile."""
    return (ElementarityLevel.authoritative if getattr(args, "terminal_gate", False)
            else ElementarityLevel.soft)


# The structural flags profile_from_args reads, with their argparse defaults. A flag "overrides" a
# loaded --profile base ONLY when it differs from its default — so loading authoritative.yaml does not
# get its lean.per_node silently clobbered by the (default-False) --lean-per-node flag, while an
# explicit `--profile codex.yaml --population 4` still takes effect.
_FLAG_DEFAULTS = {
    "direct": False, "terminal_gate": False, "lean_per_node": False, "lean_strict": False,
    "server": False, "refine": False, "population": 0, "evolve_fallback": 0,
    "budget": 60, "max_depth": 3, "max_decomp": 2, "episodes": 3,
}


def _explicitly_set(args) -> set[str]:
    """The structural flags the user actually changed from their default (so a --profile base is only
    overridden where the CLI was explicit). store_true flags can only be set True, so default-equality
    correctly means 'not passed'; valued flags set to their default value are a harmless no-op override."""
    return {k for k, default in _FLAG_DEFAULTS.items()
            if getattr(args, k, default) != default}


def profile_from_args(args, base: "RunProfile | None" = None) -> RunProfile:
    """Map the parsed CLI args to a RunProfile (the one control lever the builder consumes).

    With NO ``base`` this is the legacy Codex profile (Codex roles, every flag mapped), so a bare
    `prove.py GOAL` builds the SAME wiring it always did. With a ``base`` (from --profile) the base is
    the starting point — its roles/identity AND its structural fields are preserved — and ONLY the CLI
    flags the user EXPLICITLY set override it (see :func:`_explicitly_set`); loading authoritative.yaml
    therefore keeps its Lean wiring unless you pass the corresponding flag.

    The mapping is total over the structural flags: --direct->mode; --max-depth/--max-decomp/--episodes/
    --budget->budgets; --population/--refine/--evolve-fallback->stages; --terminal-gate->elementarity
    (+lean.terminal); --lean-per-node/--lean-strict/--server->lean."""
    normalize_lean_flags(args)
    if base is None:
        # Legacy path: every flag maps (full, byte-identical Codex profile).
        return _profile_all_flags(args, _codex_roles(args), name="prove-cli", seed=0, notes="")
    # --profile path: start from the base, then layer ONLY explicit flag overrides.
    explicit = _explicitly_set(args)
    if not explicit:
        return base
    # Build a full flag-derived profile, then copy across only the explicitly-set fields.
    full = _profile_all_flags(args, base.roles, name=base.name, seed=base.seed, notes=base.notes)
    update: dict = {}
    if {"direct"} & explicit:
        update["mode"] = full.mode
    if {"terminal_gate"} & explicit:
        update["elementarity"] = full.elementarity
    stage_keys = {"direct": ("decompose", "review"), "population": ("population",),
                  "evolve_fallback": ("evolve_fallback",), "refine": ("refine",)}
    stage_update = {f: getattr(full.stages, f)
                    for flag, fields in stage_keys.items() if flag in explicit for f in fields}
    if stage_update:
        update["stages"] = base.stages.model_copy(update=stage_update)
    budget_keys = {"budget": "max_llm_calls", "max_depth": "max_depth",
                   "max_decomp": "max_decomp_attempts", "episodes": "episodes"}
    budget_update = {field: getattr(full.budgets, field)
                     for flag, field in budget_keys.items() if flag in explicit}
    if budget_update:
        update["budgets"] = base.budgets.model_copy(update=budget_update)
    lean_keys = {"lean_per_node": "per_node", "terminal_gate": "terminal",
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
            review=not args.direct,
            population=int(args.population),
            evolve_fallback=int(args.evolve_fallback),
            refine=bool(args.refine),
            h0_consistency=True,
        ),
        budgets=BudgetProfile(
            max_llm_calls=int(args.budget),
            max_depth=int(args.max_depth),
            max_decomp_attempts=int(args.max_decomp),
            episodes=int(args.episodes),
        ),
        lean=LeanProfile(
            per_node=bool(args.lean_per_node),
            terminal=bool(getattr(args, "terminal_gate", False)),
            strict=bool(args.lean_strict),
            server=bool(args.server),
        ),
    )


def build_lean_node_gates(args, toolkit, cfg, server, retriever=None):
    """Build the PER-NODE Lean verifiers for the DAG driver from the parsed CLI args.

    Returns ``(node_verifier, sketch_verifier)``:
      * ``(None, None)`` when ``--lean-per-node`` is NOT set — the BYTE-IDENTICAL default: the DAG
        driver gets no per-node Lean authority and runs the offline path it ran before this feature.
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
    from agent.tools.formalizer import CodexFormalizer, ClaudeFormalizer
    from agent.orchestrator.formalize_bridge import make_node_gate, make_sketch_gate
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
    ``node_verifier=None``/``sketch_verifier=None``/``lean_strict=False``/``lean_server=None`` exactly as
    before (byte-identical).

    The decomposer and reviewer are RESOLVED via the registry from the SAME effective ``profile`` the
    supervisor validated (not hardwired), so the legacy path runs the provider the profile pins. For the
    legacy Codex profile (``_codex_roles`` pins provider=codex for every role) the resolved components
    are CodexDecomposer/CodexReviewer with the CLI's model/effort — byte-identical to the old hardwiring."""
    from agent.orchestrator.registry import Deps, resolve
    from agent.orchestrator.run_profile import Role
    deps = Deps(toolkit=toolkit)
    node_verifier, sketch_verifier = build_lean_node_gates(
        args, toolkit, cfg, server, retriever=getattr(args, "_retriever", None))
    return DagDriver(
        prover,
        decomposer=resolve(Role.decomposer, profile.roles.decomposer, deps),
        reviewer=resolve(Role.reviewer, profile.roles.reviewer, deps),
        toolkit=toolkit, budget=budget, trace=trace,
        max_depth=args.max_depth, max_decomp_attempts=args.max_decomp,
        ralph_episodes=args.episodes, terminal_gate=terminal_gate,
        comparator=comparator, population_k=args.population, refiner=refiner,
        evolve_fallback=evolve_fallback,
        node_verifier=node_verifier, sketch_verifier=sketch_verifier,
        lean_strict=bool(args.lean_strict),
        lean_server=(server if getattr(args, "lean_per_node", False) else None),
    )


def build_arg_parser() -> argparse.ArgumentParser:
    """The REAL CLI parser (the single source of the flag definitions). Factored out of ``main`` so the
    flag wiring (e.g. ``--lean-per-node`` -> DagDriver) can be driven through the ACTUAL argparse path in
    tests, not a hand-rolled stub."""
    ap = argparse.ArgumentParser(description="Prove a number-theory goal with the Codex-backed harness.")
    ap.add_argument("goal", nargs="?", help="the goal/theorem statement to prove")
    ap.add_argument("--profile", type=Path, metavar="PATH",
                    help="load a RunProfile YAML as the base wiring (roles/stages/budget/lean); the "
                         "structural CLI flags then OVERRIDE its fields. The shipped profiles/ presets "
                         "(default.yaml=Claude, codex.yaml=legacy, solution-only.yaml, authoritative.yaml) "
                         "are good starting points.")
    ap.add_argument("--dump-profile", action="store_true",
                    help="print the EFFECTIVE RunProfile (after merging --profile + flags) as YAML and "
                         "exit WITHOUT running — for inspecting/diffing the exact wiring a run would use.")
    ap.add_argument("--model", default="gpt-5.5")
    ap.add_argument("--effort", default="xhigh", choices=["low", "medium", "high", "xhigh"])
    ap.add_argument("--direct", action="store_true", help="single Ralph loop, no DAG decomposition")
    ap.add_argument("--max-depth", type=int, default=3)
    ap.add_argument("--max-decomp", type=int, default=2)
    ap.add_argument("--episodes", type=int, default=3, help="Ralph episodes per node")
    ap.add_argument("--budget", type=int, default=60, help="max Codex calls for the whole run")
    ap.add_argument("--timeout", type=int, default=1200, help="per-Codex-call timeout (s)")
    ap.add_argument("--out", type=Path, help="write the winning ledger / trace JSONL here (prefix)")
    ap.add_argument("--formalize", action="store_true",
                    help="after a direct proof, formalize the ledger to Lean and run the Layer-4 audit")
    ap.add_argument("--terminal-gate", action="store_true",
                    help="(dag mode) run Layer 4 as the terminal authoritative gate on the proven root")
    ap.add_argument("--faithfulness", action="store_true",
                    help="force the adversarial statement-faithfulness panel on (default ON for "
                         "certification modes --terminal-gate / --formalize)")
    ap.add_argument("--no-faithfulness", action="store_true",
                    help="opt out of the faithfulness panel in certification modes (the result can "
                         "then NOT be authoritative — audited only)")
    ap.add_argument("--server", action="store_true",
                    help="use the persistent Lean server (loads Mathlib once) for audits")
    ap.add_argument("--lean-per-node", action="store_true",
                    help="(dag) Lean-verify EVERY leaf and AND-composition per node (formalize -> "
                         "compile -> Layer-4 audit on a warm Lean server) so LEAN_VERIFIED becomes "
                         "reachable; implies --server. Errors out if Lean is unavailable.")
    ap.add_argument("--lean-strict", action="store_true",
                    help="(dag) implies --lean-per-node and fails CLOSED on inability to formalize: a "
                         "leaf/sketch that cannot be compiled is NOT accepted as proven (no unverified "
                         "PROVEN is ever minted)")
    ap.add_argument("--formalizer-model", default="codex", choices=["codex", "claude"],
                    help="which model formalizes per-node proofs to Lean under --lean-per-node "
                         "(codex=CodexFormalizer default; claude=ClaudeFormalizer/Opus)")
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
                    help="Codex judges in the population / refiner panels")
    ap.add_argument("--refine", action="store_true",
                    help="(dag) refine each directly-proven ledger with the Codex Autoreason tournament")
    ap.add_argument("--max-replan", type=int, default=2, metavar="D",
                    help="global re-plan budget (max_replan_depth)")
    ap.add_argument("--evolve", type=int, default=0, metavar="K",
                    help="FIRST-CLASS evolutionary proving: run the breadth-led OpenEvolve proof-sketch "
                         "search for K iterations to explore a goal-bound, obligation-discharged "
                         "CHAMPION ledger; a goal-bound PASSED champion short-circuits the prover and "
                         "is handed to the DAG/Lean (needs mathagent[evolve]); graceful no-op if "
                         "openevolve is not installed")
    ap.add_argument("--evolve-witness", type=int, default=0, metavar="K",
                    help="NUMERIC-GROUNDING evolution: evolve a witness/construction SPEC (residue "
                         "cover / descent measure / solution set) for K iterations, scored ONLY by the "
                         "exact-integer checker (numeric.py, no eval/exec). Reports the best confirmed "
                         "construction (needs mathagent[evolve]); graceful no-op if not installed")
    ap.add_argument("--evolve-fallback", type=int, default=0, metavar="K",
                    help="(dag) wire the OpenEvolve backend as a FALLBACK decomposer that fires ONLY "
                         "on stuck nodes (K evolve iterations per fire); commits an evolved blueprint "
                         "only if it is goal-bound + obligation-discharged (needs mathagent[evolve]); "
                         "graceful no-op if openevolve is not installed")
    return ap


def normalize_lean_flags(args) -> None:
    """In-place flag normalization (shared by main + tests): ``--lean-strict`` implies
    ``--lean-per-node`` which implies ``--server`` (one warm Lean env reused by every per-node compile),
    so the rest of the pipeline reads a single source of truth."""
    if args.lean_strict:
        args.lean_per_node = True
    if args.lean_per_node:
        args.server = True


def effective_profile(args) -> RunProfile:
    """The EFFECTIVE RunProfile for this invocation: the --profile base (if any) overridden by the
    structural CLI flags. The single source of truth shared by --dump-profile and the DAG build."""
    base = RunProfile.from_yaml(args.profile) if getattr(args, "profile", None) else None
    return profile_from_args(args, base=base)


def main() -> int:
    args = build_arg_parser().parse_args()
    normalize_lean_flags(args)

    # --dump-profile: print the effective profile (after merging --profile + flags) and exit. Pure data
    # — performs NO model/Lean call, so it works even with no goal and no backend installed.
    if args.dump_profile:
        import yaml as _yaml
        prof = effective_profile(args)
        print(_yaml.safe_dump(prof.model_dump(mode="json"), sort_keys=False), end="")
        return 0

    if not args.goal:
        print("ERROR: a goal is required (or use --dump-profile to inspect the profile).",
              file=sys.stderr)
        return 2

    # SUPERVISOR = the SOLE pre-flight chokepoint. Compute the effective RunProfile and validate it
    # FAIL-CLOSED before ANY construction or model/Lean call, so EVERY CLI path (direct / legacy DAG /
    # --profile / evolve) is supervised. The supervisor's per-profile provider guards replace the old
    # blanket 'codex CLI required' check, so a Claude profile no longer depends on Codex being present.
    from agent.orchestrator.supervisor import validate_profile, SupervisorError
    from agent.orchestrator.registry import resolve, Deps
    from agent.orchestrator.run_profile import Role
    profile = effective_profile(args)
    try:
        validate_profile(profile)
    except SupervisorError as e:
        print(f"ERROR: profile rejected by supervisor: {e}", file=sys.stderr)
        return 2

    toolkit = load_toolkit()
    cfg = CodexConfig(model=args.model, reasoning_effort=args.effort, timeout_s=args.timeout)
    budget = Budget(max_llm_calls=args.budget, max_replan_depth=args.max_replan)
    trace = RunTrace("codex-prove")
    # The prover is resolved FROM the supervised profile via the registry (Claude by default; Codex only
    # when the profile pins it), so the direct / legacy paths run the SAME provider the supervisor just
    # validated instead of a hardwired CodexProver. For the legacy codex profile this is byte-identical.
    prover = resolve(Role.prover, profile.roles.prover, Deps(toolkit=toolkit))

    print(f"# Proving (model={args.model}, effort={args.effort}, mode={'direct' if args.direct else 'dag'}):")
    print(f"  {args.goal}\n")

    # Build the retriever EARLY (before --evolve) so the evolutionary search can retrieval-seed its
    # islands with elementary Mathlib exemplars matching the goal. None unless --retrieval/--neural.
    retriever = None
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
            else:
                print("# (--neural requested but sentence-transformers/Mathlib unavailable; skipping)")
        sem = SemanticRetriever()
        if sem.available():
            rs.append(sem)                        # BM25: relevance from the claim's meaning words
        retriever = HybridRetriever(rs) if len(rs) > 1 else rs[0]

    # FIRST-CLASS evolutionary proving. When --evolve K is set we run the breadth-led OpenEvolve
    # exploration loop (Sonnet samples MANY diverse candidate ledgers per generation; MAP-Elites + the
    # island database evolve them; the HARD-gated, goal-bound, obligation-debt-graded fitness SELECTS
    # across generations) for K iterations to produce a CHAMPION ledger. If openevolve is not installed
    # this is a graceful no-op with a clear message. The champion is ACCEPTED — and handed to the DAG +
    # Lean for verification — iff it is a GOAL-BOUND ledger that cleared the PASSED band; we do NOT
    # require the unreachable fitness == 1.0 (the HARD-gated band caps a genuine PASSED ledger well below
    # 1.0, so an == 1.0 gate would discard every real champion and degrade --evolve to a no-op).
    evolved_ledger = None
    if args.evolve:
        from agent.tools.openevolve_bridge import OpenEvolveBackend, evolve_prove, PASSED_FLOOR
        if not OpenEvolveBackend.available():
            print("# (--evolve requested but openevolve not installed; "
                  "pip install mathagent[evolve] — skipping evolutionary search)\n")
        else:
            print(f"# evolving proof-sketch ledgers ({args.evolve} iterations, "
                  "Sonnet-breadth + Opus-depth ensemble)...")
            champ = evolve_prove(args.goal, toolkit, iterations=args.evolve, retriever=retriever)
            print(f"# evolve: best gate fitness = {champ.fitness:.2f} "
                  f"(goal_bound={champ.goal_bound}, passed={champ.passed}, "
                  f"PASSED_FLOOR={PASSED_FLOOR})")
            # Accept the champion as the winning ledger for THIS goal iff it is goal-bound AND cleared
            # the PASSED band (champ.accepted). It then short-circuits the prover and is handed to the
            # DAG + Lean for verification just like any other proven ledger.
            if champ.accepted:
                evolved_ledger = champ.ledger
                print("# evolve: champion is goal-bound + PASSED — accepting as the winning ledger\n")
            elif champ.goal_bound:
                print("# evolve: best champion binds to the goal but is below the PASSED band; "
                      "using it only as a seed for the prover\n")
            else:
                print("# evolve: no goal-bound champion; falling through to the prover\n")

    # NUMERIC-GROUNDING witness evolution (opt-in). Evolve a witness/construction SPEC scored ONLY by
    # the exact-integer checker (numeric.py) — a non-gameable signal: non-elementary objects are
    # literally unrepresentable in the integer-only AST. This grounds a CONSTRUCTION (e.g. a complete
    # residue cover) rather than the claim; it reports the best confirmed spec and never short-circuits
    # the prover. Wires the previously-unreachable evolve_witnesses entrypoint.
    if args.evolve_witness:
        from agent.tools.openevolve_bridge import OpenEvolveBackend, evolve_witnesses, score_witness_spec
        if not OpenEvolveBackend.available():
            print("# (--evolve-witness requested but openevolve not installed; "
                  "pip install mathagent[evolve] — skipping witness search)\n")
        else:
            print(f"# evolving numeric witness/construction specs ({args.evolve_witness} iterations)...")
            best_spec, wfit, _wm = evolve_witnesses(args.goal, iterations=args.evolve_witness)
            confirmed = score_witness_spec(best_spec)["combined_score"] >= 1.0
            print(f"# witness: best exact-integer fitness = {wfit:.2f} "
                  f"(confirmed={confirmed})")
            if confirmed:
                print("# witness: construction CONFIRMED by the exact-integer checker\n"
                      "--- witness spec ---\n" + best_spec + "\n")
            else:
                print("# witness: no confirmed construction evolved\n")

    # A clean evolved ledger that proves the goal short-circuits the prover entirely.
    if evolved_ledger is not None:
        print("result: PROVEN  (via evolutionary proof-sketch search)")
        if args.out:
            Path(str(args.out) + ".ledger.json").write_text(evolved_ledger, encoding="utf-8")
        else:
            print("\n--- ledger ---\n" + evolved_ledger)
        print(f"\ncalls spent: {budget.calls_spent}/{budget.max_llm_calls}")
        return 0

    server = None
    if args.server:
        from agent.gates.lean_server import LeanServer
        if LeanServer.available():
            print("# starting persistent Lean server (loads Mathlib once)...")
            server = LeanServer().start()
        elif args.lean_per_node:
            # --lean-per-node REQUIRES a Lean toolchain: every leaf/AND-composition is Lean-verified,
            # so a missing REPL is a hard error (not a graceful fall-back to per-call lean). Print a
            # clear message and exit cleanly — never crash.
            print("ERROR: --lean-per-node requires the Lean server (Mathlib REPL), but it is not "
                  "built. Run `lake build repl` in the Mathlib project, then retry.", file=sys.stderr)
            return 2
        else:
            print("# (--server requested but Lean REPL not built; using per-call lean)")
    # Faithfulness FAILS CLOSED: a Layer-4 result can only be "authoritative" if a faithfulness panel
    # actually ran and passed. So for the certification modes (--terminal-gate / --formalize) the panel
    # is ON BY DEFAULT; --no-faithfulness opts out (the run is then audited-only, never authoritative).
    certifying = args.terminal_gate or args.formalize
    want_faith = args.faithfulness or (certifying and not args.no_faithfulness)
    faith = None
    if want_faith:
        from agent.tools.codex_prover import CodexFaithfulnessChecker
        faith = CodexFaithfulnessChecker(cfg)
    elif certifying:
        print("# (--no-faithfulness: Layer-4 audit only; result will NOT be authoritative)")
    # (retriever was built above, before the --evolve block, so evolve can retrieval-seed its islands.)

    cert_authoritative = None   # set in certifying modes once a Layer-4 certification actually runs
    if args.direct:
        res = RalphLoop(prover, toolkit=toolkit, budget=budget, trace=trace,
                        max_episodes=args.episodes).run(args.goal)
        ok = res.success
        # Goal<->claim binding: a prover may return a clean, self-consistent ledger that proves a
        # DIFFERENT statement than the one requested. Bind BOTH the top-level claim AND the terminal
        # conclusion step to args.goal (mirroring DagDriver), so a ledger whose claim==goal but whose
        # conclusion proves a fresh unrelated statement is not reported PROVEN for this goal.
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
        if ok and res.ledger and args.out:
            Path(str(args.out) + ".ledger.json").write_text(res.ledger, encoding="utf-8")
        elif ok and res.ledger:
            print("\n--- ledger ---\n" + res.ledger)
    else:
        if getattr(args, "profile", None):
            # PROFILE-DRIVEN path (W6): delegate construction to the builder — the single
            # RunProfile -> supervisor -> registry -> DagDriver site. The supervisor fails CLOSED
            # before any model/Lean call if the loaded profile is inadmissible.
            from agent.orchestrator.builder import build_driver
            prof = effective_profile(args)
            print(f"# profile: {args.profile} (hash {prof.profile_hash[:12]}, "
                  f"elementarity={prof.elementarity.value})")
            driver = build_driver(prof, toolkit=toolkit, trace=trace)
        else:
            terminal_gate = None
            if args.terminal_gate:
                from agent.orchestrator.formalize_bridge import make_terminal_gate
                from agent.orchestrator.registry import Deps, resolve
                from agent.orchestrator.run_profile import Role
                # RESOLVE the terminal-gate formalizer via the registry from the SAME effective profile
                # (so --formalizer-model=claude, which maps to the profile's formalizer RoleSpec in
                # _codex_roles, is honored: it resolves ClaudeFormalizer). For the default legacy codex
                # profile this resolves CodexFormalizer(toolkit, codex-cfg) — byte-identical to before.
                formalizer = resolve(Role.formalizer, profile.roles.formalizer, Deps(toolkit=toolkit))
                terminal_gate = make_terminal_gate(formalizer, toolkit,
                                                   faithfulness_checker=faith, server=server,
                                                   retriever=retriever, repair_iters=args.repair)
            # Codex-backed search/revision machinery (population Elo+BT+PUCT; Autoreason refiner).
            comparator = CodexComparator(cfg) if args.population else None
            refiner = (make_codex_refiner(cfg, n_judges=args.judges, budget=budget, trace=trace)
                       if args.refine else None)
            # OpenEvolve fallback decomposer (fires ONLY on stuck nodes; commits only goal-bound,
            # obligation-discharged blueprints). Graceful no-op if openevolve is unavailable.
            evolve_fallback = None
            if args.evolve_fallback:
                from agent.tools.openevolve_bridge import OpenEvolveBackend
                from agent.tools.claude_cli import ClaudeConfig
                if OpenEvolveBackend.available():
                    evolve_fallback = OpenEvolveBackend(
                        toolkit, ClaudeConfig(timeout_s=args.timeout),
                        generations=args.evolve_fallback, retriever=retriever)
                else:
                    print("# (--evolve-fallback requested but openevolve not installed; "
                          "pip install mathagent[evolve] — fallback disabled)")
            # Single argparse->DagDriver construction site (factored into build_dag_driver so the wiring
            # is testable). It builds the per-node Lean verifiers from --lean-per-node and threads
            # lean_strict + the warm Lean server in; without --lean-per-node the driver is byte-identical.
            args._retriever = retriever
            if args.lean_per_node:
                print(f"# --lean-per-node: Lean-verifying every leaf + AND-composition "
                      f"(formalizer={args.formalizer_model}, strict={bool(args.lean_strict)})")
            driver = build_dag_driver(
                args, profile=profile, prover=prover, toolkit=toolkit, cfg=cfg, budget=budget,
                trace=trace, server=server, terminal_gate=terminal_gate, comparator=comparator,
                refiner=refiner, evolve_fallback=evolve_fallback,
            )
        res = driver.run(args.goal)
        # In the --profile path the builder constructed its OWN budget from the profile; point the
        # final "calls spent" report at the budget the driver actually spent (not main's unused one).
        if getattr(args, "profile", None):
            budget = res.budget
        ok = res.proven
        print(f"result: {'PROVEN' if ok else 'NOT PROVEN'}")
        print(f"dag: {res.dag.stats()}")
        import json as _json
        print("\n--- proof tree ---\n" + _json.dumps(res.proof_tree(), indent=2))
        # Report per-node Lean states: PROVEN (soft) vs the first-class LEAN_VERIFIED hard-success state.
        if args.lean_per_node:
            from agent.orchestrator.state import NodeState as _NodeState
            lean_nodes = sum(1 for n in res.dag.nodes.values()
                             if n.state is _NodeState.LEAN_VERIFIED)
            soft_nodes = sum(1 for n in res.dag.nodes.values()
                             if n.state is _NodeState.PROVEN)
            print(f"node states: proven={soft_nodes} lean_verified={lean_nodes}")
        if res.terminal is not None:
            print("\nterminal Layer-4 gate:", res.terminal.summary())
            print("authoritative_elementary:", res.authoritative_elementary)
            cert_authoritative = res.authoritative_elementary

    # Optional: close the loop — formalize the proven ledger to Lean and run the Layer-4 audit.
    winning_ledger = res.ledger if (args.direct and ok and res.ledger) else None
    if args.formalize and winning_ledger:
        from agent.tools.formalizer import CodexFormalizer
        from agent.orchestrator.formalize_bridge import formalize_and_audit
        print("\n# Formalizing the ledger to Lean and running the Layer-4 audit...")
        fa = formalize_and_audit(winning_ledger, CodexFormalizer(toolkit, cfg), toolkit=toolkit,
                                 informal_claim=args.goal,
                                 faithfulness_checker=faith, server=server,
                                 retriever=retriever, repair_iters=args.repair)
        print("formalize + audit:", fa.summary())
        print("authoritative_elementary:", fa.authoritative)
        cert_authoritative = fa.authoritative
        if fa.lean_source:
            print("\n--- formalized Lean ---\n" + fa.lean_source)
    elif args.formalize:
        print("\n(--formalize is supported in --direct mode on a proven ledger)")

    if server is not None:
        server.close()

    print(f"\ncalls spent: {budget.calls_spent}/{budget.max_llm_calls}")
    # CATEGORICAL user-facing status (P3): the graded search fitness stays INTERNAL; the user sees only
    # a category from the certification ladder, so a search score never leaks into certification language.
    status = report_status(
        proven=ok,
        formalized=bool(certifying and cert_authoritative is not None),
        authoritative_elementary=bool(cert_authoritative),
    )
    print(f"status: {status.label}")
    if args.out:
        trace.write_jsonl(str(args.out) + ".trace.jsonl")
    print("trace events: " + ", ".join(f"{k}={len(trace.by_kind(k))}"
          for k in ["ralph_episode", "decompose", "review", "prove_node", "cache_hit", "final"]))
    # In certifying modes the exit code reflects CERTIFICATION (authoritative_elementary), not just the
    # informal PROVEN verdict, so automation keyed on the exit code is not misled.
    success = ok
    if certifying and cert_authoritative is not None:
        success = ok and cert_authoritative
        if ok and not cert_authoritative:
            print("\n# NOTE: informally PROVEN but NOT authoritative_elementary; exiting non-zero "
                  "(certifying mode). Use --no-faithfulness for an explicitly audited-only run.")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
