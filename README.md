# MathAgent

MathAgent is a research repository for an **agentic system that solves mathematics problems**. v1 focuses on
number theory, with elementary methods as the default objective. `elementarity=soft` rejects known
non-elementary proof paths during search, while `elementarity=authoritative` additionally requires the
terminal Lean certificate. The explicit `elementarity=none` control admits sound non-elementary proofs for
solution-only measurements; it does not relax logical soundness checks.

Whether a run may call a proof **certified elementary** is decided by an authoritative **Layer-4 Lean
audit** — a proof-term dependency + axiom audit against Lean/Mathlib — followed by statement-faithfulness
checking through trusted production components (`agent/gates/lean_audit.py`,
`agent/gates/lean/Audit.lean`, `agent/orchestrator/formalize_bridge.py`). Passing the informal gate alone is
only `soft_proven`, never a certificate.

It is a collaboration between Ishan and Kieren, with room for multiple AI workflows to explore separate branches.

The build plan for the agent is **[`agent/PLAN.md`](agent/PLAN.md)** — read it first. For the
(slowly-changing) architecture, see **[`research/docs/system_design.md`](research/docs/system_design.md)**;
for the dated build evidence and current implementation addendum, see
**[`research/docs/build_status.md`](research/docs/build_status.md)**.

## Repository map

The repo is grouped into role-based top-level categories:

| Category | Contents | Purpose |
| --- | --- | --- |
| **`agent/`** | `orchestrator/`, `roles/`, `gates/`, `tools/`, `instructions/`, `workflows/` | The agentic system: control loop, swarm prompts, the **elementary-constraint gates**, tools, rules, and run configurations. |
| **`knowledge/`** | `methods/`, `library/`, `examples/` | Human-curated proof techniques, identities, and demonstrations. The current live adapters do not automatically inject these files into prompts. |
| **`benchmarks/`** | `problems/`, `evaluation/` | What the agent is tested on: target theorems and the scoring rubric / metrics. |
| **`formal/`** | `lean/` | Lean/Mathlib environment used by the **authoritative Layer-4** elementarity audit. |
| **`research/`** | `papers/`, `docs/` | Hand-curated systems literature and project design docs (incl. the [literature synthesis](research/docs/literature_design_implications.md)). |
| **`scripts/`**, **`.agents/`** | skeleton check; agent skills | Infrastructure and skill discovery. |

Examples and problems are intentionally separated. Examples may contain solved demonstrations and method
skeletons. Problem folders should usually avoid full standard proofs, so workflow evaluations are not
contaminated by copied solutions. The `research/papers/` folder is architecture inspiration for workflow and
gate design — it should not silently expand the mathematical facts available to a problem run.

## Validate the repository

The harness, profiles, gates, benchmark adapters, and Lean bridge are implemented. Validate the current
tree rather than relying on a copied test count:

```sh
make check
make test
make demo
```

The offline suite uses scripted model/Lean doubles; live provider and Lean tests remain opt-in. See
[`research/docs/system_design.md`](research/docs/system_design.md) for the execution and trust model.
