# Ablation profiles

One `RunProfile` YAML per ablation arm. Each is a **single-axis perturbation** of a base profile
(`profiles/default.yaml` — `elementarity=soft`, Claude roles — unless noted), so a sweep isolates the
contribution of one knob. Every file here parses via `RunProfile.from_yaml` and passes
`supervisor.validate_profile` in this environment (Codex CLI, Claude CLI, `openevolve`, and a built Lean
REPL are all present, so the provider/evolve/Lean guards clear).

Run a sweep with `scripts/ablate.py`. The elementarity axis itself (`none` / `soft` / `authoritative`)
is covered by the top-level `profiles/solution-only.yaml`, `profiles/default.yaml`, and
`profiles/authoritative.yaml` — not duplicated here.

## Note on the mixed-provider evolve ensemble (why there is no `ensemble-codex-breadth.yaml`)

The OpenEvolve breadth/depth ensemble is **Claude-CLI-only**. Its picklable model factory
(`agent/tools/openevolve_bridge.py :: _ClaudeLLMFactory`) builds a `ClaudeEvolveLLM` that shells the
headless `claude` CLI (`agent.tools.claude_cli._run_claude`) with the ensemble's `breadth_model` /
`depth_model` names bound as **Claude** model ids. There is **no Codex/GPT factory** on that path, so an
`ensemble.breadth_model: gpt-5.5` would be handed to the `claude` CLI as an unknown model — a broken
profile, not a mixed-provider run. (The supervisor's S11 only checks the weights/names are well-formed;
it does not — and cannot cheaply — verify the model name resolves on the ensemble backend.)

So the mixed-provider comparison is run at the **role level** instead, where the registry genuinely
supports per-role providers: `ensemble-roles-codex-prover.yaml` keeps the default base and routes only
`roles.prover` to Codex/`gpt-5.5`. If a Codex (GPT) evolve factory is added later, a true
`ensemble-codex-breadth.yaml` can join the ensemble axis; until then the evolve-ensemble arms
(`ensemble-breadth-only` / `-depth-only` / `-50-50`) stay Claude-only (Sonnet breadth / Opus depth).

## Axis map

| Profile | Base | Axis / change | Paper evidence | Expected measurement |
|---|---|---|---|---|
| `no-decompose.yaml` | default | `decompose=false` (decomposer=None) | LEAP decompose-vs-direct | Value of blueprint decomposition vs direct proving |
| `no-review.yaml` | default | `review=false` (reviewer=None) | Reviewer/critic loop | Value of soft-reviewing decomposition sketches |
| `no-h0.yaml` | default | `h0_consistency=false` | H⁰ sibling-consistency gate | Contribution of the 0-cocycle child-consistency check at AND-nodes |
| `no-refine.yaml` | default | `refine=false` (= default) | AutoReason k=2 | Refine-OFF control; pair with `refine-on` |
| `refine-on.yaml` | default | `refine=true` (refiner=Opus) | AutoReason k=2 revision tournament | Lift from the AutoReason incumbent tournament (ON arm) |
| `no-memo.yaml` | default | `memo=false` (no goal-cache reuse) | LEAP DAG-vs-tree, Advanced NT 66.6→100 | Contribution of split-keyed memo / DAG subgoal reuse |
| `population-3.yaml` | default | `population=3` | AlphaProof Nexus best-of-K, K∈{1,3,6,10} | Low-breadth point on the best-of-K curve |
| `population-10.yaml` | default | `population=10` | AlphaProof Nexus best-of-K, K∈{1,3,6,10} | High-breadth (diminishing-returns) point on the curve |
| `evolve-fallback-20.yaml` | default | `evolve_fallback=20` | OpenEvolve MAP-Elites fallback, bridge default iters | Value of the OpenEvolve ledger-search fallback decomposer |
| `ensemble-breadth-only.yaml` | default + evolve=20 | ensemble Sonnet 1.0 / 0.0 | AlphaProof: cheap-only solved zero | Failure signal of a pure-breadth (cheap-only) evolve search |
| `ensemble-depth-only.yaml` | default + evolve=20 | ensemble Opus 0.0 / 1.0 | AlphaProof cheap-only warning (depth counterpart) | Cost/quality ceiling of an all-strong-model evolve search |
| `ensemble-50-50.yaml` | default + evolve=20 | ensemble Sonnet 0.5 / Opus 0.5 | AlphaEvolve tunable breadth/depth mix | Effect of shifting weight to depth past the 80/20 default |
| `ensemble-roles-codex-prover.yaml` | default | `roles.prover=codex/gpt-5.5` | AlphaProof heterogeneous-prover comparison | Effect of a different-provider prover (role-level mixed provider) |
| `authoritative-full.yaml` | authoritative | `+population=3 +refine=true`, per-node+terminal Lean | AlphaProof best-of-K + AutoReason + Layer-4 | Upper-bound: does certified-elementary reach improve with every knob on |

### Elementarity axis (top-level, not duplicated here)

| Profile | Elementarity | Measurement |
|---|---|---|
| `profiles/solution-only.yaml` | `none` | Reach when elementarity is NOT enforced (non-elementary proofs admitted) |
| `profiles/default.yaml` | `soft` | Reach with in-engine elementarity enforcement, no terminal Lean |
| `profiles/authoritative.yaml` | `authoritative` | Certified-elementary reach (terminal Layer-4 + per-node Lean) |
