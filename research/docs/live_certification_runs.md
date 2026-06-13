# Live proof-certification runs (2026-06-13)

First live end-to-end runs of the MathAgent **certification pipeline** — the harness's actual thesis,
the thing a final-answer benchmark cannot measure: not "is the answer right" but "**is the proof
certifiably elementary**." Pipeline: Codex GPT-5.5-xHigh prover → deterministic gate → Codex formalize
→ compile on the persistent Mathlib server → **Layer-4 transitive dependency + axiom audit** →
adversarial faithfulness panel → `authoritative_elementary`.

Command shape:

```sh
python scripts/prove.py "<theorem>" --terminal-gate --server --faithfulness --retrieval --repair 3 \
  --model gpt-5.5 --effort xhigh
```

| # | Theorem (informal) | Proven | Formalize + compile | Layer-4 audit | Faithfulness | **authoritative_elementary** | Codex calls |
|---|---|---|---|---|---|---|---|
| T1 | For every integer `n`, `n² ≡ 0 or 1 (mod 4)` | ✅ direct | ✅ | **PASS** (0 rejects) | 4/4 | **TRUE** | 1 |
| T2 | `∀ integers a,b: 3 ∣ a²+b² ⇒ 3∣a ∧ 3∣b` | ✅ direct | ❌ compile failed | — | — | **FALSE** | 1 |
| T3 | No positive integers `x,y` with `x² = 2y²` (√2 irrational, **infinite descent**) | ✅ direct | ✅ | **PASS** (0 rejects) | 4/4 | **TRUE** | 2 |

**Result: 2/3 certified authoritative-elementary** (T1, T3) — including the infinite-descent case
[`PLAN.md`](../../agent/PLAN.md) §7 flagged as the formalization *hard case*. T2 hit the
autoformalization wall, and the harness reported it **honestly** (`authoritative=False`) instead of
false-positiving.

## T2 — concrete gap-#1 data

The formalizer produced a `decide`-based proof that case-split over `Fin`/`ZMod`-indexed terms; Lean
could not synthesize the required `Decidable` instance for goals shaped `… = 0 → … = 0 ∧ … = 0`, so it
**failed to compile**, and the 3-iteration repair loop did not recover it (the error feedback didn't
steer it off `decide`). **Fix direction:** bias the formalizer / repair prompts toward
`omega` / `Int.ModEq` / explicit `ZMod 3` residue case-analysis instead of `decide` on
not-as-written-decidable goals, and add a "prefer `omega` over `decide` on a `failed to synthesize
Decidable` error" repair hint. This is a prompt/repair improvement, not an architectural gap.

## What this validates

- **The core thesis works end-to-end:** an autonomously-produced NT proof is formalized, compiled
  against Mathlib, **audited as elementary** (proof-term dependency closure clean; axioms ⊆
  `{propext, Classical.choice, Quot.sound}`, so no `sorry` slips through), and the Lean statement
  verified faithful — all authoritative.
- **The audit is discriminating in the right direction:** T2's non-compiling formalization is *not*
  certified. Certification requires a genuine, compiling, dependency-clean proof — exactly the
  non-gameable property that makes "elementary" mean something.
- **Descent is not the wall here:** T3 (infinite descent) formalized cleanly; the wall was a brittle
  *tactic choice* on an easier statement (T2), which is a fixable prompt/repair issue.

> Reproducibility: Lean 4.30.0 + Mathlib v4.30.0 via the persistent REPL server. Runs were one-shot at
> xHigh; the `prove.py` trace JSONL for each is under the run's `--out` prefix.
