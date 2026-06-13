# MathAgent — Build Plan

> **Status:** v1 design. Living document. *(Revised after a 5-lens adversarial review — see
> [`../research/docs/plan_redteam.md`](../research/docs/plan_redteam.md).)*
> **Scope of v1:** an agentic system that solves **number-theory problems by elementary means only**.
> **Decisions locked for v1** (Ishan): (1) **harness-first, training-free** — orchestrate frontier general
> LLMs, no model training in v1; (2) **Lean is soft/advisory** in v1 (the *authoritative* hard gate is a
> later phase, but a minimal version is spiked early — §7, §9); (3) **tiered target** — IMO-tier NT first as a
> ladder, then specific research re-proof targets; (4) the repo reorg is done.
> **Evidence base:** [`../research/docs/literature_design_implications.md`](../research/docs/literature_design_implications.md)
> (synthesis of 20 systems papers) and [`../research/docs/paper_extractions.md`](../research/docs/paper_extractions.md).

---

## 1. Mission and what "good" means

MathAgent should become very good at mathematics; **v1 is deliberately narrow**: number theory, solved with
the **elementary / IMO-usable toolkit only**. "Good" for v1 = on a held-out, expert-validated NT problem set,
the system produces **correct proofs that pass the elementary gate**, at a useful rate, with run records that
let us compare workflow configurations fairly.

The elementary restriction is not a footnote — **it is the product.** A system that proves `x²+1=y³` via
elliptic curves has failed, even though the proof is correct. So the plan is organized around two questions:

1. How do we build a capable training-free proving harness for NT? (§4)
2. How do we **induce and pressure** "elementary only" — and where, exactly, can we *hard-enforce* it? (§5)
   ← the defining problem.

> **Honest framing (load-bearing).** v1 does **not** deterministically *enforce* elementarity. It *induces,
> pressures, and ranks* it (soft + structural mechanisms), and grounds truth numerically. The only
> deterministic accept/reject of *proof method* is the Lean dependency audit (Layer 4, §5), which v1 spikes
> early but does not rely on at scale. Calling a "gate-passed" v1 proof "elementary" would just relocate the
> "Lean-verified = elementary" category error one step over. We report status honestly (§8).

### Non-goals for v1
- No model training / RL / fine-tuning (reconsidered in v2, §9).
- No requirement that proofs compile in Lean (Lean is advisory; §7).
- No breadth beyond number theory (geometry, combinatorics, analysis come later).

---

## 2. The elementary toolkit (the positive definition)

A final proof may use **only** the tools below. Studying a non-elementary proof **for inspiration** is allowed;
using it as a final step is not (existing rule:
[`instructions/elementary_proof_rules.md`](instructions/elementary_proof_rules.md)).

**Core (uncontested):** integer arithmetic; divisibility; congruences / modular arithmetic and the Chinese
Remainder Theorem; gcd/lcm and coprimality (Bézout, Euclid's lemma); parity; elementary factorization;
**mathematical induction** (and its equivalents, finite/infinite **descent** and the **extremal /
well-ordering** principle); the **pigeonhole** principle; inequalities and **size/bounding** arguments
(including **AM–GM and elementary symmetric inequalities** — these are elementary, *not* a non-elementary
smell); **polynomial reasoning over ℤ** (factor theorem, rational-root theorem, `a−b ∣ P(a)−P(b)`);
**multiplicative functions and divisor counting** (`φ`, `τ`, `σ` identities); **counting / double-counting**;
orders of elements and Fermat–Euler; `p`-adic valuation `v_p` and **lifting-the-exponent (LTE)**; and the named
methods curated in [`../knowledge/methods/`](../knowledge/methods/).

A final proof may **not** rely on (the denylist; mirrors
[`instructions/disallowed_final_methods.md`](instructions/disallowed_final_methods.md)): UFDs in rings of
algebraic integers; ideals / class groups / class field theory; algebraic number fields; elliptic curves;
modular forms / modularity; algebraic geometry; **`p`-adic theory beyond `v_p` and elementary congruences**
(ℚ_p completions, Newton polygons, Hensel beyond first-order lifting); Catalan / Mihailescu; Baker's theory of
linear forms in logarithms; analytic number theory machinery (Gauss-sum / `L`-function evaluations); and
computational brute force unless the finite bound is elementary and explicit.

### 2.1 Boundary rulings (the contested 50/50 calls — the closed Layer-1 vocabulary can't be closed without these)
These are pinned now and live in [`gates/allowed_toolkit.md`](gates/) so judges (and the ledger) adjudicate
consistently rather than per-run:

| Tool | Ruling | Rationale |
| --- | --- | --- |
| **Pell fundamental-solution theorem** (all solutions of `X²−DY²=1` from a fundamental unit) | **ALLOWED as a citable fact** | Has an elementary descent / continued-fraction / pigeonhole proof; do not require re-deriving via the unit group. List citable Pell facts in `pell_equation.md`. |
| **Roots-of-unity filter** (extract `Σ_{n≡r}` via summing over ζ) | **ALLOWED** | Finite, mechanical, no analysis. |
| **Generating functions / formal power series** | **ALLOWED as formal manipulation** | Flag for review when convergence/complex-analytic facts are invoked. |
| **Dirichlet approximation / pigeonhole-on-the-circle** | **ALLOWED** | Pigeonhole. |
| **Zsygmondy / primitive-prime-divisor** | **ALLOWED WITH CITATION** | Elementary but hard; treat like LTE — citable, not re-derived. |
| **Quadratic reciprocity + Jacobi symbol + Euler's criterion + supplementary laws** | **ALLOWED** | Replaces the vague "elementary parts of QR" in earlier drafts. |
| **Gauss-sum / analytic QR proofs; cubic/quartic (biquadratic) reciprocity** | **DISALLOWED unless whitelisted per-problem** | Analytic or higher-reciprocity; the Ljunggren elementary proofs need quartic residues — handle as an explicit per-problem whitelist (§8.1). |
| **LTE** | **ALLOWED — pin the exact statement incl. the `p=2` clause** | The `p=2` case is where errors hide. |
| **`v_p` valuation** | **ALLOWED** | Integer-valued valuation + LTE are elementary; ℚ_p machinery is not (see denylist). |

### 2.2 Knowledge-base gaps to fill (IMO-standard tools missing/empty today)
`knowledge/methods/descent.md` is an empty template (descent is *the* central NT method — fill first, §6).
Also missing as first-class methods: **LTE**, **orders / primitive roots / Fermat–Euler**, **quadratic
residues / Jacobi**, **CRT & modular arithmetic**, **`v_p` bookkeeping**, **bounding / size arguments**, and
**pigeonhole**. (The frontier LLM already *knows* most of these — method files mainly bias search and seed Lean
lemma targets, so authoring beyond `descent` can move to Phase 2; §6.)

---

## 3. Design principles

1. **Training-free first — but calibrate what the evidence shows.** A frontier-LLM harness can produce correct
   proofs of elementary-*statement* NT problems. It does **not** by itself produce elementary-*method* proofs:
   LEAP's reported 100% on Lean-IMO-Bench NT let the model use *all* of Mathlib (no elementarity guarantee —
   verified in `paper_extractions.md`), and AlphaProof Nexus's elementariness came from **SafeVerify**, an
   axiom/dependency gate (a Layer-4 mechanism). So the capability evidence supports the harness; the
   *elementarity* evidence argues for bringing the Layer-4 audit **forward** (§9), not deferring it. Train a
   model only if cost forces it (§9).
2. **The constraint is induced softly and *enforced* only deterministically, never by a single judge.** Soft
   mechanisms *rank and prune*; an LLM judging its own elementariness is necessary but never sufficient
   (same-model judges inherit the prover's blind spots; reward-hacking is observed in the literature). The
   authoritative accept/reject is Layer 4. (§5.)
3. **"Lean-verified ≠ elementary."** A proof can compile against Mathlib while invoking class groups or
   Mihailescu. The gate is a **proof-term dependency audit** (content-denylist + infrastructure-allowlist),
   not "it compiled." (§5, §7.)
4. **Ground everything numerically.** Cheap integer/residue search kills false statements and false lemmas
   early. (Caveat: it gives *little* assurance for true, sparse-solution targets and can miss subtle
   misformalizations — so expert statement-validation stays mandatory; §8.3.)
5. **Defense in depth + cheap-first + honest reporting.** An evaluation cascade runs cheap structural checks
   before expensive judge/Lean passes; we always report which layer accepted/rejected and which it could not
   decide.
6. **Everything is a recorded, machine-comparable experiment.** Every run emits a structured trace (§4.4) +
   a rendered [run record](../benchmarks/evaluation/run_record_TEMPLATE.md); workflows are compared only on a
   fixed problem with fixed allowed context and metric weights (existing branch protocol).

---

## 4. Architecture — a training-free proving swarm (staged)

The target pattern is **LEAP / Aristotle / Autoreason / Pantograph(DSP)**: an informal blueprint decomposed
into an AND-OR proof DAG, attacked by role-specialized LLM agents under a compile/critique→repair loop, with
memoized sub-lemmas and an incumbent tournament for revision control.

> **But v1 ships a deliberately smaller slice.** The full DAG + memoization + multi-judge swarm is over-built
> for the Phase-1 exit ("prove ≥1 target"); memoization only pays off if sub-lemmas recur, which is unproven
> on a fresh set. So:
> - **Phase 1 = a flat, sequential driver** (no DAG engine, no goal cache, no incumbent tournament), with
>   **two roles**: a Prover and one adversarial Critic/Judge. Instrument sub-lemma reuse and repair-loop
>   convergence to *measure* whether the heavier machinery will pay off.
> - **Phase 2 promotes** the AND-OR DAG, hierarchical memo, goal cache, incumbent tournament, retrieval, and
>   the multi-judge panel — **gated on the measured reuse signal** (the LEAP ablation that justifies the DAG —
>   Advanced-NT 66.6%→100% — is real, so this is *deferred, not deleted*).
> - The v1 state API is designed **parallel-ready from day one** (immutable / compare-and-set node updates,
>   idempotent attempts) so "single → swarm" is configuration, not a rewrite.

> **Status update (Phase 2 underway).** The AND-OR DAG + deep-hash memoization and the AlphaProof_Nexus
> Ralph-loop harness are now **built and tested** ([`orchestrator/dag.py`](orchestrator/dag.py),
> [`ralph.py`](orchestrator/ralph.py), [`dag_driver.py`](orchestrator/dag_driver.py)), with
> **Codex / GPT-5.5-xHigh wired as the focused-prover tool** ([`tools/codex_prover.py`](tools/codex_prover.py))
> in place of AlphaProof, plus a CLI ([`scripts/prove.py`](../scripts/prove.py)). See
> [`research/docs/codex_harness.md`](../research/docs/codex_harness.md). The incumbent tournament, retrieval,
> and multi-family judge panel remain Phase-2 TODOs.

### 4.1 Components (and their source patterns)
| Component | Role | Status | Source |
| --- | --- | --- | --- |
| **Flat driver** | Sequential plan → prove → critique → gate loop. | built (P1) | — |
| **AND-OR proof DAG + memo** | Blueprint → sub-lemma tree; proven nodes cached/reused; acyclicity guard. | **built** | LEAP, Aristotle MCGS, Pantograph |
| **Deep-hash goal cache** | Memoize by normalized statement hash; reuse a sub-lemma across branches. | **built** | AlphaProof_Nexus deep-hash |
| **Focused prover (Codex)** | GPT-5.5-xHigh via `codex exec` proves a node → step-ledger (AlphaProof's role). | **built** | AlphaProof_Nexus (AlphaProof tool) |
| **Ralph loop** | Per-node episodes: prove → gate → carry "lessons learned" → repeat. | **built** | AlphaProof_Nexus |
| **Decomposition reviewer** | Gate a blueprint on "does it simplify?" + "is it elementary?" before commit. | **built** | LEAP reviewer |
| **Revision controller** | Incumbent tournament; "do nothing" first-class; failure-analysis *before* revision; k=2 stop. | P2 TODO | Autoreason |
| **Evaluation cascade** | Cheap structural + numeric checks gate expensive judge/Lean passes. | built (P1) | AlphaEvolve |
| **Population / Elo over sketches** | Generate K candidate decompositions, rank by a pairwise-comparison Elo tournament (+ Bradley-Terry MLE, PUCT), try best-first. | **built** ([`population.py`](orchestrator/population.py)) | AlphaProof_Nexus/AlphaEvolve |
| **Lean Layer-4 auditor** | Proof-term dependency + axiom audit (the authoritative gate). | **built + live-validated** ([`gates/lean_audit.py`](gates/lean_audit.py), [`lean_bridge.py`](gates/lean_bridge.py), [`lean/Audit.lean`](gates/lean/Audit.lean)) | AlphaProof_Nexus SafeVerify, AXLE |
| **Tools** ([`tools/`](tools/)) | Numeric/witness search, Codex prover, elementary auditor, retrieval, CAS, Lean bridge. | numeric + Codex + Lean built | Axplorer, MathCode, LeanDojo/Pantograph |

### 4.2 The swarm roles ([`roles/`](roles/))
v1 uses the **bold** two; the rest are added in Phase 2. All are written as separate prompts now.
| Role | Job | Phase |
| --- | --- | --- |
| **Prover** | Produce an elementary proof for a node as a **step ledger** (§5), or decompose further. | **1** |
| **Critic / Elementary Judge** | Adversarially hunt logical gaps **and** smuggled non-elementary steps; score admissibility; prune. | **1** |
| Planner / Blueprinter | Problem → strategy → AND-OR decomposition; retrieves methods. | 2 |
| Lean Liaison *(advisory)* | Formalize key lemmas; read diagnostics; run the dependency audit. | 1 (spike) → 3 |
| Evaluator | Score the admitted proof; write the structured trace + run record. | 1 |

### 4.3 Control loop (per problem) + liveness model
**Happy path:** Plan → (numeric statement check) → prove node(s) → Critic/Judge review → assemble → final gate
(§5) → score & record.

**Liveness (the most likely real failure is a silent stall / unbounded repair-or-replan loop):**
- Per-node **state machine**: `{open, in_progress, proven, failed-elementary, failed-gap, exhausted}`.
- Hard caps: max repair iters/node, max re-plan depth, per-node and per-problem token/wall-clock budgets;
  on exhaustion → deterministic transition to `exhausted` and honest failure, never retry-forever.
- Timeout + cancel on every LLM/tool call; any contract-violating model output is a **typed error → bounded
  repair**, not a silent pass or infinite retry.
- **Default budgets (v1, costable):** judge panel `N=1` in Phase 1 (`N=3` in Phase 2); `max_repair_iters=6`;
  `max_replan_depth=2`; per-problem cap `≈150` LLM calls / a fixed `$` ceiling. Tune from measured runs.

### 4.4 Observability — machine-readable run trace
"Everything is a comparable experiment" requires more than a prose template. Emit an append-only **JSONL event
stream**: `{ts, run_id, node_id, role, model, tokens_in, tokens_out, cost, latency, verdict, repair_iter,
cache_hit, gate_outcome, failing_layer, failing_step_id}`. The prose run record becomes a rendered view. Record
the **(Lean+Mathlib toolchain hash, denylist version)** with every run — Layer-4 verdicts are toolchain-relative
and otherwise not comparable across runs.

---

## 5. The elementary gate — how the constraint is induced and where it is enforced

Two foundational facts shape every choice:
- **Elementarity has no projection operator.** AlphaEvolve enforces a numeric property (integrality) with soft
  penalty + *hard projection* (round) + prompt nudge. You **cannot** "round" a class-group argument into a
  descent argument. Only the soft-penalty and prompt parts transfer; the hard part is rebuilt as a
  **verification gate**, not a projection.
- **A judge is never the final gate.** Judges rank/prune; a deterministic mechanism accepts.

The gate is **graded, defense-in-depth**. **Be precise about what each layer does:** Layers 0–3 *pressure and
filter*; only Layer 4 *enforces method admissibility*.

### Layer 0 — Framing (soft; present, not obeyed)
- **Objective spec injected into every role's context** ([`gates/allowed_toolkit.md`](gates/),
  [`gates/denylist.md`](gates/)). An unwritable file guarantees the constraint is always **present**, not that
  it is **obeyed** — obedience is pressured by Layers 1–3 and enforced only by Layer 4.
- **Constrained-scope framing** (Autoreason's strongest finding: bounding the solution space flips refinement
  from worst to best — olympiad NT is naturally scope-bounded).
- **Paradigm scaffold** — force an intermediate elementary-arithmetic reasoning trace before the write-up
  (BRIDGE: prompt-injected paradigm measurably reshapes output).
- **Retrieval bias** toward the curated elementary corpus in [`../knowledge/`](../knowledge/).
- **No trust-by-cache:** every artifact entering a proof — memoized sub-lemmas *and* retrieved premises —
  passes the same Layer-1/2 checks before reuse. The knowledge corpus is untrusted input, gated at ingestion.

### Layer 1 — Structural validation (split into deterministic vs soft)
The Prover emits each proof as a **typed step ledger** (schema frozen *before* Phase 1):
```
step := { id, statement, justification: <enum from allowed_toolkit>, depends_on: [id...],
          method_ref?, obligations?: {...} }
```
- **(1a) Truly deterministic validator** over the *typed* ledger: justification ∈ allowlist enum; DAG
  acyclicity; no orphan/dangling `depends_on`; Unicode/NFC + symbol normalization. Malformed/unparseable ⇒
  deterministic **REJECT-AND-REPAIR**, never a silent pass.
- **(1b) Free-text denylist / euphemism scan** (keywords like "class group", "elliptic curve", "Mihailescu",
  with an allow-context list for overloaded NT words — *order, unit, norm, valuation, descent, ideal*). This is
  **reclassified as a soft router to review**, *not* a deterministic gate (a euphemism scan cannot be sound).
- **Discharged obligations make the ledger carry load-bearing content** (a "descent"/"bounding" tag alone is
  worthless — and these elastic tags are exactly where heavy steps hide). Per-method schemas require, e.g.:
  case-split → enumerated cases + a covering claim (Layer 3 re-checks the cover); descent/Vieta jumping →
  explicit measure + proof it strictly decreases and stays in-domain (+ companion-root integrality);
  square-splitting → reference to the prior coprimality step; bound → the explicit inequality with strictness.
  Any use of an elastic category is routed to mandatory adversarial review.

### Layer 2 — Adversarial review (soft consensus; rank/prune only)
N independent Critic/Judge agents (varied prompts/seeds; varied *model families* when available — otherwise
this is "prompt/seed diversity," not true independence, and is recorded as such). Each must affirm: every step
elementary **and** no logical gap. Disagreement ⇒ reject/repair; "reject / do nothing" is first-class.

### Layer 3 — Numeric grounding (deterministic, but bounded scope)
The witness/search tool confirms the statement + computational lemmas on small cases and hunts counterexamples,
and **re-runs the finite case-checks** the ledger claims rather than trusting prose. *Scope caveat:* this kills
false statements/lemmas but gives little assurance for true sparse-solution targets and can miss subtle
misformalizations — expert statement validation remains mandatory (§8.3).

### Layer 4 — Lean dependency audit (deterministic, **authoritative**) — ✅ BUILT + LIVE-VALIDATED
> **Status:** implemented and validated against **Lean 4.30.0**. [`gates/lean/Audit.lean`](gates/lean/Audit.lean)
> walks a compiled proof's transitive constant closure + `collectAxioms` and emits a JSON report;
> [`gates/lean_audit.py`](gates/lean_audit.py) (the toolchain-independent decision logic) classifies it;
> [`gates/lean_bridge.py`](gates/lean_bridge.py) runs the whole pipeline. Confirmed live: `n+0=n`
> **passes**; a `sorry` proof is **rejected** by the axiom gate (`sorryAx`). The content-denylist path
> (Mathlib namespaces) is unit-tested with synthetic reports (needs a Mathlib build to trigger live).

The only non-gameable gate. **Design it correctly — a naive transitive-closure / namespace-prefix filter
over-rejects nearly every elementary proof**, because elementary Mathlib lemmas internally depend on
`WellFounded.fix`/`Acc.rec`/`Nat.rec`, `Decidable`/`DecidableEq` instances, `SizeOf`, and algebra-hierarchy
instance projections. So:
1. **Two-tier dependency audit over the kernel proof term:**
   - a small **content-bearing denylist** of fully-qualified declarations dispositive on appearance
     (`EllipticCurve`, `IsDedekindDomain`, `ClassGroup`, `NumberField.RingOfIntegers`, Mihailescu, …);
   - an explicit **infrastructure allowlist** always permitted (`WellFounded.fix`, `Acc.rec`, `Nat.rec`,
     `Decidable`/`DecidableEq` instances, `SizeOf`, hierarchy instance projections);
   - classify each closure constant by `ConstantInfo` kind (instance / def / theorem / axiom) so plumbing is
     never mistaken for mathematical content. Namespace-prefix matching is at best a coarse heuristic.
   - **"Elementary-by-fiat" allowlist** for allowed-but-heavily-implemented APIs (e.g. Mathlib's Legendre/QR
     `legendreSym` whose internals touch Gauss sums / cyclotomic files): exempt their *internal* provenance
     from the closure audit and document the trust boundary. (Re-proving them elementarily is too costly for
     v1.) This resolves the "allowed method, non-elementary Mathlib proof" trap.
2. **Axiom integrity (kernel `collectAxioms`)**: accepted axiom set ⊆ `{propext, Classical.choice, Quot.sound}`
   — this, not a source-AST scan, is how `sorry`/injected-axiom smuggling is caught.
3. **AST legality** (LongCat `V_leg`-style, deterministic): statement unchanged; no `unsafe`/`macro`/redefinition
   of `pow`/`Dvd`/constants; forbidden `import`/`open` rejection — a coarse first filter backstopped by (1).
4. **Tactic-palette whitelist** — *necessary but not sufficient* (`simp`/`nlinarith`/`decide`/`polyrith`/`exact?`
   can silently pull heavy lemmas → always re-audit the term).

> **Bottom line for v1.** v1 delivers Layers 0–3 (a strong *nudge + filter + honest-report* system) **plus a
> minimal Layer-4 spike** (§9) that proves the authoritative gate is real on at least the descent/Vieta hard
> case. We do not claim v1 *enforces* elementarity at scale; we claim it pressures it hard and tells the truth
> about what it could and couldn't verify.

---

## 6. Knowledge base plan ([`../knowledge/`](../knowledge/))

1. **Fill `descent.md` first** (it is a blank template). Make the Lean angle a concrete deliverable, not a
   "wall": author a reusable **Lean descent / strong-induction combinator** parameterized by `measure : α → ℕ`
   + a per-step decrease lemma (`termination_by`/`decreasing_by`, `Nat.strongRecOn`, `WellFounded.min`,
   `Nat.find`, `Nat.lt_wfRel`), so the LLM only supplies the measure + the decrease proof.
2. **Method ontology for the gate.** Extend each method's frontmatter with the justification enum it licenses
   (`justifies: [congruence, descent, ...]`) and `allowed_in_final_proofs`, so Layer 1a validates ledger tags
   against real method files. The contested tools get their ruling from §2.1.
3. **Author the remaining core methods in Phase 2** (modular/CRT, bounding, orders & Fermat–Euler, LTE, QR/
   Jacobi, `v_p`, pigeonhole) — the frontier LLM already knows these, so they bias search + seed Lean targets
   rather than block Phase 1.
4. **Promote Tagebuch identities** from `knowledge/library/untriaged_tagebuch/` only with a trigger pattern + proof
   status (existing policy). Kieren's Vierzahlensatz pipeline is the model worked example.

---

## 7. Lean track — advisory in v1, authoritative gate later (spiked early)

Local Lean/Mathlib is **not installed**. Note: **Lean/elan/lake run natively on Windows**; the real constraint
is that some *harness binaries and tracing tooling* (OpenGauss/MathCode) are Linux-only — mine their ideas,
don't depend on them; use WSL2 only if a specific tool needs it.

- **v1 (advisory) + early spike (Phase 1, parallel):** install Lean+Mathlib; build the **minimal Layer-4
  auditor** and demonstrate it **accepts a genuine elementary proof and rejects one citing a denylisted
  declaration — including on a descent/Vieta proof** (the hard case), not just a trivial one. The audit should
  read the compiled `Environment` directly (the kernel is the source of truth), not a serializer.
- **Contingency (stated up front):** if Layer-4 formalization of descent-class proofs proves intractable within
  budget, v1's elementary guarantee is **downgraded to soft-only** and scope is reassessed — we will not pretend
  otherwise.
- **Substrate (Phase 3, scale):** adopt **Pantograph** (Lean-native REPL, and-or tree, `sorry`-extraction,
  proof-term export) with a persistent REPL for fast checks (MathCode: ~90 s warmup, then ~0.4 s/check). Evaluate
  building the auditor in-house vs adapting AXLE — but never trust compile-success.

**Where elementary-NT formalization is genuinely hard** (budget for it): finite/infinite **descent** (the
Lean-accepted well-founded *measure* is the wall — mitigated by the combinator in §6); **gcd/coprimality case
splits** (long, brittle); **Vieta jumping** (Mathlib's `esymm`/`Polynomial` route is the *wrong,
non-elementary-flavored* path — hand-build the elementary version); **Pell/recurrence** faithfulness. Mathlib
*helps* with `ZMod`/`Int.ModEq`/`Nat.gcd_*`/`Nat.Prime` and `omega`/`interval_cases`/`norm_num`/`decide`; it is
also the *source* of the heavy machinery — exactly why Layer 4 is a dependency audit, not a compile check.

---

## 8. Benchmark and evaluation plan ([`../benchmarks/`](../benchmarks/))

### 8.1 Problem ladder (the "tiered" target) — sorted by *known-elementary-proof existence, length, and method count*
| Tier | Content | Examples |
| --- | --- | --- |
| **T0 — warmup** | trivial divisibility / congruence; smoke-tests for harness + gate. | (to author) |
| **T1 — olympiad NT** | IMO/shortlist descent, Diophantine, divisibility, orders, LTE. | `imo_1988_finite_descent` (statement to author) |
| **Calibration** | results with a **known short elementary proof** — capability checks, *not* novel research. | `x2_plus_1_eq_y3` (complete), `hardy_wright_theorem_120` |
| **T2 — hard olympiad NT** | multi-method, harder shortlist. | (to author) |
| **T3-hard — known-hard re-proofs** | elementary proofs that are real, *long*, and a different species. | `ljunggren_equation` |

**Corrections forced by the math (these were mis-tiered before):**
- **Drop generic "Mordell curves."** `y²=x³+k` for general `k` is **not** elementarily solvable — it needs
  Baker or elliptic descent (both denylisted). Replace with an explicit, vetted list of *specific* `k` that
  have an elementary obstruction (record `k` + mechanism, e.g. "`y²=x³+7` unsolvable via mod-4"), and note the
  general equation is **out of scope by §2**.
- **Pin Ljunggren exactly:** `x²+1 = 2y⁴`, solutions `(x,y) = (1,1), (239,13)`. Its known elementary proofs use
  **quartic-residue** machinery (a per-problem whitelist item, §2.1) and are book-length — its own *T3-hard*
  bucket, **not** "a bit harder than T2."
- **`x²+1=y³` and Hardy–Wright Thm 120 are *calibration*, not research** — they have short textbook elementary
  proofs.
- **Triangular-number theorem:** pin the exact statement; if it is Gauss's three-triangular-numbers theorem,
  flag its dependence on the three-squares theorem and decide whether three-squares is a citable input.

Fill each folder's `problem.md` + `allowed_inputs.md` (most are placeholders) **and numerically + expert-validate
every statement** before running — a mis-stated theorem can be vacuously true (§3.4).

### 8.2 Metrics — DECIDED (not an open question)
The current rubric ([`rubric.yaml`](../benchmarks/evaluation/rubric.yaml)) scores `elementary_compliance` at
0.10 and `lean_compilability` at 0.20 — so a correct *non-elementary* proof outranks a clumsy elementary one,
directly contradicting §1. **Decision:** `elementary_compliance` is a **binary admit/reject gate applied before
weighted scoring**. Among *admitted* proofs, renormalize over `{correctness, novelty, generalizability,
simplicity (= step×complexity)}`; `lean_compilability` moves to a **Lean-track-only** rubric. This makes the
Phase-1 exit criterion ("a gate-passing proof") well-defined. `rubric.yaml` is updated to reflect this.

### 8.3 Anti-contamination & honest measurement
- Keep problem folders free of standard proofs (existing contamination policy); mark example-adjacent folders.
- Build a **fresh, expert-validated elementary-NT eval set**; do **not** import miniF2F/ProofNet headline
  numbers as capability evidence (contamination-prone, largely non-NT, unrestricted-tactic regimes).
- Compare workflows only on fixed problem + fixed context + fixed weights + recorded toolchain/denylist version.

---

## 9. Roadmap

| Phase | Goal | Key deliverables | Exit criterion |
| --- | --- | --- | --- |
| **0 — Foundations** *(done)* | Understand repo; reorganize; plan; literature synthesis. | Reorg ✓, this plan ✓, [`literature_design_implications.md`](../research/docs/literature_design_implications.md) ✓. | — |
| **1 — MVP slice (flat) + gate + Layer-4 spike** | Minimal end-to-end prover with a working soft+structural gate and a *proven-real* hard gate on the hard case. | (a) `gates/allowed_toolkit.md` (+ §2.1 rulings) + `denylist.yaml`; (b) **frozen step-ledger schema** + deterministic validator (1a) + soft scanner (1b); (c) Prover + Critic/Judge prompts; (d) numeric/witness tool; (e) flat sequential driver + liveness caps + JSONL trace; (f) author 1 T0 + the `imo_1988` (or use `x2_plus_1_eq_y3`) statement, numerically validated; (g) fill `descent.md` + the Lean descent combinator; (h) **Layer-4 spike**: accept a genuine elementary descent/Vieta proof, reject a denylisted one. | A correct, gate-passing proof of ≥1 target **and** the Layer-4 spike demonstrably accepts an elementary proof while rejecting a planted heavy one. |
| **2 — DAG/memo/swarm (gated on measured reuse) + methods + retrieval** | Promote the heavier harness *only where measurement says it pays*. | AND-OR DAG + memo + goal cache + incumbent tournament + N=3 judges; retrieval over `knowledge/`; author core methods (LTE, orders, QR, `v_p`, pigeonhole, modular/CRT, bounding). | Beats Phase-1 on T1; attempts T2; reproducible workflow comparison; measured sub-lemma reuse justifies the DAG. |
| **3 — Lean auditor at scale** | Scale Layer 4 from spike to routine gate. | Pantograph bridge; in-Environment dependency audit + `collectAxioms` + AST legality; % compilable on key lemmas. | Audit routinely accepts elementary proofs (≥3, zero false positives) and rejects planted heavy ones. |
| **4 — Research targets + hardening** | Push T3-hard / calibration→research; harden gate; optionally add small helper models. | Specific-`k` Mordell + Ljunggren attempts; hardened denylist/audit; (optional, *hybrid*) an elementary-compliance classifier or method-selector. | A defensible elementary attempt on ≥1 T3-hard target with audit evidence. |
| **5 — (optional v2) trained prover** | Only if frontier-LLM per-problem cost dominates. | Specialized step-prover + RL with elementary reward + V_leg gate (Goedel/BFS/LongCat patterns). | Lower cost at equal/better gate-passing rate. |

---

## 10. Risks, anti-patterns, and open decisions

| Risk | Mitigation |
| --- | --- |
| **"Lean-verified = elementary" category error** (#1) — *and its sibling, "gate-passed = elementary."* | Dependency audit over the proof term (Layer 4); honest reporting that Layers 0–3 only pressure (§1, §5). |
| **Layer-4 over-rejection** (naive closure fires on `WellFounded.fix`, `Decidable`, …). | Content-denylist + infrastructure-allowlist + `ConstantInfo`-kind classification + elementary-by-fiat allowlist (§5 Layer 4). |
| **Mathlib hammers smuggle heavy lemmas** (`simp`/`nlinarith`/`decide`). | Tactic whitelist + always re-audit the term; never trust hammer success. |
| **Import-whitelist circumvention** (banned result re-proved inline / pulled transitively). | Transitive dependency audit is mandatory; imports are a coarse first filter. |
| **Reward / judge gaming; elastic ledger tags hide heavy steps.** | Deterministic Layer-1a + Layer-4 are the real gates; obligation-carrying ledger; judges only rank/prune; vary judge prompts/seeds/families. |
| **Autoformalization / statement-faithfulness wall.** | Expert/equivalence-validate every statement; numeric + OEIS test-lemma sanity gate (bounded scope, §8.3). |
| **Silent stall / unbounded loop burning budget.** | Per-node state machine + hard caps + deterministic `exhausted` transition (§4.3). |
| **Cost blowups** (LEAP 46–3000 calls/problem). | Eval cascade, memoization (Phase 2), branch-budget caps, fast+strong model ensemble, costable default budgets. |
| **Benchmark contamination / over-claiming.** | Fresh expert-validated NT eval set; never port others' numbers. |
| **Diversity collapse** (search settles on one strategy). | Method-type diversity dimensions; incumbent-wins-ties + margin rule (Phase 2). |
| **Toolchain churn** (Lean/Mathlib coupling; denylist drift). | Pin one toolchain; version the denylist; record both per run; budget ongoing curation. |

### Open decisions (need Ishan/Kieren input)
1. **Frontier model + budget** for the harness (which model(s); per-problem call/$ cap → sets §4.3 defaults).
2. **Auditor build-vs-adapt** (§7): in-house in-Environment audit vs adapt AXLE/LeanDojo.
3. **Eval-set sourcing** (which shortlist years; which specific Mordell `k`; who validates statements).
4. *(Resolved: metrics gate-vs-weight — see §8.2. Resolved: Phase-1 = flat sequential — see §4.)*

---

## 11. Phase 1 checklist — status

**Built (this branch). Deterministic core is green: `make check` + 88 passing tests + `make demo`.**
- [x] `gates/allowed_toolkit.yaml` (incl. §2.1 boundary rulings) + `gates/denylist.yaml`.
- [x] Frozen step-ledger schema (`gates/ledger.schema.json`) + worked example (`gates/examples/squares_mod4.json`);
      deterministic validator 1a (`gates/ledger.py`), obligation checks (`gates/obligations.py`), soft scanner 1b
      (`gates/scanner.py`), composer (`gates/gate.py`).
- [x] **Prover** + **Critic/Elementary-Judge** role prompts (`roles/prover.md`, `roles/critic_judge.md`).
- [x] **Numeric/witness search** tool (`tools/numeric.py`, sympy, exact-integer): residue-cover, solution-set,
      and descent-decrease checks.
- [x] Filled `knowledge/methods/descent.md` (+ the reusable Lean descent-combinator plan).
- [x] Flat sequential driver + liveness state machine + budgets (`orchestrator/`) + JSONL run trace;
      end-to-end `python -m agent.demo`.
- [x] Applied the §8.2 rubric decision in `rubric.yaml` (elementary compliance = binary gate).

**Remaining for Phase 1 → 2:**
- [ ] Wire a **real LLM-backed Prover/Judge** behind the `Prover`/`Judge` protocols (replaces the scripted stubs).
- [ ] Author 1 T0 problem folder + author/numerically-validate `benchmarks/problems/imo_1988_finite_descent/`
      (or start on the complete `x2_plus_1_eq_y3`).
- [ ] **Layer-4 spike:** install Lean+Mathlib; accept an elementary descent/Vieta proof, reject a denylisted one.
- [ ] First real end-to-end run on a branch with a run record.

---

*Sources: [`../research/docs/literature_design_implications.md`](../research/docs/literature_design_implications.md),
[`../research/docs/paper_extractions.md`](../research/docs/paper_extractions.md), and the adversarial review in
[`../research/docs/plan_redteam.md`](../research/docs/plan_redteam.md). Paper claims are architecture references,
not verified MathAgent results.*
