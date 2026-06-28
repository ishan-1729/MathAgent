# Paper / Mechanism Fidelity Audit — 2026-06-28

Scope: every paper-attributed or self-claimed mechanism in MathAgent, audited against the
source paper (or, for synthesized mechanisms, against the project's own claims in
`agent/PLAN.md` and `research/docs/system_design.md`). The framing question throughout is the
project's own stated risk: **"faithful scaffold, substituted engine"** — is the structure
reproduced but the load-bearing *guarantee* replaced by something weaker?

---

## Headline verdict

**MathAgent is a structurally faithful, candidly-labelled reproduction whose central soundness
guarantee is substituted by default, and whose strongest verification layer is built but inert.**

The named mechanisms are genuinely present and, in most cases, exercised: deep-hash split-keyed
goal memoization, the Ralph self-improvement loop, the Elo/Bradley-Terry/PUCT population, the
AutoReason revision controller, the layered deterministic gate, the exact-integer numeric
checker, and the hybrid Lean retriever are all real code, mostly wired into the shipped CLI.
The AlphaProof → Codex/Claude prover substitution is exactly as advertised and is honestly
flagged in code and docs (`PLAN.md`, `system_design.md` both say "Substituted").

The crux gap is one layer down. The paper that anchors the whole project (AlphaProof Nexus)
exists *because* the **Lean compiler mechanically verifies every step and the output is
sorry-free**. In MathAgent's default/shipped path that authority is replaced by a deterministic
**text gate** over a JSON step-ledger plus **same-model-family** Codex/Claude judges — no
compiler. A "PROVEN" result is therefore `soft_proven`, explicitly **not** a certificate. Real
Lean exists but is opt-in (`--terminal-gate` / `--formalize`, off by default), and the
**per-node `LEAN_VERIFIED` machinery** — the direct LEAP analog requested in the brief — is
~150 lines of fully-written code that **no production call site ever wires** and **no CLI flag
enables**, so it is decorative in every real run.

To the project's credit this is **honest, not deceptive**: the naming (`soft_proven` vs
`authoritative_elementary`), the docstrings, and the layer tables openly say Layers 0–3 only
*pressure/filter/route* and that Layer 4 / Lean is the sole authority. The risk is purely that a
reader skimming PLAN.md's "built" checkmarks would over-estimate how often any Lean authority
actually runs. Two items improved since the last pass: **OpenEvolve** is now a genuine
first-class evolutionary proving mode (was a `>= 1.0`-gated no-op fallback), and the
**suspending-vs-restarting scheduler** bug (the real `NEEDS_REVIEW`-stuck diagnosis) is fully
fixed and regression-tested.

**Full offline suite: PASS** — `python -m pytest` → 887 passed, 11 skipped (baseline 875; +12
new evolve tests; skip count unchanged). The 11 skips are pre-existing live-only gates (Codex /
Lean / neural extras).

---

## Mechanism fidelity table

Legend: **F** faithful · **P** partial · **S** substituted (structure present, guarantee weaker
than the paper) · **M** missing/decorative (built but not wired / no engine).

| # | Paper / source | Mechanism | Verdict | Key gap |
|---|---|---|---|---|
| 1 | AlphaProof Nexus | Deep-hash goal memoization (split-keyed, acyclicity guard) | **F** | Hash is over canonicalized **natural-language** statements (`dag.py:102-127`), not Lean terms — cannot detect a sorry'd helper that merely restates the target. Memo *mechanism* faithful and load-bearing. |
| 2 | AlphaProof Nexus | Ralph self-improvement loop (episodes + lessons + budget) | **P** | Loop shape faithful; the inner check is substituted — paper recompiles with **Lean** each turn and feeds Lean's error; here the per-turn check is the deterministic **text gate** `evaluate()` (`ralph.py:80`, `gate.py:62-96`) and "lessons" are gate findings, not compiler diagnostics. |
| 3 | AlphaProof Nexus | Population Elo / Bradley-Terry / PUCT search | **P** | Machinery real and exercised (`population.py`; `--population K`). NOT evolutionary: no persisted cross-episode population DB, no mutation/crossover, no prompt-construction feedback (paper Fig 2). One-shot rank-K-then-pick within a single decompose call; BT/PUCT only reorder candidates the gate already admitted. |
| 4 | AlphaProof Nexus | AlphaProof → Codex/Claude prover substitution | **S** | Honestly documented, not hidden (`codex_prover.py:1-15`; `PLAN.md:158`; `system_design.md:42-47` say "Substituted"). Consequence: AlphaProof returns **Lean-verified** terms; this returns an **unverified text ledger** checked only by a text gate. |
| 5 | AlphaProof Nexus | **Core verification guarantee (Lean compiler authority)** | **S** | THE central substitution. Default path's only authority is the deterministic text gate + same-family judges — **no Lean**. Lean is opt-in (`--terminal-gate`/`--formalize`), off by default. Even an opt-in leaf verifier **fails open** to soft-PROVEN on a non-compiling formalization (`dag_driver.py:695-700`). A "PROVEN" is `soft_proven`, not a certificate. |
| 6 | AlphaProof Nexus / LEAP | **Per-node / per-composition Lean authority (`LEAN_VERIFIED`)** | **M** | Fully written (`dag_driver.py:606-759`; `dag.py:346-396`) but **no production call site passes `node_verifier=`/`sketch_verifier=`** and **no CLI flag enables it** (confirmed: grep across `agent/`, `scripts/`, `ui/` finds only definitions/tests). `LEAN_VERIFIED` is unreachable in practice. The LEAP per-node-Lean analog — built but inert. |
| 7 | AutoReason | Three-way candidate set {A, B, AB} per pass | **F** | `tournament.py:128-158` builds incumbent + adversarial revision + blind synthesis; all three compete. |
| 8 | AutoReason | Critic = failure-analysis only, before revision | **F** | `tournament.py:38-41,128` — problems only, no fixes, computed before revise. |
| 9 | AutoReason | First-class "do nothing" + conservative tiebreak | **F** | `tournament.py:164-182` — incumbent retained unless a challenger beats it by `margin`. |
| 10 | AutoReason | k=2 consecutive-survival stop | **P** | `k_stop=2` real, but production `max_passes=2` (`codex_prover.py:506`) caps the loop before genuine 2-consecutive convergence; paper runs ~3.9 (up to 28) passes — unreachable. |
| 11 | AutoReason | Blind judge panel, randomized orientation | **P** | Only a/b **order** randomized (`tournament.py:197`); **no label randomization** (prompt hard-codes CANDIDATE A/B, `codex_prover.py:494`); default `n_judges=1` — a "panel" of one vs paper's 3. |
| 12 | AutoReason | Per-pass aggregation = **Borda** over {A,AB,B} | **S** | No Borda anywhere. `tournament.py:185-209` runs pairwise round-robin tallying **net head-to-head votes vs the incumbent** + margin. Different aggregator. |
| 13 | AutoReason | Margin requirement for displacement | **F** | `tournament.py:165` — paper-grounded (paper's Round-1 convergence remedy). |
| 14 | AutoReason | Bradley-Terry + PUCT seed selection | **S** | Paper **explicitly disclaims** game/voting-theoretic machinery (`autoreason.md:51`). Grafted from `population.py`; largely non-load-bearing — displacement decided solely by `net>=margin`; BT only breaks ties among qualifiers, PUCT only picks the next seed. Named prominently in `PLAN.md:161` but decorative to the guarantee. |
| 15 | AutoReason | "Elementary admissibility guard" | **S** | `_is_admissible` = `not evaluate(ledger).rejected` (`dag_driver.py:571-577`), the deterministic gate whose own docstring (`gate.py:10-11`) says passing does **not** certify elementary. A non-elementary challenger passing structural/obligation checks is admissible — the "stay elementary to displace" guarantee is asserted, not enforced. |
| 16 | AutoReason | Monotone "no-regression" guarantee | **S** | Holds only w.r.t. an LLM judge panel + the soft gate. Paper is candid the filter is "bounded by the judges' biases" (`autoreason.md:93`) and shows quality-degrading displacement (Tables 4-6); project docstrings drop that caveat and assert it unconditionally. |
| 17 | AutoReason | Production wiring into the prover | **P** | Genuinely wired (`prove.py:256,278` under `--refine`) but off by default, fires only on `ACCEPT_PROVEN` direct leaves (never decomposed/internal nodes), and live roles unexercised in tests. |
| 18 | Synth. (LongCat/LEAP/AlphaEvolve) | Gate compose / fail-closed (max-severity) | **F** | `gate.py:62-96` — parse failure → REJECT, any internal exception → REJECT, true max-severity verdict. The strongest part of the implementation; matches the claim exactly. |
| 19 | Synth. | Layer 1a structural validator (vocab, acyclicity, no dangling, one conclusion) | **F** | `ledger.py:188-280` — all checks deterministic; iterative Kahn acyclicity; NFC normalization. Delivers its guarantee. |
| 20 | Synth. | Goal↔claim binding | **P** | `ledger.py:241-250` only rejects when the conclusion restates **another body step**; a conclusion unrelated to both goal and body passes. Robust goal binding lives in the orchestrator (`goal_hash`), outside the audited gate. |
| 21 | Synth. | Discharged-obligation content checks (case-split / descent / square-split / **bound**) | **P** | 3 of 4 have real content checks (case_cover residues re-run, descent flags + optional numeric, split_coprimality cites a real ancestor). **`bounding` has NO content checker** — `inequality`/`strict` validated for shape only, never inspected, despite `PLAN:246` claiming "bound → the explicit inequality with strictness." Clearest claim-vs-code gap. |
| 22 | Synth. | Descent obligation (NT "prime smuggling site") | **P** | `strictly_decreases`/`stays_in_domain` checked only as **self-asserted booleans**; the exact numeric decrease check fires **only if** the prover volunteers optional `measure_expr`/`next_expr`/`sample_bounds` (`obligations.py:46-79`). Honor-system by default; mitigated (not closed) by scanner routing all descent to REVIEW and ultimately Layer 4. |
| 23 | Synth. | Layer 1b prose denylist / euphemism scanner (soft router) | **F** | `scanner.py:33-58` — every finding REVIEW, never rejects; honestly framed as unsound-by-nature router. Code matches the claim. |
| 24 | Synth. | Elastic-justification router (descent/vieta/bounding → REVIEW) | **F** | `scanner.py:22,51-57` — exact set, wired, tested. The real mitigation for the self-asserted descent obligation. |
| 25 | Synth. (MathCode/Axplorer lineage) | Layer 3 no-eval restricted-AST integer evaluator | **F** | `numeric.py:67-138` — node-by-node AST translator, never uses eval/exec/sympify; Call/Attribute/Subscript/undeclared names rejected before any value built. Exact Python-int arithmetic (no float/rational leak). DoS caps enforced. The most rigorous module; verified live against `os.system`/`__import__`/subscript payloads. |
| 26 | Synth. | Residue-cover completeness re-check | **F** | `numeric.py:293-305` + `obligations.py:28-43` — incomplete cover → hard REJECT, load-bearing. |
| 27 | Synth. | Witness / solution-set completeness checker | **P/M** | `verify_solution_set`/`find_integer_solutions` are sound (box-bounded) and tested but **never invoked by any gate or orchestrator path** — decorative outside tests/openevolve. Only `verify_residue_cover` + `find_points_where_nonneg` are wired. |
| 28 | Synth. | Boundary rulings (four-valued contested-edge vocabulary) | **M** | `Toolkit.ruling()` exists but **no gate layer calls it**. NOT a fidelity failure — `system_design.md:131-133` states verbatim it is "declared but not yet wired ... enforcement a tracked follow-up." Honestly labelled decorative. |
| 29 | Synth. | `SpecCheck` witness-spec evolution checker | **P** | Fail-closed dispatch real and referenced by `openevolve_bridge.py:1408`. **Latent NameError**: `SpecCheck.error: Optional[str]` (`numeric.py:346`) with no `from typing import Optional` — masked today by PEP 563 string annotations, but `get_type_hints()` raises (real latent bug). |
| 30 | Forge study | Suspending-vs-restarting scheduler (the P0 `NEEDS_REVIEW`-stuck fix) | **F** | `dag_driver.py:273-283,357-376` + `node_fsm.py:138-141` — the diagnosed bug arm; falls through to decomposition instead of `mark_failed`. Regression-tested (`test_dag_driver.py:446,463`). The one fully load-bearing, faithful, and fixed item. |
| 31 | Forge study | Total per-node FSM (no wildcard, totality proptest) | **F** | `node_fsm.py:95-169` single total table; `test_node_fsm.py:26-51` itertools.product proptest. Real, total, tested. Caveat: a per-call decision helper, not a persisted state register (DAG state still set by `mark_*`). |
| 32 | Forge study | Dilworth width (Hopcroft-Karp min-path-cover) | **S** | Algorithm a correct, tested stdlib port — but caps a **parallel fan-out that does not exist** (`_prove_and_children` proves children **serially**; no asyncio/threads/processes anywhere). For flat decompositions width == child count, so the real cap is `min(width, budget, fanout_cap=16)`. Correct theorem doing no real work. |
| 33 | Forge study | Mirsky / `upward_rank` ((max,+) critical-path priority) | **S** | Correct port, but **demoted** to the 3rd sort key behind a separate `_critical_path_depths` + leverage; for flat plans all ranks are 0. Ordering is also outcome-irrelevant (results order-independent by invariant). Near-decorative. |
| 34 | Forge study | H0 child-consistency (sheaf 0-cocycle gate) | **P** | Wired, on by default, load-bearing (`dag_driver.py:890-902`), tested. But the "sheaf/0-cocycle" framing oversells a **shallow regex matcher** over a hardcoded 6-family exclusivity table that only fires on `given`/`assumption`/`lemma` steps; frequently inspects 0 overlaps. Catches literal even/odd clashes, nothing subtler. Docstring honestly hedges the label. |
| 35 | Forge study | Category theory (SMC ∘/⊗, Applicative/Monad tiering, functors) | **M** | **No SMC/monoidal/Applicative/Monad/functor code anywhere** — labels only, exactly as the study's own "reason-with, not implemented" disclaimer says. Faithful to the disclaimer; decorative as "category theory in the codebase." |
| 36 | Forge study | Adversarial elementary-verifier + downgrade-don't-discard | **P** | `elementary_verifier.refute_elementary` is a real independent deterministic **refuter**, wired and tested (`dag_driver.py:579-604`). Sound and fails loud. Partial because it can only catch violations it has a rule for — weaker than an independent positive elementary *proof*; the closest honestly-scoped analog of the LEAP soft-gate pattern. |
| 37 | LeanExplore/Loogle/LEAP recipe | Loogle backend (exact names from compiler errors) | **F** | `retrieval.py:21,57-90` — mines hallucinated idents from real compiler errors, hits the real Loogle JSON endpoint, degrades to `[]` on failure. |
| 38 | LeanExplore recipe | BM25 local lexical index | **F** | `semantic_retrieval.py:80-130` — correct textbook BM25 over an elementary Mathlib subset, disk-cached, graceful no-Mathlib degradation. Load-bearing. |
| 39 | LeanExplore recipe | Neural bi-encoder (dense cosine) | **P** | Real bge-small engine exists but gated behind the **`mathagent[neural]` extra the repo never installs/ships**. Default path + ALL tests use `HashingEmbedder` — md5 token-hashing the code itself labels "Not a real semantic model" (`neural_retrieval.py:98`), which **cannot** close the abbreviation gap that justifies the component. Minor overclaim: docs say "name+signature+doc" but only (name, signature) is embedded. |
| 40 | LeanExplore recipe | Cross-encoder reranker | **P** | Correct wrapper (`neural_retrieval.py:126-149`) but reachable only via the same never-installed neural extra; never exercised against a real model in CI. Decorative until `[neural]` installed. |
| 41 | Recipe | HybridRetriever fusion | **F** | `semantic_retrieval.py:199-221` — round-robin interleave + dedupe; graceful fallback to Loogle+BM25. |
| 42 | LEAP role | Retrieval actually guides repair (load-bearing) | **F** | `formalize_bridge.py:164/176/370/382` — retrieved lemmas injected into the repair prompt on every compile-fail and audit-reject. Genuinely exercised. |
| 43 | AlphaEvolve / OpenEvolve | **Evolutionary proof-sketch search (`--evolve`)** | **F (now fixed)** | Was a `best_fitness >= 1.0`-gated no-op fallback (the HARD-gated band caps a genuine PASSED at ~0.815, never 1.0, so every real champion was discarded). Now a first-class mode: `evolve_prove` → `EvolveChampion` accepted on goal-bound + PASSED (`>= PASSED_FLOOR`), short-circuiting the prover (`prove.py:173-195`). Drives the REAL `run_evolution` controller (only the LLM mocked); seed 0.3417 → champion 0.815 across generations. See "OpenEvolve outcome" below. |

---

## OpenEvolve outcome (now fixed)

The diagnosis flagged three issues; all three are confirmed and closed, verified against the real
`openevolve 0.2.27` controller (only the breadth LLM mocked):

1. **Crash on every mutation (`set language`).** `openevolve 0.2.27` leaves
   `config.language=None`; the full-rewrite parser does `"```" + language`, raising
   `TypeError`. Fixed by pinning `config.language="text"` in `build_evolve_config` so breadth/depth
   mutations land. The controller previously survived only by luck (`extract_code_language` returned
   "unknown" for a JSON ledger).
2. **First-class, not fallback-only.** Root cause in `scripts/prove.py`: `--evolve` only accepted
   the champion at `best_fitness >= 1.0`, but a genuine goal-bound PASSED ledger is HARD-capped in
   `[0.60, 1.0)` and tops out at ~0.815 — never 1.0 — so every real champion was discarded and
   `--evolve` degenerated to a no-op. Fixed: `evolve_prove()` returns an `EvolveChampion` accepted on
   **goal-bound AND PASSED** (`fitness >= PASSED_FLOOR`); an accepted champion short-circuits the
   prover and is handed to the DAG/Lean.
3. **`evolve_witnesses` unwired.** Was fully implemented + live-tested but had no CLI entrypoint.
   Wired as opt-in `--evolve-witness K`, scored **only** by the exact-integer checker (no eval/exec).

**Loop is genuinely evolutionary, not a fixed-seed re-score** — independently confirmed by the
verify and skeptic passes: distinct child UUIDs across generations, parent IDs changing (a child
becoming a parent), migrations firing; `iterations=0` leaves the champion stuck at the seed's
0.3417 < `PASSED_FLOOR`, so the headline test's `fitness >= 0.6` assertion would FAIL under a no-op
(non-vacuous). **No exec/eval/import of evolved text** was introduced — the evaluator reads the
ledger as TEXT via a file path and gates it; the numeric path uses the no-eval integer AST.
**Default path byte-identical** — every evolve branch is gated on a default-false/zero flag.

**Caveat (unchanged, by design):** `evolve_prove`'s acceptance is a **search-level** gate, not a
certificate. The documented trivial-cover-citation spoof can still reach the PASSED band, so an
accepted champion only ever reports `SOFT_PROVEN` and must still pass Layer-4 Lean for
`authoritative_elementary`. This is the same `soft_proven` ceiling as the rest of the default path.

---

## LEAP per-node-Lean status (recently closed *elsewhere*, still inert *here*)

The brief asked specifically about the LEAP per-node-Lean analog. Two distinct things share that
description; do not conflate them:

- **The forge-study "suspending scheduler" P0 fix is CLOSED and faithful** (table #30): the real
  `NEEDS_REVIEW`-stuck bug is fixed — exhaustion now falls through to decomposition rather than
  terminating, with regression tests. This is the recently-closed item.
- **The LEAP-style per-node `LEAN_VERIFIED` authority is STILL DECORATIVE** (table #6). The
  machinery is fully written (`dag_driver.py:606-759`, `dag.py:346-396`) but **no production call
  site passes `node_verifier=` / `sketch_verifier=`** (confirmed by grep across `agent/`,
  `scripts/`, `ui/` — only definitions and tests reference them) and **no CLI flag enables it**.
  So in every real run `LEAN_VERIFIED` is unreachable, and even the opt-in leaf verifier **fails
  open** to soft-PROVEN on a non-compiling formalization. The repeated "byte-identical default
  path" comments confirm the default never touches Lean. The honestly-scoped analog that IS live is
  the deterministic `refute_elementary` checker (#36) — a refuter, not a positive per-node Lean
  proof.

---

## Prioritized list — what is not properly implemented

Severity HIGH/MED/LOW; effort S/M/L. Separated into genuine **substituted-engine** gaps (structure
present, guarantee weaker than the paper) and **cosmetic/decorative** gaps (built-but-inert or
labelling).

### A. Substituted-engine gaps (the guarantee is weaker than advertised)

| Pri | Effort | Area | Gap |
|---|---|---|---|
| **HIGH** | M | Core verification (AlphaProof Nexus #5) | The paper's whole premise — Lean compiler verifies every step, output sorry-free — is replaced in the **default path** by a text gate over a JSON ledger + same-family LLM judges. A "PROVEN" is `soft_proven`, not a certificate. Lean is opt-in and off by default. |
| **HIGH** | M | Per-node Lean authority (#6, LEAP analog) | `node_verifier`/`sketch_verifier` are wired by **no production call site** and have **no CLI flag**; `LEAN_VERIFIED` is unreachable in practice. The strongest verification layer is inert. (Fix = add a CLI flag + a production wiring path; the ~150 lines already exist.) |
| **HIGH** | S | Opt-in leaf Lean **fails open** (#5) | A non-compiling formalization is downgraded to soft-PROVEN, not rejected (`dag_driver.py:695-700`) — Lean can confirm a leaf but a Lean **failure-to-compile** does not block its promotion. A Lean *compile failure* should hard-fail or REVIEW, never silently pass. |
| **MED** | S | `bounding` obligation has no content checker (#21) | `inequality`/`strict` are validated for shape only and never inspected, despite `PLAN:246` claiming strictness enforcement. A nonsense inequality passes content review. Add `_check_bounding` (numeric inequality + strictness) to match the other three obligation engines. |
| **MED** | S | Descent decrease is honor-system by default (#22, #29) | `strictly_decreases`/`stays_in_domain` are self-asserted booleans; the exact numeric check is **opt-in**. Either make `measure_expr`/`next_expr`/`sample_bounds` mandatory for a `descent` tag, or downgrade an exprs-absent descent to REVIEW rather than PASS. |
| **MED** | S | AutoReason "elementary admissibility guard" cannot enforce elementarity (#15) | `_is_admissible` is the deterministic gate, which by its own docstring does not certify elementary. Either route challengers through the Layer-4/elementary auditor, or rename the guard to what it is (a structural/scope gate). |
| **MED** | S | AutoReason "monotone / never-regress" overclaimed (#16) | Holds only w.r.t. a (possibly single) LLM judge's preference; paper shows quality-degrading displacement. Drop the unconditional claim from docstrings; restore the paper's judge-bias caveat. |
| **MED** | S | Neural retriever degrades to lexical out-of-the-box (#39, #40) | The differentiating bge-small bi-encoder + cross-encoder reranker sit behind the `mathagent[neural]` extra the repo never installs; the exercised default is md5 hashing the code disclaims as "not a real semantic model." Make the real engine the exercised default in a smoke path, and have `--neural` **warn** (not silently lexical-fallback) when it degrades. |
| **LOW** | S | Memo identity is over natural-language, not Lean types (#1) | `semantic_goal_hash` canonicalizes English/notation; faithful as a memo but cannot detect the paper's sorry'd-restatement failure mode (that needed Lean). Scope limit to document, not a code fix. |
| **LOW** | M | Population/AutoReason "evolutionary" framing (#3, #14) | `population.py` is a one-shot Elo ranker (no persisted DB, no mutation/crossover); BT/PUCT in AutoReason are grafted from `population.py`, non-load-bearing, and the AutoReason paper explicitly disclaims them. Either build the persisted population DB or correct `PLAN.md:161` and the docstrings. |

### B. Cosmetic / decorative gaps (built-but-inert, or labelling)

| Pri | Effort | Area | Gap |
|---|---|---|---|
| **MED** | S | Dilworth width + Mirsky rank are precision over no engine (#32, #33) | Correct, tested ports that cap a **parallel fan-out that does not exist** (children proved serially) and, for flat plans, return the trivial answer (width == child count, rank == 0). Either implement concurrent child proving so the cap binds, or candidly mark them aspirational/decorative. |
| **MED** | S | H0 "sheaf 0-cocycle" oversells a regex matcher (#34) | A shallow surface-pattern check over a hardcoded 6-family table that frequently inspects 0 overlaps. Rename the headline to "surface signature-compatibility gate (a narrow computable shadow of H0)" or strengthen it beyond lexical even/odd matching. |
| **MED** | S | AutoReason aggregation is not Borda (#12) | Paper specifies Borda over {A,AB,B}; impl uses pairwise net-vote-vs-incumbent + margin. Morally similar, different aggregator. Implement Borda to match, or document the substitution (the margin rule is itself paper-sanctioned, so this is optional). |
| **LOW** | S | `verify_solution_set` / `find_integer_solutions` unwired (#27) | The named witness/completeness verifier is sound and tested but called by **no gate or orchestrator path** — decorative outside tests/openevolve. Wire it or mark it as evolution-mode-only. |
| **LOW** | S | Latent un-imported `Optional` NameError (#29) | `SpecCheck.error: Optional[str]` with no `from typing import Optional`; masked by PEP 563, but `get_type_hints()` raises. One-line import fix. |
| **LOW** | S | Boundary rulings wired to nothing (#28) | `Toolkit.ruling()` consulted by no gate layer — **already honestly labelled** "declared but not yet wired" in `system_design.md:131-133`. Not a fidelity failure; tracked follow-up. |
| **LOW** | S | Category-theory claims are labels only (#35) | No SMC/Applicative/Monad/functor code — **already disclaimed** as "reason-with, not implemented." Fine as documented intent. |
| **LOW** | S | Judge blindness half-implemented (#11) | Only a/b order randomized, not labels; default `n_judges=1` vs paper's 3. Add label randomization; raise default panel size. |
| **LOW** | S | AutoReason convergence truncated, not earned (#10) | `max_passes=2` with `k_stop=2` caps the loop before genuine 2-consecutive convergence. Raise `max_passes` so k=2 can be earned. |
| **LOW** | S | Neural docs claim "+doc" embeddings (#39) | `_build_corpus` yields only (name, signature); no doc field extracted. Drop the claim or extract docstrings. |

---

## Bottom line

Structure is faithfully reproduced across the board, and the project is unusually **honest** about
where the engine is softer than the paper — the naming (`soft_proven` vs
`authoritative_elementary`), the layer tables, and many docstrings say so plainly. The genuinely
sound, load-bearing pieces are real: fail-closed gate composition, deterministic structural
validation, the no-eval exact-integer checker (with the residue-cover and coprimality-ancestor
checks), the suspending-scheduler fix, and the now-first-class OpenEvolve loop.

The single highest-leverage truth the report must not soften: **by default, no Lean compiler runs**,
the central guarantee is an LLM-judged text gate, and the per-node `LEAN_VERIFIED` layer — the LEAP
analog requested here — is built but wired by nothing. Closing that is the difference between
"faithful scaffold" and "faithful system." The remaining items are a mix of small honesty fixes
(labelling, one import, panel sizes) and a handful of S/M engine-strengthening tasks (mandatory
descent grounding, a `bounding` content check, routing the admissibility gate through Layer 4,
making the real neural engine the default, and either implementing parallel child proving or
demoting the Dilworth/Mirsky labels).
