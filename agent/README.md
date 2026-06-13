# agent/

This directory is the **agentic system itself** — the harness that turns frontier LLMs into an
elementary number-theory prover. It was previously implicit (only protocol *descriptions* in
`workflows/` and rules in `instructions/` existed); this area collects the runnable/spec'd harness.

The master design is **[`PLAN.md`](PLAN.md)**. Read it first.

## Layout

| Subdir | Role |
| --- | --- |
| `orchestrator/` | Control loop, swarm topology, the AND-OR proof DAG, scheduling, memoization, budgets. |
| `roles/` | Per-agent system prompts for the swarm (Planner, Prover, Critic, Elementary Judge, Lean Liaison, Evaluator, …). |
| `gates/` | **The defining mechanism:** elementary-constraint enforcement — soft gates (judge/retrieval/scope) and hard gates (Lean dependency audit, AST legality, restricted environment). |
| `tools/` | Adapters the agent can call: Lean bridge, CAS/numeric search, the elementary dependency auditor, retrieval over `knowledge/`. |
| `instructions/` | Global rules the agent obeys (elementary-proof rules, disallowed methods, contamination, Lean output, proof-attempt protocol). |
| `workflows/` | Named run configurations (context exposure + search bias) used for branch experiments. |

## Design pillars (from the literature review)

1. **Training-free first.** Orchestrate frontier general LLMs; add trained models only if cost forces it.
2. **The elementary constraint is the product.** Enforced by a *dual gate*: soft pruning during search +
   a hard, deterministic accept/reject gate. See `gates/`.
3. **Lean is advisory in v1.** "Lean-verified ≠ elementary" — compilation alone certifies nothing about
   method admissibility, so the hard gate is a proof-term dependency audit, not "it compiled."

See `research/docs/literature_design_implications.md` for the evidence behind these pillars.
