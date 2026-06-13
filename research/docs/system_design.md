# MathAgent — System Design

> **Persistent design document.** This is the deep, current elucidation of *what the system is, how it
> is shaped, and why*. It changes slowly. For *what is built/validated right now* (timestamp-stamped),
> see **[`build_status.md`](build_status.md)**; for the staged plan and v1 scope decisions,
> **[`../../agent/PLAN.md`](../../agent/PLAN.md)**; for the visual companion,
> **[`system_design.html`](system_design.html)** (Excalidraw diagrams). Evidence base for every
> architectural borrow: **[`literature_design_implications.md`](literature_design_implications.md)** (20
> systems papers) and **[`paper_extractions.md`](paper_extractions.md)**.

---

## 1. What this system is

MathAgent is a **training-free agentic harness** that attempts to prove **number-theory theorems by
elementary means only** (the IMO-usable toolkit) and — the part that makes it *ours* — to **prove that
the proof is elementary**. It orchestrates frontier general-purpose LLMs (Codex / GPT-5.5-xHigh stands
in for a trained prover) inside a LEAP-style blueprint→AND-OR-DAG→compiler-feedback loop, then subjects
any successful argument to a **dual elementary gate**: soft/structural pressure on an informal
"step-ledger," backed by an authoritative, non-gameable **Lean proof-term dependency + axiom audit**.

The defining product property: a correct *non-elementary* proof is a **failure**, not a success. So the
system is built around two questions — *can it find the proof?* (the harness) and *can it prove the
proof is elementary?* (the gate, esp. Layer 4) — and the second is the harder, more original one.

---

## 2. One system, one spine, grafts — not a re-implementation of any paper

The 20 paper dossiers are a **component menu**, not 20 competing blueprints. They sort into two
architecture families; we chose family (a):

| Family | Papers | Our stance |
|---|---|---|
| **(a) Training-free harness around frontier LLMs** | LEAP, Aristotle, Autoreason, AlphaProof_Nexus (*basic* agent), Pantograph-DSP | **This is us.** v1 = harness-first, no training. |
| **(b) Trained / specialized-prover stacks** | Goedel-Prover-V2, BFS-Prover-V2, LongCat | **Reference-only / v2.** Harvest ideas (V_leg legality gate, sketch-with-axiom-stubs), not the GPU training. |

Within family (a) the system has a definite shape:

- **Spine = LEAP.** Informal blueprint → AND-OR proof DAG → compiler-feedback revision → memoized
  lemma reuse, with a pre-commit decomposition reviewer.
- **Grafted from AlphaProof_Nexus.** The Ralph loop, the deep-hash goal cache, population/Elo, and —
  most important — **SafeVerify → our Layer-4 axiom/dependency audit**.
- **Substituted.** AlphaProof_Nexus uses an RL-trained prover (AlphaProof) as the per-subgoal tool —
  the one piece nobody can reproduce. **We swap in Codex GPT-5.5-xHigh.**
- **Grafted from Autoreason.** The incumbent revision tournament (do-nothing first-class, blind judge
  panel, k=2 stop, margin gate) as the revision controller / synthesis-drift guard.
- **Grafted from AlphaEvolve.** Population/Elo ranking + the cheap-first evaluation cascade.
- **Borrowed infra.** Loogle/LeanSearch + a neural bi-encoder (the LeanExplore recipe) for retrieval;
  AXLE/Pantograph/LeanDojo ideas for the Lean bridge + persistent REPL (built in-house); AXLE/Goedel
  faithfulness → the adversarial faithfulness panel.
- **Ours, and the actual product.** The **elementary-method constraint** (the layered gate, esp. the
  Layer-4 dependency audit). Every Lean paper in the corpus is explicit that **"Lean-verified ≠
  elementary."**

So a new paper is never "do we switch to it?" — it is "**which building block does it contribute, and
does it graft onto the spine?**"

---

## 3. The defining mechanism — the layered elementary gate

Two load-bearing facts shape every choice:

1. **Elementarity has no projection operator.** AlphaEvolve enforces a *numeric* property (integrality)
   with soft penalty + *hard projection* (rounding) + prompt nudge. You **cannot** "round" a class-group
   argument into a descent argument. Only the soft-penalty and prompt parts transfer; the hard part must
   be rebuilt as a **verification gate**, not a projection.
2. **A judge is never the final gate.** Same-model judges inherit the prover's blind spots and can be
   reward-hacked. Judges *rank and prune*; a **deterministic** mechanism *accepts*.

Therefore the constraint is enforced as **defense-in-depth, cheap-first**, and **only the last layer is
authoritative**:

| Layer | What it does | Authority |
|---|---|---|
| **0 — Framing** | Objective spec (`allowed_toolkit.yaml`/`denylist.yaml`) injected into every role; constrained-scope + paradigm scaffolding; retrieval bias to the elementary corpus. | soft (present, not obeyed) |
| **1a — Structural** | Deterministic validator over the typed step-ledger: justification ∈ closed vocabulary; DAG acyclicity; no dangling deps; one connected conclusion; discharged obligations. | deterministic *filter* |
| **1b — Prose scan** | Euphemism/denylist keyword scan → routes to review. | soft router (never rejects) |
| **2 — Adversarial review** | N independent critic/judge agents: every step elementary, no logical gap. | soft consensus (rank/prune) |
| **3 — Numeric grounding** | Exact-integer witness search re-checks the statement + finite case-covers + descent decreases. | deterministic, bounded scope |
| **4 — Lean dependency audit** | Compile → walk the kernel proof-term's transitive constant closure + `collectAxioms`; reject on a content-denylist hit (unless allowlisted) or any non-whitelisted axiom (catches `sorry`). | **deterministic, authoritative — the only place "elementary" is enforced** |

**Why Layer 4 is a dependency audit, not "it compiled":** Mathlib *is* the source of the heavy
machinery, so a compiling proof can freely route through class groups or Mihăilescu. **Why it needs a
two-tier allow/deny design:** a naive namespace-prefix filter over the closure over-rejects nearly every
elementary proof (they all touch `WellFounded.fix`, `Decidable`, `Nat.rec`, …) — so a small **content
denylist** is backstopped by an **infrastructure allowlist** + an **"elementary-by-fiat" allowlist**
(heavy-impl but elementary-result APIs like `legendreSym`), with allowlist-wins precedence. The axiom
check (`collectAxioms ⊆ {propext, Classical.choice, Quot.sound}`) is how a `sorry` (`sorryAx`) is caught
— a source-AST scan would miss an error-recovered `sorry`.

**Honest framing (locked):** v1 *induces, pressures, ranks* elementarity (Layers 0–3) and *enforces* it
deterministically only at Layer 4. Calling a Layers-0–3-passed proof "elementary" would relocate the
"Lean-verified = elementary" category error one step over.

---

## 4. End-to-end pipeline (with data shapes)

The unit of work is a **step-ledger**: a proof as a typed DAG of justified steps —
`step := {id, claim, justification ∈ closed-vocabulary, depends_on:[id…], obligations?}` with exactly
one `conclusion`. It is the contract every gate layer reads and the artifact the formalizer consumes.

```
informal claim → [Prover (Codex)] → JSON step-ledger
   → [L1a structural + L1a/L3 obligations + L1b scan]  ⇒ REJECTED | NEEDS_REVIEW | PASSED
   → [Codex harness: FlatDriver | DagDriver (direct→decompose→review→recurse) + Ralph + tournament]
   → assembled informal proof
   → [Formalizer (Codex)] → Lean 4
       → [compile on persistent Mathlib server]  --error/audit-reject→ [repair loop: + diagnostics + retrieved lemmas]
   → [Layer-4 audit: closure + collectAxioms]  → [faithfulness panel]
   → authoritative_elementary = compiled AND audit.passed AND faithful
```

There are **two distinct verdicts**, and conflating them is a category error: `PROVEN` (an informal
proof was found and passed the soft+structural gate) vs `authoritative_elementary=True` (that proof was
formalized, compiled, its proof-term audited elementary, and its Lean statement verified faithful). The
harness can reach the first without the second — and reports that honestly.

---

## 5. Control flow — how each mechanism engages (there is NO classifier)

A common question: *what decides whether LEAP decomposition, the population search, or the tournament
runs?* **Nothing learned. It is a deterministic control-flow cascade plus operator config flags**
(`DagDriver._prove`):

1. **Ralph loop (direct attempt) — unconditional.** Every node first tries a direct proof via the Ralph
   loop (multi-episode, lessons-learned). If it succeeds, the node returns immediately.
2. **`_refine` tournament — `if self.refiner is not None`** (set by `--refine`). Runs on a directly-proven
   ledger; monotone (a challenger must beat the incumbent on a blind panel *and* stay elementary).
3. **LEAP decomposition — only if the direct attempt *failed***. The cascade falls through to decompose →
   review → recurse. (Reached only on direct failure; gated also by `decomposer is not None`.)
4. **Population/Elo + PUCT/BT — `if population_k and comparator`** (set by `--population`). Lives *inside*
   the decomposition step (ranks K candidate decompositions).
5. **Re-plan — bounded by `max_replan_depth`** (each re-decomposition after the first consumes the global
   replan budget).
6. **Deep-hash cache — always active**; reuses a proven node by its semantic hash (inert when there's
   nothing to reuse).

So "the easy theorem didn't use the DAG/population" means only that the **direct attempt succeeded and
the cascade short-circuited** — *and* that the optional layers were off by flag. The only "intelligence"
in the gating is **try-cheap-first, escalate-on-failure**, which is LEAP's own design — not a classifier.

---

## 6. Two axes: why an *easy* theorem can fail at *formalization*

**Proof difficulty and formalization difficulty are nearly orthogonal axes**, and the system is
**lopsidedly tooled** across them — which is the central thing to understand about where it breaks.

- **Proof-search axis — heavily tooled.** DAG decomposition, Ralph loop, deep-hash memo, population/Elo,
  PUCT, Bradley-Terry, the Autoreason tournament. Rich, diverse, verifier-guided search.
- **Formalization axis — thinly tooled.** Essentially one mechanism: prompt → compile → **local-patch**
  repair loop (+ retrieved lemmas). No diversity search over formalization strategies.

Consequence: once the search machinery makes proof-*finding* cheap, the bottleneck **moves** to the
under-tooled formalization step (Goedel reports >80% of failures are mis/non-formalization). A
*proof-trivial* theorem is where you see this nakedly — there's no proof difficulty to confound it.

**Live example (T2, `3 ∣ a²+b² ⇒ 3∣a ∧ 3∣b`):** the prover found the (correct) mod-3 argument in one
shot (`PROVEN`), but the formalizer wrote a `decide`-based Lean proof over `Fin`/`ZMod` cases whose
`Decidable` instance Lean couldn't synthesize → **compile failed**; the 3-iteration repair loop stayed
in the same `decide` basin (it patches errors, doesn't switch strategy; the error text "failed to
synthesize Decidable" misdirects toward fixing decidability rather than abandoning `decide`; retrieval
had nothing to add because no lemma was missing). Result: honest `authoritative_elementary=False`. The
fix is *not* to hand-bias the prompt toward `omega` (that would teach-to-the-test); it is to **apply the
search/diversity discipline to the formalization axis** — formalize N ways, compile all, let the
compiler+audit select — validated on **held-out** theorems.

---

## 7. Component inventory (contracts + Codex roles)

Everything that touches the model is a **Protocol**, so the Codex implementation and the offline test
stub are interchangeable; the whole harness is deterministically testable with scripted stubs.

| Subsystem | Key modules | Contract / role | Source |
|---|---|---|---|
| **Gate (Layers 1–3)** | `gates/ledger.py`, `obligations.py`, `scanner.py`, `gate.py` | `evaluate(ledger,toolkit)→GateReport` | LongCat V_leg, LEAP reviewer, AlphaEvolve cascade |
| **Gate vocabulary** | `gates/allowed_toolkit.yaml`, `denylist.yaml`, `ledger.schema.json` | closed justification enum + obligation shapes + denylists | PLAN §2 |
| **Layer-4 audit** | `gates/lean_audit.py`, `lean_bridge.py`, `lean_server.py`, `lean/Audit.lean` | `audit_report(DependencyReport)→LeanAuditResult` | Nexus SafeVerify, AXLE |
| **Codex roles** | `tools/codex_prover.py` | Prover / Decomposer / Reviewer / Comparator + tournament Critic / Author / Synthesizer / Judge | AlphaProof tool; LEAP; Autoreason |
| **Numeric grounding** | `tools/numeric.py` | exact-integer witness / residue-cover / descent checks | Axplorer, MathCode |
| **Retrieval** | `tools/retrieval.py` (Loogle), `semantic_retrieval.py` (BM25), `neural_retrieval.py` (bge-small) → `HybridRetriever` | `retrieve(claim,error)→[lemma]` | LeanSearch/Loogle, LeanExplore |
| **Formalizer** | `tools/formalizer.py` | `formalize(prior,errors,lemmas)→Lean` | LEAP/Aristotle informal→formal |
| **Drivers** | `orchestrator/driver.py` (flat), `dag_driver.py` (AND-OR) | plan→prove→gate→repair; direct→decompose→review→recurse | LEAP |
| **Proof DAG** | `orchestrator/dag.py` | AND-OR DAG, **semantic** deep-hash memo, acyclicity | LEAP, Nexus deep-hash |
| **Ralph loop** | `orchestrator/ralph.py` | per-goal episodes + lessons-learned | AlphaProof_Nexus |
| **Population** | `orchestrator/population.py` | Elo tournament, `fit_bradley_terry`, `select_puct` | Nexus / AlphaEvolve |
| **Revision tournament** | `orchestrator/tournament.py` | Autoreason incumbent tournament (do-nothing wins ties, PUCT+BT, admissibility guard) | Autoreason |
| **Faithfulness** | `orchestrator/faithfulness.py` | adversarial multi-lens, default-unfaithful | AXLE / Goedel |
| **Terminal gate** | `orchestrator/formalize_bridge.py` | formalize→compile→audit→faithfulness → `authoritative` | PLAN §5 Layer 4 |
| **Liveness** | `orchestrator/state.py` | NodeState + Budget (calls / repairs / replans) | PLAN §4.3 |
| **Benchmark** | `agent/benchmarks/arxivmath.py`, `tools/answer_check.py`, `scripts/run_benchmark.py` | non-contaminative loader + SymPy grader + run records | MathArena ArXivMath |
| **Web UI** | `ui/server.py`, `ui/index.html` | SSE console driving `prove.py` with every lever | — |

---

## 8. Search & revision machinery — PUCT, Bradley-Terry, the tournament

Two distinct mechanisms at two layers (both *search/quality*, neither touches the gate):

- **Population/Elo + Bradley-Terry + PUCT — *candidate selection*.** When a goal admits several candidate
  *decompositions*, generate K, run a pairwise Elo tournament (Codex `Comparator` judges), fit **Bradley-
  Terry** latent strengths from the win matrix (stable batch estimate over noisy online Elo), then expand
  **PUCT-best-first** (exploit strength + explore under-visited). Makes search *cheaper*.
- **Autoreason incumbent tournament — *revision control*.** Given a *working* proof, each pass runs Codex
  Critic (failure analysis only) → Author (revise) → Synthesizer (blind merge) → a Codex judge panel; a
  challenger displaces the incumbent only if it beats it head-to-head by `margin` **and** passes the
  elementary admissibility gate. "Do nothing" wins ties; stop after **k=2** consecutive incumbent wins.
  Internally reuses Bradley-Terry (strengths) and PUCT (next-pass seed). Its job is the **synthesis-drift
  / elementary-incumbent guard**: a "better-sounding but non-elementary" revision can't displace a correct
  elementary proof. Makes search *safer* (monotone — never regresses).

Both are built and Codex-wired (`CodexComparator`, `make_codex_refiner`); neither is what blocks a first
result. They pay off once there is a search worth prioritizing and an incumbent worth defending.

---

## 9. Memoization & the goal hash (a soundness-critical contract)

The DAG memoizes by a **deep hash of the goal statement** (`dag.py::goal_hash`), so an identical
sub-lemma on different branches resolves to one node, proven once. This key is **soundness-critical**: a
*false hit* (two distinct goals hashing equal) would reuse a proof of the wrong lemma. So
`canonical_form` folds **only meaning-preserving surface differences** (NFC + symbols: `→`/`->`, `≤`/`<=`,
`²`/`^2`, `ℤ`/`Int`, `∣`/`|`; synonyms: `for all`/`∀`, `divides`/`∣`; spacing/markup) and **deliberately
never renames variables** (occurrence-order renaming of free single letters is unsound — it would merge
`x ∣ y` with `y ∣ x`) **nor reorders operands**. A miss only costs recomputation; a false hit would be
unsound, so we bias hard toward never merging. Soundness guards are tested.

---

## 10. Autoformalization & retrieval

The **repair loop** (`formalize_and_audit(repair_iters=N)`): formalize → compile on the persistent server
(~0.1s) → on a compile error *or* an audit reject, feed diagnostics + retrieved real Mathlib lemmas back
and re-formalize. A deep dive concluded Codex's `/goal` mode should **not** own this loop (a Python loop
keeps control of retrieval/denylist/budget/audit; the persistent server's ~0.1s compile beats Codex
re-running `lean` at ~60s/iter). Retrieval is a **hybrid**: Loogle (exact names from compile errors) +
BM25 (lexical) + a **neural bi-encoder** (`bge-small-en-v1.5` over `name+signature+doc`, the LeanExplore
recipe) that closes the abbreviation gap (`gcd` ↔ "greatest common divisor"); the neural backend is an
optional dependency with graceful degradation.

---

## 11. Benchmarking & the certification ladder

v1's headline benchmark is **MathArena ArXivMath** — a *final-answer*, contamination-resistant set (only
gold answers ship; problems are reverse-engineered from very recent arXiv papers). The integration is
**non-contaminative by construction**: the loader splits each item into a *prompt* (statement only) and a
*held-out oracle* (answer); a `Problem` has no `answer`/`source` field, so a solver cannot see them.
Grading is **SymPy answer-equivalence**. Two solvers plug into the same harness: a *vanilla* single-shot
Codex answerer and a *harness* answerer (Codex + the Autoreason tournament under an elementary gate).

> **Genre note:** ArXivMath is final-answer; the harness's distinctive value is *elementary-proof
> certification*, which a final-answer benchmark cannot measure. The **certification ladder**
> (`prove.py --terminal-gate …`) is the test that does: it runs real NT theorems through the full
> formalize→audit pipeline and reports `authoritative_elementary`.

---

## 12. The web UI

`ui/server.py` is a zero-dependency (stdlib) SSE server: it builds the `prove.py` argv from the controls
chosen in the browser and streams the harness's output back live. `ui/index.html` exposes **every lever**
as a control (model/effort/mode, the **Formalize + Layer-4 certify** toggle, refine/population/judges,
retrieval, budgets) and shows live `PROVEN` / `certified-elementary` badges. Safety: binds `127.0.0.1`;
argv is a list with the typed problem passed after `--` (no shell/flag injection).

---

## 13. What is live-validated (evidence, not claims)

- **Certification pipeline authoritative on real NT theorems (2026-06-13):** `n²≡0,1 mod 4` and `√2`
  irrational (`x²=2y²`, **infinite descent**) both went prove→formalize→compile→**Layer-4 audit PASS (0
  rejects)**→faithfulness 4/4→`authoritative_elementary=True`. **2/3** of the ladder certified; the miss
  (`3∣a²+b²`) was an autoformalization compile failure, reported honestly as `False` (not waved through).
  See [`live_certification_runs.md`](live_certification_runs.md).
- **Non-gameable audit:** an `IsDedekindDomain` proof is rejected by the closure denylist even though it
  compiles; a `sorry` is caught via `sorryAx`.
- **ArXivMath live (GPT-5.5-xHigh):** vanilla 4/5 on the NT subset; harness {7,15,17} 3/3 — no regression
  (base model at ceiling on this tiny subset, so no lift to show — a dataset limit).
- **Persistent server:** Mathlib loads once (~76s), then audits ~0.1s.

---

## 14. Design invariants (must not be broken)

1. **The gate is the product.** A correct *non-elementary* proof is a failure (binary admit/reject before
   weighted scoring).
2. **Only Layer 4 certifies "elementary."** Layers 0–3 pressure/filter.
3. **Never trust compile-success or hammer-success** — always audit the proof term.
4. **Judges rank; deterministic mechanisms accept.**
5. **The memo key never merges distinct goals** (§9).
6. **Everything terminates** (Budget caps → `EXHAUSTED` with honest failure).
7. **Everything is a recorded, comparable experiment** (JSONL trace + run record + toolchain/denylist
   version).
8. **Stubs mirror Codex** (every model-touching component is a Protocol with a deterministic offline stub).
9. **Untrusted-input-gated constraint spec** (memoized/retrieved artifacts pass the gate before reuse).
10. **No teaching-to-the-test.** Improvements are problem-independent and measured on held-out problems;
    the audit makes search-bias safe, but it does not make eval-on-seen-problems honest.

---

## 15. Status & roadmap pointers

- **Built / validated now:** [`build_status.md`](build_status.md) (timestamp-stamped; component table +
  the adoption-list scorecard + live-run evidence).
- **Staged plan & scope decisions:** [`../../agent/PLAN.md`](../../agent/PLAN.md) (§9 roadmap).
- **The bottleneck:** autoformalization rate (§6) — the under-tooled axis; the legitimate fix is
  diversity-search over formalizations, measured on a held-out NT eval set.
- **Visual companion:** [`system_design.html`](system_design.html) (Excalidraw diagrams of the pipeline,
  the layered gate, and the two-axis bottleneck).
