# Lean Layer-4 audit + Population/Elo search

Two pieces, completing the AlphaProof_Nexus + LEAP adaptation.

## 1. Lean Layer 4 — the authoritative elementary gate

The only **non-gameable** gate (PLAN.md §5): a proof can compile against Mathlib while using class
groups or Mihailescu, so elementarity is decided by auditing the **proof term's transitive dependency
closure + axioms**, not by "it compiled."

| Piece | File | Role |
| --- | --- | --- |
| Extractor (Lean) | [`agent/gates/lean/Audit.lean`](../../agent/gates/lean/Audit.lean) | `#audit <decl>` walks the constant closure (`Expr.getUsedConstants`, env BFS) + `collectAxioms`, emits one-line JSON via `Lean.Json`. Self-contained (`import Lean`, no Mathlib). |
| Auditor (Python) | [`agent/gates/lean_audit.py`](../../agent/gates/lean_audit.py) | Toolchain-independent decision logic: **axiom whitelist** ({propext, Classical.choice, Quot.sound}) + **content denylist** over the closure, with an **infrastructure allowlist** and an **elementary-by-fiat allowlist** so it doesn't over-reject plumbing or heavy-impl-but-elementary APIs. |
| Bridge (Python) | [`agent/gates/lean_bridge.py`](../../agent/gates/lean_bridge.py) | Prepends the extractor to a proof, runs `lean <file>`, parses the JSON, calls the auditor. Guarded: no Lean → degrades. |
| Policy data | [`agent/gates/denylist.yaml`](../../agent/gates/denylist.yaml) | `lean_denylist_decls`, `lean_infrastructure_allowlist`, `lean_elementary_by_fiat`, `lean_axiom_whitelist`. |

**Design correctness (the review's B1):** a naive transitive closure fires on nearly every elementary
proof (they all depend on `WellFounded.fix`, `Decidable`, `Nat.rec`, …). So the audit is
**content-denylist + infrastructure/elementary-by-fiat allowlist**, with allowlist winning over denylist.

**Live validation (Lean 4.30.0, installed via elan):**
- `theorem ma_add_zero (n : Nat) : n + 0 = n := Nat.add_zero n` → **PASS** (real kernel closure: `Nat.add`,
  `Nat.rec`, `instHAdd`, …; axioms `[]`).
- `theorem ma_sorry : (2:Nat) = 2 := by sorry` → **REJECT** (`sorry_axiom`: `sorryAx`).
- Content-denylist (e.g. `Mathlib.NumberTheory.ClassNumber.*`, `EllipticCurve` component) → unit-tested
  with synthetic reports (`tests/test_lean_audit.py`); needs a Mathlib build to trigger live.

Run the live tests: `MATHAGENT_LEAN_TESTS=1 python -m pytest tests/test_lean_bridge.py`.

**Still open:** a Mathlib-backed lake project so Mathlib proofs can be audited live; AST-legality
(import/`open`/redefinition) checks; making Layer 4 the terminal gate in the DAG driver once the
informal→Lean formalization step exists.

## 2. Population / Elo search

When several candidate decompositions compete for a goal, rank them by a **latent strength estimated
from pairwise LLM-judge comparisons** and try the best first (AlphaProof_Nexus's population DB).

| Piece | File | Role |
| --- | --- | --- |
| Population | [`agent/orchestrator/population.py`](../../agent/orchestrator/population.py) | `EloPopulation` (Elo updates from pairwise outcomes, round-robin tournament with budget cap, ranking, **PUCT** selection) + `fit_bradley_terry` (MM-algorithm MLE — the Plackett-Luce analog) + `Comparator` protocol. |
| Comparator (live) | `CodexComparator` in [`agent/tools/codex_prover.py`](../../agent/tools/codex_prover.py) | Asks Codex which of two candidate decompositions is the more promising *elementary* split. |
| Integration | [`agent/orchestrator/dag_driver.py`](../../agent/orchestrator/dag_driver.py) | `population_k>0` + a comparator → generate K candidate decompositions, run an Elo tournament (budget-bounded), try best-first; otherwise the prior DFS path. |

**Tested (offline, deterministic):** Elo updates, tournament ranking by strength, budget-bounded
comparisons, PUCT exploration, Bradley-Terry ordering, and an end-to-end DAG test showing the Elo
search picks the provable decomposition first where plain DFS (1-attempt) picks the dead end and fails
(`tests/test_population.py`).

**Caveats:** the comparator is the same model family as the prover (same-model-judge blind spots —
the deterministic gate is still the real guard); islands/Gibbs-sampled Plackett-Luce are not built
(Elo + Bradley-Terry suffice for v1); like the rest, this steers *search*, it does not relax the gate.
