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

### 2026-06-28 — Autoformalization reach map (and gap #1 reassessed)

**Gap #1 is already closed by `ClaudeFormalizer`.** The documented `3|a²+b²` formalization failure was a
**Codex**-formalizer limitation (`decide` on the non-decidable quantified `ℤ` goal → `Decidable`-synthesis
failure). The opus `ClaudeFormalizer` (added in P1) produces, on a *fresh* attempt, a compiling proof using
`decide` **only on the finite `ZMod 3` core** (9 cases) and bridging `ℤ↔ZMod 3` via
`ZMod.intCast_zmod_eq_zero_iff_dvd` → `elementary_verified=True`. That winning tactic discipline (decide on
the finite ring core only; `omega`/`Int.ModEq` on the integer goal; `ZMod m` reduction for mod-m claims;
strong-induction/`Nat.find` for descent) is now **encoded in `Formalizer._RULES`** so it is reliable, not
model-luck (offline suite unchanged at 775).

**Reach map** (`make_node_gate(ClaudeFormalizer, warm LeanServer)`, `repair_iters=3`; per-node
`elementary_verified` = compiled + axiom-audited, axioms ⊆ `{propext, Classical.choice, Quot.sound}`):

| problem | family | result | wall |
|---|---|---|---|
| `3 ∣ a²+b² ⇒ 3∣a ∧ 3∣b` | `ZMod 3` casework | ✅ `elementary_verified` (130 s) | |
| `n⁵ ≡ n (mod 5)` | Fermat little, `ZMod 5` | ✅ (138 s) | |
| `7 ∣ a²+b² ⇒ 7∣a ∧ 7∣b` | bigger `ZMod 7` core | ✅ (81 s) | |
| `¬∃ x,y>0. x² = 3y²` (√3 irr.) | **infinite descent** | ✅ (301 s) | |

So autoformalization reliably covers residue casework **and** infinite descent at the T1 rung — the wall is
*not* here. The genuine frontier (untested) is IMO-hard multi-step proofs (Vieta jumping, coprime-factorization
Diophantine, sum-of-two-squares descent), which also stress *proof-finding*, not just formalization. Note:
these are per-node `elementary_verified` (faithfulness panel deferred to the root gate), generated by Claude
(Codex quota-blocked until Jul 27).

### 2026-06-28 — First above-T1 attempt (`x²+1=y³`) and a Layer-4 denylist gap (FIXED)

Held-out phrasing "for integers x,y, if x²+1=y³ then x=0 ∧ y=1" (the `benchmarks/problems/x2_plus_1_eq_y3`
folder is flagged contaminated), full DAG machine + per-node Lean, Claude generator. **The wall is NOT
proof-finding** — opus produced a coherent proof in 478 s — but it produced the **NON-elementary Gaussian-integer
(`ℤ[i]`) unique-factorization** proof (`(x+i)(x-i)=y³`, coprime factors of a cube are cubes, expand `(a+bi)³`).
A correct *non-elementary* proof is a failure by the project's defining property. Where the layers landed: the
**soft gate** flagged `elastic_justification` and returned `NEEDS_REVIEW` (it suspected the `factorization`/
`euclid_splitting` steps) but could not deterministically reject; the **formalizer timed out** (600 s) rendering
the hard `ℤ[i]` proof, so Layer-4 never ruled in-run.

**Soundness gap found + fixed.** Auditing a hand-written `ℤ[i]` proof directly: `theorem _ :
UniqueFactorizationMonoid GaussianInt := inferInstance` was admitted by the Layer-4 audit (`audit.passed=True`).
The `lean_denylist_decls` covered class groups / Dedekind / number fields / elliptic curves / modular forms /
cyclotomic / Catalan / algebraic geometry but **NOT `GaussianInt`/`Zsqrtd`** — even though the prose scanner
already lists `"unique factorization domain"` (a soft-vs-authoritative asymmetry). So a `ℤ[i]` proof, had it
formalized, would likely have **falsely certified as elementary**. Fix: added `GaussianInt`, `Zsqrtd`,
`Mathlib.NumberTheory.Zsqrtd` to `lean_denylist_decls`. Verified live: the `UniqueFactorizationMonoid
GaussianInt` proof now **REJECTS** (`denylisted_dependency 'Zsqrtd.instCommSemiring' matches 'Zsqrtd'`), while
`Int`/`ZMod` elementary proofs still pass; offline suite **775 green**. (Boundary choice: this places *any*
use of `ℤ[√d]` outside "elementary", matching the project's thesis of forcing proofs over ℤ.)

**Robustness gaps surfaced (tracked separately):**

- **Model-call exception crashes `DagDriver.run()` — CLOSED.** Every live model-call site is now wrapped so a
  raising prover/decomposer/reviewer/verifier (subprocess timeout, non-zero exit, malformed output) is caught,
  surfaced on the trace, and classified as a *failed attempt* (`unknown_tool_error`) instead of propagating out
  of the run: prover in [`ralph.py:71-79`](../../agent/orchestrator/ralph.py) (→ lessons-learned note, next
  episode); decomposer in [`dag_driver.py:472-481`](../../agent/orchestrator/dag_driver.py) (re-plan/backtrack)
  and `1286-1292` (skip the candidate in population generation); reviewer in `907-914` (reject the candidate);
  per-node `node_verifier` in `671-674` (never refutes a clean challenger) and `754-763` (fail-open → soft
  `PROVEN`); `sketch_verifier` in `857-862` (reject the composition). The budget unit is already spent at each
  site, so the loop stays bounded and terminates. Regression-tested by `test_raising_decomposer_does_not_crash_run`
  / `test_raising_reviewer_does_not_crash_run` ([`tests/test_dag_driver.py`](../../tests/test_dag_driver.py)) and
  `test_raising_prover_does_not_crash_run` ([`tests/test_ralph.py`](../../tests/test_ralph.py)).
- **Formalizer too slow for a hard `ℤ[i]` proof at a 600 s cap — still open.** The `ℤ[i]` render timed out; the
  autoformalization frontier is now IMO-hard multi-step proofs (see the reach map above), which stress
  proof-finding, not just formalization.

**Update — first-class `LEAN_VERIFIED` state landed.** The P5 follow-up ("a first-class `LEAN_VERIFIED` state",
flagged open in the P0–P2 and P4 sections above) has shipped. `NodeState.LEAN_VERIFIED`
([`agent/orchestrator/state.py:18`](../../agent/orchestrator/state.py)) is a first-class hard-success state that
*dominates* `PROVEN` (`is_success` treats both as terminal-success; `state.py:27,37`). It is reachable via
`ProofDAG.mark_proven_direct(goal, ledger, lean_verified=True)`
([`dag.py:346-354`](../../agent/orchestrator/dag.py)) for a Lean-confirmed leaf, and via the P4 parent-composition
rule (`dag.py:390-395`: a parent is `LEAN_VERIFIED` only when its sketch compiled **and** every child is itself
`LEAN_VERIFIED`). It is consumed by the CLI at
[`scripts/prove.py:606`](../../scripts/prove.py) (the `--lean-per-node` report distinguishes soft `PROVEN` from
hard `LEAN_VERIFIED` node counts).
