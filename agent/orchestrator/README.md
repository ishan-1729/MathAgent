# agent/orchestrator/

The control layer that coordinates the swarm and owns the shared proof state.

The harness is **staged** (see `../PLAN.md` §4). The Phase-1 `FlatDriver` remains implemented for its
standalone protocol/test path. The profile builder now constructs `DagDriver` in both modes;
`mode=direct` selects that driver's direct-only path rather than selecting `FlatDriver`.

**Phase 1 — flat sequential driver (no DAG engine):**
- A linear `plan → prove → critique → gate` loop (sub-lemmas are an ordered list, not a DAG).
- **Liveness model** — per-node state machine `{open, in_progress, proven, failed-elementary, failed-gap,
  exhausted}`, hard caps on repair iters / re-plan depth / per-problem budget, timeout+cancel on every call.
- **Observability** — append-only JSONL run trace (`../PLAN.md` §4.4) rendered into
  [`../../benchmarks/evaluation/run_record_TEMPLATE.md`](../../benchmarks/evaluation/run_record_TEMPLATE.md).
- Execution is currently **serial**. `ProofDAG` and `Budget` are mutable shared state; fanout/width logic
  partitions deterministic serial waves and does not claim concurrent model calls.

**Phase 2 — promoted, gated on measured reuse:**
- **AND-OR proof DAG** (LEAP-style) with memoized, reusable proven nodes + acyclicity guard.
- **Goal cache** keyed by a full SHA-256 of the statement after NFC + whitespace normalization only;
  notation/synonym guessing is deliberately excluded from proof identity. A separate full-SHA-256
  proof-context receipt prevents reuse across permission, gate, proof-judge, reviewer, enforcement, or
  Lean-verifier policy changes.
- **Revision control** — incumbent tournament (Autoreason): "do nothing" first-class; failure-analysis
  before revision; k=2 stop. It is disabled without Layer-2 proof review; each challenger needs
  `no_gaps=true` from every proof judge plus deterministic admissibility and any configured budgeted Lean
  verifier. Displacement is still not an absolute no-regression guarantee when the panel itself is fallible.
- **Evaluation cascade** — cheap structural/numeric checks before expensive judge/Lean passes.

## Profile control and review semantics

`RunProfile` is the declarative control plane:

```text
RunProfile -> validate_profile (fail closed) -> build_driver -> registry.resolve -> DagDriver
```

The supervisor validates only roles and capabilities that the effective wiring will use. A direct profile
may still be `elementarity=authoritative`; it must disable the DAG-only decomposition, population,
refinement, and evolve-fallback stages, and the builder still attaches the terminal Layer-4 gate. The CLI's
`--formalize` convenience flag is narrower: it requires direct mode and prints the resulting Lean source;
`--terminal-gate` is the certification switch for either execution mode.

`stages.review` controls two different Layer-2 components. In DAG mode it wires both the decomposition
reviewer and the full-ledger judge used by each Ralph direct attempt. In direct mode only the full-ledger
judge applies. Consequently the `no-review` ablation removes both checks; `stages.judges` is separate and
sets the optional refinement tournament's panel size.

The operative profile default `budgets.max_llm_calls=60` caps calls explicitly metered by the
orchestrator: prover, full-ledger judge, decomposer, decomposition reviewer, pairwise comparator, refiner,
and one evolve-fallback invocation. It is not an all-subprocess or all-provider-call ceiling. Terminal
formalization/repair/faithfulness calls have their own bounded loop and are reported separately as
`FormalizeAuditResult.model_calls`. OpenEvolve is bounded and reported by the applicable stage iteration
count (`evolve`, `evolve_witness`, or `evolve_fallback`), with `ensemble.timeout_s` bounding each ensemble
subprocess; its internal generations do not consume the orchestration counter. Per-node Lean verification
uses `max_node_verify_calls` when configured. `max_replan_depth` is the global re-plan cap and is distinct
from the per-node `max_decomp_attempts` cap.

The evolution controls are first-class profile fields, not unsupervised CLI side paths. Proof-ledger
pre-search offers its champion as an ordinary first prover candidate, witness evolution is diagnostic, and
the fallback is a last-resort decomposer; all remain subject to the normal proof gates. The supervisor
requires the OpenEvolve package and declared Claude ensemble transport whenever one is enabled. Role-level
`effort` is Codex-only and is rejected for any role whose declared provider chain can select Claude.

Status: **Phase-1 and the described Phase-2 machinery are implemented.**
- `state.py` (state machine + budgets), `trace.py` (JSONL run trace + run-record rendering),
  `driver.py` (the flat sequential `FlatDriver` + `Prover`/`Judge` protocols + scripted stubs).
- `dag.py` (LEAP AND-OR DAG + **deep-hash goal cache / memoization** + acyclicity guard),
  `ralph.py` (the AlphaProof_Nexus per-node Ralph loop), and `dag_driver.py` (the
  direct→decompose→review→recurse `DagDriver` with DFS/backtracking).
- `RolesProfile` and the shipped YAML profiles default the prover role to **Claude/opus**; the registry
  resolves that declared spec. **Codex / GPT-5.5-xHigh**
  ([`../tools/codex_prover.py`](../tools/codex_prover.py)) is selectable per role, and the bare legacy CLI
  constructs Codex role specs. See [`../../research/docs/codex_harness.md`](../../research/docs/codex_harness.md).
- Also built and wired (no longer design-only): the incumbent tournament (`tournament.py`, the
  `refiner` role), retrieval, and the population/Elo search layer
  (`dag_driver._prove_via_population` + `population.py`, enabled by `stages.population > 0`).
