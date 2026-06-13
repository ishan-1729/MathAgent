# MathAgent

MathAgent is a research repository for an **agentic system that solves mathematics problems**. v1 focuses on
**number theory by elementary means only** (the IMO-usable toolkit), enforced as a hard project constraint.
Successful arguments are later formalized in Lean. It is a collaboration between Ishan and Kieren, with room
for multiple AI workflows to explore separate branches.

The build plan for the agent is **[`agent/PLAN.md`](agent/PLAN.md)** — read it first. For the
(slowly-changing) architecture, see **[`research/docs/system_design.md`](research/docs/system_design.md)**;
for what is actually built and live-validated so far (timestamp-stamped), see
**[`research/docs/build_status.md`](research/docs/build_status.md)**.

## Repository map

The repo is grouped into role-based top-level categories:

| Category | Contents | Purpose |
| --- | --- | --- |
| **`agent/`** | `orchestrator/`, `roles/`, `gates/`, `tools/`, `instructions/`, `workflows/` | The agentic system: control loop, swarm prompts, the **elementary-constraint gates**, tools, rules, and run configurations. |
| **`knowledge/`** | `methods/`, `library/`, `examples/` | What the agent knows: reusable proof techniques, high-impact identities, and solved demonstrations. |
| **`benchmarks/`** | `problems/`, `evaluation/` | What the agent is tested on: target theorems and the scoring rubric / metrics. |
| **`formal/`** | `lean/` | Lean formalization area (advisory in v1). |
| **`research/`** | `papers/`, `docs/` | Hand-curated systems literature and project design docs (incl. the [literature synthesis](research/docs/literature_design_implications.md)). |
| **`scripts/`**, **`.agents/`** | skeleton check; agent skills | Infrastructure and skill discovery. |

Examples and problems are intentionally separated. Examples may contain solved demonstrations and method
skeletons. Problem folders should usually avoid full standard proofs, so workflow evaluations are not
contaminated by copied solutions. The `research/papers/` folder is architecture inspiration for workflow and
gate design — it should not silently expand the mathematical facts available to a problem run.

## First tasks

1. Read `agent/PLAN.md` and the literature synthesis in `research/docs/`.
2. Fill method files in `knowledge/methods/` and add high-impact Tagebuch identities.
3. Create target problem statements in `benchmarks/problems/`.
4. Build the elementary-constraint gate (`agent/gates/`) and the swarm role prompts (`agent/roles/`).
5. Run workflows on separate branches and score attempts with `benchmarks/evaluation/`.

Run the skeleton check with:

```sh
make check
```
