# MathAgent — System Design

> **Persistent design document.** This describes *what the system is and why it is shaped this way* —
> the architecture decisions, the component contracts, and the invariants. It is meant to change
> slowly. For *what is actually built and validated right now* (which moves with every commit), see
> the timestamp-stamped **[`build_status.md`](build_status.md)**. For the staged build plan and the
> v1 scope decisions, see **[`../../agent/PLAN.md`](../../agent/PLAN.md)**. The evidence base for every
> architectural borrow is **[`literature_design_implications.md`](literature_design_implications.md)**
> (synthesis of 20 systems papers) and **[`paper_extractions.md`](paper_extractions.md)**.

---

## 1. What this system is (in one paragraph)

MathAgent is a **training-free agentic harness** that attempts to prove **number-theory theorems by
elementary means only** (the IMO-usable toolkit) and — the part that makes it *ours* — to **prove that
the proof is elementary**. It orchestrates frontier general-purpose LLMs (Codex / GPT-5.5-xHigh stands
in for a trained prover) inside a LEAP-style blueprint→AND-OR-DAG→compiler-feedback loop, then subjects
any successful argument to a **dual elementary gate**: soft/structural pressure on an informal
"step-ledger," backed by an authoritative, non-gameable **Lean proof-term dependency + axiom audit**.

---

## 2. The single most important design question: "are we one paper, several papers, or a combination?"

**One system. One spine. Components grafted on. Organized around a constraint no source paper targets.**

The 20 paper dossiers in [`../papers/`](../papers/) are a **component menu**, not 20 competing
blueprints. They sort into two architecture families
([`literature_design_implications.md`](literature_design_implications.md) §3):

| Family | Papers | Our stance |
|---|---|---|
| **(a) Training-free harness around frontier LLMs** | LEAP, Aristotle, Autoreason, AlphaProof_Nexus (*basic* agent), Pantograph-DSP | **This is us.** v1 = harness-first, no training. |
| **(b) Trained / specialized-prover stacks** | Goedel-Prover-V2, BFS-Prover-V2, LongCat | **Reference-only / v2.** Harvest ideas (V_leg legality gate, sketch-with-axiom-stubs), not the GPU training. |

We chose family **(a)**. Within it the system has a definite shape:

- **Spine = LEAP.** Informal blueprint → AND-OR proof DAG → compiler-feedback revision → memoized
  lemma reuse, with a pre-commit decomposition reviewer.
- **Grafted from AlphaProof_Nexus.** The Ralph loop, the deep-hash goal cache, the population/Elo
  idea, and — most important — **SafeVerify → our Layer-4 axiom/dependency audit**.
- **Substituted.** AlphaProof_Nexus uses an RL-trained prover (AlphaProof) as the per-subgoal tool —
  the one piece nobody can reproduce. **We swap in Codex GPT-5.5-xHigh.**
- **Grafted from Autoreason.** The incumbent revision tournament (do-nothing first-class, blind judge
  panel, k=2 stop, margin gate) as the revision controller / synthesis-drift guard.
- **Grafted from AlphaEvolve.** Population/Elo ranking + the cheap-first evaluation cascade.
- **Borrowed infra.** Loogle/LeanSearch + a neural bi-encoder (the LeanExplore recipe) for retrieval;
  AXLE/Pantograph/LeanDojo ideas for the Lean bridge + persistent REPL (built in-house, not depended on);
  AXLE/Goedel faithfulness → the adversarial faithfulness panel.
- **Ours, and the actual product.** The **elementary-method constraint**, enforced by the dual gate.
  Every Lean paper in the corpus is explicit that **"Lean-verified ≠ elementary"** — LEAP itself
  routes NT proofs through Mathlib's Vieta/AM-GM machinery and reports 100% on NT with *no* elementarity
  guarantee. MathAgent = **LEAP's harness + AlphaProof_Nexus's mechanisms (Codex as the prover) + a
  novel elementary gate that none of the source papers attempt.**

So when a new paper arrives, the question is never "do we switch to it?" — it is "**which building block
does it contribute, and does it graft onto the spine?**"

---

## 3. The defining mechanism — the elementary constraint

Two load-bearing facts shape everything (PLAN §5):

1. **Elementarity has no projection operator.** AlphaEvolve enforces a *numeric* property (integrality)
   with soft penalty + *hard projection* (rounding) + prompt nudge. You **cannot** "round" a class-group
   argument into a descent argument. Only the soft-penalty and prompt parts transfer; the hard part must
   be rebuilt as a **verification gate**, not a projection.
2. **A judge is never the final gate.** Same-model judges inherit the prover's blind spots and can be
   reward-hacked. Judges *rank and prune*; a **deterministic** mechanism *accepts*.

Therefore the constraint is enforced as **defense-in-depth, cheap-first**, and only the last layer is
authoritative:

| Layer | What it does | Authority |
|---|---|---|
| **0 — Framing** | Objective spec (`allowed_toolkit.yaml` / `denylist.yaml`) injected into every role; constrained-scope + paradigm scaffolding; retrieval bias to the elementary corpus. | soft (present, not obeyed) |
| **1a — Structural** | Deterministic validator over the typed step-ledger: justification ∈ closed vocabulary; DAG acyclicity; no dangling deps; one connected conclusion; discharged obligations. | deterministic *filter* |
| **1b — Prose scan** | Euphemism/denylist keyword scan → routes to review. | soft router (never rejects) |
| **2 — Adversarial review** | N independent critic/judge agents: every step elementary, no logical gap. | soft consensus (rank/prune) |
| **3 — Numeric grounding** | Exact-integer witness search re-checks the statement + finite case-covers + descent decreases. | deterministic, bounded scope |
| **4 — Lean dependency audit** | Compile → walk the kernel proof-term's transitive constant closure + `collectAxioms`; reject on a content-denylist hit (unless allowlisted) or any non-whitelisted axiom (catches `sorry`). | **deterministic, authoritative — the only place "elementary" is enforced** |

**Honest framing (locked):** v1 *induces, pressures, and ranks* elementarity (Layers 0–3) and *enforces*
it deterministically only at Layer 4. Calling a Layers-0–3-passed proof "elementary" would just relocate
the "Lean-verified = elementary" category error one step over. The system reports which layer
accepted/rejected, and which it could not decide.

Why Layer 4 is a **dependency audit**, not "it compiled": Mathlib *is* the source of the heavy machinery,
so a compiling proof can freely route through class groups or Mihăilescu. Why it needs a two-tier
allow/deny design: a naive namespace-prefix filter over the closure over-rejects nearly every elementary
proof (they all touch `WellFounded.fix`, `Decidable`, `Nat.rec`, …) — so a small **content denylist** is
backstopped by an **infrastructure allowlist** + an **"elementary-by-fiat" allowlist** (heavy-impl but
elementary-result APIs like `legendreSym`), with allowlist-wins precedence.

---

## 4. End-to-end pipeline

```
                          informal claim / goal
                                   │
                                   ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │ PROVER (Codex GPT-5.5-xHigh)  →  JSON step-ledger                      │
 │   • justified steps, depends-on DAG, exactly one conclusion           │
 └──────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
 ┌──────────────── DETERMINISTIC + SOFT GATE (agent/gates) ──────────────┐
 │ L1a structural · L1a/L3 obligations · L1b soft scan                    │
 │  ⇒ GateReport: REJECTED | NEEDS_REVIEW | PASSED_DETERMINISTIC          │
 └──────────────────────────────────────────────────────────────────────┘
        REJECT → repair          │ NEEDS_REVIEW → Layer-2 judge panel
                                   ▼
 ┌──────────────── CODEX HARNESS (agent/orchestrator) ───────────────────┐
 │ FlatDriver (Phase-1)  |  DagDriver (AND-OR DAG)                        │
 │   • semantic deep-hash memoization (cache_hits), acyclicity guard      │
 │   • RalphLoop per node (episodes → lessons-learned)                    │
 │   • Population/Elo + Bradley-Terry + PUCT select candidate decomps     │
 │   • Autoreason incumbent tournament refines a proof (no regression)    │
 │   • Liveness: NodeState machine + Budget (calls / repairs / replans)   │
 └──────────────────────────────────────────────────────────────────────┘
                                   │ assembled informal proof
                                   ▼
 ┌──────────────── AUTOFORMALIZATION (tools + formalize_bridge) ──────────┐
 │ Formalizer: ledger → Lean 4   →   compile (persistent server ~0.1s)    │
 │   └─ on error/audit-reject → REPAIR LOOP: inject diagnostics +         │
 │        retrieved Mathlib lemmas (Loogle + BM25 + NEURAL hybrid)        │
 └──────────────────────────────────────────────────────────────────────┘
                                   │ compiles
                                   ▼
 ┌──────────────── LAYER 4 — AUTHORITATIVE AUDIT ────────────────────────┐
 │ Audit.lean #audit: transitive const closure + collectAxioms           │
 │ lean_audit.py: axiom-whitelist + content-denylist (allowlist wins)     │
 └──────────────────────────────────────────────────────────────────────┘
                                   │ audit.passed
                                   ▼
 ┌──────────────── FAITHFULNESS PANEL ───────────────────────────────────┐
 │ adversarial lenses (back-translation/quantifiers/vacuity/strength),    │
 │ each default-unfaithful                                                │
 └──────────────────────────────────────────────────────────────────────┘
                                   ▼
        AUTHORITATIVE VERDICT  =  compiled  AND  audit.passed  AND  faithful
```

---

## 5. Component inventory (contracts + source pattern)

Everything that touches the model is a **Protocol**, so the Codex implementation and the offline test
stub are interchangeable; the entire harness is deterministically testable with scripted stubs.

| Subsystem | Key modules | Contract / role | Source pattern |
|---|---|---|---|
| **Gate (Layers 1–3)** | `gates/ledger.py`, `obligations.py`, `scanner.py`, `gate.py` | `evaluate(ledger, toolkit) → GateReport` | LongCat V_leg, LEAP reviewer, AlphaEvolve cascade |
| **Gate vocabulary** | `gates/allowed_toolkit.yaml`, `denylist.yaml`, `ledger.schema.json` | closed justification enum + obligation shapes + denylists | PLAN §2 boundary rulings |
| **Layer 4 audit** | `gates/lean_audit.py`, `lean_bridge.py`, `lean_server.py`, `lean/Audit.lean` | `audit_report(DependencyReport) → LeanAuditResult` | AlphaProof_Nexus SafeVerify, AXLE |
| **Focused prover** | `tools/codex_prover.py` | `Prover.prove(goal) → ledger` (+ Decomposer/Reviewer/Comparator/Faithfulness) | AlphaProof_Nexus (AlphaProof tool) |
| **Numeric grounding** | `tools/numeric.py` | exact-integer witness / residue-cover / descent checks | Axplorer, MathCode |
| **Retrieval** | `tools/retrieval.py` (Loogle), `semantic_retrieval.py` (BM25), `neural_retrieval.py` (bi-encoder) → `HybridRetriever` | `Retriever.retrieve(claim, error) → [lemma]` | LeanSearch/Loogle, LeanExplore |
| **Formalizer** | `tools/formalizer.py` | `Formalizer.formalize(prior, errors, lemmas) → Lean` | LEAP/Aristotle informal→formal |
| **Flat driver** | `orchestrator/driver.py` | Phase-1 plan→prove→gate→repair→judges | — |
| **Proof DAG** | `orchestrator/dag.py` | AND-OR DAG, **semantic** deep-hash memo, acyclicity, assemble | LEAP, AlphaProof_Nexus deep-hash |
| **Ralph loop** | `orchestrator/ralph.py` | per-goal episodes + lessons-learned | AlphaProof_Nexus |
| **DAG driver** | `orchestrator/dag_driver.py` | direct→decompose→review→recurse; terminal gate; population + refiner hooks | LEAP |
| **Population search** | `orchestrator/population.py` | Elo tournament, `fit_bradley_terry`, `select_puct` | AlphaProof_Nexus / AlphaEvolve |
| **Revision tournament** | `orchestrator/tournament.py` | Autoreason incumbent tournament (PUCT + Bradley-Terry, do-nothing wins ties) | Autoreason |
| **Faithfulness** | `orchestrator/faithfulness.py` | adversarial multi-lens, default-unfaithful | AXLE / Goedel |
| **Terminal gate** | `orchestrator/formalize_bridge.py` | formalize→compile→audit→faithfulness → `authoritative` | PLAN §5 Layer 4 |
| **Liveness** | `orchestrator/state.py` | NodeState + Budget (calls / repairs / **replans**) | PLAN §4.3 |
| **Observability** | `orchestrator/trace.py` | append-only JSONL events + run-record render | PLAN §4.4 |
| **Benchmark** | `benchmarks/datasets/`, `tools/answer_check.py`, `scripts/run_benchmark.py` | non-contaminative loader + SymPy answer-equivalence scorer + run records | MathArena ArXivMath |

---

## 6. Search & revision control — how PUCT, Bradley-Terry, and the Autoreason tournament fit

These are **search/quality machinery; they do not touch the gate.** Two distinct mechanisms at two
different layers (PLAN §4.1):

- **Population/Elo + Bradley-Terry + PUCT — *candidate selection* (`population.py`, used by
  `DagDriver._prove_via_population`).** When a goal admits several candidate *decompositions*, generate
  K of them, run a pairwise Elo tournament (Codex `Comparator` judges), fit **Bradley-Terry** latent
  strengths from the win matrix (a stable batch estimate over noisy online Elo), then expand them
  **PUCT-best-first** (exploit strength + explore under-visited). This is LEAP's stated future work
  ("branch prioritization, decomposition strategies, compute allocation"). It makes search *cheaper*.

- **Autoreason incumbent tournament — *revision control* (`tournament.py`, used by `DagDriver` as a
  `refiner`).** Given a *working* proof (the incumbent), each pass runs Critic (failure analysis only) →
  Author (revise) → Synthesizer (blind merge) → a pairwise judge panel; a challenger displaces the
  incumbent only if it **beats it by a margin AND passes the elementary gate**. "Do nothing" is
  first-class and wins ties; the loop stops when the incumbent survives **k=2** consecutive passes.
  Internally it reuses `population.py`'s Bradley-Terry (strengths from the judge win-matrix) and PUCT
  (which surviving candidate seeds the next pass). Its real job is the **synthesis-drift / elementary-
  incumbent guard**: a "better-sounding but non-elementary" revision cannot displace a correct
  elementary proof. It makes search *safer*.

**Priority note.** Both are harness improvements; neither is what blocks a first real result —
autoformalization-on-a-real-problem and an actual benchmark with run records are. They pay off once
there is a search worth prioritizing and an incumbent worth defending.

---

## 7. Memoization & the goal hash (a soundness-critical contract)

The DAG memoizes by a **deep hash of the goal statement**, so an identical sub-lemma arising on
different branches resolves to the *same* node and is proven once (`dag.py::goal_hash`). This key is
**soundness-critical**: a *false hit* (two different goals hashing equal) would reuse a proof of the
wrong lemma. Therefore the "semantic" canonicalization is deliberately **conservative — it only folds
*meaning-preserving surface differences*, never anything that could merge distinct statements**:

- ✅ Unicode NFC + math-symbol folding (`→`/`->`, `≤`/`<=`, `≠`/`!=`, `²`/`^2`, `ℤ`/`Int`, `∣`/`|`, …).
- ✅ Logical-connective synonym folding (`for all`/`∀`, `there exists`/`∃`, `iff`/`⇔`, `divides`/`∣`).
- ✅ Whitespace / operator-spacing / surrounding-markup normalization.
- ❌ **No variable renaming** (α-canonicalization): occurrence-order renaming of *free* single letters
  is unsound — it would merge `x ∣ y` with `y ∣ x`. True α-equivalence needs the binding structure,
  which we cannot reliably parse from mixed informal/Lean text, so we do not attempt it.
- ❌ **No commutative reordering** (we do not know precedence/associativity from text).

The cost of a *miss* is only recomputation; the cost of a false *hit* is unsound reuse — so we bias hard
toward never merging distinct goals. True semantic dedup would require parsing + typechecking (v2).

---

## 8. Autoformalization & retrieval

The **autoformalization wall** (faithful informal→Lean) is the empirically dominant failure mode in the
literature (>80% of Goedel's unsolved problems were *mis-formalized*). The system attacks it with a
Python-orchestrated **repair loop** (`formalize_and_audit(repair_iters=…)`): formalize → compile on the
persistent server (~0.1s) → on a compile error *or* an audit reject, feed the diagnostics + retrieved
real Mathlib lemmas back and re-formalize. A deep dive
([`autoformalization_repair.md`](autoformalization_repair.md)) concluded Codex's `/goal` mode should
**not** own this loop (a Python loop keeps control of retrieval/denylist/budget/audit, and the
persistent server's ~0.1s compile beats Codex re-running `lean` at ~60s/iter).

**Retrieval is a hybrid** (`HybridRetriever`): Loogle supplies *exact* names from compile errors;
BM25 supplies *lexical* relevance from the claim; the **neural bi-encoder** (`bge-small-en-v1.5` over
`name + signature + doc`, cosine top-k, optional reranker — the LeanExplore recipe) closes the
abbreviation/paraphrase gap that defeats lexical IR (`gcd` ↔ "greatest common divisor"). The neural
backend is an **optional dependency** with graceful degradation: absent the model, the hybrid silently
falls back to Loogle + BM25.

---

## 9. Benchmarking — non-contaminative by construction

v1's headline target is **MathArena's ArXivMath** (final-answer, contamination-resistant: problems are
reverse-engineered from very recent arXiv papers and refreshed monthly; only the gold `answer` ships, no
worked solution). Vanilla GPT-5.5-xHigh scores ~77.5% on the 03/2026 release — so the question is **how
much the harness adds on top of the base model.**

The integration is **non-contaminative by construction** (PLAN §8.3):

- The loader splits each item into a **prompt** (the `problem` text *only*) and a **held-out oracle**
  (the `answer`), keyed by `problem_idx`. The agent's context never contains the answer, the worked
  solution (none ships), or the `source` arXiv id (which would let it fetch the originating paper).
- Downloaded data (which contains answers) is cached **outside the repo** (under the gitignored
  `.lake/`) and is **never committed**; the repo ships only the adapter, the scorer, a manifest, and a
  tiny *synthetic* fixture for tests — no real ArXivMath problems in git history.
- Scoring mirrors MathArena: **SymPy symbolic-equivalence** on parsed answers, with normalized
  string/numeric fallbacks (`tools/answer_check.py`); we already depend on SymPy.
- Every run emits a structured run record (the toolchain hash + denylist version + per-problem
  verdict), so workflow configurations are compared fairly.
- v1 is number theory only, so the default harness target is the **`problem_type` ⊇ "Number Theory"**
  subset; the full set is available for base-model comparison.

> Note the genre gap: ArXivMath is *final-answer*, our harness is a *proof* system. The bridge is that
> an elementary proof of a "find all…/compute…" NT problem *yields the answer as a byproduct*; the
> answer is graded, and the proof + elementary gate are the value the harness adds over a bare answer.

---

## 10. Design invariants (the rules that must not be broken)

1. **The gate is the product.** A correct *non-elementary* proof is a *failure*. `elementary_compliance`
   is a binary admit/reject gate *before* any weighted scoring (`rubric.yaml`).
2. **Only Layer 4 certifies "elementary."** Layers 0–3 pressure/filter; never report a
   gate-passed-but-unaudited proof as elementary.
3. **Never trust compile-success or hammer-success** — always audit the proof term. `simp`/`nlinarith`/
   `decide` can silently pull heavy lemmas.
4. **Judges rank; deterministic mechanisms accept.** No single LLM judge is a hard gate.
5. **The memo key never merges distinct goals** (§7).
6. **Everything terminates.** Every loop is bounded by `Budget` (calls / repairs / replans) and
   transitions to `EXHAUSTED` with honest failure rather than retrying forever.
7. **Everything is a recorded, comparable experiment.** JSONL trace + run record + recorded
   (toolchain hash, denylist version).
8. **Stubs mirror Codex.** Every model-touching component is a Protocol with a deterministic offline
   stub; the offline suite never calls a model or Lean.
9. **The constraint spec is untrusted-input-gated.** Memoized sub-lemmas and retrieved premises pass the
   same gate before reuse; the knowledge corpus does not silently expand a problem's available facts.

---

## 11. Status & roadmap pointers

- **Built / validated now:** [`build_status.md`](build_status.md) (timestamp-stamped; component table +
  the implemented-vs-remaining comparison against the design memo).
- **Staged plan & scope decisions:** [`../../agent/PLAN.md`](../../agent/PLAN.md) (§9 roadmap, Phases 0–5).
- **Evidence base:** [`literature_design_implications.md`](literature_design_implications.md),
  [`paper_extractions.md`](paper_extractions.md).
- **Per-subsystem build records:** [`lean_layer4_and_population.md`](lean_layer4_and_population.md),
  [`formalization_bridge.md`](formalization_bridge.md), [`autoformalization_repair.md`](autoformalization_repair.md),
  [`codex_harness.md`](codex_harness.md).
