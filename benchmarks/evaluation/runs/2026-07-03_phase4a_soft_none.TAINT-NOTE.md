# Taint note — 2026-07-03_phase4a_soft_none.jsonl

This sweep ran on code with two since-fixed liveness defects, discovered BY this sweep:

1. **Context-injection broke goal-binding.** `run_problems.goal_for` appended the per-problem
   citable-inputs clause into the goal string, so the goal's hash included the clause while the
   prover's conclusion restated only the mathematics → the adversarial verifier refuted on
   `goal_binding` → terminal `FAILED_ELEMENTARY` at 1 call. Affects every `injected_context=true`
   row (hardy_wright ×2, ljunggren ×2, triangular ×2).
2. **Unbound NEEDS_REVIEW ledgers had no feedback retry.** Ralph's review_unhandled return fired
   before its per-episode goal-binding check, so a paraphrased NEEDS_REVIEW ledger terminated
   without repair. May additionally depress any row.

Rows that remain interpretable: the non-injected runs (imo_1988, mordell, x2_plus_1). Notably
`x2_plus_1_eq_y3 × default` = `soft_proven` in 2 calls (a genuine result), while
`x2_plus_1_eq_y3 × solution-only` failed in 7 calls (search variance).

Both defects fixed and regression-tested (see git history 2026-07-03); post-fix live smoke:
hardy_wright × default = `soft_proven`, 5 calls, 2 nodes. A clean replacement sweep covering all
nine runnable problems supersedes this file — treat these rows as evidence about the HARNESS BUGS,
not about the problems or profiles.
