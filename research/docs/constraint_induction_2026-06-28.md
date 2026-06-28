# Inducing the "Elementary" Constraint in Automated Proofs: A Cross-Field Synthesis of Constraint-Induction Mechanisms

*Deep-research report — 2026-06-28*
*Scope: how five research streams (proof theory, AI/ML proof generation, control theory & cybernetics, operator theory & optimization, software/formal-methods systems thinking) induce constraints on generated objects, and what that implies for MathAgent's task of certifying that a proof is "elementary" without weakening logical soundness.*

---

## Executive summary

MathAgent must enforce a property — "the proof is *elementary*" — that is **orthogonal to correctness**. The Lean kernel can certify a proof is *valid*; it can never certify it is *elementary*. That single fact, which recurs verbatim across every stream surveyed here, is the organizing insight of this report: **class-membership always requires a gate separate from the correctness verifier**.

Across proof theory, machine learning, control theory, optimization, and software systems, the *same* small set of mechanisms is used to induce a constraint, under five different vocabularies. We organize them into a **seven-family taxonomy** (restrict-the-generator, filter/reject, project, penalize/barrier, supervise/gate, make-unrepresentable, feedback-regulate) and distill **five cross-field invariants** (P1–P5). The decisive one, P1, is that **a constraint is a membership predicate over a feasible set, and soundness lives in the *definition of that set and the correctness of the membership oracle* — never in the elegance of the enforcement operator** ([Boyd & Parikh, *Proximal Algorithms*](https://web.stanford.edu/~boyd/papers/pdf/prox_algs.pdf)). A flawlessly convergent projection onto a *wrongly specified* feasible set converges flawlessly to an unsound point — which is exactly MathAgent's documented `ℤ[i]` / Gaussian-integer denylist gap.

The recommended architecture for MathAgent is a **layered defense-in-depth** whose **authority is a hard ex-post filter on the verified proof term** (the Layer-4 axiom/dependency audit), **fronted by hard restrict-the-generator** (premise-pool restriction over a weak base), with **soft penalties confined to the unformalizable residue** (prose justification), and **every layer fail-closed**. This is not a single mechanism but a convergent consensus of all five streams.

This report is deliberately adversarial about its own conclusions. Two independent red-team passes surfaced material corrections that we preserve honestly in **Part 5**:

1. **The Lean Issue #8840 remediation is materially stale.** The issue is *closed*; `native_decide` was reworked in Lean 4.29.0 (2026-03) to emit one axiom per computation, and `#print axioms` no longer shows `Lean.trustCompiler`. The *advice* (ban `native_decide` in the elementary fragment) survives, but its *justification* must be re-grounded on **TCB expansion**, not axiom under-reporting.
2. **"Elementary" is non-canonical and undecidable to certify negatively.** By Gödel/Church–Turing, "φ has *no* elementary proof" is undecidable. The authoritative gate is therefore **sound-but-incomplete by theorem**, not by effort. The honest deliverable is a certificate of *"this derivation's footprint ⊆ an explicitly-stipulated theory T"* — **not** a certificate of "elementary" simpliciter.

Throughout, the maintainer's invariant is preserved: **relaxing "elementary" must never relax soundness** — and we add its dual, surfaced by the adversarial review: relaxing must also not *silently over-reject* genuinely-elementary proofs, because an over-rejecting gate trains the operator to disable it (fail-open by a social path).

---

# PART 1 — Inducing the "elementary" constraint in automated proofs

## 1a. Non-AI / proof-theoretic: what "elementary" means and how formal systems enforce it

### What "elementary" means — there is no canonical definition

The first uncomfortable fact, load-bearing for everything downstream, is that **"elementary" has no single rigorous, universally accepted definition** ([Wikipedia, *Prime number theorem*](https://en.wikipedia.org/wiki/Prime_number_theorem); [Goldfeld, *The Erdős–Selberg dispute*](https://www.math.columbia.edu/~goldfeld/ErdosSelbergDispute.pdf)). Historically, in number theory, "elementary" meant "using no complex analysis" — Hardy and others held a *depth hierarchy* (integers < reals < complex) in which the Prime Number Theorem was "deep" because every proof routed through the complex zeta function. The 1949 Erdős–Selberg elementary proof, built on Selberg's asymptotic formula, shattered that hierarchy. Crucially, "elementary" refers to the **techniques, not the difficulty**: the elementary PNT proof is in fact *more* technical than the analytic one.

The candidate *formalizations* of "elementary" are mutually inequivalent, and any choice is a **stipulation, not a discovered fact**:

- **"No complex analysis"** — the informal, method-relative number-theorist's sense.
- **First-order Peano Arithmetic (PA)** — a proof is elementary if carried out in PA.
- **IΔ₀ + exp** — Cornaros & Dimitracopoulos (1994) formalized Selberg's PNT proof in IΔ₀+exp (induction restricted to bounded formulas plus totality of exponentiation), a fragment *far below* full PA ([Springer, *The prime number theorem and fragments of PA*](https://link.springer.com/article/10.1007/BF01270626)).
- **The reverse-mathematics Big Five** — RCA₀ / WKL₀ / ACA₀ / ATR₀ / Π¹₁-CA₀ ([SEP, *Reverse mathematics*](https://plato.stanford.edu/entries/reverse-mathematics/); [Wikipedia, *Reverse mathematics*](https://en.wikipedia.org/wiki/Reverse_mathematics)).
- **Buss's feasible / poly-time S¹₂** — "elementary" = feasibly justifiable, via the witnessing theorem that the Σᵇ₁-definable functions of S¹₂ are exactly FP ([Wikipedia, *Bounded arithmetic*](https://en.wikipedia.org/wiki/Bounded_arithmetic)).

**Implication for MathAgent: the denylist *implicitly stipulates one of these*. It should be made an explicit, documented choice of target theory T, because the entire audit is a projection onto whatever feasible set T defines** (this is open question #1).

### Reverse mathematics: calibrating logical strength

Reverse mathematics inverts ordinary practice: instead of deriving a theorem from axioms, it finds the *weakest* axiom system that proves it, via two proofs — (1) forward (system S proves theorem τ) and (2) reversal (τ implies S over a weak base, usually RCA₀). The **Big Five**, by increasing strength, are RCA₀ (computable mathematics), WKL₀ (Weak König's Lemma / compactness), ACA₀ (conservative over PA), ATR₀ (predicative ceiling, ordinal Γ₀), and Π¹₁-CA₀ ([Wikipedia, *Reverse mathematics*](https://en.wikipedia.org/wiki/Reverse_mathematics); [Hirst, tour slides](https://www.appstate.edu/~hirstjl/snp/pdfslides/chrmtour.pdf)). The "Big Five phenomenon" — most ordinary theorems land in exactly one level — gives a precise gradient of "how elementary" a theorem is. (Caveat: Ramsey's theorem for pairs RT²₂ escapes the Big Five, sitting between WKL₀ and ACA₀.)

### Conservativity: the rigorous form of "safe to admit"

The single strongest match to the maintainer's principle is a **conservativity theorem**. **WKL₀ is Π⁰₂-conservative over PRA** (Friedman, model-theoretic; Sieg, constructive proof-transformation) ([SEP, *Hilbert's program*](https://plato.stanford.edu/entries/hilbert-program/)). Interpreted via Simpson, this is a *partial realization of finitistic reductionism*: a Σ⁰₁/Π⁰₂ theorem proved with the "ideal" machinery of WKL₀ can be **mechanically converted** to a finitistic PRA proof, so the ideal use was eliminable. This is exactly "relaxing the tool must not relax soundness" — admitting a stronger tool is licensed *precisely when* it proves no new low-complexity theorems. (**Caveat surfaced in Part 5:** conservativity is a meta-theorem about a *whole subsystem* and a *syntactic complexity class*, asserting a base-theory proof *exists* — it does not license *using* the strong lemma in the actual proof term, which still contains the non-elementary dependency.)

### Enforcement-by-audit: proof-assistant axiom auditing

The formal-methods layer reduces "proof uses only base T" to "the transitive axiom/dependency set ⊆ allowlist." In Lean 4, `#print axioms` invokes `Lean.collectAxioms` to walk the proof term's dependency graph; the three benign standard axioms are `propext`, `Classical.choice`, `Quot.sound`, and any user axiom or `sorryAx` flags the proof ([Lean reference, *Validating proofs*](https://lean-lang.org/doc/reference/latest/ValidatingProofs/)). Trust escalates through `lean4checker` (independent kernel replay) and cross-checking with the Rust `nanoda` kernel. Coq/Rocq's `Print Assumptions` is the analogue, with `coqchk` for independent re-validation ([Rocq manual](https://rocq-prover.org/doc/V8.18.0/refman/proof-engine/vernacular-commands.html)). ATP/SMT analogues restrict the delivered axiom set and read back the **unsat core** as the minimal dependency certificate ([Sorensson & Biere, mincore](https://groups.csail.mit.edu/sdg/pubs/2008/mincore-fm08.pdf)).

**The critical, originally load-bearing, now-stale gap:** Lean Issue #8840 was reported as showing `collectAxioms` under-reports — it did not walk axiom *type* signatures, and `native_decide` reported `Lean.ofReduceBool` while omitting `Lean.trustCompiler` ([Lean Issue #8840](https://github.com/leanprover/lean4/issues/8840)). **This is now fixed** (see Part 5): the issue is *closed* (PR #8842 made `collectAxioms` walk axiom types), and RFC #12216 / PR #12217 (Lean 4.29.0, 2026-03-27) reworked native computation so each emits one axiom per computation and `#print axioms` no longer shows `Lean.trustCompiler` ([Lean 4.29.0 release notes](https://lean-lang.org/doc/reference/latest/releases/v4.29.0/)). The *correct* residual concern is **TCB expansion**: `native_decide` pulls ~30k LOC of compiler plus every `@[implemented_by]`/`@[extern]` into the trusted base and is "almost certainly capable of proving `False`" via known `@[implemented_by]` exploits ([Niels Voss, *Lean pitfalls*](https://github.com/nielsvoss/lean-pitfalls)).

### Enforcement-by-construction

The strongest non-AI lever removes the *capability*: build over a weak base theory and whitelist the importable library (Lean `assert_not_imported` / `#import_path`, Mathlib `directoryDependency` linter; Coq dependency extraction), or deliver only a chosen axiom subset to a saturation prover, or pick a weak SMT fragment (QF_LIA, difference logic). This is stronger than post-hoc auditing because it removes the capability rather than detecting its use.

**Recurring honest caveat across all three layers:** axiom audits certify the *derivation*, never that the *statement* is the intended, non-vacuous claim. A correct, elementary proof of a *vacuously true* or *misstated* theorem passes every axiom audit ([Lean community, *Did you prove it?*](https://leanprover-community.github.io/did_you_prove_it.html)). Statement fidelity is a separate obligation.

## 1b. AI/ML methods for inducing method-class constraints

The dominant pattern across AI provers is **verifier/engine-as-floor**: the neural net only *proposes*; an authoritative symbolic component *disposes*.

- **AlphaProof** couples an LM with AlphaZero-style RL inside Lean; only kernel-accepted proofs count, and soundness is independent of the network ([Nature, *AI achieves silver-medal standard*](https://www.nature.com/articles/s41586-025-09833-y); [DeepMind blog](https://deepmind.google/blog/ai-solves-imo-problems-at-silver-medal-level/)). **Key finding: Lean checks correctness, not method class** — to constrain "elementary" you need an *additional* gate on the proof term.
- **AlphaGeometry / AlphaGeometry2** constrains the entire proof to a restricted DSL (construction + ~9 predicates) plus the deterministic DDAR deduction engine; the LM's only degree of freedom is auxiliary constructions ([arXiv 2502.03544](https://arxiv.org/html/2502.03544v3)). This is *restrict-the-generator* in its hard form — and its cost is **coverage**: the DSL provably cannot express inequalities, variable numbers of points, or non-linear equations, leaving ~12% of IMO geometry uncovered even after the 66%→88% expansion.
- **LeanDojo / ReProver** restricts retrieval to *accessible premises*, a corpus-level bound on the lemma/method space ([arXiv 2306.15626](https://arxiv.org/abs/2306.15626)). Soft: it biases but does not strictly forbid.
- **Grammar-constrained decoding** (GBNF, Outlines, XGrammar, llguidance) masks logits so only grammar-legal tokens survive ([arXiv 2502.05111](https://arxiv.org/pdf/2502.05111)). **It enforces only *syntax*; "elementary" is a *semantic* property of the proof term, generally not a CFG property** — masking alone cannot certify elementarity.
- **Type-constrained generation** (prefix automata, inhabitable-type search) pushes decode-time constraints toward semantics ([arXiv 2504.09246](https://arxiv.org/pdf/2504.09246)), but still "can only exclude" and cannot teach the type system.
- **Soft steering** — Process Reward Models / step-level verification ([OpenAI, *Let's Verify Step by Step*](https://cdn.openai.com/improving-mathematical-reasoning-with-process-supervision/Lets_Verify_Step_by_Step.pdf)), Constitutional AI / RLAIF ([Anthropic](https://www.anthropic.com/research/constitutional-ai-harmlessness-from-ai-feedback)), and neuro-symbolic semantic loss ([Xu & Van den Broeck](https://ar5iv.labs.arxiv.org/html/1711.11157)) — biases toward a class but gives **no hard guarantee and is reward-hackable**.
- **Authoritative proof-term audit** — inspect the verified proof's transitive axiom/dependency set and reject if it uses disallowed lemmas; this is the formal analogue of reverse mathematics (axiom footprint ⊆ allowed subsystem) and the **only place that gives a hard, non-hackable certificate of method class** ([SEP, *Reverse mathematics*](https://plato.stanford.edu/entries/reverse-mathematics/)). Its soundness depends on **denylist completeness** — the documented `ℤ[i]` gap.

**The sharpest cross-stream finding of 1b:** correctness-verification and method-class membership are **orthogonal**. The three places to impose a class constraint, in increasing hardness: (a) decode-time syntactic masking (cheap, hard, but elementarity ≠ CFG property); (b) corpus/premise restriction (make non-elementary lemmas unreachable); (c) post-hoc authoritative proof-term audit (the only hard certificate). Soft signals (PRM, Constitutional) are for the unformalizable stylistic residue only, never the authority.

---

# PART 2 — Inducing constraints in systems generally

## 2a. Control theory & cybernetics

**Supervisory Control Theory (Ramadge–Wonham).** The plant is a discrete-event system over an alphabet split into **controllable** Σ_c (a supervisor may disable) and **uncontrollable** Σ_u (never disabled). A specification is a sublanguage K; the central condition is **controllability** (K̄·Σ_u ∩ L(G) ⊆ K̄). When K is not controllable, one synthesizes the **supremal controllable sublanguage** sup C(K) — the unique largest controllable sublanguage, yielding the **minimally restrictive (maximally permissive)** safe nonblocking supervisor ([Wikipedia, *Supervisory control theory*](https://en.wikipedia.org/wiki/Supervisory_control_theory); [Wonham–Ramadge, *On the supremal controllable sublanguage*](https://arxiv.org/pdf/1306.2422)). The controllable/uncontrollable split is the formal model of *"constraints I may impose vs. invariants I must never disable."*

**Constrained MPC.** Receding-horizon optimization with hard state/input constraints and a terminal control-invariant set guarantees **recursive feasibility + stability** via the Mayne–Rawlings terminal ingredients ([Mayne et al., *Constrained MPC: Stability and Optimality*](https://www.researchgate.net/publication/220159558_Constrained_Model_Predictive_Control_Stability_and_Optimality)).

**Control Barrier Functions.** A CBF-QP safety filter solves `min ||u − u_nom||²` s.t. `L_f h + L_g h·u ≥ −α(h(x))` each instant, rendering the safe set forward-invariant via Nagumo / the comparison lemma — a **minimally-invasive correction** that wraps an unverified/learned controller without modifying it ([Ames et al., arXiv 1609.06408](https://arxiv.org/abs/1609.06408)). **Caveat (Part 5):** the guarantee is *oracle-conditional* — a false-data-injection attack biasing the state estimate can stealthily deactivate the filter ([Tan et al., arXiv 2403.17861](https://arxiv.org/abs/2403.17861)).

**Reference / command governors** add constraint enforcement *in front of* an unchanged, already-certified inner loop, via the finitely-determined maximal output admissible set O_∞ ([Gilbert–Tan; survey](https://www.sciencedirect.com/science/article/abs/pii/S0005109816303715)). This is the precise justification for **layering an elementarity gate on top of an unchanged soundness verifier**.

**Set invariance & viability.** CBF safe sets, MPC terminal sets, governor admissible sets, and Aubin's viability kernels are the *same object* — the maximal control-invariant subset of the constraint set — with Nagumo's tangent-cone condition the common boundary test ([Blanchini, *Set invariance in control*](https://dl.acm.org/doi/10.1016/S0005-1098%2899%2900113-2); [Wikipedia, *Viability theory*](https://en.wikipedia.org/wiki/Viability_theory)). SCT's sup C(K) is the discrete-event twin.

**Cybernetic meta-layer.** **Ashby's Law of Requisite Variety** (V(O) ≥ V(D) − V(R): "only variety absorbs variety") bounds *any* regulator: a shallow keyword scanner has less variety than the adaptive space of non-elementary shortcuts an LLM invents, so leakage is **information-theoretically expected** ([Wikipedia, *Variety (cybernetics)*](https://en.wikipedia.org/wiki/Variety_(cybernetics))). The **Conant–Ashby Good Regulator Theorem** ("every good regulator must be a model of the system") is invoked for the model-grounded audit — but is **contested** (see Part 5): the formal result is a deterministic state→action *mapping* (a policy), and Baez/Wentworth document that the proof does not support the title ([Wikipedia, *Good regulator*](https://en.wikipedia.org/wiki/Good_regulator)).

## 2b. Operator theory & optimization

The unifying identity: the **proximal operator** prox_{λf} = resolvent (I+λ∂f)⁻¹, and for f = indicator ι_C, prox = **projection** P_C = resolvent of the normal cone N_C ([Boyd & Parikh](https://web.stanford.edu/~boyd/papers/pdf/prox_algs.pdf)). So "hard constraint" (indicator → exact projection) and "soft constraint" (penalty/regularizer → shrinkage) are the *same operation* at two ends of a spectrum.

- **Projection P_C** is the atomic "snap onto the feasible set"; firmly nonexpansive, unique for convex C ([Wikipedia, *POCS*](https://en.wikipedia.org/wiki/Projections_onto_convex_sets)).
- **POCS / alternating projection** finds a point in C ∩ D; **Dykstra** upgrades it to the *nearest* feasible point ([Wikipedia, *Dykstra's algorithm*](https://en.wikipedia.org/wiki/Dykstra%27s_projection_algorithm)). **Caveat (Part 5):** when the (wrongly-specified) sets are *disjoint*, POCS oscillates in order-dependent limit cycles rather than converging cleanly.
- **Projected/proximal gradient, Douglas–Rachford, ADMM** all inherit convergence from **monotone-operator theory** (firmly-nonexpansive resolvents → averaged → Krasnoselskii–Mann) ([Combettes notes](https://pcombet.math.ncsu.edu/optim1.pdf)). ADMM = Douglas–Rachford on the dual; multi-block (N≥3) ADMM can *diverge* ([arXiv 1408.4266](https://arxiv.org/pdf/1408.4266)).
- **Penalty / augmented-Lagrangian** prices violation; a finite penalty gives only approximate feasibility and **can be "paid off"** by a strong objective — the exact failure mode the maintainer guards against ([Fiveable, *Penalty methods*](https://fiveable.me/mathematical-methods-for-optimization/unit-14)).
- **Log-barrier / interior-point** is the *hard* member: **every iterate stays strictly feasible** ([Nemirovski, *Interior-point lectures*](https://www2.isye.gatech.edu/~nemirovs/Lect_IPM.pdf)).
- **Banach fixed-point** with a forward-invariant set T(S) ⊆ S guarantees the limit stays in S ([Wikipedia, *Banach fixed-point theorem*](https://en.wikipedia.org/wiki/Banach_fixed-point_theorem)).

**The decisive corollary (P1):** all these operators enforce the constraint *as encoded*. A firmly-nonexpansive, provably-convergent projection onto a *wrongly-specified* C converges beautifully to an unsound point. **Soundness lives in the definition of the feasible set and the correctness of the membership oracle, not in the elegance of the enforcement operator.** (**Caveat (Part 5):** metric-projection theory assumes a convex, closed, metric feasible set; "elementary proofs" is a *discrete, non-metric, undecidable* class, so the projection metaphor is an intuition pump, not a transferable theorem.)

## 2c. Systems thinking & software/formal-methods guardrails

- **Constraint propagation / arc consistency (AC-3)** prunes domains to a fixpoint; **local consistency is necessary but not sufficient** for a global solution, and a missing/weak constraint silently admits bad solutions ([Wikipedia, *AC-3*](https://en.wikipedia.org/wiki/AC-3_algorithm)).
- **Types as constraints** — refinement types (Liquid Haskell, F*) discharge verification conditions via SMT ([Vazou et al.](https://dl.acm.org/doi/10.1145/2628136.2628161)); an incomplete refinement predicate admits what you meant to forbid.
- **Make illegal states unrepresentable / parse-don't-validate / Design by Contract** push enforcement to construction time, eliminating whole error classes at zero runtime cost ([King, *Parse, don't validate*](https://lexi-lambda.github.io/blog/2019/11/05/parse-don-t-validate/); [Wikipedia, *Design by contract*](https://en.wikipedia.org/wiki/Design_by_contract)).
- **Runtime verification** (LTL3 monitors) *detects*; **runtime enforcement** (Schneider truncation = safety only; Ligatti–Bauer–Walker suppression/insertion/**edit** automata; shielded RL) *prevents* ([Bauer–Leucker–Schallhart, RV](https://cs.uwaterloo.ca/~bbonakda/teaching/CS745/papers/RV.pdf); [Ligatti et al., edit automata](http://users.ece.cmu.edu/~lbauer/papers/2009/tissec09-editauto.pdf)). Correctness = **soundness** (all output satisfies policy) + **transparency** (valid inputs pass unchanged) — exactly the maintainer's "reject the bad, don't over-reject the good." **A monitor that fails open on uncertainty gives no guarantee.**
- **Policy-as-code (OPA/Rego, default-deny), object capabilities (POLA), sandboxing (Wasm/seccomp)** decouple decide-then-enforce; the enforcement point must actually obey, and **fail-open voids the guarantee** ([OPA docs](https://www.openpolicyagent.org/docs/policy-language); [Wikipedia, *Capability-based security*](https://en.wikipedia.org/wiki/Capability-based_security)).
- **Systems thinking** sits one level up: Meadows ranks "rules (constraints)" as a high-leverage point but subordinate to goals; Goldratt's Theory of Constraints finds the one binding constraint and subordinates the rest; Beer's VSM matches control variety to the controlled space ([Meadows, *Leverage points*](https://donellameadows.org/archives/leverage-points-places-to-intervene-in-a-system/)).

---

# PART 3 — Unified synthesis

## The cross-field taxonomy of constraint-induction mechanisms

Each family is defined by **where** in the lifecycle it acts (ex-ante = before/during generation vs. ex-post = after), **what** it acts on (the generator, the candidate, the feasible set, the cost), and its **guarantee type** (hard/sound-by-construction vs. soft/biasing).

| # | Family | Lifecycle | Acts on | Guarantee | Representative mechanisms |
|---|--------|-----------|---------|-----------|---------------------------|
| 1 | **Restrict-the-generator** | ex-ante | generator | hard or soft | grammar-constrained decoding (GBNF/Outlines/XGrammar); type-constrained generation; restricted DSL (AlphaGeometry); premise/corpus restriction (LeanDojo, hammer pool, HTPS vocabulary); capability confinement (Wasm/seccomp, POLA); building over a weak base / whitelisted library (Lean `assert_not_imported`, Coq extraction) |
| 2 | **Filter / reject** | ex-post | candidate | hard (if oracle complete) | verifier-as-floor (Lean kernel, DDAR); proof-term axiom audits (`#print axioms`/`collectAxioms`, Coq `Print Assumptions`, unsat-core); reverse-math footprint; refinement-type/SMT VCs; metric projection's membership oracle |
| 3 | **Project** | ex-post | candidate → feasible set | hard (convex) / local (nonconvex) | P_C, POCS, Dykstra, projected/proximal gradient, Douglas–Rachford, ADMM, manifold retractions; runtime suppress/edit automata |
| 4 | **Penalize / barrier** | soft (or invariant) | cost | soft (penalty) / hard (barrier) | exterior penalty / augmented Lagrangian; log-barrier/interior-point; reward shaping / PRM; Constitutional-AI/RLAIF; semantic loss; soft prose scanner |
| 5 | **Supervise / gate** | ex-ante (admission) | generator's moves | hard, minimally restrictive | Ramadge–Wonham supervisory control (sup C(K)); policy-as-code OPA decide-then-enforce; shielded RL; truncation automata |
| 6 | **Make-unrepresentable** | construction-time | the type/space | strongest (oracle = checker) | make illegal states unrepresentable / parse-don't-validate / dependent & refinement types as domain types; Design-by-Contract invariants statically verified; Banach forward-invariant set |
| 7 | **Feedback-regulate / safety-filter** | runtime (for-all-time) | the trajectory | hard given accurate model | CBF-QP safety filters; MPC terminal control-invariant set; reference/command governors; CLF+CBF; viability kernels; per-node Lean invariant maintained step-by-step |

**Orthogonal axes (the synthesis lattice):** (a) **ex-ante** (1,5,6,7) vs. **ex-post** (2,3,4); (b) **hard/sound-by-construction** (grammar, kernel, RW, types, barrier+CBF) vs. **soft/biasing** (penalty/PRM/Constitutional, prose scanner) — *with the Part 5 correction that only the kernel and make-unrepresentable types are **unconditionally** hard; CBF, MPC, RW-supervisor, and projection are **oracle-conditional***; (c) **prevent** (1,5,6,7) vs. **detect** (2) vs. **detect-and-correct** (3); (d) **syntactic scope** (grammar masking) vs. **semantic scope** (type / axiom-footprint / refinement).

The cybernetic **meta-layer** (Ashby's requisite variety; Conant–Ashby, *qualified per Part 5*) sits above all as impossibility/structure bounds.

## Five unified principles (cross-field invariants)

**P1 — A constraint is a membership predicate over a feasible set; enforcement is projection / filtering / restriction onto that set.** Literal in optimization (constraint = indicator ι_C; projection = prox of ι_C = resolvent of the normal cone), isomorphic everywhere else (SCT spec = sublanguage K; elementary class = "axiom footprint ⊆ allowed subsystem"; CSP/refinement predicate carves the admissible set). **The decisive corollary: soundness lives in the definition of the feasible set and the correctness of the membership oracle, not in the elegance of the enforcement operator.** A provably-convergent projection onto a wrongly-specified C converges to an unsound point — MathAgent's `ℤ[i]` denylist gap.

**P2 — The recurring desiderata: minimally-restrictive + nonblocking + fail-closed + sound-over-complete.** "Minimally restrictive" is one idea under five names: SCT's supremal controllable sublanguage, the CBF-QP's least-norm correction, the reference governor's largest admissible step, the viability kernel, and Ligatti's soundness+transparency pair. This *is* the maintainer's "relaxing elementary must not relax soundness": reject precisely the non-elementary proofs and no correct elementary one. **Fail-closed** (OPA default-deny) is the dual of the cardinal sin **fail-open** — identical to MathAgent's deep-audit finding that faithfulness failing open broke the core soundness promise.

**P3 — Prevent > detect-and-correct > detect > bias (a strict hardness ordering; ex-ante beats ex-post).** Construction-time impossibility is strongest; runtime enforcement next; passive auditing only detects; soft penalty only biases and is reward-hackable. **And correctness vs. class-membership are orthogonal** — a verifier guarantees VALID, never ELEMENTARY; class-membership always needs a separate gate (the single sharpest cross-stream finding).

**P4 — Everything that must hold for-all-time reduces to (control-)invariance, and the layered/add-on architecture adds a constraint without disturbing a proven core.** CBF safe sets, MPC terminal sets, governor admissible sets, viability kernels, and sup C(K) are the *same object* — the maximal subset closed under uncontrollable dynamics, with Nagumo's tangent-cone condition the common boundary test; Banach's T(S)⊆S is its fixed-point form. The reference governor and CBF safety filter enforce constraints *without modifying an already-trusted inner controller* — the precise justification for layering an elementarity gate on top of an unchanged soundness verifier.

**P5 — The enforcer must match the variety of, and model, what it regulates.** Ashby: only variety absorbs variety — a partial denylist has less variety than the adaptive space of non-elementary shortcuts, so leakage is *expected, not a one-time bug*. (Conant–Ashby's "must be a model" is invoked as motivation but **demoted to a motivating analogy** per Part 5; the architectural conclusion stands on independent soundness/completeness grounds.) This same incompleteness — "a missing constraint is faithfully enforced and silently admits exactly what it omits" — is the shared failure mode of CSP arc-consistency, refinement predicates, denylists, and policy rules.

## The trade-off table

| Family | Strength | Price / failure mode |
|--------|----------|----------------------|
| 1 Restrict-generator | Removes capability, not just detects use; shrinks attack surface | Only enforces what's expressible in the generator's language; a CFG/type system cannot capture a *semantic* property like "elementary"; **coverage cost** (AlphaGeometry's DSL covers a subset) |
| 2 Filter/reject | Authoritative, non-hackable certificate of the checked property | Only as sound as the membership predicate is **complete** (the `ℤ[i]` under-inclusive-set failure); detects, does not prevent |
| 3 Project | Yields a feasible (or nearest) output, not just a verdict | Convex/monotone gives convergence; **nonconvex → only local**; disjoint wrong sets → oscillation; faithfully projects onto whatever C is encoded |
| 4 Penalize/barrier | Very general (no projection oracle); ideal for **fuzzy** constraints | Finite penalty → only approximate feasibility; **can be "paid off"**; never an authority for a hard-failure property |
| 5 Supervise/gate | Provably exact and minimally-restrictive; clean model of impose-vs-never-disable | Needs a finite/regular model; **state-explosion**; the decide/enforce split is a risk (missing enforcement point or fail-open supervisor voids the guarantee) |
| 6 Make-unrepresentable | Eliminates whole error classes at zero runtime cost; oracle = the checker | Requires expressive-enough types (arbitrary predicates need dependent types); can still mis-model the domain |
| 7 Feedback-regulate | Maintains invariant for-all-time; wraps an unverified core without modifying it | Needs an **accurate model** and a valid invariant; **oracle-conditional** (CBF stealth-deactivation); reduces to the maximal control-invariant subset |

---

# PART 4 — Application to MathAgent

## Mapping each component to a mechanism (verified against the codebase)

The following maps MathAgent's actual components (confirmed by inspecting `agent/gates/`, `agent/orchestrator/`, `agent/tools/`, and `agent/gates/denylist.yaml`) onto the taxonomy.

- **`agent/gates/lean_audit.py` — Layer-4 Lean proof-term audit** → **FILTER/REJECT (family #2)**, the hard membership predicate for C = {elementary proofs}. Exactly the metric-projection membership oracle of 2b and the `#print axioms`/`collectAxioms` / unsat-core / reverse-math-footprint pattern of 1a/1b. Two sub-checks: (i) axiom integrity (used axioms ⊆ {`propext`, `Classical.choice`, `Quot.sound`}, catching `sorryAx`); (ii) content denylist over the transitive constant closure with a *dominating-allowlist* exemption that **fails closed**. **The only mechanism giving a hard class-certificate; the system's authoritative gate.**

- **`agent/gates/denylist.yaml` — `lean_denylist_decls` + `prose_terms`** → **SUPERVISOR SPECIFICATION (family #5)**. `lean_denylist_decls` (e.g. `Mathlib.RingTheory.ClassGroup`, `IsDedekindDomain`, `EllipticCurve`, plus the live-audit additions `GaussianInt`/`Zsqrtd`, `UniqueFactorizationMonoid`/`IsPrincipalIdealRing`, `FractionalIdeal`/`Localization`, `Ideal.Quotient` — patched 2026-06-28 against ADMITTED shortcuts) is the forbidden-string sublanguage the Layer-4 supervisor enforces. Those additions are precisely the **requisite-variety / good-regulator expansion** P5 demands. `lean_infrastructure_allowlist` + `lean_elementary_by_fiat` are the minimally-restrictive carve-outs (P2).

- **`agent/tools/formalizer.py` `_RULES`** (prefer core/Std and elementary Mathlib; reduce to ZMod m and decide; omega for linear; interval_cases for finite; descent...) → **RESTRICT-THE-GENERATOR (family #1) in its SOFT, prompt-level form**. It is 1b's premise/DSL restriction delivered as natural-language principles (closer to Constitutional-AI steering than to GBNF masking) — it biases toward the elementary toolkit but does **not** make non-elementary tactics unrepresentable.

- **`agent/orchestrator/elementary_verifier.py` `refute_elementary` + `dag_driver.py` `_verify_or_downgrade`** → **FILTER/REJECT as an adversarial, downgrade-don't-discard MONITOR (family #2)**. Embodies P2's fail-closed via its anti-vacuity tripwire (`VacuousVerificationError` — "a check that proves nothing must not be mistaken for a check that proved the proof safe"). Deterministic, prover-independent (never execs/evals model output).

- **Per-node / sketch Lean (`node_verifier`, `sketch_verifier`; LEAP P0–P4)** → **FEEDBACK-REGULATE / INVARIANT MAINTENANCE (family #7)** — maintains the elementary+valid invariant at each leaf/AND-node, the proof-search analogue of step-by-step forward-invariance (CBF/MPC). The `lean_strict` flag is the **fail-open↔fail-closed switch** on the "could-not-formalize" arm (default fail-open is the documented residual soundness hole; `lean_strict=True` routes to FAILED_GAP).

- **`agent/gates/scanner.py` + denylist `prose_terms`** → **PENALIZE / soft router (family #4)** — explicitly labeled in `denylist.yaml` as "a SOFT ROUTER TO REVIEW, not a deterministic gate (a euphemism scan cannot be sound)." Correctly never the authority — matches P3/P5.

- **`agent/orchestrator/supervisor.py` `validate_profile`** → **textbook RAMADGE–WONHAM SUPERVISE/GATE (family #5)** for the *configuration* plant — minimally restrictive, fail-closed; guards S1–S8 are the controllability conditions. `elementarity_policy.policy_for` is the policy-as-code lookup table (OPA-style decide), fail-closed on its unreachable branch.

- **`ElementarityLevel` `none`/`soft`/`authoritative` (`run_profile.py`)** → the **hard↔soft dial** of P2/P3. Confirmed in code: `none` = no constraint (no Lean gates wired); `soft` = enforce in-engine but downgrade a FAILED_ELEMENTARY to soft PROVEN (penalty, family #4); `authoritative` = terminal hard filter (#2) + optional per-node invariant (#7).

**Orthogonality of correctness vs. class (P3) is structurally honored:** the Lean kernel decides VALID; the denylist audit decides ELEMENTARY; separate gates. The documented residual gaps are precisely P1/P5 failures: (a) the terminal/per-node gate fails open on non-compiling formalization unless `lean_strict`; (b) the denylist is necessarily incomplete; (c) Lean Issue #8840 *was* a concern but is now closed (Part 5).

## The concrete recommended approach for the elementary constraint

Induce "elementary" with a **layered defense-in-depth** whose **authority is a hard ex-post filter on the verified proof term**, **fronted by hard restrict-the-generator**, with **soft penalties confined to the unformalizable residue** — **every layer fail-closed**. In priority order:

1. **Keep the Lean kernel as the correctness floor and the Layer-4 proof-term axiom/dependency audit as the *sole authoritative elementarity gate*.** This verifier-as-floor pattern is the single most consistent finding across all streams (AlphaProof, DDAR, the projection membership oracle, the good-regulator analogy). Correctness and class-membership are orthogonal (P3), so the audit *must* be a gate separate from the kernel — which the codebase already does.

2. **Harden the audit's membership oracle — completeness is where soundness actually lives (P1, P5).** (a) **Forbid `native_decide`/`native_compile` in the elementary fragment outright** — *re-grounded on TCB expansion per Part 5*, not on the now-fixed #8840 under-reporting; denylist the per-computation native axioms / `ofReduceBool` by namespace. (b) Continue **adversarially expanding** the denylist/allowlist against the shortcuts LLMs actually reach for (the `ℤ[i]`→UFD→FractionalIdeal→Ideal.Quotient progression already in `denylist.yaml` is the right discipline); Ashby's law guarantees this is never "done," so institutionalize it as a **standing adversarial probe suite**, and cross-check with an **independent kernel** (lean4checker / nanoda / coqchk) — *noting (Part 5) that an external re-checker defends the **correctness** floor, not the elementarity gate*.

3. **Add hard restrict-the-generator up front** to make non-elementary moves *less reachable* (P3 prevent>detect). Upgrade `_RULES` (currently soft prompt discipline) by restricting the formalizer's **premise pool / importable library** to a whitelist over a weak base (LeanDojo-style restriction, Mathlib `directoryDependency` linter). *Reframed per Part 5 as **attack-surface reduction, never completeness*** — Mathlib's dense dependency graph means an allowlisted "elementary" lemma can transitively rest on the very machinery you excluded, so the proof-term footprint audit (step 1) remains the true authority.

4. **Make every layer fail-closed.** Default `lean_strict=True` (or remove the fail-open arm) so a non-compiling formalization is *not* silently accepted as soft-PROVEN — the fail-open arm is the exact "monitor fails open ⇒ no guarantee" / "faithfulness fails open ⇒ broken soundness" failure mode. Keep the `elementary_verifier` anti-vacuity tripwire and the supervisor's fail-closed default.

5. **Keep soft signals (prose scanner, any PRM/Constitutional steering) strictly subordinate** — for the fuzzy residue only (justification prose), never an authority, never able to "pay off" a violation (P2). The codebase already labels the prose scanner a "soft router to review"; preserve that boundary.

6. **Retain per-node/sketch Lean as invariant-maintenance** (the CBF/MPC layered add-on, P4) so elementarity is enforced compositionally onto an unchanged soundness core — but recognize it is defense-in-depth, not the authority. *Make the composition rule explicit (Part 5): the elementarity footprint of a composed proof is the **union** of children's footprints plus the glue lemma's footprint, so closure holds iff the glue lemma is itself elementary.*

**Why this combination and not a single mechanism:** no single layer is both sound and complete. Restrict-the-generator is hard but cannot express the semantic property and trades coverage. A pure filter is authoritative but only as complete as its oracle. Soft penalties are reward-hackable. The cross-field consensus converges on the *same* architecture: hard ex-ante restriction to shrink the attack surface, a hard ex-post model-grounded audit as the authority, soft signals only for what cannot be formalized, every layer fail-closed, and the audit's oracle adversarially hardened forever.

**Crucial caveat spanning every stream:** an elementarity certificate is necessary but **not sufficient** — it certifies the *derivation*, never that the *statement* is the intended, non-vacuous claim ("2+2=5" / false-hypothesis vacuity). Statement-fidelity (the faithfulness gate) remains a separate obligation that must *also* fail closed.

---

# PART 5 — Adversarial caveats, failure modes & open questions

This section integrates two independent red-team passes (Adversarial A: staleness/over-claim audit; Adversarial B: gaming/over-restriction audit) and the citation-verification results. It is deliberately unflattering to Parts 1–4.

## Citation-verification results

**Verified as real and accurately characterized (~90% of named methods):** reverse-math conservativity (Friedman 1976 model-theoretic; Sieg 1985 finitary transformation; WKL₀ Π⁰₂-conservative over PRA); Cornaros–Dimitracopoulos elementary PNT in IΔ₀+exp (*Arch. Math. Logic* 33(4):265–281, 1994); Ramadge–Wonham supremal controllable sublanguage (*SIAM J. Control Optim.* 1987); CBF-QP / Nagumo forward-invariance / least-norm correction (Ames et al. 2017/2019); Ashby's Law of Requisite Variety (1956); AlphaGeometry DSL + DDAR (DeepMind/Nature 2024; arXiv 2502.03544); AlphaProof (Nature s41586-025-09833-y); Erdős–Selberg elementary PNT and "no consensus on what counts as elementary"; POCS, Dykstra, Douglas–Rachford, ADMM, log-barrier, augmented Lagrangian, viability kernel, reference governor, Ligatti edit/suppress automata, Schneider truncation automata. **No fabricated source or non-existent method was found.**

**Materially stale / mischaracterized:**

- **(A) Lean Issue #8840 — the single most consequential factual error in the original synthesis.** It was presented as an *open*, load-bearing gap requiring the auditor to "walk axiom types to catch `trustCompiler`." In fact the issue is **CLOSED** (fixed by [PR #8842](https://github.com/leanprover/lean4/issues/8840), which made `collectAxioms` walk axiom type signatures), and **RFC #12216 / PR #12217 (Lean 4.29.0, 2026-03-27)** reworked native computation so each emits **one axiom per computation** and "`#print axioms` will no longer show `Lean.trustCompiler`" ([Lean 4.29.0 release notes](https://lean-lang.org/doc/reference/latest/releases/v4.29.0/)). The actionable advice (ban `native_decide`) is right; the *justification* is obsolete. The correct justification is **TCB expansion**: `native_decide` pulls ~30k LOC of compiler plus every `@[implemented_by]`/`@[extern]` into the trusted base and "is almost certainly capable of proving `False`" ([Niels Voss, *Lean pitfalls*](https://github.com/nielsvoss/lean-pitfalls)). **Remediation:** denylist the per-computation native axioms / `ofReduceBool` by namespace — which a stale "walk-the-types" fix would not even target.

- **(B) Conant–Ashby "good regulator must be a model" is CONTESTED, not settled.** The 1970 theorem's formal content (single-step, finite-state, entropy-minimization) is that the simplest optimal regulator is a deterministic *mapping* h: S→R — i.e. a **policy, not a world-model**. Baez and Wentworth document that the proof does not support the title ([Wikipedia, *Good regulator theorem*](https://en.wikipedia.org/wiki/Good_regulator_theorem); [Baez](https://johncarlosbaez.wordpress.com/2016/01/27/the-good-regulator-theorem/); [Wentworth, *Fixing the Good Regulator Theorem*](https://www.lesswrong.com/posts/Dx9LoqsEh3gHNJMDk/fixing-the-good-regulator-theorem)). **Demoted** here to a motivating analogy; the architectural conclusion (prefer a proof-term audit over a prose scanner) stands on independent soundness/completeness and requisite-variety grounds.

- **(Minor) DDAR** is AlphaGeometry's primary *sound deductive engine*; calling it merely "verifier-as-floor" slightly understates it.

## The hardest critiques

**Critique 1 — "Elementary" is non-canonical *and* undecidable to certify negatively, so the authoritative gate is sound-but-incomplete *by theorem*.** The proof-term audit can decide "this proof's footprint ⊆ T" (decidable) but can **never** decide "φ has *no* elementary proof" (provability-in-T is r.e., non-recursive — Gödel/Church–Turing; [Poonen, *Undecidable problems: a sampler*]). So the gate **will reject genuinely-elementary theorems** whose only *found* proof routes through a denylisted-but-conservatively-eliminable lemma — directly in tension with the maintainer principle that relaxing elementary must not *over-reject*. The conservativity "license" (open question #2) **cannot rescue this**: conservativity asserts a base-theory proof *exists* without exhibiting it, while the audit inspects the proof term *actually produced* — a category error. **Honest conclusion: stop calling the output a certificate of "elementary"; call it a certificate of "footprint ⊆ stipulated T," and accept a permanent, theorem-mandated false-rejection rate as the price of soundness.**

**Critique 2 — The "hard" layers are all oracle-conditional except the kernel and make-unrepresentable types.** Axis (b) of the taxonomy conflates "hard given a correct model/oracle" with "unconditionally hard." Only the Lean kernel and dependent/refinement types are *unconditionally* hard (their oracle is the checker itself). CBF safety filters can be **stealthily deactivated** by biasing the state estimate ([Tan et al., arXiv 2403.17861](https://arxiv.org/abs/2403.17861)); projection onto a wrong/infeasible C **oscillates** or converges to a stable wrong point ([Wikipedia, *POCS*](https://en.wikipedia.org/wiki/Projections_onto_convex_sets)); the RW-supervisor and premise-whitelist enforce only what their finite model captures. The whole elementarity stack reduces to the soundness of one human-authored, non-canonical, transitively-incomplete denylist/whitelist over Mathlib.

**Critique 3 — Adaptive gaming is the *governing regime*, not a contingency.** It is a theorem that **no non-trivial proxy is unhackable** for all true objectives/environments (Skalse et al., *Defining and Characterizing Reward Hacking*; corrupted-reward No-Free-Lunch, [Everitt et al.](https://arxiv.org/pdf/1705.08417)). Goodhart-in-RL gives the *mechanism*: optimization rides the proxy to a facet of the feasible polytope, and from there steepest ascent on the proxy actively *decreases* the true objective (Goodharting observed in 19.3% of sampled MDPs) ([arXiv 2310.09144](https://arxiv.org/html/2310.09144v1); [Weng, *Reward hacking* survey](https://lilianweng.github.io/posts/2024-11-28-reward-hacking/)). Because **OpenEvolve makes MathAgent an explicit proxy-maximizer** ("pass the elementarity audit," not "be elementary"), expanding the denylist just relocates the facet. The right defenses are the RL ones: **regularize toward a trusted reference** (positive premise-allowlist over a weak base, so unknown ⇒ unreachable, not unknown ⇒ admitted), **early-stopping conservatism**, and **pessimism with a quantified risk budget**.

**Critique 4 — The dual failure of over-restriction is under-weighted.** In reactive synthesis, an over-constrained spec is simply **unrealizable** — no winning strategy exists — forcing maximum-realizability / best-effort synthesis and unrealizable-cores ([arXiv 1804.00415](https://arxiv.org/pdf/1804.00415); [arXiv 1409.1455](https://arxiv.org/pdf/1409.1455)). In program analysis, **soundness XOR completeness** is a theorem, and it surfaces operationally as **alarm fatigue**: tools hitting >95% false-alarm rates lead developers to ignore *all* warnings ([arXiv 2601.18844](https://arxiv.org/html/2601.18844v1)). A fail-closed elementarity gate that over-rejects is **not "safely conservative"** — it trains the operator to turn it off, which is fail-open by a social path. A **completeness-side guarantee** (a held-out known-elementary regression corpus measuring the false-rejection rate; unrealizable-core diagnostics that blame the *gate*, not the theorem) is missing and matters as much as denylist completeness.

**Critique 5 — The enforceability ceiling: a halting monitor enforces safety, never liveness.** Schneider's EM class: a monitor that can only *halt* enforces exactly the **safety** properties ([Schneider, *Enforceable Security Policies*](https://www.cs.cornell.edu/fbs/publications/EnfSecPols.pdf)). "The proof is non-elementary" is safety (a bad finite prefix) and *is* monitorable; but "the search eventually *finds* an elementary proof" (progress/non-blocking) is **liveness** and is **not enforceable by a truncating gate** — a fail-closed per-node Lean gate can guarantee "never accept non-elementary" while silently guaranteeing nothing about ever succeeding (it can deadlock the prover). Ligatti's **edit/PROJECT automata** enforce strictly more by *rewriting* the stream (rewrite a near-elementary step to the nearest elementary one) — a PROJECT capability MathAgent currently lacks (it only filters) ([Bauer–Ligatti–Walker](https://www.cs.princeton.edu/~dpw/papers/FCS02.pdf)).

**Critique 6 — Vacuity is a separate, cheap, mandatory check.** An implication-shaped property passes *vacuously* when its antecedent is unsatisfiable, and temporal-antecedent-failure experience reports this "always indicates a problem" ([Beer et al., *Vacuity detection*](https://www.cs.toronto.edu/~chechik/courses05/csc2108/beer01.pdf)). The proof analogue is the "2+2=5 / false-hypothesis" caveat. **Statement-fidelity must fail closed independently of elementarity.**

## Missing methods worth importing

- **Abstract interpretation (Cousot & Cousot)** — induce the constraint as a **sound over-approximation** via a Galois connection: compute an over-approximating abstraction of the proof term's trusted base and require it ⊑ a declared base T; soundness lives in the Galois connection, and incompleteness shows up as **over-rejection (visible, fixable), not silent admission** ([Leroy, lecture](https://xavierleroy.org/CdF/2019-2020/5.pdf)).
- **CEGAR (counterexample-guided abstraction refinement)** — treat each *admitted* non-elementary shortcut as a spurious counterexample and mechanically refine the abstraction, a convergent procedure instead of ad-hoc patching ([Wikipedia, *CEGAR*](https://en.wikipedia.org/wiki/Counterexample-guided_abstraction_refinement)).
- **SyGuS / CEGIS** — fuse restrict-the-generator (the grammar = elementary tactic DSL) and filter (the verifier) into one convergent loop ([Alur et al., arXiv 1405.5590](https://arxiv.org/pdf/1405.5590)).
- **Information-flow / contract-based (assume-guarantee) typing** — give each lemma an "elementarity level" in a security lattice; a typing discipline rejects any derivation that lets a non-elementary premise *flow* into the final term (make-unrepresentable, with a soundness theorem). Contract composition detects the **glue-lemma composition leak** ([Volpano–Smith–Irvine](https://journals.sagepub.com/doi/10.3233/JCS-1996-42-304); [Benveniste et al.](https://link.springer.com/article/10.1007/s10703-017-0304-9)).
- **Chance-constrained / distributionally-robust optimization** — for a probabilistic auditor of the fuzzy residue, impose a chance constraint with an explicit risk budget — a principled middle between hard gate and soft penalty ([arXiv 2101.08746](https://arxiv.org/pdf/2101.08746)).
- **Undecidability ceiling (Poonen; SEP Reverse Mathematics)** — name the hard ceiling: the gate is sound-but-incomplete by mathematical necessity, independent of denylist effort.

## Open questions

1. **Which target theory T defines "elementary" for MathAgent?** There is no canonical definition; the denylist *implicitly* stipulates one. Make it an explicit, documented choice, since the whole audit is a projection onto whatever C that T defines (P1).
2. **Can the conservativity license be operationalized?** *Adversarial verdict: no, not as an automated gate relaxation* — conservativity asserts a base-theory proof *exists* without exhibiting it, while the audit inspects the proof term produced (category error). It justifies a human's confidence, not a sound automated relaxation.
3. **How to bound denylist incompleteness given Ashby's law guarantees leakage?** Flip from blacklist to **positive allowlist over a weak base** (so anything not explicitly elementary is unreachable by construction) — but *reframed as attack-surface reduction, not completeness*, because Mathlib's transitive dependency density defeats import-boundary restriction below the boundary.
4. **Closing #8840 in practice** — *resolved (Part 5): the issue is closed and native computation reworked.* The live question is whether MathAgent's extractor **denylists the per-computation native axioms / `ofReduceBool`** and whether an **independent re-checker** (lean4checker/nanoda/coqchk) is in the loop — noting it defends the *correctness* floor, not elementarity.
5. **Should restrict-the-generator be pushed to make-unrepresentable (family #6)?** Could a restricted Lean tactic/elaboration environment make denylisted constructions un-elaborable — at what coverage cost (the AlphaGeometry coverage-for-hardness trade)?
6. **Multi-block / composition soundness** — is "elementary" closed under the sketch-composition operator? *Answered in part (Part 5): the composed footprint is the **union** of children's footprints plus the glue lemma's footprint, so closure holds iff the glue lemma is itself elementary — a checkable invariant, not an open mystery; the per-node audit already implements its discrete form.*
7. **Statement-fidelity vs. elementarity** — how strong is MathAgent's faithfulness gate, and does it *also* fail closed? Orthogonal to elementarity but a necessary companion gate (vacuity caveat).
8. **Is the soft prose scanner pulling its weight, or is it pure attack surface?** Given P5 (it lacks requisite variety) and that the codebase already treats it as non-authoritative, is its router-to-review value worth the false-positive cost, or should that budget move to hardening the Layer-4 oracle and adding premise-pool restriction?

## Net assessment

The synthesis is largely accurate and unusually well-grounded: ~90% of named methods and citations are real and correctly characterized, and the seven-family taxonomy plus the five invariants (P1–P5) are a genuine, non-trivial unification. The central recommendation — **hard ex-ante restriction + a hard model-grounded proof-term audit as the authority + soft signals only for the unformalizable residue, every layer fail-closed, oracle hardened adversarially forever** — is the correct architecture, and the codebase already implements most of it well. The corrections to **adopt** alongside it: (a) re-justify the `native_decide` ban on TCB-expansion grounds and denylist the per-computation native axioms / `ofReduceBool` rather than chasing `trustCompiler`; (b) demote Conant–Ashby to a motivating analogy; (c) force an **explicit, documented choice of target theory T** and openly accept a measured, theorem-mandated false-rejection rate, with a known-elementary regression corpus measuring it; (d) reframe premise-pool restriction as attack-surface reduction, never completeness, keeping the proof-term footprint audit (with footprint-union closure under composition) as the sole authority; (e) add an independent external re-checker for the kernel floor while noting it does nothing for elementarity; (f) treat the prover/OpenEvolve search as an **adaptive adversary of the gate** and import RL-grade anti-Goodhart machinery (reference-regularization, pessimism, early stopping). **Net: adopt the recommendation with these corrections, and stop calling the output a certificate of "elementary" — call it a certificate of "footprint ⊆ stipulated T."**

---

# References

**Proof theory & "elementary" (Part 1a)**
- Wikipedia, *Prime number theorem* — https://en.wikipedia.org/wiki/Prime_number_theorem
- Goldfeld, *The Erdős–Selberg dispute* — https://www.math.columbia.edu/~goldfeld/ErdosSelbergDispute.pdf
- Cornaros & Dimitracopoulos, *The prime number theorem and fragments of PA*, Arch. Math. Logic 33(4):265–281 (1994) — https://link.springer.com/article/10.1007/BF01270626
- Wikipedia, *Reverse mathematics* — https://en.wikipedia.org/wiki/Reverse_mathematics
- Stanford Encyclopedia of Philosophy, *Reverse mathematics* — https://plato.stanford.edu/entries/reverse-mathematics/
- Hirst, reverse-mathematics tour slides — https://www.appstate.edu/~hirstjl/snp/pdfslides/chrmtour.pdf
- Wikipedia, *Bounded arithmetic* — https://en.wikipedia.org/wiki/Bounded_arithmetic
- Stanford Encyclopedia of Philosophy, *Hilbert's program* — https://plato.stanford.edu/entries/hilbert-program/
- Feferman, *Predicativity* — https://math.stanford.edu/~feferman/papers/predicativity.pdf
- Wikipedia, *Feferman–Schütte ordinal* — https://en.wikipedia.org/wiki/Feferman%E2%80%93Sch%C3%BCtte_ordinal

**Proof-assistant / ATP auditing (Part 1a)**
- Lean reference, *Validating proofs* — https://lean-lang.org/doc/reference/latest/ValidatingProofs/
- Lean Issue #8840 (CLOSED; fixed by PR #8842) — https://github.com/leanprover/lean4/issues/8840
- Lean 4.29.0 release notes (RFC #12216 / PR #12217; native computation rework) — https://lean-lang.org/doc/reference/latest/releases/v4.29.0/
- Niels Voss, *Lean pitfalls* (native_decide TCB expansion / `@[implemented_by]` exploits) — https://github.com/nielsvoss/lean-pitfalls
- Lean community, *Did you prove it?* (statement fidelity) — https://leanprover-community.github.io/did_you_prove_it.html
- Rocq/Coq manual, *Print Assumptions* — https://rocq-prover.org/doc/V8.18.0/refman/proof-engine/vernacular-commands.html
- Sörensson & Biere, minimal unsat cores — https://groups.csail.mit.edu/sdg/pubs/2008/mincore-fm08.pdf

**AI/ML proof generation (Part 1b)**
- Nature, AlphaProof (s41586-025-09833-y) — https://www.nature.com/articles/s41586-025-09833-y
- DeepMind blog, AI solves IMO at silver-medal level — https://deepmind.google/blog/ai-solves-imo-problems-at-silver-medal-level/
- AlphaGeometry2 — https://arxiv.org/html/2502.03544v3
- LeanDojo / ReProver — https://arxiv.org/abs/2306.15626
- Grammar-constrained decoding survey — https://arxiv.org/pdf/2502.05111
- Type-constrained code generation — https://arxiv.org/pdf/2504.09246
- OpenAI, *Let's Verify Step by Step* (PRM800K) — https://cdn.openai.com/improving-mathematical-reasoning-with-process-supervision/Lets_Verify_Step_by_Step.pdf
- Anthropic, Constitutional AI — https://www.anthropic.com/research/constitutional-ai-harmlessness-from-ai-feedback
- Xu & Van den Broeck, Semantic Loss — https://ar5iv.labs.arxiv.org/html/1711.11157

**Control theory & cybernetics (Part 2a)**
- Wikipedia, *Supervisory control theory* — https://en.wikipedia.org/wiki/Supervisory_control_theory
- Wonham–Ramadge, supremal controllable sublanguage — https://arxiv.org/pdf/1306.2422
- Mayne et al., *Constrained MPC: Stability and Optimality* — https://www.researchgate.net/publication/220159558_Constrained_Model_Predictive_Control_Stability_and_Optimality
- Ames et al., Control Barrier Functions — https://arxiv.org/abs/1609.06408
- Tan et al., *Stealthy Deactivation of Safety Filters* — https://arxiv.org/abs/2403.17861
- Garone et al., reference/command governor survey — https://www.sciencedirect.com/science/article/abs/pii/S0005109816303715
- Blanchini, *Set invariance in control* — https://dl.acm.org/doi/10.1016/S0005-1098%2899%2900113-2
- Wikipedia, *Viability theory* — https://en.wikipedia.org/wiki/Viability_theory
- Wikipedia, *Variety (cybernetics)* / Law of Requisite Variety — https://en.wikipedia.org/wiki/Variety_(cybernetics)
- Wikipedia, *Good regulator theorem* (contested) — https://en.wikipedia.org/wiki/Good_regulator_theorem
- Baez, *The Good Regulator Theorem* — https://johncarlosbaez.wordpress.com/2016/01/27/the-good-regulator-theorem/
- Wentworth, *Fixing the Good Regulator Theorem* — https://www.lesswrong.com/posts/Dx9LoqsEh3gHNJMDk/fixing-the-good-regulator-theorem

**Operator theory & optimization (Part 2b)**
- Boyd & Parikh, *Proximal Algorithms* — https://web.stanford.edu/~boyd/papers/pdf/prox_algs.pdf
- Wikipedia, *Projections onto convex sets* — https://en.wikipedia.org/wiki/Projections_onto_convex_sets
- Wikipedia, *Dykstra's projection algorithm* — https://en.wikipedia.org/wiki/Dykstra%27s_projection_algorithm
- Combettes, monotone operator notes — https://pcombet.math.ncsu.edu/optim1.pdf
- Multi-block ADMM divergence — https://arxiv.org/pdf/1408.4266
- Nemirovski, interior-point lectures — https://www2.isye.gatech.edu/~nemirovs/Lect_IPM.pdf
- Fiveable, penalty & barrier methods — https://fiveable.me/mathematical-methods-for-optimization/unit-14
- Wikipedia, *Banach fixed-point theorem* — https://en.wikipedia.org/wiki/Banach_fixed-point_theorem

**Software / formal-methods systems thinking (Part 2c)**
- Wikipedia, *AC-3 algorithm* — https://en.wikipedia.org/wiki/AC-3_algorithm
- Vazou et al., Liquid Haskell refinement types — https://dl.acm.org/doi/10.1145/2628136.2628161
- King, *Parse, don't validate* — https://lexi-lambda.github.io/blog/2019/11/05/parse-don-t-validate/
- Wikipedia, *Design by contract* — https://en.wikipedia.org/wiki/Design_by_contract
- Bauer–Leucker–Schallhart, runtime verification (LTL3) — https://cs.uwaterloo.ca/~bbonakda/teaching/CS745/papers/RV.pdf
- Ligatti–Bauer–Walker, edit automata (TISSEC 2009) — http://users.ece.cmu.edu/~lbauer/papers/2009/tissec09-editauto.pdf
- Bauer–Ligatti–Walker, *More Enforceable Security Policies* — https://www.cs.princeton.edu/~dpw/papers/FCS02.pdf
- Schneider, *Enforceable Security Policies* — https://www.cs.cornell.edu/fbs/publications/EnfSecPols.pdf
- Open Policy Agent docs (Rego) — https://www.openpolicyagent.org/docs/policy-language
- Wikipedia, *Capability-based security* — https://en.wikipedia.org/wiki/Capability-based_security
- Meadows, *Leverage points* — https://donellameadows.org/archives/leverage-points-places-to-intervene-in-a-system/
- Wikipedia, *Viable system model* — https://en.wikipedia.org/wiki/Viable_system_model
- Wikipedia, *Theory of constraints* — https://en.wikipedia.org/wiki/Theory_of_constraints

**Adversarial / gaming / missing methods (Part 5)**
- Goodhart's Law in RL (occupancy-measure LP) — https://arxiv.org/html/2310.09144v1
- Weng, *Reward Hacking* survey — https://lilianweng.github.io/posts/2024-11-28-reward-hacking/
- Everitt et al., corrupted reward channel (No-Free-Lunch) — https://arxiv.org/pdf/1705.08417
- Maximum realizability (over-constrained ⇒ unrealizable) — https://arxiv.org/pdf/1804.00415
- Unsynthesizable cores — https://arxiv.org/pdf/1409.1455
- LLM static-analysis false-positive / alarm-fatigue study — https://arxiv.org/html/2601.18844v1
- Beer et al., *Vacuity detection in temporal model checking* — https://www.cs.toronto.edu/~chechik/courses05/csc2108/beer01.pdf
- Leroy, abstract interpretation lecture — https://xavierleroy.org/CdF/2019-2020/5.pdf
- Wikipedia, *Counterexample-guided abstraction refinement (CEGAR)* — https://en.wikipedia.org/wiki/Counterexample-guided_abstraction_refinement
- Alur et al., Syntax-Guided Synthesis (SyGuS) — https://arxiv.org/pdf/1405.5590
- Volpano–Smith–Irvine, information-flow type soundness — https://journals.sagepub.com/doi/10.3233/JCS-1996-42-304
- Benveniste/Nuzzo et al., contract-based design — https://link.springer.com/article/10.1007/s10703-017-0304-9
- Chance-constrained / distributionally-robust optimization review — https://arxiv.org/pdf/2101.08746
- Marabou (formal NN verification) — https://theory.stanford.edu/~barrett/pubs/WIZ+24.pdf
