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

---

## 2026-06-27 — Per-node Lean verification (P0–P2), live-validated

Closing the LEAP "engine gap": LEAP verifies **every node** with the Lean compiler, whereas MathAgent
previously verified each node with the soft deterministic gate and ran Lean only as an *optional,
default-off, root-level* terminal pass (so a default run reported `PROVEN` with **zero** Lean). P0–P2
add an **opt-in per-node verifier** (`DagDriver(node_verifier=…)`, default `None` so the offline suite
is byte-identical): a LEAF resolves `lean_verified=True` only after its ledger is formalized, **compiled
against Mathlib**, and passes the Layer-4 axiom/denylist audit. Routing is fail-closed on a Lean *reject*
(→ `FAILED_ELEMENTARY`) and fail-open to soft `PROVEN` if formalization won't compile (gap #1 defense).

Generator/formalizer: **Claude (opus)** behind the prover-agnostic interfaces — `CodexProver`/
`CodexFormalizer` were quota-blocked (monthly cap, resets Jul 27), and the new `ClaudeFormalizer` is a
drop-in for the `Formalizer` protocol. Verifier = `make_node_gate(ClaudeFormalizer, server=warm LeanServer)`.

**Part 1 — per-node gate on a live ledger (formalize → compile → audit):**

| leaf | `elementary_verified` | `audit.passed` | axioms | constants | wall |
|---|---|---|---|---|---|
| `n + 0 = n` | ✅ | ✅ | `{propext}` | 56 | 10 s |
| `n² ≡ 0 or 1 (mod 4)` | ✅ | ✅ | `{propext, Classical.choice, Quot.sound}` | 235 | 35 s |
| `¬∃ x,y>0. x² = 2y²` (√2 irr.) | ✅ | ✅ | `{propext, Classical.choice, Quot.sound}` | 124 | 436 s |

**Part 2 — full `DagDriver` integration (`n+0=n`):** the gate-passing leaf routes through `node_verifier`
→ `node.state = PROVEN`, **`node.lean_verified = True`**, `node_lean` trace event
`outcome='elementary_verified'`. Offline: **758 passed / 9 skipped**, default path byte-identical, no new
`NodeState`/`NodeEvent`.

**What this validates / scope:** per-node Lean is now a real authority for **leaf** nodes (the LEAP
invariant), opt-in, `elementary_verified`-level (per-leaf faithfulness deferred to the root gate). Still
open: **P4** AND-node `sorry`-sketch compilation (the LEAP *composition* check), **P5** a first-class
`LEAN_VERIFIED` state, and the standing **autoformalization reach** caveat (worked on these easy leaves;
harder leaves may fail-open to soft `PROVEN`).

### P4 — AND-node sketch-compilation (the LEAP composition check), live-validated

A decomposition now commits only if its **sketch** compiles in Lean. Mode A: `make_sketch_gate` formalizes
the sketch with each child-lemma as an **axiomatized hypothesis** (`theorem T (h0 : ⟨child0⟩) (h1 : ⟨child1⟩)
… : ⟨parent⟩ := <sorry-free body>`); if it compiles + audits elementary the composition is Lean-valid, and
`_try_decomposition` commits it (fail-closed: a non-compiling/unavailable/crashing sketch rejects the
candidate and backtracks). The parent becomes Lean-verified by the composition rule
`parent.lean_verified = sketch_lean_verified ∧ all(child.lean_verified)` — i.e. only when the composition
compiled **and** every hypothesis (child) is itself Lean-verified.

Live (`make_sketch_gate(ClaudeFormalizer, warm LeanServer)`): parent `∀n, n+0=n ∧ 0+n=n` with the two
conjuncts supplied as hypotheses → **`elementary_verified=True`, `audit.passed=True`, axioms `{}`** (8 s).
Offline: **775 passed / 10 skipped**, default path byte-identical, no new `NodeState`/`NodeEvent`. Remaining:
**P5** first-class `LEAN_VERIFIED` state, and the autoformalization-reach work for hard sketches/leaves.
