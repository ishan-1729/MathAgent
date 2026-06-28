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


def main() -> int:
    ap = argparse.ArgumentParser(description="Prove a number-theory goal with the Codex-backed harness.")
    ap.add_argument("goal", help="the goal/theorem statement to prove")
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
    args = ap.parse_args()

    if not CodexProver.available():
        print("ERROR: codex CLI not found on PATH.", file=sys.stderr)
        return 2

    toolkit = load_toolkit()
    cfg = CodexConfig(model=args.model, reasoning_effort=args.effort, timeout_s=args.timeout)
    budget = Budget(max_llm_calls=args.budget, max_replan_depth=args.max_replan)
    trace = RunTrace("codex-prove")
    prover = CodexProver(toolkit, cfg)

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
        terminal_gate = None
        if args.terminal_gate:
            from agent.tools.formalizer import CodexFormalizer
            from agent.orchestrator.formalize_bridge import make_terminal_gate
            terminal_gate = make_terminal_gate(CodexFormalizer(toolkit, cfg), toolkit,
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
        driver = DagDriver(
            prover,
            decomposer=CodexDecomposer(toolkit, cfg),
            reviewer=CodexReviewer(toolkit, cfg),
            toolkit=toolkit, budget=budget, trace=trace,
            max_depth=args.max_depth, max_decomp_attempts=args.max_decomp,
            ralph_episodes=args.episodes, terminal_gate=terminal_gate,
            comparator=comparator, population_k=args.population, refiner=refiner,
            evolve_fallback=evolve_fallback,
        )
        res = driver.run(args.goal)
        ok = res.proven
        print(f"result: {'PROVEN' if ok else 'NOT PROVEN'}")
        print(f"dag: {res.dag.stats()}")
        import json as _json
        print("\n--- proof tree ---\n" + _json.dumps(res.proof_tree(), indent=2))
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
