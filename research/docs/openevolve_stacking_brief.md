# MathAgent — Context Brief for Judging the OpenEvolve / AlphaEvolve Stacking Recommendations

> **Who this is for.** You are an independent reviewer with **no prior knowledge of this project and no
> access to its source files**. Everything you need is in this document — it is deliberately
> self-contained (terms are defined inline; a glossary is at the end).
>
> **Your task.** Read this brief, then **(1) critically judge the stacking recommendations in §9**
> (are they sound, well-prioritised, safe, and likely to help?), and **(2) propose your own
> recommendations** for how to compose an AlphaEvolve-style evolutionary search with the rest of this
> system. §10 ("Decision context") tells you the constraints a good proposal must respect. You will not
> be able to read any code, so reason from the descriptions here; where a recommendation hinges on a
> detail you can't verify, say so explicitly rather than assuming.

---

## 1. What MathAgent is (the mission)

MathAgent is a **training-free agentic harness** with an unusual goal. Most automated-theorem-proving
(ATP) systems try to answer *"can the machine find a proof?"*. MathAgent asks **two** questions:

1. **Can it find a proof?** (the standard ATP problem), and
2. **Can it prove that the proof is *elementary*?** — i.e. that the argument stays inside a restricted,
   "IMO-usable" toolkit and does **not** secretly rely on heavy machinery (algebraic number theory,
   class field theory, elliptic curves, Dedekind domains, analytic methods, etc.).

The second question is the product. **A correct but *non-elementary* proof is treated as a failure, not
a success.** This is the original, hard part — and it exists because of one load-bearing fact:

> **"Lean-verified ≠ elementary."** A proof can pass a formal proof checker (the Lean theorem prover's
> kernel) and still route through arbitrarily heavy mathematics, because the standard library (Mathlib)
> *is* the source of that heavy machinery. So "it compiled / it's formally verified" says nothing about
> whether the proof is elementary.

Domain (v1): **elementary number theory** — divisibility, congruences, descent, casework, Diophantine
equations, etc. The intended difficulty ladder runs from IMO-style problems up toward research-grade NT.

"Training-free" means: **no model is fine-tuned**. The system orchestrates *general-purpose* frontier
LLMs as tools. (Historically the per-subgoal prover slot is filled by a GPT-5.5 model accessed through a
local `codex` CLI; the new evolutionary component additionally drives Anthropic's Sonnet and Opus
models through a local `claude` CLI — see §8.)

---

## 2. The architecture at a glance

MathAgent is **one system with a definite shape**, assembled from ideas in ~20 research papers (treated
as a "component menu", not competing blueprints). The shape:

- **Spine — a LEAP-style blueprint loop.** A goal is attacked by *first trying a direct proof*, and on
  failure *decomposing* it into sub-lemmas, recursively, organised as an **AND-OR DAG** (defined below).
- **Grafts:**
  - From **AlphaProof_Nexus**: the "Ralph" lessons-learned retry loop; a deep-hash goal cache
    (memoization); a population/Elo candidate-ranking search; and — most importantly — **SafeVerify →
    our Layer-4 audit** (a non-gameable check of what a formal proof actually depends on).
  - From **AutoReason**: an incumbent-revision *tournament* that polishes a proof without ever
    regressing it.
  - From **AlphaEvolve**: a *cheap-first evaluation cascade*, and a key *negative* lesson (§7).
- **Substituted:** the one thing nobody can reproduce — a specialised RL-trained prover — is replaced by
  a general LLM.
- **Ours (the product):** the **layered elementary gate** (§3), especially the authoritative Layer-4
  audit.

### Two verdicts (do not conflate them)

- **`PROVEN`** — an *informal* proof was found and passed the soft/structural checks (Layers 0–3 below).
  This is **not** a certification of elementarity.
- **`authoritative_elementary = True`** — the proof was *formalised* into Lean, *compiled*, its proof
  term *audited* to be elementary, **and** its Lean statement verified to faithfully match the requested
  goal. This is the only real certificate. The harness can reach `PROVEN` without reaching
  `authoritative_elementary`, and it reports that honestly.

---

## 3. The layered elementary gate (the heart of the system)

Elementarity is enforced as **defense-in-depth, cheap-first**, where **only the last layer is
authoritative**. Understanding which layers are *gameable* vs *non-gameable* is **the single most
important thing for judging any stacking proposal**, because an evolutionary search optimises against
whatever fitness signal you give it — and will ruthlessly exploit a weak one.

| Layer | What it does | Authority | Gameable? |
|---|---|---|---|
| **0 — Framing** | Injects the objective spec (allowed-toolkit / denylist) into every LLM prompt; biases generation toward elementary methods. | soft (present, not obeyed) | yes |
| **1a — Structural** | Deterministic validator over the typed "step-ledger" (defined below): every step's justification is in a closed vocabulary; the dependency graph is acyclic; no dangling references; exactly one connected conclusion; declared "obligations" are discharged. | deterministic *filter* | mostly not (structural) |
| **1b — Prose scan** | Keyword/euphemism scan of the prose; routes suspicious steps to review (never rejects on its own). | soft router | yes |
| **2 — Adversarial review** | N independent LLM critics judge whether every step is elementary and gap-free. | soft consensus (ranks/prunes) | yes (LLM judges) |
| **3 — Numeric grounding** | **Exact-integer** re-checking: searches for witnesses, verifies finite case-covers are complete, verifies descent steps strictly decrease — all in a restricted integer-only arithmetic sandbox (no floats, no code execution). | deterministic, bounded | **NO (exact arithmetic)** |
| **4 — Lean dependency audit** | Compile the formal proof, then walk the kernel proof term's *entire transitive constant-dependency closure* and its axiom set. Reject if any dependency hits a content denylist (class groups, Dedekind domains, elliptic curves, number fields, cyclotomic/modular theory, …) unless an allowlist wins; reject any non-whitelisted axiom (which is also how a `sorry`/hole is caught). | **deterministic, authoritative — the ONLY place "elementary" is enforced** | **NO (reads the kernel term)** |

The deterministic gate (Layers 1a/1b/3) produces one of three verdicts:
`REJECTED` · `NEEDS_REVIEW` · `PASSED_DETERMINISTIC`. It **fails closed** (any malformed input or internal
error becomes `REJECTED`).

**Why this matters for evolution:** Layers 0, 1b, 2 are *gameable* (they can be satisfied by clever
wording or by relabeling). Layer 3 (exact integer checks) and Layer 4 (kernel dependency audit) are
*not* gameable, because they check **content** against ground truth rather than form. A fitness function
built only on the soft layers can be hill-climbed toward proofs that *look* valid but aren't.

### A concrete demonstration that the soft gate is gameable

We asked the harness for an *elementary* proof of `x² + 1 = 2y⁴` (Ljunggren's equation — famously
**non-elementary**; the real proof needs Pell-equation / quartic machinery). The LLM produced a ledger
whose crux step asserted the genuinely hard fact ("the only square in this Pell denominator sequence is
…") under the *allowed* method label **`vierzahlensatz`**, with no actual justification. The deterministic
gate returned **`PASSED_DETERMINISTIC`** — i.e. the soft gate was fooled by a relabeled hand-wave. Only a
Layer-4 formalisation+audit (which we had turned off for that run) would have caught it. **This is the
canonical failure mode an evolutionary search will amplify if pointed at the soft gate alone.**

---

## 4. The proof-search machinery (what generates and selects candidates)

- **AND-OR proof DAG.** The proof plan is a directed acyclic graph. An **OR-node** is a *goal*: it can be
  discharged *either* by a direct proof *or* by *one* decomposition (OR = any one route suffices). An
  **AND-node** is a *decomposition*: a sketch that proves the parent **assuming** a list of sub-lemmas, so
  **all** of its child goals must be proven (AND = every part required). Children are themselves OR-nodes,
  so it recurses. It's a DAG (not a tree) because an identical sub-lemma reached on two branches becomes a
  single shared node. An **acyclicity guard** forbids a proof from depending on itself, and **memoization**
  (a cache keyed by a semantic hash of the goal statement) means each distinct sub-lemma is proven once
  and reused.
- **The driver** walks the DAG depth-first with backtracking: try a *direct* proof first (cheap); on
  failure ask a **Decomposer** (an LLM) for a sketch + child goals; a **Reviewer** (an LLM) gates the
  decomposition (is it useful, non-circular, each child strictly simpler, and elementary?); commit it;
  recurse.
- **Ralph loop.** The per-node direct-proof attempt: an LLM emits a step-ledger → the deterministic gate
  re-checks it → on rejection, the gate's findings are appended as "lessons-learned" and the LLM retries.
  Budget-bounded.
- **Population / Elo / Bradley-Terry / PUCT.** When configured, the decomposer generates *K* candidate
  decompositions; they are ranked by **pairwise LLM comparisons** turned into strengths via an **Elo**
  rating (online) and a **Bradley-Terry** maximum-likelihood fit (batch), and the best is expanded first
  using **PUCT** (a multi-armed-bandit explore/exploit rule from AlphaZero-style search). *This is a
  ranking tournament over candidates generated once — it is NOT an evolutionary loop.*
- **AutoReason incumbent tournament.** A no-regression refinement: a "challenger" revision can displace
  the current proof only if it beats it on a blind judge panel by a margin **and** stays elementary;
  "do nothing" wins ties. Monotone — a "better-sounding but non-elementary" revision can never win.
- **Evaluation cascade** (the AlphaEvolve idea actually adopted): run cheap deterministic checks before
  expensive judge/Lean passes, rejecting early.

---

## 5. Supporting components

- **The step-ledger** is the universal data contract: a proof expressed as JSON — a list of steps, each
  `{id, claim, justification ∈ closed-vocabulary, depends_on:[…], obligations?}`, with exactly one
  `conclusion` step. Every gate layer reads it; the formalizer consumes it.
- **Numeric grounding** is a *restricted, exact-integer* evaluator: model-supplied expressions are parsed
  into an allowlisted AST (only `+ − × ^`, integer leaves, bounded exponents — **no `eval`, no floats**)
  and checked over bounded integer search boxes. It can confirm a descent strictly decreases, a finite
  case-cover is complete, or a claimed solution set is exactly right. **This is a non-gameable signal
  that rewards real mathematical content**, and (importantly) non-elementary objects are *literally
  unrepresentable* in an integer-only AST.
- **Faithfulness panel.** After formalisation, an adversarial panel checks the Lean *statement* against
  the English goal through four diverse "lenses" — **back-translation** (does the Lean mean the same?),
  **quantifiers/domain** (are ∀/∃, types, edge cases right?), **vacuity** (is the hypothesis impossible /
  the statement a trivial restatement?), and **strength** (is it weaker/stronger than asked?). Each lens
  is told to actively *find* a discrepancy and default to "unfaithful" if unsure; it fails closed.
- **Hybrid retrieval** fetches real library (Mathlib) lemmas — exact-name (Loogle), lexical (BM25), and
  semantic (a neural bi-encoder) — to help the formalisation step.
- **Lean Layer-4 audit** (the authoritative gate) walks the compiled proof term's transitive dependency
  closure + axioms (§3).

---

## 6. Honest state, fidelity, and limitations (read this before judging)

- **Test status:** the offline test suite passes — **398 passed, 8 skipped** (the skips are opt-in
  live-integration tests). The code is well-covered and was recently hardened.
- **A security/soundness audit was run and its findings fixed.** Notably, several ways to *fool* the
  certification were closed: a faithfulness check that "failed open" (certifying with no check) was made
  fail-closed; an injection that let a generated proof self-report a clean audit was closed; and **two
  arbitrary-code-execution holes were removed** (model-supplied expressions used to be evaluated with an
  unsafe `sympify`/`parse_expr`). **The project is now security-conscious about executing model output —
  this directly constrains how an evolutionary search may be wired (see anti-patterns, §9).**
- **A fidelity caveat about the "LEAP spine".** LEAP (the paper our DAG is modeled on) verifies *every
  node with the Lean compiler* — the proof artifact at each node *is* Lean code. **MathAgent diverges:**
  it runs the DAG over *informal* step-ledgers gated by the *soft* deterministic gate, and defers Lean to
  a single *optional, terminal* audit step. So per-node verification in MathAgent is *soft*, not
  kernel-checked. This is deliberate (it's how we can target "elementary", which LEAP doesn't), but it
  means the per-node selection pressure is gameable unless paired with Layers 3/4.
- **The target problems are genuinely hard and often non-elementary.** E.g. `x²+2=y³` (Mordell) and
  `x²+1=2y⁴` (Ljunggren) have *no* elementary proofs in general; the standard proofs use algebraic
  number theory or Pell machinery. The system *finds the answers* but correctly *declines to certify them
  elementary*. A stacking proposal should not assume every goal *has* an elementary proof.

---

## 7. AlphaEvolve and OpenEvolve (the evolutionary method we are stacking)

**AlphaEvolve** (Google DeepMind) is an LLM-driven **evolutionary coding agent**. Its loop: keep a
*program database*; sample parent programs to build a rich prompt; an LLM proposes *diffs*; apply them to
make child programs; **evaluate** each child with a user-supplied evaluator; register promising children
back into the database. Two design pieces matter here:

- **The model ensemble (breadth vs depth).** Quoting the paper's "Models used": it uses *"a combination
  of Gemini 2.0 Flash and Gemini 2.0 Pro… Flash, with its lower latency, enables a higher rate of
  candidate generation"* (**breadth** — explore many ideas fast) *"…Pro… provides occasional,
  higher-quality suggestions that can significantly advance the search"* (**depth** — rare, high-quality
  moves). The ensemble samples models by weight: the fast model often, the strong model occasionally.
- **The database** combines **MAP-Elites** (a "quality-diversity" archive: keep the best candidate in
  each cell of a behaviour grid whose axes are chosen *feature descriptors*, so you preserve *diverse*
  high performers instead of collapsing onto one) with an **island model** (several semi-isolated
  sub-populations that occasionally migrate, preserving diversity).

**What AlphaEvolve actually achieved** (so you don't over-credit it): it *discovers* programs/constructions
optimised by an executable score (e.g. faster matrix-multiplication algorithms, better mathematical
constructions). It is a *search/optimisation* tool driven by an automatic evaluator — not a theorem
prover and not a "solve any open problem" oracle. (Aside, to prevent a common confusion: a *different*
system, AlphaProof_Nexus, is the one that reports machine-checked formalisations of some already-resolved
Erdős/OEIS problems — and it relies on a trained prover plus human statement-validation, which we do
**not** have.)

**OpenEvolve** (`github.com/algorithmicsuperintelligence/openevolve`) is the **open-source implementation
of AlphaEvolve**: a Python package with a `Config`, a controller, the MAP-Elites + island `database`, a
pluggable LLM interface, and an `Evaluator` you supply. **We depend on it directly.**

---

## 8. Our OpenEvolve integration — what we actually built

We wired OpenEvolve in as an **optional, third candidate-generation path** alongside the direct Ralph
loop and the single-shot Decomposer. Key design decisions:

1. **It evolves *proof-sketch ledgers*, not code.** The "program" OpenEvolve mutates is a **JSON
   step-ledger (text)**. This is a deliberate safety choice: OpenEvolve's *native* mode is "evolve a
   Python program → **execute it** → score it", which is **arbitrary-code-execution by design** — exactly
   the class of hole our audit just removed. So our evaluator **reads the evolved ledger as text and
   scores it with the deterministic gate; it never `exec`/`eval`/`import`s the evolved content.** There is
   no new code-execution surface.
2. **The fitness oracle is the deterministic gate.** A candidate ledger's fitness is its gate verdict
   mapped to a scalar: `REJECTED → 0.0`, `NEEDS_REVIEW → 0.5`, `PASSED_DETERMINISTIC → 1.0`. The MAP-Elites
   feature dimensions are currently **`step_count`** and **`justification_diversity`** (number of distinct
   justification types used). *(Both of these facts — soft-gate-only fitness, and incidental/structural
   feature axes — are central to the recommendations and anti-patterns below.)*
3. **The mutation LLM is a real AlphaEvolve-style ensemble: Sonnet = breadth, Opus = depth.** We drive
   Anthropic's Claude models through a headless `claude` CLI. `config.llm.models` is a weighted ensemble:
   **`sonnet` at weight 0.8 (breadth — sampled often, fast, many candidate mutations)** and **`opus` at
   weight 0.2 (depth — sampled occasionally, stronger, higher-quality moves)** — mirroring the paper's
   Flash/Pro split.
4. **The MAP-Elites + island engine is OpenEvolve's real one** (we use the package, not a re-implementation).
5. **Verified working:** both ensemble arms respond at runtime; the configuration is exactly
   sonnet@0.8 + opus@0.2; the evolution loop genuinely runs (a deliberately under-specified seed that the
   gate scores 0.5 evolves up to a 1.0 best); and the safety property holds (a ledger whose *claim text*
   contains a "poison" string is still merely scored, with no side effect). The full test suite stays
   green.

**Current honest limitation (verified in code, not estimated).** The fitness oracle `score_ledger`
returns `combined_score ∈ {0.0, 0.5, 1.0}` — the **bare soft-gate verdict** — plus `step_count` /
`justification_diversity` as the MAP-Elites axes; it carries **no goal-binding, no faithfulness, and no
numeric-obligation term**. Goal↔claim binding exists **only at the CLI's *final acceptance* gate**
(`prove.py`: an evolved ledger is used only "if it gates clean AND its claim+conclusion bind to the
goal"), **not inside the evolutionary loop / at archive insertion**, and `OpenEvolveBackend.decompose`
returns `children_from_sketch(sketch)` with no binding enforcement. So the search **optimises a gameable
scalar**: today the integration is sound *plumbing* and a safe *diversity proposer*, but its selection
pressure is not yet non-gameable. **Consequence (load-bearing):** hardening the fitness is a
**prerequisite**, not an enhancement — OpenEvolve must not be *relied upon* (its output committed into the
DAG, ranked, or reported) until goal-binding/vacuity and numeric-obligation checks gate the fitness *at
archive insertion*. Closing that gap is the substance of — and the required *ordering* of — the
recommendations.

---

## 9. The stacking recommendations to be judged

> These are the proposals you must critically evaluate. Each names which existing component is being
> composed with OpenEvolve, *how* the composition works, *why* it should help, and a rough value/effort.

### Top recommendation
**Use OpenEvolve as a verification-guided, cascade-gated diversity *proposer* inside the proof DAG, with
the *non-gameable* signals doing the actual selection** — never the soft gate alone. The three parts are
**strictly ordered** (do them in this order; do not skip ahead):
**(1, PREREQUISITE) harden the fitness *before* relying on any OpenEvolve output** — a **hard, fitness-zeroing**
goal↔claim-binding + vacuity gate at *archive insertion* (deterministic hash equality, not an LLM vote), plus
**numeric-grounding confirmation of discharged obligations** as the primary contentful reward;
**(2) only then** wire the evolutionary backend as a *fallback decomposer* that fires on hard/stuck nodes —
its output is committed to the DAG only after binding + the existing acyclicity/strict-simpler-child checks;
**(3) run the Lean Layer-4 audit only as the final cascade stage on surviving elites.** Treat
`audit.passed` as a **hard certification *predicate*, not a score component**: a candidate may rank highly
on soft/numeric fitness and still be **uncertified** — Layer 4 remains the *sole* authority for
`authoritative_elementary`, and `audit.passed` may additionally feed back as rare search *telemetry*.

> *Reframed after independent review + code verification:* part (1) is a safety prerequisite, not an
> enhancement; pairing **#1 below (DAG decomposition) must NOT be done first** — it only becomes safe once
> the fitness is hardened, because committing "best-under-the-soft-gate" blueprints amplifies the known
> game-the-gate failure mode (§3's `vierzahlensatz` example).

### Pairings

| # | Stack | Value / Effort | How it works | Why it helps |
|---|---|---|---|---|
| 1 | **OpenEvolve + LEAP DAG decomposition** | high / low — **BUT gated on #2/#3 first** | The evolutionary backend already implements the Decomposer interface. Fire it on *stuck* nodes only: MAP-Elites evolves a *population* of sketch ledgers; the best one's sub-lemma steps become the node's child goals. ⚠️ **Correction (verified):** goal-binding does **not** currently run in the bridge fitness/`decompose` path — only the acyclicity guard + reviewer + memoization do, and binding is enforced only at CLI acceptance. So this pairing is **not** "mostly wiring on top of unchanged guards": it requires #3's binding gate at archive insertion *first*, otherwise "best" means "best under the gameable soft gate." | The DAG's weakest link is *decomposition diversity*: one bad single-shot blueprint dead-ends a whole subtree. An island/MAP-Elites archive supplies structurally diverse blueprints — **but only valuable once they are diverse *among goal-bound, obligation-discharging* candidates**, not diverse among gate-gaming ones. Sequence it after #2/#3. |
| 2 | **OpenEvolve + numeric grounding (witness/construction search)** | **very high** / med–high | A *second* evolution mode that evolves the **numeric witness/construction specs themselves** — the descent measure/next-value expressions, the case-split modulus + residues, claimed solution tuples — scored by whether the exact-integer checker confirms the finite check. Feature axes: modulus size, box coverage. | The single **strongest substantive** stack: the exact checker is a *non-gameable*, contentful oracle, and non-elementary objects are unrepresentable in the integer-only AST (zero hallucination surface). **Caveat (its bound):** it rewards content *only* for goals expressible as **bounded-integer obligations** (witness search, residue covers, descent decrease); many elementary proofs hinge on a structural/logical crux with no numeric obligation, so this is a strong but **partial** signal — pair it with #3 (binding) and #4 (audit), never the sole reward. Effort is closer to *high* if obligation schemas must be inferred from prose. |
| 3 | **OpenEvolve + goal-binding (hard) + faithfulness lenses (soft routing)** | **essential** / med | Two *distinct-authority* gates folded into fitness **at archive insertion**: (a) a **deterministic, non-gameable** goal↔claim + terminal-conclusion **hash-binding** check that **zeroes** the fitness of any off-goal/vacuous candidate (this carries the authority — it is a hash equality, not a vote); (b) the **LLM** faithfulness lenses (back-translation/quantifiers/vacuity/strength) used **only as fail-closed routing/pre-filter signals**, never as a positive proof-quality *reward* (they are themselves gameable). | Closes the most dangerous failure mode: drifting `claim`/`conclusion` toward a weaker/vacuous/off-goal statement that still passes the *structural* gate (which does **not** bind the claim to the goal) and scores high while proving nothing. The deterministic binding must out-rank the panel because LLM judges share blind spots with the generators. |
| 4 | **OpenEvolve + Lean Layer-4 audit (cascade)** | high / med–high | Use OpenEvolve's native cascade: stage-1 = cheap deterministic gate (ms), stage-2 = deterministic goal-binding, stage-3 = full formalise→compile→dependency/axiom audit **only on the few elites that survive stages 1–2**. | Realises true *verification-guided evolution*. **`audit.passed` is a hard, terminal CERTIFICATION predicate — not a score component:** a candidate without a passing Layer-4 audit stays **uncertified** regardless of soft/numeric fitness; the audit may *additionally* feed back as rare search telemetry, but it can never *promote* a ledger to "elementary" via fitness. **Treat a *failed* formalisation as a diagnostic class (unfaithful / compile-fail / heavy-dependency / non-whitelisted-axiom / timeout / missing-lemma / formalizer-error), not a flat 0** — "tooling failed" ≠ "mathematically bad", so don't over-penalise an otherwise-good candidate. Effort is closer to *high* where formalisation is brittle. |
| 5 | **OpenEvolve + Elo/Bradley-Terry/PUCT ranking** | med / med | Export OpenEvolve's top-K elites per goal as candidates into the existing Elo/Bradley-Terry/PUCT tournament: OpenEvolve does coarse quality-diversity *proposal*; the LLM-judged pairwise tournament does fine-grained *selection* of which elite to actually expand. | The gate score can't judge subtler "promising-ness" (which of two passing blueprints is closer to a real elementary proof); the comparator can. Diversity (evolution) + discriminative selection (Elo) avoids the collapse you get from either alone. |
| 6 | **OpenEvolve + AutoReason tournament** | med / **low** | Pipeline: OpenEvolve **explores** (rough, diverse champions); AutoReason **exploits** (monotone, no-regression polish of the single champion). Champion-out of evolve → incumbent-in of refine. Keep them one-directional. | Evolution yields novel-but-rough/bloated ledgers; AutoReason's incumbent-wins-ties + margin gate is exactly the drift-free cleaner. Complementary on the explore/exploit axis; both already exist. |
| 7 | **Re-key MAP-Elites to proof *strategy* + retrieval-seed islands** | med / med | Change the MAP-Elites feature axes from the incidental `(step_count, justification_diversity)` to **method-type descriptors** (dominant-justification-class: descent vs casework vs gcd vs induction; modulus band; depth), so each cell holds the best ledger *using that strategy*. Seed each island from a *retrieved* elementary exemplar matching the goal, not a trivial default seed. | Without method-type axes the search collapses onto one strategy; strategy-keyed cells preserve a descent-based *and* a casework-based route side by side. Retrieval-seeded islands inject elementary priors and steer mutation away from heavy-library instincts. |

### Anti-patterns (things a proposal must NOT do)

- **Never evolve-and-execute code.** The integration is safe *only* because it evolves ledger *text* that
  the evaluator *reads*. Any evaluator that `exec()`s an evolved program — or that evolves numeric-witness
  specs and then `eval()`s them instead of routing through the restricted no-`eval` integer AST —
  reintroduces arbitrary-code-execution.
- **Never use the bare soft-gate verdict as the sole fitness.** It maps `NEEDS_REVIEW → 0.5` and does not
  bind the claim to the requested goal, so evolution will hill-climb toward vacuous / claim-weakened /
  off-goal ledgers. Always conjoin faithfulness/goal-binding and numeric-grounding signals; treat 0.5
  (`NEEDS_REVIEW`) as **not** a win.
- **Never treat an evolved high-fitness ledger as "elementary".** Only the Layer-4 audit certifies.
  Reporting an evolved ledger as elementary without that audit is the project's #1 category error.
- **Never put the Lean Layer-4 audit in the inner loop.** Compiling/auditing every raw mutation is
  cost-prohibitive; use the cascade so the audit only runs on elites that already passed the cheap stages.
- **Don't feed AutoReason's refined output back into the evolutionary archive.** AutoReason's
  no-regression guarantee depends on its monotone incumbent tournament; mixing it into a
  diversity-maximising explore loop can let a "better-sounding but non-elementary" synthesis propagate.
  Keep explore (evolve) and exploit (refine) as separate, one-directional stages.
- **Don't leave the MAP-Elites axes as incidental structural features.** `step_count` /
  `justification_diversity` give diversity over *surface structure*, not *strategy*, so the archive can
  still collapse onto one proof method.
- **Beware same-model judge monoculture.** If the mutation LLM, the faithfulness judge, and the ranking
  comparator are all the *same* model, they share blind spots and evolution learns to satisfy those shared
  biases (reward hacking). Keep the deterministic numeric/Layer-4 gates as the real selection pressure and
  use model judges only for soft ranking. *(Note: our new mutation ensemble already mixes two different
  model families — Sonnet + Opus — which partially mitigates this on the generation side.)*

### Prioritised roadmap & hardening (revised after independent review + code verification)

The pairings are **not** a flat menu — they are strictly ordered. *Parallelism/diversity make a correct
search faster; they do not make a gameable search safe.* The ordering:

- **P0 (prerequisite):** the **hard, fitness-zeroing goal-binding + vacuity gate at archive insertion**,
  and **"`NEEDS_REVIEW` (0.5) is not a win."** Until this exists, do not commit, rank, or report any
  evolved output.
- **P1:** the **numeric-grounding evolution mode** (#2) as the primary contentful reward.
- **P2:** OpenEvolve as the **fallback decomposer** (#1) on stuck nodes, committing only goal-bound,
  obligation-discharging blueprints; then **strategy-keyed MAP-Elites + retrieval-seeded islands** (#7).
- **P3:** **Elo/BT/PUCT ranking over already-hard-filtered elites** (#5); **AutoReason one-way polish**
  (#6); rank-aware model routing — all *discriminators of promise*, never *arbiters of correctness*.
- **Terminal/always-on:** **Layer-4 audit as the certificate boundary** (#4).

Three implementation refinements that sharpen the above:

- **Fitness is a vector, collapsed late — reconciled with OpenEvolve's scalar API.** Track structural
  validity, goal-binding/faithfulness, unresolved **"obligation debt"** (every asserted nontrivial lemma
  creates a typed obligation — case-cover / descent-decrease / divisibility / witness / formalisation —
  and fitness is penalised for unresolved debt even when the ledger is structurally valid), numeric
  obligations passed, subgoal decrease, and formalisation/audit status. Concretely against OpenEvolve:
  the **hard checks zero `combined_score`** (a gate), the **graded terms feed `combined_score`**, and the
  **strategy descriptors become the MAP-Elites feature axes** — so a vector reward fits the package's
  scalar-rank + multi-feature model without fighting it. Never let "passed structure but failed
  goal-binding" be comparable to "on-goal but incomplete."
- **Separate *search-fitness* from *reporting-status*.** Search may use graded scores; the user-facing
  status must stay **categorical** — `rejected` · `candidate/incomplete` · `soft-proven` (`PROVEN`) ·
  `formalised-but-not-elementary` · `authoritative_elementary` — so score never leaks into certification
  language.
- **Add adversarial reward-hacking regression tests.** Pin known traps (e.g. the §3 Ljunggren
  "relabel the hard theorem under an allowed method" ledger): assert that relabeling / claim-weakening /
  obligation-hiding does **not** raise fitness. This makes the "don't optimise a gameable signal"
  invariant executable, not just documented.

### Implementation status (built + adversarially audited)

The roadmap above is **implemented** (P0–P3 of this brief *and* the orchestration roadmap in
`forge_relevance_study.md` §7), each slice verified against a green offline suite, then put through an
**adversarial audit** (a different-model-family Codex GPT-5.5-xHigh refuter where quota allowed, plus
independent Opus skeptics) that re-attacked the soundness, reward-hacking, and safety surfaces. The audit
**found real defects** — that is the point of it — and successive remediation rounds closed them, with each
fix's regression test verified non-vacuous (it fails against the reverted code, on the *real* code path,
not an orphaned helper). Closed by audit: the hard fitness-zeroing goal-binding gate; the structural
**un-grounded-crux cap** (a candidate whose conclusion rests on an un-grounded crux is demoted to the
`NEEDS_REVIEW` band, provably below the PASSED floor) that bounds every *syntactic* relabel/launder/
`method_ref`/restatement reward-hack below PASSED; Elo ranking gated by the real `evaluate()`; the
`explore→exploit` one-way barrier on a canonical fingerprint; and the orchestrator soundness/completeness
fixes (suspending-scheduler, total node-FSM, split-keyed memo with stale-context eviction, budget-starved
retryability through the committed-child path, H⁰ child-consistency).

### Accepted known limitation (search-signal only — never a certificate)

One reward-hack is **accepted by decision**, not because it is harmless to ignore but because closing it
deterministically is **provably out of reach** of a soft gate. The deterministic fitness grounds a crux
*structurally* — a crux is "grounded" if it **depends on** a non-vacuous discharged typed obligation (or is
the required-and-cited provenance of one). That is a test of **dependency, not entailment.** A fake crux
whose claim is arbitrary (literally "2+2=5") but which simply lists a *trivially-true* complete cover
(`case_cover{mod 3, [0,1,2]}`) as a dependency is thereby declared grounded, so the cap does not fire and
the ledger can reach the PASSED band (~0.82). Distinguishing this from a *legitimate* use of that cover
requires deciding whether the obligation's content **entails** the crux's claim — i.e. proof-checking,
which is **undecidable offline.** This is the same reason the project does not trust any soft signal for
certification and relies on **Layer-4 Lean**.

Why this is acceptable: the soft fitness is a **search heuristic, not a certifier.** The spoof can mislead
the *search* (waste budget, surface a fake as a candidate) but it can **never mint a false
`authoritative_elementary`** — the only authoritative elementary verdict comes from the Layer-4 proof-term
**dependency-closure + `collectAxioms`** audit, which rejects a relabeled / non-entailing step because it
will not compile. Across the entire audit, **no round ever produced a false certificate.** The limitation is
documented in code (the `score_ledger` and cap-predicate docstrings) and pinned by a regression test
(`test_known_limitation_trivial_cover_citation_spoof_is_search_only`) so it is tracked, not silently
forgotten; if a future obligation kind carries a *gate-checked* provenance field, the grounding rule
extends to it (the gate must enforce the ancestor/type check, as it does for `split_coprimality`).

---

## 10. Decision context for your judgment (the constraints a good proposal must respect)

When you evaluate the above and propose your own, hold these invariants and facts:

1. **Soundness is the product.** A non-elementary or wrong "proof" that scores well is a *failure*, even
   if it's mathematically correct. Selection pressure that can be satisfied without real elementary
   content is worse than no search.
2. **Gameable vs non-gameable signals** (the §3 table). Cheap+gameable: Layers 0/1b/2 and LLM judges.
   Non-gameable: Layer 3 (exact integer checks) and Layer 4 (kernel dependency audit). A good proposal
   ties *final* selection to a non-gameable signal and uses gameable signals only for cheap pre-ranking.
3. **No arbitrary code execution.** The repo was just hardened to remove exactly this; any proposal that
   executes evolved/model-generated code (Python or otherwise) outside the restricted integer AST or a
   real sandbox is unacceptable.
4. **Cost asymmetry.** LLM mutation calls are seconds-to-minutes each; a Lean compile+audit is much more
   expensive. The AlphaEvolve "evaluation cascade" exists precisely to keep the expensive checks rare.
5. **Domain reality.** Many target problems have *no* elementary proof; "find any proof" ≠ "find an
   elementary, certifiable proof". Don't assume every goal is solvable within the elementary toolkit.
6. **Explore vs exploit.** Evolution/MAP-Elites is an *explore/diversity* engine. The system also has an
   *exploit/no-regression* refiner (AutoReason) and a *discriminative ranker* (Elo/BT/PUCT). A good
   composition assigns each method the role it's actually good at.
7. **What's cheap vs what's new work.** Some stacks are "mostly wiring" (the decomposer adapter already
   exists; the Elo/PUCT and AutoReason machinery already exist); others require new fitness terms or new
   evolution modes. Weigh value against effort honestly.

**What a strong proposal from you would include:** an assessment of whether each recommendation above is
sound and correctly prioritised; identification of any that are risky, redundant, or under-justified;
and your own composition(s) — ideally ones that (a) pin final selection to a non-gameable signal, (b)
respect the no-code-execution and cost-cascade constraints, (c) exploit the breadth/depth ensemble and
MAP-Elites diversity for what they're good at, and (d) are explicit about value vs implementation effort.
Where you can't verify a claim from this brief, flag the assumption rather than guessing.

---

## 11. Glossary (so this brief is self-contained)

- **ATP** — automated theorem proving.
- **Lean / Mathlib** — Lean is a formal proof assistant whose kernel mechanically checks proofs; Mathlib
  is its large mathematics library (and the source of "heavy machinery").
- **Elementary (number theory)** — provable with a restricted, IMO-style toolkit; *not* using algebraic
  number theory, class field theory, elliptic curves, analytic methods, etc.
- **Step-ledger** — the JSON proof format: justified steps with a dependency graph and one conclusion.
- **AND-OR DAG** — proof-plan graph: OR-node = a goal (direct *or* one decomposition); AND-node = a
  decomposition (all sub-lemmas required); shared sub-lemmas make it a DAG.
- **Memoization / goal-hash** — caching a proven sub-goal (keyed by a semantic hash of its statement) so
  it's proven once and reused.
- **Ralph loop** — per-goal retry loop that feeds gate findings back as "lessons-learned".
- **Elo / Bradley-Terry / PUCT** — Elo: online strength rating from pairwise results; Bradley-Terry:
  batch maximum-likelihood version of the same model; PUCT: a bandit rule (exploit strength + explore
  under-tried options) for choosing what to expand.
- **MAP-Elites** — a quality-diversity archive: a grid whose axes are chosen "feature descriptors"; each
  cell keeps the best candidate with those features, preserving diverse high performers.
- **Island model** — several semi-isolated sub-populations with occasional migration (preserves diversity).
- **Breadth/depth ensemble** — AlphaEvolve's use of a fast model (breadth, sampled often) + a strong model
  (depth, sampled rarely); here Sonnet (breadth) + Opus (depth).
- **Faithfulness panel** — adversarial check that a formalised Lean *statement* matches the English goal
  (lenses: back-translation, quantifiers/domain, vacuity, strength).
- **Numeric grounding** — exact-integer re-checking in a restricted no-`eval` AST (witness search,
  case-cover completeness, descent decrease).
- **Layer-4 audit / SafeVerify** — walks a compiled proof term's transitive dependency closure + axioms;
  rejects denylisted heavy dependencies or non-whitelisted axioms (and `sorry` holes). The only
  authoritative "elementary" gate.
- **`PROVEN` vs `authoritative_elementary`** — informal proof found (soft gate) vs formalised + compiled +
  dependency-audited + faithfulness-verified (the real certificate).
- **OpenEvolve** — the open-source implementation of AlphaEvolve that we depend on.

---

*End of brief. Everything needed to judge §9 and to propose alternatives is above; if a needed detail is
absent, treat it as unknown and state the assumption you make.*
