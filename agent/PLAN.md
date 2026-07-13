# MathAgent — Build Plan

> **Status:** v1 design. Living document. *(Revised after a 5-lens adversarial review — see
> [`../research/docs/plan_redteam.md`](../research/docs/plan_redteam.md).)*
> **Scope of v1:** an agentic system that solves **number-theory problems by elementary means only**.
> **Decisions locked for v1** (Ishan): (1) **harness-first, training-free** — orchestrate frontier general
> LLMs, no model training in v1; (2) **Layer-4 Lean audit is authoritative for certification** (soft
> profiles remain useful for search but never mint a certificate — §7, §9); (3) **tiered target** — IMO-tier NT first as a
> ladder, then specific research re-proof targets; (4) the repo reorg is done.
> **Progress (2026-06-14):** Phases 0–1 are **complete**; Phase-2 machinery (AND-OR DAG + memo +
> population/Elo + Autoreason tournament + retrieval) and the Phase-3 **Layer-4 audit are built and
> live-validated** — decision (2)'s "spiked early" Layer-4 has **graduated to the authoritative gate**,
> now certifying real elementary NT theorems end-to-end (`authoritative_elementary=True` on `n²≡0,1 mod 4`
> and `√2`-irrational/descent). For the current, timestamp-stamped status and live gaps see
> [`../research/docs/build_status.md`](../research/docs/build_status.md); for the architecture,
> [`../research/docs/system_design.md`](../research/docs/system_design.md).
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

> **Honest framing (load-bearing).** Soft profiles induce/pressure elementarity and operationally reject
> known violations through the ledger gates and refuters, but they cannot establish a certificate. The
> authoritative profile deterministically accepts/rejects proof method through the compiled Lean dependency
> and axiom audit (Layer 4), then requires statement faithfulness. Calling a Layers-1–3 result "certified
> elementary" would relocate the "Lean-verified = elementary" category error one step over (§8).

### Non-goals for v1
- No model training / RL / fine-tuning (reconsidered in v2, §9).
- Search/soft runs need not compile in Lean; certification runs must compile and pass Layer 4 (§7).
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
These are pinned now and live in [`gates/allowed_toolkit.yaml`](gates/allowed_toolkit.yaml) so judges (and the ledger) adjudicate
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

### 2.2 Knowledge-base gaps to fill
`knowledge/methods/descent.md` is now a filled draft rather than the historical empty template. Still
missing as first-class method cards are **LTE**, **orders / primitive roots / Fermat–Euler**, **quadratic
residues / Jacobi**, **CRT & modular arithmetic**, **`v_p` bookkeeping**, **bounding / size arguments**, and
**pigeonhole**. The current adapters do not read method files automatically: today these cards serve human
workflow design and ledger provenance metadata. Any future search bias or Lean-lemma seeding from their
contents must be wired explicitly rather than inferred from the files' presence (§6).

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

> **Original staging rationale.** The project deliberately built a smaller Phase-1 slice before promoting
> the heavier DAG machinery; memoization only pays off if sub-lemmas recur. That historical sequence was:
> - **Phase 1 = a flat, sequential driver** (no DAG engine, no goal cache, no incumbent tournament), with
>   **two roles**: a Prover and one adversarial Critic/Judge. Instrument sub-lemma reuse and repair-loop
>   convergence to *measure* whether the heavier machinery will pay off.
> - **Phase 2 promotes** the AND-OR DAG, hierarchical memo, goal cache, incumbent tournament, retrieval, and
>   the multi-judge panel — **gated on the measured reuse signal** (the LEAP ablation that justifies the DAG —
>   Advanced-NT 66.6%→100% — is real, so this is *deferred, not deleted*).
> - The current driver is deliberately **serial**: `ProofDAG` and `Budget` are shared mutable state. Width
>   and fanout calculations bound deterministic serial waves; a concurrent executor will require explicit
>   synchronization rather than a configuration-only switch.

> **Current status (Phase 2 built).** The AND-OR DAG + deep-hash memoization and the AlphaProof_Nexus
> Ralph-loop harness are now **built and tested** ([`orchestrator/dag.py`](orchestrator/dag.py),
> [`ralph.py`](orchestrator/ralph.py), [`dag_driver.py`](orchestrator/dag_driver.py)), with
> **model-agnostic focused-prover role** (Claude default, Codex optional) in place of AlphaProof, plus a
> CLI ([`scripts/prove.py`](../scripts/prove.py)). See
> [`research/docs/codex_harness.md`](../research/docs/codex_harness.md). The incumbent tournament and
> retrieval are **built** (`orchestrator/tournament.py`, `tools/retrieval.py`). The refinement panel size is
> configurable through `stages.judges`; shipped profiles use one declared judge RoleSpec. A genuinely
> heterogeneous multi-provider panel remains an experimental extension, not missing basic wiring.

### 4.1 Components (and their source patterns)
| Component | Role | Status | Source |
| --- | --- | --- | --- |
| **Flat driver** | Sequential prove → deterministic gate → repair → full-ledger judge loop. | built (P1) | — |
| **AND-OR proof DAG + memo** | Blueprint → sub-lemma tree; proven nodes cached/reused; acyclicity guard. | **built** | LEAP, Aristotle MCGS, Pantograph |
| **Deep-hash goal cache** | Memoize by normalized statement hash; reuse a sub-lemma across branches. | **built** | AlphaProof_Nexus deep-hash |
| **Focused prover** | Proves a node → step-ledger (AlphaProof's role). `RolesProfile` and shipped YAML profiles default this role to Claude/opus; the bare legacy CLI constructs Codex / GPT-5.5-xHigh role specs. The registry resolves the declared per-role provider. | **built** | AlphaProof_Nexus (AlphaProof tool) |
| **Ralph loop** | Per-node episodes: prove → gate → carry "lessons learned" → repeat. | **built** | AlphaProof_Nexus |
| **Decomposition reviewer** | Gate a blueprint on "does it simplify?" + "is it elementary?" before commit. | **built** | LEAP reviewer |
| **Revision controller** | Incumbent tournament; "do nothing" first-class; failure-analysis *before* revision; k=2 stop; margin-gated displacement + a goal-bound admissibility predicate. Refinement is disabled without the independent Layer-2 proof judge; every challenger must receive `no_gaps=true`, pass deterministic binding/elementarity checks, and—when per-node Lean is configured—obtain a budgeted `elementary_verified` receipt that is reused at promotion. Controller metadata/content is postchecked as untrusted. This still does not mint a terminal certificate. **Note:** PUCT + Bradley-Terry here are grafted from `population.py`, **not from AutoReason** (the paper disclaims that game/voting machinery) and are near-decorative — displacement is decided by `net >= margin`. Per-pass aggregation is **pairwise-net, not Borda**. | **built** ([`orchestrator/tournament.py`](orchestrator/tournament.py); wired via `DagDriver(refiner=…)`; `max_replan_depth` now consumed) | Autoreason (Critic/do-nothing/margin); BT/PUCT from `population.py` |
| **Evaluation cascade** | Cheap structural + numeric checks gate expensive judge/Lean passes. | built (P1) | AlphaEvolve (cheap-first cascade) |
| **Population / Elo over sketches** | Generate K candidate decompositions, rank by a pairwise-comparison Elo tournament (+ Bradley-Terry MLE, PUCT), try best-first. **Not evolutionary:** this is a one-shot *rank-K-then-pick* within a single `decompose` call — **no mutation, no crossover, no persisted cross-episode population DB**. (The only path that genuinely *evolves* a population is the optional OpenEvolve backend below.) Ranks/filters only; never changes whether a proof is accepted. | **built** ([`population.py`](orchestrator/population.py)) | AlphaProof_Nexus |
| **Evolutionary ledger search (OpenEvolve)** | *Evolve* (not just rank) proof-sketch ledgers via a MAP-Elites population, scored by the deterministic gate as fitness; mutations driven by a real **AlphaEvolve-style LLM ensemble** via the `claude` CLI — **Sonnet = breadth** (fast, high sampling weight, many candidates) + **Opus = depth** (stronger, low weight, occasional high-quality), mirroring AlphaEvolve's Flash/Pro split; **reads ledger as text, never exec/eval** (SAFE no-exec). Slots into `DagDriver(decomposer=…)`. **Optional dep** (`mathagent[evolve]`); ranks/filters only — does **not** certify elementary. | **built + unit-tested** ([`tools/openevolve_bridge.py`](tools/openevolve_bridge.py)) | OpenEvolve (AlphaEvolve OSS) |
| **Lean Layer-4 auditor** | Proof-term dependency + axiom audit (the authoritative gate). | **built + live-validated** ([`gates/lean_audit.py`](gates/lean_audit.py), [`lean_bridge.py`](gates/lean_bridge.py), [`lean/Audit.lean`](gates/lean/Audit.lean)) | AlphaProof_Nexus SafeVerify, AXLE |
| **Ledger→Lean formalization bridge** | Formalize a gate-passed ledger to Lean, compile, run Layer 4 → `authoritative_elementary`. | **built + live (Mathlib)** ([`tools/formalizer.py`](tools/formalizer.py), [`orchestrator/formalize_bridge.py`](orchestrator/formalize_bridge.py)) | LEAP, Aristotle informal→formal |
| **Adversarial faithfulness panel** | Multi-lens adversarial check that the Lean statement faithfully captures the informal claim. | **built + live** ([`orchestrator/faithfulness.py`](orchestrator/faithfulness.py)) | autoformalization-faithfulness wall (Goedel/AlphaProof_Nexus) |
| **Layer-4 terminal gate** | After either a DAG or direct profile proves the root, formalize+audit+faithfulness as the authoritative gate. | **built** (`DagDriver.terminal_gate`, `make_terminal_gate`) | PLAN §5 Layer 4 |
| **Persistent Lean server** | Keep Mathlib + `#audit` loaded; ~0.1s/audit after a one-time load. | **built + live** ([`gates/lean_server.py`](gates/lean_server.py), community REPL) | LeanDojo/Pantograph |
| **Autoformalization repair loop** | Feed Lean compile errors / audit rejects back to the formalizer to iterate. | **built + live** (`formalize_and_audit(repair_iters=...)`) | LEAP/Aristotle compiler-feedback |
| **Mathlib lemma retrieval** | Loogle (exact names from errors) + a local **BM25 index** + an optional **neural bi-encoder** (`bge-small-en-v1.5` + optional reranker), combined via `HybridRetriever`. **Default path is lexical (Loogle + BM25).** Without `mathagent[neural]`, `NeuralRetriever.available()` is false/returns no neural hits and the hybrid continues with the lexical legs; `HashingEmbedder` exists only as an injected offline test double. (Embeds `name+signature` only — no `doc` field is extracted.) | **built; neural leg opt-in** ([`tools/retrieval.py`](tools/retrieval.py), [`tools/semantic_retrieval.py`](tools/semantic_retrieval.py), [`tools/neural_retrieval.py`](tools/neural_retrieval.py)) | LeanSearch/Loogle/LeanExplore, LEAP premise retrieval |
| **Tools** ([`tools/`](tools/)) | Bounded numeric/witness search, Claude/Codex model adapters, final-answer checker, retrieval, and Lean bridge/auditor. | built | Axplorer, MathCode, LeanDojo/Pantograph |

### 4.2 The swarm roles ([`roles/`](roles/))
Only the Prover and Critic/Judge have standalone specification files. Live prompts for every registry role
are constructed in `agent/tools/*.py`; the files under `roles/` and `instructions/` are not dynamically
loaded into a run.
| Role | Job | Phase |
| --- | --- | --- |
| **Prover** | Produce an elementary proof for a node as a **step ledger** (§5). | **1** |
| **Full-ledger Judge** | Adversarially hunt logical gaps and smuggled non-elementary steps in a direct proof. | **1** |
| Decomposer / Blueprinter | Problem → strategy → AND-OR decomposition. | 2 |
| Decomposition Reviewer | Check simplification/usefulness and elementary admissibility before commit. | 2 |
| Comparator / Refiner | Rank candidate decompositions or run the incumbent revision tournament. | 2 |
| Formalizer / Faithfulness | Produce Lean, consume diagnostics, then compare the formal statement with the claim. | built |

### 4.3 Control loop (per problem) + liveness model
**Happy path:** Plan → (numeric statement check) → prove node(s) → Critic/Judge review → assemble → final gate
(§5) → score & record.

**Liveness (the most likely real failure is a silent stall / unbounded repair-or-replan loop):**
- Per-node **state machine**: `{open, in_progress, proven, failed-elementary, failed-gap, exhausted}`.
- Hard caps: orchestrator model calls, depth, per-node decomposition attempts, global re-plan depth, and
  Ralph episodes. Provider subprocesses have timeouts; terminal repair, per-node verification, and evolution
  have separate bounded controls. Exhaustion transitions honestly instead of retrying forever. There is no
  profile-level token-budget field, so this plan does not claim one.
- Live subprocess adapters have timeouts, and driver call sites classify provider failures instead of
  silently passing them. Contract-violating output enters a bounded repair/retry path.
- **Default budgets (v1, costable):** the operative refinement panel default is `stages.judges=1`
  (the original Phase-2 design target was three, but that is not the shipped default). The operative
  defaults are the `BudgetProfile` in [`orchestrator/run_profile.py`](orchestrator/run_profile.py) —
  `60` orchestrator-metered search/review calls / `max_depth 3` / `max_decomp 2` /
  `max_replan_depth 2` / `3` episodes. `max_llm_calls` is **not** an all-provider-call ceiling: terminal
  formalization/repair/faithfulness is separately bounded and reports its cost as
  `FormalizeAuditResult.model_calls`; OpenEvolve is separately bounded/reported by the enabled stage's
  iteration count and `ensemble.timeout_s`; and per-node Lean verification uses `max_node_verify_calls`
  when configured. The legacy bare `Budget` dataclass defaults are not the profile defaults.

### 4.4 Observability — machine-readable run trace
"Everything is a comparable experiment" requires more than a prose template. `RunTrace` emits an
append-only JSONL stream with `{run_id, seq, t, kind, ...event_data}`; drivers record gate/review outcomes,
budget counters, cache hits, Lean outcomes, and final state as applicable. The rendered run record is a view
of that event stream. Layer-4 artifacts retain the bridge-verified runtime Lean identity and exact Lake
manifest SHA-256 receipt, and should also retain the policy identity, because certificates are toolchain-
and denylist-relative. Missing or caller-labeled toolchain provenance cannot mint Layer-4 authority.

### 4.5 Profiles, reporting status, and certificate trust

`RunProfile` is the single declarative control lever; `validate_profile` rejects contradictory wiring or a
missing active provider/Lean capability before construction. Authoritative direct profiles are legal when
their DAG-only stages are off. At the CLI, `--formalize` is a direct-mode convenience that also prints Lean
source, while `--terminal-gate` is the general certification switch. Certification always requires the
faithfulness panel; `--no-faithfulness` is rejected in a certifying invocation.

The same validation prevents decorative controls: `lean.server=true` is rejected unless a terminal
authoritative or per-node gate consumes it, and `elementarity=none` requires every Lean flag to be false.
`RoleSpec.effort` is Codex-only; a role whose declared provider/fallback chain can select Claude must leave
it unset. The three OpenEvolve knobs are supervised profile fields: `evolve` seeds the ordinary prover,
`evolve_witness` only reports diagnostic constructions, and `evolve_fallback` is a last-resort decomposer.

User-facing outcomes are categorical, never derived from a search score:

```text
rejected < candidate_incomplete < soft_proven < audited_not_certified < authoritative_elementary
```

`audited_not_certified` requires an informally proven result plus a completed Lean audit.
`authoritative_elementary` additionally requires the audit to pass, statement faithfulness to pass, and
both the formalizer and faithfulness checker to be explicit production authorities
(`certification_trusted=True`). Generic/scripted components default to untrusted; a persistent server is
also accepted only through its trusted audit interface. In certifying CLI modes, an informal proof with an
absent or failed certificate exits non-zero.

---

## 5. The elementary gate — how the constraint is induced and where it is enforced

Two foundational facts shape every choice:
- **Elementarity has no projection operator.** AlphaEvolve enforces a numeric property (integrality) with soft
  penalty + *hard projection* (round) + prompt nudge. You **cannot** "round" a class-group argument into a
  descent argument. Only the soft-penalty and prompt parts transfer; the hard part is rebuilt as a
  **verification gate**, not a projection.
- **A judge is never the final gate.** Judges rank/prune; a deterministic mechanism accepts.

The gate is **graded, defense-in-depth**. **Be precise about what each layer does:** Layers 0–3 pressure,
filter, and can reject candidates for an elementarity-enforcing run, but only Layer 4 can certify method
admissibility. `elementarity=none` bypasses elementarity-objective refutations while preserving logical
soundness checks.

### Layer 0 — Framing (soft; present, not obeyed)
- **Objective spec in the live role builders.** Prover/decomposer prompts receive the closed justification
  keys from `gates/allowed_toolkit.yaml`; reviewer/refiner prompts receive concise elementary-method rules.
  The markdown under `agent/instructions/` and the contents of `knowledge/` are human references and are
  **not** implicitly loaded into every role.
- **Constrained-scope framing** (Autoreason's strongest finding: bounding the solution space flips refinement
  from worst to best — olympiad NT is naturally scope-bounded).
- **Paradigm scaffold** — force an intermediate elementary-arithmetic reasoning trace before the write-up
  (BRIDGE: prompt-injected paradigm measurably reshapes output).
- **Retrieval bias** toward a curated elementary subset of Mathlib (Loogle + local BM25, with optional
  neural retrieval). This is not retrieval over `knowledge/`.
- **No trust-by-cache:** memoized proof artifacts retain the gate/context identity and must already have
  cleared the relevant proof checks. Retrieved lemma names are suggestions to the formalizer; the resulting
  Lean term is compiled and dependency-audited rather than trusted at ingestion.

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
The current DAG path has one configured decomposition reviewer and one configured Ralph full-ledger judge
when `stages.review=true`. The optional refinement tournament makes `stages.judges` judge calls using
separate adapter instances of the single declared judge/refiner provider configuration; this is repeated
sampling, not cross-model independence. These checks look for non-elementary steps and logical gaps, but
remain soft rank/review signals; "reject / do nothing" is first-class. A heterogeneous multi-provider panel
would require additional wiring and should be reported as such if added.

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
> Production authority additionally requires the extractor's runtime-derived toolchain identity and the
> bridge's verified Lake-manifest receipt; offline/synthetic reports remain classification-only.

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
3. **Implemented source boundary (lexer-level, not full `V_leg`).** `lean_bridge.py` restricts imports to
   trusted umbrella modules, requires a local theorem declaration, rejects model-authored `#` commands,
   quoted literals/identifiers, attributes, `unsafe`/`macro`/elaborator/evaluation/native bridges, and binds
   the appended audit to a fresh nonce. A full elaborated-AST legality pass remains open.
4. **Tactic-palette whitelist — deferred.** Prompt rules constrain tactics, but no deterministic tactic
   whitelist is claimed. `simp`/`nlinarith`/`decide`/`polyrith`/`exact?` can pull heavy lemmas, so every
   accepted term is dependency-audited.

> **Bottom line for v1.** Layers 0–3 implement the search objective and honest soft verdict. The production
> certification path is no longer a prototype: it formalizes, compiles, dependency/axiom-audits, checks
> statement faithfulness, and requires trusted production components. Its remaining limitation is reach
> (whether a difficult informal proof can be formalized), not authority of a successful certificate.

---

## 6. Knowledge base plan ([`../knowledge/`](../knowledge/))

1. **Maintain `descent.md` and its Lean counterpart.** The method file is populated; the next concrete
   deliverable is a reusable **Lean descent / strong-induction combinator** parameterized by `measure : α → ℕ`
   + a per-step decrease lemma (`termination_by`/`decreasing_by`, `Nat.strongRecOn`, `WellFounded.min`,
   `Nat.find`, `Nat.lt_wfRel`), so the LLM only supplies the measure + the decrease proof.
2. **Method ontology for the gate.** Extend each method's frontmatter with the justification enum it licenses
   (`justifies: [congruence, descent, ...]`) and `allowed_in_final_proofs`, so Layer 1a validates ledger tags
   against real method files. The contested tools get their ruling from §2.1.
3. **Continue filling ontology gaps** (modular/CRT, bounding, orders & Fermat–Euler, LTE, QR/Jacobi,
   `v_p`, pigeonhole). Existing method files are curated data; they are not implicitly retrieved or loaded
   into prompts today.
4. **Promote Tagebuch identities** from `knowledge/library/untriaged_tagebuch/` only with a trigger pattern + proof
   status (existing policy). Kieren's Vierzahlensatz pipeline is the model worked example.

---

## 7. Lean track — authoritative certification gate

> **Status: ✅ Lean + Mathlib installed; Layer 4 + the ledger→Lean formalization bridge built and
> live-validated.** Lean 4.30.0 (via elan) + a Mathlib `v4.30.0` lake project
> ([`formal/lean/mathagent_formal/`](../formal/lean/mathagent_formal/)). The full loop runs end-to-end:
> a gate-passed ledger → Codex formalization → compile (`lake env lean`) → Layer-4 dependency audit →
> `authoritative_elementary` (confirmed `True` on `n+0=n`; `IsDedekindDomain` rejected live against
> Mathlib). Also added + live-validated: an **adversarial statement-faithfulness panel** (4 diverse
> lenses; `authoritative` requires a unanimous trusted panel and a trusted production formalizer),
> **Layer 4 wired as the terminal gate for DAG and direct profiles**
> (`DagDriver(terminal_gate=...)`), and a **persistent Lean server** (Mathlib loads once: 76s, then
> ~0.1s/audit — a >500× speedup). See
> [`research/docs/formalization_bridge.md`](../research/docs/formalization_bridge.md) and
> [`lean_layer4_and_population.md`](../research/docs/lean_layer4_and_population.md).
> **Also built (autoformalization):** a Lean-error **repair loop** (`repair_iters`) that feeds compile
> errors / audit rejects back to the formalizer, with **Mathlib lemma retrieval** (Loogle) to fix
> hallucinated lemma names — see [`autoformalization_repair.md`](../research/docs/autoformalization_repair.md).
> (A deep dive concluded Codex's `/goal` agentic mode should NOT own the loop: the persistent server's
> ~0.1s compile beats Codex re-running `lean` at ~60s/iter, and a Python loop keeps control of
> retrieval/denylist/budget/audit.) Remaining: lift the success rate on hard NT statements
> (semantic retrieval, proof-skeleton transfer from the ledger).

Note: **Lean/elan/lake run natively on Windows**; the real constraint is that some *harness binaries and
tracing tooling* (OpenGauss/MathCode) are Linux-only — mine their ideas, don't depend on them.

- **Completed early spike:** Lean+Mathlib and the Layer-4 auditor accept genuine elementary proofs and
  reject denylisted dependencies/axioms. The audit reads the compiled `Environment`; compilation alone is
  never treated as authority.
- **Current scope:** a run that cannot formalize/compile/audit remains soft-only and a certifying CLI
  invocation exits non-zero. This is a reach limitation, never a reason to weaken the certificate.
- **Substrate decision:** the bridge and auditor were built in-house around the community Lean REPL and
  `Audit.lean`; Pantograph/LeanDojo remain design references, not runtime dependencies.

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
| **T0 — warmup** | trivial divisibility / congruence; smoke-tests for harness + gate. | `aime_1984_p7`, `amc12_2000_p1` |
| **T1 — olympiad NT** | IMO/shortlist descent, Diophantine, divisibility, orders, LTE. | `imo_1988_finite_descent` |
| **Calibration** | results with a **known short elementary proof** — capability checks, *not* novel research. | `x2_plus_1_eq_y3` (complete), `hardy_wright_theorem_120` |
| **T2 — hard olympiad NT** | multi-method, harder shortlist. | no current folder is designated T2; curate before measurement |
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

Every current problem folder has a `problem.md`; keep each statement and `allowed_inputs.md` numerically +
expert-validated before running — a mis-stated theorem can be vacuously true (§3.4).

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
| **1 — MVP slice + gate + Layer-4 spike** *(done)* | Minimal end-to-end prover with a working soft+structural gate and a *proven-real* hard gate on the hard case. | (a) `gates/allowed_toolkit.yaml` (+ §2.1 rulings) + `denylist.yaml`; (b) **frozen step-ledger schema** + deterministic validator (1a) + soft scanner (1b); (c) Prover + Critic/Judge prompts; (d) numeric/witness tool; (e) flat sequential driver + liveness caps + JSONL trace; (f) author 1 T0 + the `imo_1988` (or use `x2_plus_1_eq_y3`) statement, numerically validated; (g) fill `descent.md` + the Lean descent combinator; (h) **Layer-4 spike**: accept a genuine elementary descent/Vieta proof, reject a denylisted one. | A correct, gate-passing proof of ≥1 target **and** the Layer-4 spike demonstrably accepts an elementary proof while rejecting a planted heavy one. |
| **2 — DAG/memo/swarm + methods + retrieval** *(machinery built; payoff not yet measured)* | Promote the heavier harness *only where measurement says it pays*. | AND-OR DAG + split-keyed memo + goal cache + incumbent tournament + configurable refinement-judge count; lexical/optional-neural retrieval over the curated Mathlib index. Method Markdown remains human reference/provenance unless explicitly wired. | Beats Phase-1 on T1; attempts T2; reproducible workflow comparison; measured sub-lemma reuse justifies the DAG. |
| **3 — Lean certification reach** *(terminal authority built; hard-proof reach not yet routine)* | Scale formalization success without weakening Layer 4. | In-house bridge; dependency audit + `collectAxioms`; conservative source boundary; optional future elaborated-AST pass; % compilable on held-out key lemmas. | Audit routinely accepts elementary proofs and rejects planted heavy/axiom/source-boundary attacks. |
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
2. **Eval-set sourcing** (which shortlist years; which specific Mordell `k`; who validates statements) —
   the gating decision for the held-out NT eval set (build_status §7 gap #2).
3. **Per-problem model + budget policy** (gpt-5.5/xHigh chosen; the call/$ ceiling per problem is unpinned).
4. *(Resolved: metrics gate-vs-weight — §8.2. Resolved: Phase-1 = flat sequential — §4. Resolved:
   auditor build-vs-adapt — built in-house: `gates/lean_audit.py` + `lean/Audit.lean` + persistent
   server, not AXLE/LeanDojo.)*

---

## 11. Phase 1 checklist — status

**Phase 1 complete. Validate the current tree with `make check`, the offline `pytest` suite, and `make demo`;
avoid embedding a test count here because it drifts whenever coverage grows.**
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

**Completed since (Phase 1 → 2/3):**
- [x] **Live model-backed roles** behind the Protocols — `RolesProfile`/shipped YAML supply Claude defaults,
      the bare legacy CLI supplies Codex specs, and either provider is selectable per role; the registry
      resolves those declarations (`tools/claude_roles.py`, `tools/codex_prover.py`,
      `orchestrator/registry.py`).
- [x] **Layer-4 spike succeeded → now the authoritative gate:** Lean + Mathlib installed; the audit
      certifies real elementary NT theorems end-to-end (`authoritative_elementary=True` on `n²≡0,1 mod 4`
      and `√2`-irrational/descent) and rejects a denylisted one (`IsDedekindDomain`).
- [x] **First real end-to-end runs with run records** — the certification ladder
      ([`live_certification_runs.md`](../research/docs/live_certification_runs.md)) and the ArXivMath
      vanilla-vs-answer-refinement run (historical filename:
      [`runs/2026-06-13_nt_vanilla_vs_harness.md`](../benchmarks/datasets/arxivmath/runs/2026-06-13_nt_vanilla_vs_harness.md)).
      The latter was answer-only and does not measure typed-ledger/Layer-4 proof-harness lift.
- [x] Local web UI for driving the harness (`ui/`).

**Still open (now tracked live in [`build_status.md`](../research/docs/build_status.md) §7):** lift the
autoformalization rate (gap #1); a held-out NT eval set + measured lift (gap #2); faithfulness cross-model
independence (#4); the full elaborated-AST V_leg defense beyond the current source validator (#8).
The Phase-2/3 machinery is built — what remains is *measuring* its payoff and scaling Layer-4 to routine.

---

*Sources: [`../research/docs/literature_design_implications.md`](../research/docs/literature_design_implications.md),
[`../research/docs/paper_extractions.md`](../research/docs/paper_extractions.md), and the adversarial review in
[`../research/docs/plan_redteam.md`](../research/docs/plan_redteam.md). Paper claims are architecture references,
not verified MathAgent results.*
