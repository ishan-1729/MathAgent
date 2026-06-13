# agent/orchestrator/

The control layer that coordinates the swarm and owns the shared proof state.

The harness is **staged** (see `../PLAN.md` §4). Phase 1 ships a deliberately small slice; the heavier
machinery below is promoted in Phase 2 **only once measured sub-lemma reuse justifies it**.

**Phase 1 — flat sequential driver (no DAG engine):**
- A linear `plan → prove → critique → gate` loop (sub-lemmas are an ordered list, not a DAG).
- **Liveness model** — per-node state machine `{open, in_progress, proven, failed-elementary, failed-gap,
  exhausted}`, hard caps on repair iters / re-plan depth / per-problem budget, timeout+cancel on every call.
- **Observability** — append-only JSONL run trace (`../PLAN.md` §4.4) rendered into
  `benchmarks/evaluation/run_record_TEMPLATE.md`.
- **State API is parallel-ready** (immutable / compare-and-set node updates, idempotent attempts) so the
  Phase-2 swarm is a configuration change, not a rewrite.

**Phase 2 — promoted, gated on measured reuse:**
- **AND-OR proof DAG** (LEAP-style) with memoized, reusable proven nodes + acyclicity guard.
- **Goal cache** keyed by exact-canonical statement match.
- **Revision control** — incumbent tournament (Autoreason): "do nothing" first-class; failure-analysis before
  revision; k=2 stop — so a correct elementary proof is never churned into a broken/heavier one.
- **Evaluation cascade** — cheap structural/numeric checks before expensive judge/Lean passes.

Status: **Phase-1 implemented.** `state.py` (state machine + budgets), `trace.py` (JSONL run trace +
run-record rendering), and `driver.py` (the flat sequential `FlatDriver` + `Prover`/`Judge` protocols +
scripted stubs) are built and tested. The Phase-2 machinery above is still design-only.
