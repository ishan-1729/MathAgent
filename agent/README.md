# agent/

This directory is the **agentic system itself** — the harness that turns frontier LLMs into an
elementary number-theory prover. It was previously implicit (only protocol *descriptions* in
`workflows/` and rules in `instructions/` existed); this area collects the runnable/spec'd harness.

The master design is **[`PLAN.md`](PLAN.md)**. Read it first.

## Layout

| Subdir | Role |
| --- | --- |
| `orchestrator/` | Control loop, swarm topology, the AND-OR proof DAG, scheduling, memoization, budgets. |
| `roles/` | Versioned prompt files for the two v1 roles; additional role prompts are constructed in `tools/*.py`. |
| `gates/` | **The defining mechanism:** deterministic ledger checks, soft review signals, conservative Lean-source validation, and the authoritative proof-term dependency/axiom audit. A full elaborated-AST legality pass and restricted-import environment remain future defenses. |
| `tools/` | Adapters the agent can call: Lean bridge, CAS/numeric search, dependency auditor, and curated-Mathlib retrieval. |
| `instructions/` | Human-maintained policy references. They are not implicitly loaded into model prompts. |
| `workflows/` | Human-readable experiment specifications. They are not executable `RunProfile` files and are not automatically loaded by the harness. |

## Design pillars (from the literature review)

1. **Training-free first.** Orchestrate frontier general LLMs; add trained models only if cost forces it.
2. **The elementary constraint is the product.** Soft and authoritative profiles reject known
   non-elementary paths during search; only the terminal Layer-4 audit can certify the result. The
   solution-only profile disables the elementarity objective without disabling logical soundness.
3. **Lean Layer 4 is authoritative for certification.** "Lean-verified ≠ elementary" — compilation alone
   certifies nothing about method admissibility, so authority requires the proof-term dependency/axiom
   audit and statement-faithfulness gate through explicitly trusted production components, not merely
   "it compiled."

See [`../research/docs/literature_design_implications.md`](../research/docs/literature_design_implications.md)
for the evidence behind these pillars.
