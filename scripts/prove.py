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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.gates.toolkit import load_toolkit
from agent.orchestrator import Budget, RunTrace, RalphLoop, DagDriver
from agent.tools.codex_prover import CodexProver, CodexDecomposer, CodexReviewer, CodexConfig


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
    args = ap.parse_args()

    if not CodexProver.available():
        print("ERROR: codex CLI not found on PATH.", file=sys.stderr)
        return 2

    toolkit = load_toolkit()
    cfg = CodexConfig(model=args.model, reasoning_effort=args.effort, timeout_s=args.timeout)
    budget = Budget(max_llm_calls=args.budget)
    trace = RunTrace("codex-prove")
    prover = CodexProver(toolkit, cfg)

    print(f"# Proving (model={args.model}, effort={args.effort}, mode={'direct' if args.direct else 'dag'}):")
    print(f"  {args.goal}\n")

    if args.direct:
        res = RalphLoop(prover, toolkit=toolkit, budget=budget, trace=trace,
                        max_episodes=args.episodes).run(args.goal)
        ok = res.success
        print(f"result: {'PROVEN' if ok else 'NOT PROVEN'}  (episodes={res.episodes})")
        if res.report:
            print(f"gate: {res.report.summary()}")
        if ok and res.ledger and args.out:
            Path(str(args.out) + ".ledger.json").write_text(res.ledger, encoding="utf-8")
        elif ok and res.ledger:
            print("\n--- ledger ---\n" + res.ledger)
    else:
        driver = DagDriver(
            prover,
            decomposer=CodexDecomposer(toolkit, cfg),
            reviewer=CodexReviewer(toolkit, cfg),
            toolkit=toolkit, budget=budget, trace=trace,
            max_depth=args.max_depth, max_decomp_attempts=args.max_decomp,
            ralph_episodes=args.episodes,
        )
        res = driver.run(args.goal)
        ok = res.proven
        print(f"result: {'PROVEN' if ok else 'NOT PROVEN'}")
        print(f"dag: {res.dag.stats()}")
        import json as _json
        print("\n--- proof tree ---\n" + _json.dumps(res.proof_tree(), indent=2))

    # Optional: close the loop — formalize the proven ledger to Lean and run the Layer-4 audit.
    winning_ledger = res.ledger if (args.direct and ok and res.ledger) else None
    if args.formalize and winning_ledger:
        from agent.tools.formalizer import CodexFormalizer
        from agent.orchestrator.formalize_bridge import formalize_and_audit
        print("\n# Formalizing the ledger to Lean and running the Layer-4 audit...")
        fa = formalize_and_audit(winning_ledger, CodexFormalizer(toolkit, cfg), toolkit=toolkit)
        print("formalize + audit:", fa.summary())
        print("authoritative_elementary:", fa.elementary_verified)
        if fa.lean_source:
            print("\n--- formalized Lean ---\n" + fa.lean_source)
    elif args.formalize:
        print("\n(--formalize is supported in --direct mode on a proven ledger)")

    print(f"\ncalls spent: {budget.calls_spent}/{budget.max_llm_calls}")
    if args.out:
        trace.write_jsonl(str(args.out) + ".trace.jsonl")
    print("trace events: " + ", ".join(f"{k}={len(trace.by_kind(k))}"
          for k in ["ralph_episode", "decompose", "review", "prove_node", "cache_hit", "final"]))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
