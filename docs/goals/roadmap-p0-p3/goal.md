# Goal: Implement the report roadmaps (P0–P3) and verify adversarially

## What the user asked for
"Start building the intended structures in the live reports completely (ALL the P0, P1 etc.)… deeply,
thoroughly, carefully and properly, and at the end, verify it adversarially." With slash-goal-loops +
multiple Opus 4.8 workers.

## Interpreted outcome
Implement, in MathAgent, **every prioritized structure** specified by the two live reports —
`research/docs/forge_relevance_study.md` §7 (orchestration hardening) and
`research/docs/openevolve_stacking_brief.md` §9 (OpenEvolve fitness hardening) — keeping the offline
test suite green and the soundness invariants intact, then prove it survives an adversarial re-attack.

## Input shape
**existing_plan** — the two reports are the spec (preserve as facts, validate, then implement in
prioritized order). Authority: **approved** ("start building").

## The goal oracle (the live signal that the outcome is true)
1. **Full offline suite green** (`python -m pytest -q` — baseline 398 passed, 8 skipped) AND green after
   every slice.
2. **New deterministic invariants pass:** the node-FSM **enum-cartesian totality proptest**; "a
   NEEDS_REVIEW-no-judge node decomposes before EXHAUSTED"; terminal states absorb all events; a stale
   `proof_context_hash` cannot satisfy a new context; **the Ljunggren reward-hacking trap does NOT raise
   evolutionary fitness**; OpenEvolve `combined_score` is **zeroed** by failed goal-binding/vacuity.
3. **Adversarial re-attack** (independent, different-model-family refuter — Codex GPT-5.5-xHigh — plus
   Opus skeptics) finds **no** way to (a) make a NEEDS_REVIEW node give up without trying decomposition,
   (b) reuse a stale `PROVEN` after a ruleset change, (c) raise fitness by relabeling/weakening/hiding
   obligations, or (d) execute evolved/model text.

`full_outcome_complete: true` requires all three, with receipts.

## Non-negotiable constraints
- Training-free elementary-NT harness; **only the Layer-4 Lean audit certifies "elementary"**; the
  deterministic gate **fails closed**.
- **NEVER `exec`/`eval`/`import` evolved or model output** (we removed RCEs); OpenEvolve evolves ledger
  **text**, scored by the gate, no code execution.
- Python has no exhaustiveness checker → enforce FSM totality via **enum-cartesian proptests**.
- **Correctness before throughput:** P0/P1 (suspending fix, total FSM, split-hash memo, adversarial
  verifier, hard fitness gate, numeric reward) land and verify **before** P2/P3 (Dilworth/H⁰, event-driven
  scheduling, model routing, Elo-over-elites, AutoReason one-way).

## Likely misfire to avoid
"Succeeding at the wrong thing": adding parallelism/diversity machinery (P2/P3) on top of a still-gameable
fitness or a still-broken give-up path (P0/P1), or refactoring without the proptests that *prove* the
invariants. Parallelism makes a correct scheduler faster; it does not make an incorrect one safe.

## Execution discipline
Multiple **Opus 4.8** workers in parallel where file scopes are **disjoint** (e.g. the OpenEvolve fitness
package and the orchestrator-FSM package touch different files → run concurrently). Verify each slice
against the oracle before advancing. Adversarial audit (Codex refuter + Opus skeptics + full suite) at
the tranche boundary.

## Tranche
Land **P0+P1 (both roadmaps), then P2, then P3**, verifying each, then the adversarial audit. Keep
advancing until all three oracle conditions hold.
