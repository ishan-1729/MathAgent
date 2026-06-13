# agent/roles/

System prompts for the agents in the swarm. Each role is a separate prompt file (one responsibility each),
so they can be versioned and A/B-tested independently. Keep them concise; load shared rules from
`agent/instructions/` rather than duplicating.

Planned roles (see `../PLAN.md` §4.2):

| Role | Job | Key inputs |
| --- | --- | --- |
| **Planner / Blueprinter** | Turn the problem into an informal strategy and an AND-OR sub-lemma DAG. | problem statement, `knowledge/methods/`, `knowledge/library/` |
| **Prover** | Produce an elementary proof (or sub-proof) for one node. | one DAG node, retrieved methods/identities |
| **Critic / Skeptic** | Adversarially attack a candidate proof for gaps and hidden non-elementary steps. | a candidate proof |
| **Elementary Judge** | Soft pre-commit gate: is every step within the allowed toolkit? Prune violators before they enter the DAG. | a candidate step/decomposition + `agent/instructions/elementary_proof_rules.md` |
| **Lean Liaison** | (advisory in v1) Draft Lean statements/sketches, read diagnostics, run the dependency audit. | informal proof, Lean tooling |
| **Evaluator** | Score the run against `benchmarks/evaluation/metrics.md`; write the run record. | final artifact |

> The Elementary Judge is a **soft** lever (ranking/pruning) — it is *not* the authoritative gate. The
> authoritative gate is the deterministic auditor in `agent/gates/`. See `../PLAN.md` §5.
