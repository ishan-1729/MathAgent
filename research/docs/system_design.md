# MathAgent — System Design

> **Persistent design document.** This is the deep, current elucidation of *what the system is, how it
> is shaped, and why*. It changes slowly. For dated validation evidence and the current implementation
> addendum, see **[`build_status.md`](build_status.md)**; for the staged plan and v1 scope decisions,
> **[`../../agent/PLAN.md`](../../agent/PLAN.md)**; for the visual companion,
> **[`system_design.html`](system_design.html)** (Excalidraw diagrams). Evidence base for every
> architectural borrow: **[`literature_design_implications.md`](literature_design_implications.md)** (20
> systems papers) and **[`paper_extractions.md`](paper_extractions.md)**.

---

## 1. What this system is

MathAgent is a **training-free agentic harness** that attempts to prove number-theory theorems, normally
under an elementary-method objective, and — the part that makes it *ours* — to **prove that a successful
proof is elementary relative to the versioned target theory**. It orchestrates frontier general-purpose
LLMs (the trained-prover slot is model-agnostic — `RolesProfile` and the shipped YAML profiles default to
Claude, while the bare legacy CLI constructs Codex specs; the registry resolves either per-role backend)
inside a LEAP-style
blueprint→AND-OR-DAG→compiler-feedback loop, then subjects
any successful argument to a **dual elementary gate**: soft/structural pressure on an informal
"step-ledger," backed by an authoritative, non-gameable **Lean proof-term dependency + axiom audit**.

Under `elementarity=soft` or `authoritative`, a correct *non-elementary* proof is an objective failure,
not a success. The explicit solution-only control (`elementarity=none`) admits it as `soft_proven` while
retaining goal binding, H0 consistency, acyclicity, and obligation checks. The system is built around two
questions — *can it find a sound proof?* and *can it certify the proof elementary?* — and only the second
requires Layer 4.

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
- **Grafted from AlphaProof_Nexus.** The Ralph loop, the deep-hash goal cache, the **population/Elo
  candidate *ranker*** (Elo + Bradley-Terry + PUCT over K candidate decompositions, §8 — a one-shot
  rank-K-then-pick within a single decompose call, **not** an evolutionary population with mutation/
  crossover or a persisted DB), and — most important — **SafeVerify → our Layer-4 axiom/dependency
  audit**.
- **Substituted.** AlphaProof_Nexus uses an RL-trained prover (AlphaProof) as the per-subgoal tool —
  the one piece nobody can reproduce. **We swap in a frontier general-purpose LLM — model-agnostic by
  design; the shipped profiles declare Claude/opus for the prover, while Codex / GPT-5.5-xHigh is an
  optional per-role backend and the bare legacy CLI constructs Codex specs (the §13 live-run evidence used
  GPT-5.5-xHigh). The registry resolves the supplied `RoleSpec`; it does not independently choose a
  default.**
- **Grafted from Autoreason.** The incumbent revision tournament (do-nothing first-class, k=2 stop,
  margin gate; the profile knob defaults to `stages.judges=1`) as the revision controller / synthesis-drift
  guard. *(The BT/PUCT seeding inside it is grafted from `population.py`, not from AutoReason — the paper
  disclaims that machinery; and the per-pass aggregation is pairwise-net, not the paper's Borda. See §8.5.)*
- **Grafted from AlphaEvolve — *two specific, narrow lessons*, not the population machinery.** (The
  Elo/Bradley-Terry/PUCT population above is AlphaProof_Nexus-style; do **not** attribute it to
  AlphaEvolve.) From AlphaEvolve we take: (1) the **cheap-first evaluation cascade** (run cheap
  structural/numeric checks before expensive judge/Lean passes); and (2) the **"no hard projection"
  lesson** — AlphaEvolve enforces a numeric property with soft penalty *plus a hard rounding
  projection*, and elementarity has **no** such projection operator, so the hard part must be rebuilt
  as a verification gate (Layer 4), never a projection (§3, PLAN §5).
- **Wired-in evolutionary backend = OpenEvolve.** The open-source AlphaEvolve implementation
  (`algorithmicsuperintelligence/openevolve`) is grafted as an **optional** third candidate-generation
  path: a MAP-Elites population search over proof-sketch **ledgers**, scored by our deterministic gate
  as the fitness oracle (`tools/openevolve_bridge.py`, §8.6). It evolves *ledger text only* and never
  executes the evolved artifact; it ranks/filters, it does **not** certify — only Layer 4 does. Its
  mutations are driven by a real **AlphaEvolve-style LLM ensemble** — fast-breadth **Sonnet**
  (high sampling weight, many candidates) + stronger-depth **Opus** (low weight, occasional
  high-quality suggestions), via the headless `claude` CLI — mirroring AlphaEvolve's own
  Flash/Pro breadth/depth split (now Sonnet/Opus). AlphaEvolve's stated rationale: the ensemble
  "balance[s] computational throughput with the quality of generated solutions" — the fast model
  "enables a higher rate of candidate generation … per unit of time," the stronger model "provides
  occasional, higher-quality suggestions."
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

Therefore the constraint is applied as **defense-in-depth, cheap-first**. Earlier layers can reject a
candidate in an elementarity-enforcing run, but **only the last layer is authoritative for certification**:

| Layer | What it does | Authority |
|---|---|---|
| **0 — Framing** | Generation roles receive the closed justification keys and concise elementary-method rules; retrieval is biased to a curated elementary Mathlib subset. Markdown policy/method files are not implicitly loaded. | soft (present, not obeyed) |
| **1a — Structural** | Deterministic validator over the typed step-ledger: justification ∈ closed vocabulary; DAG acyclicity; no dangling deps; one connected conclusion; discharged obligations. | deterministic *filter* |
| **1b — Prose scan** | Euphemism/denylist keyword scan → routes to review. | soft router (never rejects) |
| **2 — Adversarial review** | Configured model reviewers/judges inspect elementarity and logical gaps. `stages.review` wires one decomposition reviewer and one Ralph full-ledger judge; `stages.judges` sizes the optional refinement panel, using the declared judge RoleSpec. | soft model signal (rank/prune) |
| **3 — Numeric grounding** | Exact-integer witness search re-checks the statement + finite case-covers + descent decreases. | deterministic, bounded scope |
| **4 — Lean dependency audit** | Compile → walk the kernel proof-term's transitive constant closure + `collectAxioms`; reject on a content-denylist hit (unless allowlisted) or any non-whitelisted axiom (catches `sorry`). | **deterministic and authoritative — the only layer that can certify "elementary"** |

**Why Layer 4 is a dependency audit, not "it compiled":** Mathlib *is* the source of the heavy
machinery, so a compiling proof can freely route through class groups or Mihăilescu. **Why it needs a
two-tier allow/deny design:** a naive namespace-prefix filter over the closure over-rejects nearly every
elementary proof (they all touch `WellFounded.fix`, `Decidable`, `Nat.rec`, …) — so a small **content
denylist** is backstopped by an **infrastructure allowlist** + an **"elementary-by-fiat" allowlist**
(heavy-impl but elementary-result APIs like `legendreSym`), with allowlist-wins precedence. The axiom
check (`collectAxioms ⊆ {propext, Classical.choice, Quot.sound}`) is how a `sorry` (`sorryAx`) is caught
— a source-AST scan would miss an error-recovered `sorry`.

**Honest framing (locked):** Layers 0–3 induce, pressure, rank, and operationally reject known violations
in soft/authoritative runs. They do not establish a certificate. Calling a Layers-0–3-passed proof
"certified elementary" would relocate the "Lean-verified = elementary" category error one step over.

**How the layers compose (fail-closed, max-severity).** Each deterministic check emits findings on a
three-level severity lattice — **REJECT** (authoritative refutation) > **REVIEW** (soft signal, routed to
Layer 2) > **INFO** (advisory) — and the verdict is the **maximum** severity present, so a single REJECT
decides it (`gate.py::evaluate`). Crucially the gate is **fail-closed**: a malformed ledger *and* any
uncaught internal exception are both converted into a REJECT, so a bug can only ever produce a safe
rejection, never a silent pass (invariant 1).

**Boundary rulings — the contested edge of "elementary."** Most justifications are simply allowed or not,
but a handful sit on the genuine boundary, where a binary flag is wrong. `allowed_toolkit.yaml` carries a
separate **four-valued** `boundary_rulings:` vocabulary (queryable via `Toolkit.ruling()`): **`allowed`** (e.g.
Pell's fundamental solution — an elementary descent proof exists), **`allowed_with_citation`** (e.g.
*Zsygmondy* — elementary but hard; cite it like LTE), **`allowed_per_problem_whitelist`** (e.g. *higher /
cubic–quartic reciprocity* — admit only where a specific problem needs it), and **`disallowed`** (e.g. the
*Gauss-sum / analytic* proof of quadratic reciprocity — even though a *different*, elementary proof of the
same theorem is fine). This is the harness's explicit, auditable answer to "what counts as elementary at
the edge," distinct from both the justification enum and the Layer-4 dependency denylist. **Status:** the
table is *declared but not yet wired into gate enforcement* — `Toolkit.ruling()` exists, but no gate layer
consults it yet; it documents intent at the contested edge, with enforcement a tracked follow-up.

**Layer 3, grounded safely.** The numeric re-checks must evaluate model-supplied expressions, which is
dangerous (arbitrary `eval`, float drift, runaway search). So expressions are parsed with SymPy into an
**allowlisted integer-only AST** — only `Add/Mul/Pow/Symbol`, integer leaves, non-negative bounded
exponents (no floats, rationals, transcendentals, or undeclared symbols) — giving *exact* arithmetic with
no code execution; and case-searches enumerate a **closed integer box** with hard caps (`MAX_BOX_POINTS`,
`MAX_ABS_BOUND`) so a check cannot become a combinatorial/bignum DoS (`numeric.py`). Two further routers:
the prose scanner forces **"elastic" justifications** (`bounding, factorization, squeeze, descent,
vieta_jumping`) to mandatory review — they can hide a heavy step behind an innocent label — and a
**coprimality-provenance** check requires a coprime-factorization split to cite a *real prior* `gcd` step
rather than asserting coprimality from nowhere.

**Layer 4, mechanically.** The audit stands on a custom Lean **`#audit` command** (`lean/Audit.lean`): it
walks the kernel's fully-elaborated proof term and, with an **iterative worklist** (`closure` over
`usedConsts`, so no stack blow-up), computes the **transitive constant-dependency closure** — every
theorem / def / axiom the term actually uses, in both its type and its value — plus `collectAxioms`,
emitting a one-line JSON report. Because it reads the *kernel term*, it sees through tactics, macros, and
notation to exactly what the proof depends on; "it compiled" is irrelevant. A small **bridge**
(`lean_bridge.py`) assembles the extractor + candidate into one file (imports hoisted to the top and
de-duplicated, as Lean requires) and distinguishes a real `:error:` from a recoverable "uses `sorry`"
warning, so the repair loop fires on the right signal. Before compilation, its conservative lexer-level
source boundary restricts imports to fixed umbrella modules, requires the audited theorem to be declared
locally, rejects model-authored `#` commands/attributes/quoted literals and code-bearing
`unsafe`/macro/elaborator/evaluation/native tokens, and nonce-binds the appended audit. This is an
implemented source defense, not the still-deferred full elaborated-AST `V_leg` pass.

---

## 4. End-to-end pipeline (with data shapes)

The unit of work is a **step-ledger**: a proof as a typed DAG of justified steps —
`step := {id, claim, justification ∈ closed-vocabulary, depends_on:[id…], obligations?}` with exactly
one `conclusion`. It is the contract every gate layer reads and the artifact the formalizer consumes.
(The parse front-end extracts the ledger from whatever the model emitted — a dict, a JSON string, or a
fenced ` ```json ` block — validates it against a Draft-7 JSON Schema, and NFC-normalizes prose so
equivalent Unicode glyphs cannot dodge the denylist; anything malformed is a deterministic REJECT.)

```
informal claim → [Prover (profile-declared; shipped YAML: Claude, bare CLI: Codex)] → JSON step-ledger
   → [L1a structural + L1a/L3 obligations + L1b scan]  ⇒ REJECTED | NEEDS_REVIEW | PASSED
   → [Harness: profile path = DagDriver (direct-only or direct→decompose→review→recurse);
               standalone flat path = FlatDriver]
   → assembled informal proof
   → [Formalizer (profile-declared; authoritative YAML: Claude/opus)] → Lean 4
       → [compile one-shot or on persistent Mathlib server]
           --error/audit-reject→ [repair loop: + diagnostics + retrieved lemmas]
   → [Layer-4 audit: closure + collectAxioms]  → [faithfulness panel]
   → authoritative_elementary = soft-proven AND compiled AND audit.passed
                                 AND faithful AND certification_trusted
```

There are **two distinct facts**, and conflating them is a category error: `PROVEN`/`soft_proven` means an
informal proof was found and admitted by the configured search path; `authoritative_elementary=True` means
that proof was also formalized, compiled, dependency/axiom-audited, faithfully statement-bound, and
produced through trusted certification components. The harness can reach the first without the second.

The user-facing reporting ladder is categorical and never consumes a search fitness score:

```text
rejected < candidate_incomplete < soft_proven < audited_not_certified < authoritative_elementary
```

`audited_not_certified` requires both a soft proof and a completed Lean audit. Authority additionally
requires a passing audit and the full trusted terminal result. A terminal crash or compile failure is not
"audited"; in a certifying CLI invocation it exits non-zero even when the informal proof exists.

### 4.1 Profiles, direct certification, and the trust boundary

`RunProfile` flows through `supervisor.validate_profile` before any role or Lean component is constructed.
The supervisor validates only active roles and the exact requested Lean transport. A profile with
`mode=direct` may be authoritative when decomposition, population, refinement, and evolve fallback are off;
it still uses `DagDriver`'s direct-only path and terminal gate. The CLI's `--formalize` flag is narrower than
the profile model: it requires direct mode and prints the Lean source. `--terminal-gate` is the general
certification flag. Both require faithfulness; `--no-faithfulness` is rejected for a certifying run.

Certification is an explicit capability boundary. Live formalizers and live unanimous faithfulness wrappers
declare `certification_trusted=True`. Generic panels, arbitrary duck-typed components, and scripted fixtures
default to false, so a canned success cannot mint a production certificate. The Lean bridge similarly
refuses an alternate persistent server without a trusted audit interface. Trust does not replace validation:
the source boundary, compiler, audit report binding, dependency/axiom decision, and faithfulness verdict must
still pass.

The profile default `max_llm_calls=60` is a cap on calls metered by the orchestration budget: prover,
full-ledger judge, decomposer, decomposition reviewer, comparator, refiner, and one evolve-fallback
invocation. It is not a total count of every nested provider call. Terminal formalization/repair and the
four faithfulness lenses have their own bounded loops and report their provider cost separately in
`FormalizeAuditResult.model_calls`. OpenEvolve is bounded and reported by the enabled stage's declared
iteration count, with `ensemble.timeout_s` bounding each Claude subprocess; and per-node Lean verification
uses the separate optional `max_node_verify_calls` sub-cap. `max_replan_depth` is a global decomposition
re-plan cap, not an alias for the per-node `max_decomp_attempts` cap.

Validation also rejects inert or provider-incompatible controls. `lean.server=true` requires an
authoritative terminal gate or per-node Lean gate, and `elementarity=none` requires every Lean flag to be
false. `RoleSpec.effort` is Codex-only: it must be unset whenever the role's declared provider/fallback
chain could select Claude.

---

## 5. Control flow — how each mechanism engages (there is NO classifier)

A common question: *what decides whether LEAP decomposition, the population search, or the tournament
runs?* **Nothing learned. It is a deterministic control-flow cascade plus operator config flags**
(`DagDriver._prove`):

1. **Ralph loop (direct attempt) — unconditional.** Every node first tries a direct proof via the Ralph
   loop (multi-episode, lessons-learned; explained in §5.1). When `stages.review=true`, the loop also
   runs the full-ledger judge before accepting a direct candidate.
2. **`_refine` tournament — `if self.refiner is not None`** (set by `--refine`). Runs on a directly-proven
   ledger only when Layer-2 proof review is configured. A challenger displaces the incumbent only by
   beating it on a blind preference panel, receiving `no_gaps=true` from every proof judge, passing the
   goal-bound deterministic checks, and obtaining `elementary_verified` from any configured per-node Lean
   verifier within budget. Returned controller content is independently postchecked; this remains **not**
   an unconditional no-regression guarantee (see §8.5).
3. **LEAP decomposition — only if the direct attempt *failed***. The cascade falls through to decompose →
   review → recurse. (Reached only on direct failure; gated also by `decomposer is not None`.)
4. **Population/Elo + PUCT/BT — `if population_k and comparator`** (set by `--population`). Lives *inside*
   the decomposition step (ranks K candidate decompositions).
5. **Re-plan — bounded, doubly.** The first decomposition plan is *free*; each subsequent re-plan is
   capped both **per node** (`max_decomp_attempts`) and **globally** (`Budget.max_replan_depth`), with
   reviewer feedback threaded into each new plan — so a stubborn goal must eventually give up
   (`EXHAUSTED`) rather than spin forever (invariant 6).
6. **Deep-hash cache — active when `stages.memo=true`**; reuses a proven node by its semantic hash.
   The `no-memo` ablation keeps the DAG but skips memo read short-circuits, so repeated goals are proved
   fresh.

So "the easy theorem didn't use the DAG/population" means only that the **direct attempt succeeded and
the cascade short-circuited** — *and* that the optional layers were off by flag. The only "intelligence"
in the gating is **try-cheap-first, escalate-on-failure**, which is LEAP's own design — not a classifier.

### 5.1 The Ralph loop (the always-on direct attempt)

"Ralph" (from AlphaProof_Nexus) turns one noisy LLM call into a **self-correcting loop**
(`ralph.py::RalphLoop`). Each **episode**: the prover emits a step-ledger → the **deterministic gate
re-checks it** (`evaluate`) → on rejection, the gate's specific findings are appended as capped
**lessons-learned** notes that condition the next episode (the paper's *"write a lessons-learned comment
and resume"*), optionally interleaving an adversarial judge panel (Layer 2). It repeats until the ledger
is admitted or the shared `Budget` runs out (then `exhausted=True` — never a false success). In one line:
it is **fixed-point iteration over proof attempts** — keep refining from accumulated feedback until the
output stops being rejected. This is item 1 of the cascade, and the leaf behaviour at every DAG node's
direct attempt.

The review toggle is intentionally shared: in DAG mode `review=false` disables both this full-ledger judge
and the separate decomposition reviewer; in direct mode only the full-ledger judge is relevant. The
`judges` breadth knob belongs to the optional refinement tournament and does not change the Ralph judge
count.

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

**Historical failure example (`3 ∣ a²+b² ⇒ 3∣a ∧ 3∣b`):** the prover found the correct mod-3 argument in one
shot (`PROVEN`), but the formalizer wrote a `decide`-based Lean proof over `Fin`/`ZMod` cases whose
`Decidable` instance Lean couldn't synthesize → **compile failed**; the 3-iteration repair loop stayed
in the same `decide` basin (it patches errors, doesn't switch strategy; the error text "failed to
synthesize Decidable" misdirects toward fixing decidability rather than abandoning `decide`; retrieval
had nothing to add because no lemma was missing). Result: honest `authoritative_elementary=False`. The
fix was not to waive compilation/audit. A later formalizer revision closed this particular case with a
finite `ZMod 3` core and an explicit divisibility bridge. The architectural lesson remains: formalization
failure must stay non-authoritative, and broader improvements must be validated on held-out theorems rather
than tuned to one example.

---

## 7. Component inventory (contracts + model roles)

Everything that touches the model is a **Protocol**, so the live provider chosen by the effective profile
(shipped YAML: Claude; bare legacy CLI: Codex) and the offline test stub are interchangeable; the whole
harness is deterministically testable with scripted stubs.

| Subsystem | Key modules | Contract / role | Source |
|---|---|---|---|
| **Gate (Layers 1–3)** | `gates/ledger.py`, `obligations.py`, `scanner.py`, `gate.py` | `evaluate(ledger,toolkit)→GateReport` | LongCat V_leg, LEAP reviewer, AlphaEvolve cascade |
| **Gate vocabulary** | `gates/allowed_toolkit.yaml`, `denylist.yaml`, `ledger.schema.json` | closed justification enum + obligation shapes + denylists | PLAN §2 |
| **Layer-4 audit** | `gates/lean_audit.py`, `lean_bridge.py`, `lean_server.py`, `lean/Audit.lean` | `audit_report(DependencyReport)→LeanAuditResult` | Nexus SafeVerify, AXLE |
| **Model roles** | `tools/claude_roles.py` + `orchestrator/registry.py` (shipped profiles); `tools/codex_prover.py` (optional/profile or bare CLI) | Prover / Decomposer / decomposition Reviewer / full-ledger Judge / Comparator / Refiner / Formalizer / Faithfulness. Shipped profiles default to Claude; the bare legacy CLI constructs a Codex profile. | AlphaProof tool; LEAP; Autoreason |
| **Numeric grounding** | `tools/numeric.py` | exact-integer witness / residue-cover / descent checks | Axplorer, MathCode |
| **Retrieval** | `tools/retrieval.py` (Loogle), `semantic_retrieval.py` (BM25), `neural_retrieval.py` (bge-small, opt-in) → `HybridRetriever` | `retrieve(claim,error)→[lemma]`. **Default path is lexical: Loogle + BM25.** Without `mathagent[neural]`, the neural leg is unavailable/returns no hits and the hybrid keeps the lexical results; `HashingEmbedder` is an injected offline test double, not the live fallback. | LeanSearch/Loogle, LeanExplore |
| **Formalizer** | `tools/formalizer.py` | `formalize(prior,errors,lemmas)→Lean` | LEAP/Aristotle informal→formal |
| **Drivers** | `orchestrator/driver.py` (flat), `dag_driver.py` (AND-OR) | plan→prove→gate→repair; direct→decompose→review→recurse | LEAP |
| **Proof DAG** | `orchestrator/dag.py` | AND-OR DAG, **semantic** deep-hash memo, acyclicity | LEAP, Nexus deep-hash |
| **Ralph loop** | `orchestrator/ralph.py` | per-goal episodes + lessons-learned | AlphaProof_Nexus |
| **Population** | `orchestrator/population.py` | Elo tournament, `fit_bradley_terry`, `select_puct` | AlphaProof_Nexus |
| **Evolutionary backend** *(optional)* | `tools/openevolve_bridge.py` | evolve proof-sketch **ledgers** (MAP-Elites), gate-scored fitness, no-exec; `Decomposer` for `DagDriver` | OpenEvolve (AlphaEvolve OSS) |
| **Revision tournament** | `orchestrator/tournament.py` | Autoreason incumbent tournament (do-nothing wins ties; margin-gated displacement + a *structural* admissibility gate). **BT/PUCT are grafted from `population.py`, not AutoReason** (the paper disclaims them) and are near-decorative to displacement; aggregation is **pairwise-net, not Borda**. | Autoreason (Critic/do-nothing/margin); BT/PUCT from `population.py` |
| **Faithfulness** | `orchestrator/faithfulness.py` | adversarial multi-lens, default-unfaithful | AXLE / Goedel |
| **Terminal gate** | `orchestrator/formalize_bridge.py` | formalize→compile→audit→faithfulness → `authoritative` | PLAN §5 Layer 4 |
| **Liveness** | `orchestrator/state.py`, `run_profile.py` | NodeState + orchestrator call/repair/replan caps; optional separate per-node-verify cap. Terminal/evolution internals are separately bounded. | PLAN §4.3 |
| **Benchmark** | `agent/benchmarks/arxivmath.py`, `tools/answer_check.py`, `scripts/run_benchmark.py` | non-contaminative loader + SymPy grader + run records | MathArena ArXivMath |
| **Web UI** | `ui/server.py`, `ui/index.html` | SSE console driving `prove.py` with major CLI controls and mandatory faithfulness for certification | — |

**Curated method library.** Beyond the generic justifications, a set of *named* methods (`vieta_jumping`,
`pell`, `cauchy_bezout`, `euclid_splitting`, `squeeze`, `sum_to_product`, …) each carry a `method_ref`
pointing at a hand-written write-up under `knowledge/methods/`; `Toolkit.method_ref_for()` exposes that
metadata and the scanner can inspect the reference string. The live model adapters do **not** read the
referenced file automatically, so the catalog constrains vocabulary/provenance without silently injecting
method contents into prompts.

**Two drivers, one state machine.** `FlatDriver` runs a single target as a flat *prove → gate → repair →
judge* pipeline (any truncated/unhandled-review case is forced to `EXHAUSTED`, never silently `PROVEN`);
`DagDriver` adds the §9 AND-OR recursion on top. Every node carries a `NodeState` lifecycle
(`OPEN → IN_PROGRESS → PROVEN / FAILED_* / EXHAUSTED`) that drives memoization and failure classification,
and every run is an append-only **JSONL trace** (`trace.py`, injectable clock) rendered to a markdown
record — so every run is a deterministic, comparable experiment (invariant 7).

---

## 8. Search & revision machinery — Population / Elo / Bradley-Terry / PUCT, explained

These are **search-efficiency** tools (`orchestrator/population.py`) — they never touch the elementary
gate. They're worth understanding from scratch, so this section builds them up.

**The problem they solve.** When the driver decomposes a goal, it can ask Codex for **K candidate
decompositions** ("sketches"). We don't know which is best, and *testing* one (recursing in to actually
prove it) is **expensive**. What's **cheap** is asking a judge to **compare two** candidates ("which of
these two looks more likely to lead to a correct, fully-elementary proof?"). So we have a classic
two-part problem: **(a) rank K items from noisy pairwise comparisons, then (b) decide which one to spend
the next expensive attempt on.** Three standard tools, one per sub-problem.

### 8.1 Elo — an online strength rating from pairwise results

Borrowed from chess. Give every candidate a scalar **rating** `R` (start at 1500). Model the probability
that A beats B as a **logistic** function of the rating gap:

```
E_A = P(A beats B) = 1 / (1 + 10^((R_B − R_A) / 400))
```

After a comparison with result `S_A ∈ {1 win, ½ tie, 0 loss}`, nudge both ratings toward the surprise:

```
R_A ← R_A + K · (S_A − E_A)        (and symmetrically R_B ← R_B + K · (S_B − E_B))
```

`K` is a step size. If A was expected to win (`E_A` near 1) and did, `S_A − E_A ≈ 0` → tiny update; an
**upset** moves ratings a lot. This is literally **online stochastic gradient ascent** on the
log-likelihood of a logistic model — cheap, incremental, one update per comparison (`EloPopulation.record`).
Its weakness: the result is **path-dependent** (it depends on the *order* comparisons happened in).

### 8.2 Bradley-Terry — the same model, fit properly (a batch MLE)

Bradley-Terry is the statistical model that Elo is an online approximation *of*. Each item `i` has a
latent positive **strength** `s_i`, and

```
P(i beats j) = s_i / (s_i + s_j)
```

(Writing `β_i = log s_i`, this is `P = σ(β_i − β_j)` — the *same* logistic shape as Elo, base-e instead of
base-10.) Given the full **win matrix** `W` (`W[i,j]` = number of times `i` beat `j`), we can compute the
**maximum-likelihood** strengths directly, instead of nudging them online. We use the standard
**MM (minorization-maximization)** iteration — an EM-style scheme guaranteed to increase the likelihood
each step (`fit_bradley_terry`):

```
s_i ← w_i / Σ_{j≠i}  n_ij / (s_i + s_j)          then renormalize
        where  w_i = total wins of i,  n_ij = total games between i and j
```

Unlike Elo, this uses *all* the data at once, so it is **order-independent and more stable**. So in the
harness: **Elo** updates ratings cheaply *during* the tournament; **Bradley-Terry** re-fits them *from the
whole win matrix* afterward (`set_ratings_from_bradley_terry`) — the right thing to do once every
comparison is in.

### 8.3 PUCT — deciding what to expand next (explore vs exploit)

Now we have strengths, but recursing into a candidate is **expensive**, so we shouldn't blindly always
pick the top-rated one — a promising candidate we've barely looked at deserves a shot. This is the
**multi-armed-bandit** explore/exploit trade-off. **PUCT** ("Predictor + Upper-Confidence-bound applied to
Trees" — the selection rule AlphaZero uses inside MCTS) scores each candidate `a` as

```
score(a) = Q(a)              +   c · P(a) · √(ln(ΣN)) / (1 + N(a))
           └ exploit ┘            └─────────── explore ───────────┘
```

- `Q(a)` — the **exploit** term: the candidate's strength, normalized to `[0,1]`.
- `N(a)` — how many times we've already picked `a`; `ΣN` — total picks so far.
- `P(a)` — a **prior** over candidates (here a `softmax` over the ratings).
- `c` — how much to explore.

The explore term is **large** for an under-visited candidate (`N(a)` small) with a decent prior, and
**decays** as you keep picking it — "optimism under uncertainty," the same idea as the UCB1 bandit rule
`mean + √(2 ln N / n)`. (AlphaZero's original uses `√(ΣN)`; our `select_puct` uses the UCB1-style
`√(ln ΣN)` confidence term.) Pick the `argmax`, expand it, increment its visit count, repeat.

### 8.4 How they compose — "population search"

```
K candidate decompositions
  → pairwise Codex-judge comparisons (a tournament)  → a win matrix W + online Elo
  → Bradley-Terry MLE on W                            → stable latent strengths (the ratings)
  → PUCT                                              → which candidate to expand FIRST
```

That is **population search** (the AlphaProof_Nexus idea — *not* AlphaEvolve; see §2): keep a *pool* of
candidates alive, rate them from *cheap* comparisons, and spend *expensive* attempts best-first. It is
**search-efficiency only** — none of it changes whether a proof is accepted; that is the gate's job.
(`DagDriver._prove_via_population`.)

> **Not "evolutionary," to be precise.** This is a **one-shot rank-K-then-pick** ranker *within a single
> `decompose` call*: there is **no mutation, no crossover, and no persisted cross-episode population
> database**. The K-candidate pool is ranked and one is expanded, then discarded when the call returns —
> nothing carries across episodes. Calling it "evolutionary population search" overstates it; the only
> path in the project that genuinely *mutates* a population is the optional OpenEvolve backend (§8.6).

### 8.5 The Autoreason tournament reuses the same two estimators

The revision controller (`tournament.py`) is a *different* mechanism — **revision control**, not
decomposition ranking. Given a *working* proof, each pass runs the configured provider's Critic (failure
analysis only) → Author (revise) → Synthesizer (blind merge) → judge panel; a challenger displaces the
incumbent only if it beats it head-to-head by a `margin` **and** passes the goal-bound admissibility
predicate;
"do nothing" wins ties; stop after **k=2** consecutive incumbent wins.

> **Honest attribution & scope.** Two corrections to earlier framing:
> - **Bradley-Terry and PUCT are *not* from AutoReason.** The AutoReason paper explicitly *disclaims*
>   voting/game-theoretic machinery; BT/PUCT are grafted here from `population.py`. They are largely
>   **non-load-bearing** to the displacement decision — that is decided solely by `net >= margin`; BT
>   only breaks ties among qualifiers and PUCT only picks the next seed.
> - **The aggregator is pairwise-net, not Borda.** The paper specifies Borda over {A, AB, B}; this
>   implementation tallies **net head-to-head votes vs the incumbent** plus a margin. Morally similar,
>   genuinely different aggregator (the margin rule is itself paper-sanctioned).
> - **Do *not* claim "monotone / never regresses" unconditionally.** The guard holds only with respect
>   to a (possibly single, default `stages.judges=1`) LLM judge panel plus the admissibility predicate.
>   The preference panel is not the proof judge: refinement is disabled when `stages.review=false`, and
>   every challenger separately needs `no_gaps=true`. The predicate also applies the ordinary gate and
>   deterministic elementarity refuter; when per-node Lean is wired, inability to obtain a budgeted
>   `elementary_verified` result rejects the optional challenger. This path does not mint a terminal
>   certificate. The AutoReason paper is candid that its
>   filter is "bounded by the judges' biases" and shows quality-degrading displacement; the right claim here
>   is "displacement is *resisted* by a margin + judge panel and known-violation checks," not an unconditional
>   no-regression guarantee.

Both mechanisms resolve provider-specific comparator/refiner implementations through the role registry;
shipped profiles use Claude, while the bare legacy CLI uses Codex. Neither mechanism decides certification.

### 8.6 OpenEvolve — the evolutionary candidate backend (optional, gate-scored, no-exec)

§8.1–8.4 *rank* a fixed pool of K candidate decompositions. **OpenEvolve** adds an orthogonal third path:
instead of ranking a fixed pool, it **evolves the pool itself** — a population search that *mutates*
proof-sketch ledgers and keeps strong/diverse ones. It is the open-source AlphaEvolve implementation
(`algorithmicsuperintelligence/openevolve`), grafted in via `tools/openevolve_bridge.py`.

How it maps onto our problem:

- **Genotype = a proof-sketch ledger** — a JSON string conforming to `gates/ledger.schema.json`. The
  evolved artifact is **always text**, never code.
- **Fitness oracle = the deterministic gate.** `score_ledger` runs `gates.gate.evaluate` and maps its
  verdict to a scalar: `REJECTED → 0.0`, `NEEDS_REVIEW → 0.5`, `PASSED_DETERMINISTIC → 1.0`. The gate
  **fails closed** (a malformed ledger or any internal exception → REJECTED → 0.0), so there is no
  fail-open path.
- **Quality-diversity = MAP-Elites.** Two feature dimensions — `step_count` and
  `justification_diversity` (distinct justifications used) — keep the population from collapsing onto one
  shape, the same quality-diversity idea AlphaEvolve/OpenEvolve are built around.
- **Mutations = a real AlphaEvolve-style LLM ensemble.** OpenEvolve's mutation calls are driven by a
  **two-model breadth/depth ensemble** through the headless `claude` CLI
  (`tools/claude_cli._run_claude`, wrapped in `asyncio.to_thread`): **Sonnet = BREADTH** (fast, high
  sampling weight ≈0.8 — many candidates) + **Opus = DEPTH** (stronger, low weight ≈0.2 — occasional
  high-quality suggestions). This is the **same breadth/depth split AlphaEvolve uses** (it ran a
  Gemini-2.0 *Flash* + *Pro* ensemble; we run *Sonnet* + *Opus*). AlphaEvolve's own "Models used"
  rationale: the mix "balance[s] computational throughput with the quality of generated solutions" —
  the fast model "enables a higher rate of candidate generation, increasing the number of ideas
  explored per unit of time," while the stronger model "provides occasional, higher-quality
  suggestions that can significantly advance the evolutionary search." OpenEvolve realizes the ensemble
  as a weighted `config.llm.models` list, sampling one model per generation by normalized weight. A
  dependency-free `StubEvolveLLM` makes the whole loop deterministically testable offline. (Earlier
  docs described a single Codex-backed mutator; the bridge now wires the genuine two-model ensemble.
  A subtle bug where evolution was a silent no-op — OpenEvolve pickles the whole `Config` to its
  worker processes, and the prior closure/lambda model factory was unpicklable and got dropped, so
  every iteration only re-scored the seed — is **fixed** by picklable module-level factories
  (`_ClaudeLLMFactory`/`_FixedLLMFactory`).)
- **Wiring.** There are three supervised modes. `stages.evolve` runs a proof-ledger pre-search and offers
  its champion as the first ordinary prover candidate; the Ralph/DAG judges, goal binding, per-node Lean,
  terminal gate, and shared search budget still decide it. `stages.evolve_witness` evolves an exact-integer
  construction specification for diagnostic grounding and never proves the theorem by itself.
  `stages.evolve_fallback` constructs `OpenEvolveBackend` as a last-resort decomposer after ordinary
  decomposition attempts fail; its sketch still passes the same goal/obligation/acyclicity/H0 checks before
  commit. Each mode is bounded by its declared iteration count (`evolve`, `evolve_witness`, or
  `evolve_fallback`), and those counts are reported separately from the orchestration LLM-call budget.

**SAFETY (load-bearing, and unit-tested).** The evaluator **READS** the candidate ledger as text and
gates it — it never `exec`/`eval`/`import`s the evolved content, so evolution adds **no candidate-artifact
code-execution surface** (a "poison" ledger whose text would raise on import simply parses as
invalid JSON → REJECTED → 0.0). The only subprocess is the audited headless `claude -p` call (all tools
disabled, throwaway cwd, timeout) — it only *generates text*; the evolved ledger is never shelled out.

**Scope (no over-claim).** This is **search/generation efficiency**, exactly like §8.1–8.5: it evolves
ledgers *scored by the soft, deterministic gate* (Layers 1–3) and so only **ranks and filters**. It does
**not** certify a proof elementary — that remains the sole job of **Layer 4** (the Lean dependency +
axiom audit, §3). OpenEvolve is an **optional** dependency (`pip install mathagent[evolve]`); `available()`
probes for it without importing, and the bridge is testable offline against stubs. An enabled evolutionary
stage is part of the declared profile, so the supervisor rejects a missing package or Claude ensemble
transport before the run; it is not silently reported as an executed no-op.

---

## 9. The proof DAG — AND-OR structure, memoization & the goal hash

§4–§5 keep saying "DAG" and "decompose→recurse"; this section explains what that machine actually *is*,
from scratch. Like §8, everything here is **search structure**: it changes *how fast* a proof is found,
never *whether it is accepted* (that is the gate, §3).

### 9.1 The AND-OR proof DAG (LEAP's spine)

Think of how a person plans a hard proof: *"I can prove the theorem **if** I can first prove these three
lemmas."* That sentence, made into a data structure, is the whole idea (`dag.py::ProofDAG`).

- An **OR-node is a goal.** It asserts "this statement is provable," and can be discharged in *either* of
  two ways: by a **direct** proof (a self-contained step-ledger), **or** by *one* decomposition.
  (OR = "any one route suffices.")
- An **AND-node is a decomposition.** It is a *sketch* that proves the parent **assuming** a list of
  sub-lemmas, so **all** of its child sub-lemma goals must be proven for it to count.
  (AND = "every part is required.")
- Each child is itself an OR-node, so the structure recurses — goals contain decompositions contain
  goals. This is the classic **AND-OR graph** that AI search uses for problems that split into
  independent sub-problems.

Why a **DAG** (directed acyclic graph) rather than a tree? Because different branches often need the
*same* lemma. Keyed by the goal hash (§9.4) that lemma is **one shared node**, not two copies — so the
tree becomes a graph with reconvergent edges. "Acyclic" is enforced (§9.3): a proof may never, even
transitively, depend on itself.

**How the driver walks it (`dag_driver.py::_prove`) — depth-first with backtracking.** For each goal it
(1) tries a **direct** proof first (cheap; the Ralph loop, §5.1); (2) on failure asks a **Decomposer**
for a sketch + child goals; (3) **gates** the plan (below); (4) commits it as an AND-node and **recurses
depth-first** into the children, **backtracking** — abandoning that decomposition and trying another —
the instant any child fails. Depth-first keeps only one path in memory and surfaces a finished proof the
moment a single branch closes top-to-bottom. This *is* §5's "try-cheap-first, escalate-on-failure"
cascade, concretely.

**Composition checks before a decomposition is trusted.** The AND-node guarantee — *all children proven
⇒ parent proven* — is only real if the plan is honest, so each proposed decomposition must clear the
deterministic ledger/goal/obligation checks, exact child-set validation, acyclicity, and mandatory H0
sibling-context consistency. H0 is typed as `Literal[True]` in production profiles and is not an ablation
axis; disabling it could compose mutually inconsistent branches into a false proof verdict.

- **Honest-decomposition validation** (`_lemma_claims`): the sketch is parsed as a step-ledger, and the
  set of goal-hashes of its `lemma`-justified steps must **exactly equal** the set of declared child
  goals. This blocks a sketch that secretly leans on an un-decomposed extra lemma (a hidden gap) or
  declares children it never actually uses.
- **The decomposition reviewer** (`Reviewer.review`, LEAP's pre-commit reviewer): an LLM judge checks
  the plan is **useful** — non-circular, each child *strictly simpler*, and together *implying* the
  parent — and stays inside the elementary toolkit; its notes feed back into the next re-plan. LEAP's
  ablation shows that *without* this filter the agent loops forever on "valid but non-simplifying"
  decompositions (a child that just restates the parent). It is **soft** (rank/prune) — it never
  certifies elementarity; only Layer 4 does (§3). This reviewer is optional under `stages.review`; the
  deterministic composition checks above remain mandatory.

When per-node Lean is enabled, a leaf verifier and a separate sketch verifier are attached. The sketch
verifier formalizes a SORRY-free composition theorem deriving the parent from child hypotheses; once that
verification is attempted, non-compilation, unavailability, or an audit rejection prevents the decomposition
from being committed. If the optional per-node verification sub-cap is exhausted, normal mode may soft-commit
without the sketch stamp, while `lean.strict=true` rejects it. For directly proven leaves, normal mode may
retain a soft proof after a compile/formalization failure or verifier exception, whereas strict mode does not;
an unavailable Lean toolchain leaves the leaf retryably exhausted. Root authority still requires the terminal
gate.

### 9.2 Memoization — prove each sub-lemma once (AlphaProof_Nexus's goal cache)

With `stages.memo=true`, **memoization** is the dynamic-programming trick of *caching the result of an expensive computation so an
identical sub-problem is solved once and reused.* Proof search is full of **overlapping sub-problems** —
the same lemma resurfaces on many branches — so every goal becomes a node in a table keyed by its hash
(`get_or_create`). The first time it is proven, the proof is stored; every later occurrence is a
**cache hit** (counted in `cache_hits`) that reuses it instead of re-searching. When the finished proof
is serialized (`assemble`/`proof_bundle`) a reused node is expanded once and later occurrences are
marked **`shared`** ("proven above; reused"). Failure is cached too: a node marked `FAILED_GAP` is not
re-attempted. With memo disabled, reads do not short-circuit and repeated goals are proved fresh.

This is the load-bearing efficiency idea, not a micro-optimization: it is what collapses an exponential
re-derivation (LEAP's "Hilbert" tree baseline re-proves the same lemmas over and over) into a tractable
graph search, and it lets an "anticipatory" lemma, proven once, feed many later steps.

### 9.3 The acyclicity guard (LEAP's state-writer)

A cache of "X is provable" is dangerous if a proof of X is allowed to assume X. So before committing a
decomposition, `would_create_cycle` runs a DFS reachability check (`reaches`) over the already-committed
edges plus the current ancestor set, and **rejects** any child that is the parent restated, an ancestor
on the current path, or anything that transitively depends back on the parent. This keeps the DAG
**acyclic**, making "A because B, B because A" circular proofs impossible — the precondition for the
whole *all children proven ⇒ parent proven* guarantee.

### 9.4 The goal-hash contract (soundness-critical)

The key tying §9.1–§9.3 together is a **full SHA-256 of the goal statement** (`dag.py::goal_hash`), so an
identical sub-lemma on different branches resolves to one node. This key is **soundness-critical**: a
*false hit* (two distinct goals sharing an identity) would reuse a proof of the wrong lemma. Its input
therefore normalizes only Unicode NFC and whitespace runs. It deliberately does **not** fold notation,
synonyms, punctuation, delimiters, variable names, or operand order: `×` may mean Cartesian product while
`*` may mean pointwise multiplication, so treating them as interchangeable is unsound. A separate lossy
`canonical_form` exists only for H0 signature analysis and never supplies goal binding or memo authority.
A miss only costs recomputation; a false hit is unsound, so identity is intentionally strict.

---

## 10. Autoformalization & retrieval

The **repair loop** (`formalize_and_audit(repair_iters=N)`): formalize → compile on the persistent server
(~0.1s) → on a compile error *or* an audit reject, feed diagnostics + retrieved real Mathlib lemmas back
and re-formalize. A deep dive concluded Codex's `/goal` mode should **not** own this loop (a Python loop
keeps control of retrieval/denylist/budget/audit; the persistent server's ~0.1s compile beats Codex
re-running `lean` at ~60s/iter).

Retrieval is a **hybrid of three legs**, each strong where the others are weak:

- **Loogle** (`retrieval.py`) — *exact names, by pattern.* It mines the compiler's "unknown identifier"
  errors (the names the formalizer hallucinated) plus concept keywords from the claim into Loogle's HTTP
  name/type search, surfacing the *real* Mathlib lemma that should replace a hallucinated one — the single
  most common formalization fix.
- **BM25** (`semantic_retrieval.py`) — *lexical keyword overlap.* The classic IR ranking function (rare
  terms up-weighted by inverse document frequency, repeats saturating; `k1=1.5, b=0.75`) over an inverted
  index of declaration names + signatures (name tokens boosted ×3), restricted to a curated elementary
  slice of Mathlib (`Data/Nat`, `Data/Int`, `Data/ZMod`).
- **Neural bi-encoder** (`neural_retrieval.py`) — *meaning, not words — but INERT by default.*
  When the optional `mathagent[neural]` extra is installed, `bge-small-en-v1.5` embeds each
  declaration's `name+signature` (only those two fields are extracted — there is **no `doc` field**;
  an earlier "name+signature+doc" claim was an over-statement) and the query *independently* and ranks
  by cosine similarity (the LeanExplore recipe), closing the abbreviation gap (`gcd` ↔ "greatest common
  divisor"). An optional **cross-encoder** (`ms-marco-MiniLM`) can re-score the top-N pool by feeding
  each (query, lemma) pair *jointly* through a transformer. **However:** the repo does **not** install
  or ship `mathagent[neural]`, so in the base install neither the bi-encoder nor reranker runs. The
  neural retriever reports unavailable/returns no hits and `HybridRetriever` preserves Loogle + BM25.
  `HashingEmbedder` is a dependency-free injected test double, not a silent live fallback.

The three are fused by **interleave-and-dedupe** (`HybridRetriever`): round-robin each source's best hits,
drop duplicate declaration names, and **degrade gracefully**. Because the neural dependencies are absent
by default, the **shipped/default retrieval path is lexical: Loogle + BM25**. Interleaving sidesteps
having to calibrate three incomparable score scales.

### 10.1 The faithfulness wall — proving the *right* theorem

A Lean proof can pass the kernel **and** the elementarity audit yet prove a subtly *different* statement —
a weaker special case, or a vacuously-true one with impossible hypotheses. AlphaProof_Nexus "solved" wrong
density variants this way, and Goedel reports >80% of failures are misformalization. So after compile+audit
an **adversarial panel** (`faithfulness.py::adversarial_check`) checks the Lean *statement* against the
English claim through four diverse **lenses**, each told to actively *find* a discrepancy and **default to
"unfaithful" if unsure**:

- **back-translation** — read the Lean back as English; does it match the claim?
- **quantifiers / domain** — are `∀`/`∃`, variable types, and edge values (0, negatives) right?
- **vacuity** — impossible hypotheses, or a trivially-true restatement?
- **strength** — strictly weaker or stronger than claimed (a special case, or unrelated)?

The statement is accepted only if at most `max_unfaithful` lenses object (default **0** — unanimity).
Using *diverse* lenses rather than N identical judges is deliberate: each catches a failure mode the
others miss. This is the final wall, so `authoritative_elementary` also means **faithful** — and it
**fails closed**: if no faithfulness panel ran, the result is *not* authoritative (the previous
`faithful = (checker is None or …)` fail-open was an audited soundness bug). The CLI's `--terminal-gate`
/ `--formalize` certification modes require the panel; combining them with `--no-faithfulness` is rejected.
The lower-level API can still represent a completed audit without authority, reported categorically as
`audited_not_certified`. Authority additionally requires the informal proof to have been admitted, and
both the formalizer and faithfulness wrapper to declare `certification_trusted=True`. Live wrappers are
trusted only at the default unanimity threshold; generic/scripted panels default to untrusted.

---

## 11. Benchmarking & the certification ladder

v1's headline benchmark is **MathArena ArXivMath** — a *final-answer*, contamination-resistant set (only
gold answers ship; problems are reverse-engineered from very recent arXiv papers). The integration is
**non-contaminative by construction**: the loader splits each item into a *prompt* (statement only) and a
*held-out oracle* (answer); a `Problem` has no `answer`/`source` field, so a solver cannot see them.
Grading is **conservative bounded SymPy answer-equivalence** (`answer_check.py`), not string match. An
allowlisted Python AST is converted node-by-node into a small SymPy vocabulary; attributes, unknown calls,
statements, non-finite floats, oversized/deep ASTs, and excessive exact-power materialization are declined.
The cascade is: exact normalized text; typed collection comparison (sets are deduplicated and matched by
bounded maximum bipartite matching, while tuples/bracketed forms stay ordered and delimiter-sensitive);
exact rational equality; finite numeric comparison within tolerance (with explicit infinity handling); then
structural SymPy equality. It deliberately does **not** run attacker-controlled `simplify()`/`equals()` or
collapse a collection to a scalar. Thus `1/2`, `0.5`, and `\frac{1}{2}` compare equal without turning the
grader into an unbounded symbolic solver. The runner isolates faults **per item**: an item error is recorded
without aborting the run. Two answer-only solvers plug into the runner: a vanilla single-shot answerer
and an optional answer-refinement tournament. Neither is the typed-ledger/Layer-4 proof harness.

> **Genre note:** ArXivMath is final-answer; the harness's distinctive value is *elementary-proof
> certification*, which a final-answer benchmark cannot measure. The **certification ladder**
> (`prove.py --terminal-gate …`) is the test that does: it runs real NT theorems through the full
> formalize→audit pipeline and reports `authoritative_elementary`.

---

## 12. The web UI

`ui/server.py` is a zero-dependency (stdlib) SSE server: it builds the `prove.py` argv from the controls
chosen in the browser and streams the harness's output back live. `ui/index.html` exposes the major CLI
controls (model/effort/mode, the **Formalize + Layer-4 certify** toggle, refine/population/judges,
retrieval, budgets) and displays the categorical proof/certification outcome. It is not the schema editor
for every `RunProfile` field. The certification path always includes the required faithfulness panel; the
UI does not expose an insecure "skip faithfulness" control. Safety: it binds `127.0.0.1`; argv is a list
with the typed problem passed after `--` (no shell/flag injection).

---

## 13. What is live-validated (evidence, not claims)

- **Certification pipeline authoritative on real NT theorems (2026-06-13):** `n²≡0,1 mod 4` and `√2`
  irrational (`x²=2y²`, **infinite descent**) both went prove→formalize→compile→**Layer-4 audit PASS (0
  rejects)**→faithfulness 4/4→`authoritative_elementary=True`. **Two of three historical attempts**
  certified; the miss
  (`3∣a²+b²`) was an autoformalization compile failure, reported honestly as `False` (not waved through).
  See [`live_certification_runs.md`](live_certification_runs.md).
- **Non-gameable audit:** an `IsDedekindDomain` proof is rejected by the closure denylist even though it
  compiles; a `sorry` is caught via `sorryAx`.
- **ArXivMath live (GPT-5.5-xHigh):** vanilla 4/5 on the NT subset; the answer-refinement tournament
  scored 3/3 on items {7,15,17} — no regression on that overlap. This was not a full proof-harness run,
  so it measures no typed-ledger/Layer-4 lift.
- **Persistent server:** Mathlib loads once (~76s), then audits ~0.1s.

---

## 14. Design invariants (must not be broken)

1. **The gate is the product.** Under soft/authoritative elementarity, a correct non-elementary proof is
   an objective failure. `elementarity=none` is an explicit solution-only control, never an implicit
   weakening of soundness.
2. **Only Layer 4 certifies "elementary."** Layers 0–3 pressure/filter.
3. **Never trust compile-success or hammer-success** — always audit the proof term.
4. **Judges rank; deterministic mechanisms accept.**
5. **The memo key never merges distinct goals** (§9).
6. **Everything terminates.** Orchestrator budgets bound search/review loops; terminal repair,
   faithfulness lenses, per-node verification, and evolution are separately bounded. Verification model
   calls and evolution iteration counts are separately reported; exhaustion is reported honestly.
7. **Everything is a recorded, comparable experiment** (JSONL trace + run record + toolchain/denylist
   version).
8. **Providers sit behind Protocols.** Claude/Codex implementations and deterministic offline stubs share
   contracts, but scripted/generic components are not certification authorities by default.
9. **Untrusted artifacts are never authority.** Memoized proofs retain their gate/context identity;
   retrieved lemma names are only formalizer hints and the resulting proof term is recompiled/audited.
10. **No teaching-to-the-test.** Improvements are problem-independent and measured on held-out problems;
    the audit makes search-bias safe, but it does not make eval-on-seen-problems honest.
11. **H0 consistency is mandatory.** It is a logical composition invariant (`Literal[True]`), not a
    sweepable performance axis.
12. **Search fitness is never reporting status.** Only the categorical status ladder may reach
    `authoritative_elementary`, and only with its logical prerequisites.

---

## 15. Status & roadmap pointers

- **Build evidence:** [`build_status.md`](build_status.md) (dated historical snapshot plus a current
  implementation-semantics addendum; rerun validation commands for the present tree).
- **Staged plan & scope decisions:** [`../../agent/PLAN.md`](../../agent/PLAN.md) (§9 roadmap).
- **The bottleneck:** measured end-to-end reach on hard, held-out number-theory problems. Search and
  autoformalization both remain candidate failure axes; neither is inferred from old live examples.
- **Visual companion:** [`system_design.html`](system_design.html) (Excalidraw diagrams of the pipeline,
  the layered gate, and the two-axis bottleneck).
