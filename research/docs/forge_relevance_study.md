# Forge → MathAgent: Deep Structural Study & Relevance Assessment

> **What this is.** A read-only deep study of a *separate* project — **Forge** (`D:/Projects/forge`), a
> Rust/WASM TradingView-parity charting platform built by a multi-agent swarm — and an assessment of
> which of its ideas transfer to **MathAgent** (this repo: the training-free elementary-NT proving
> harness). Forge uses **DAGs, Dilworth's theorem, finite-state machines, and category theory**; the
> question this answers is *"is any of it relevant here?"*
>
> **Method.** Five Opus-4.8 readers studied Forge read-only (architecture; the DAG/Dilworth scheduler;
> category-theory usage; the FSM engine) and a synthesizer mapped each finding onto MathAgent's
> structures. File:line citations below are into **Forge** unless prefixed with a MathAgent path.
>
> **One-line verdict.** **Yes — substantially relevant, and unusually so:** Forge is ~90% a generic
> dependency-DAG orchestration engine, and its design docs *already diagnosed MathAgent's exact known
> bug* in another domain. The orchestration/order-theory layer transfers cleanly; the charting product
> does not; and Forge's pure-**AND** graph has **nothing** for MathAgent's distinctive **OR-branching**
> (which is MathAgent's value-add).

---

> **Implementation-status note (added 2026-06-28 — read before the recommendations below).** Several
> items in this study were subsequently *ported* but are **aspirational / decorative in the current
> tree, not load-bearing wins** — state this honestly when citing them:
> - **Dilworth width (§3.2, §4 🥈) and Mirsky `upward_rank` (§4 🥉).** Ported faithfully
>   (`agent/orchestrator/dilworth.py`), but they cap a **safe-parallel fan-out that does not exist yet**:
>   there is **no concurrent executor** — AND-node children are proven **serially**. For the common
>   *flat* AND-decomposition the children are precedence-independent, so **width == child_count** and the
>   cap rarely binds; `upward_rank` is **computed but decorative** (only a 3rd tie-break sort key behind
>   `_critical_path_depths`, and 0 for flat plans, in an ordering that is in any case outcome-irrelevant).
>   They are *correct theorems doing no real throughput work yet* — kept for a future concurrent executor.
> - **H⁰ child-consistency (§4 🥉 "Sheaf / H⁰ interface check").** What is actually wired is a **surface
>   signature-compatibility gate** — a narrow, computable regex/family shadow of an H⁰ 0-cocycle check
>   (a hardcoded ~6-family exclusivity table, firing only on `given`/`assumption`/`lemma` steps, often
>   inspecting 0 overlaps). It is **not** a full sheaf / global-section computation; do not read the
>   category-theory label as implemented cohomology.
> - **Category theory (§3.4, §6).** SMC `∘`/`⊗`, Applicative/Monad tiering, and functors are **labels /
>   reasoning aids only — there is no such code in MathAgent.** Faithful to Forge's own "reason-with, not
>   implemented" disclaimer; decorative as "category theory in the codebase."

---

## 1. Executive summary — what to take and what to leave

**Take (the orchestration "shadows"):**

1. **The suspending-vs-restarting scheduler distinction** — the *principled* fix for MathAgent's known
   `NEEDS_REVIEW`-stuck bug (`agent/orchestrator/dag_driver.py:229-231`). *Highest value, lowest effort.*
2. **A total finite-state node lifecycle** (`node_transition(state,event)->(state,Action)`, no `_`
   wildcard, totality proptest) — the *structural* form of that fix; the proptest would have caught it.
3. **Split-keyed proof memo** — *not* one content-addressed hash but **two**: a `semantic_goal_hash`
   (normalised statement identity → used for sub-goal dedup / fan-in) and a `proof_context_hash`
   (toolkit version + gate config + citable-lemma set + allowed axioms + denylist/allowlist state); key
   the cache on the **pair**. This preserves DAG sharing across identical goals *while* invalidating a
   stale `PROVEN` certificate when the elementary ruleset changes. *(Refined from a single hash after
   review — see §4.)*
4. **Untrusted-worker + adversarial-verifier + downgrade-don't-discard** — hardens the
   elementary-certification trust problem (relevant to the deep-audit finding that the guarantee can fail
   open). **Soundness-critical, not a "stretch."**
5. **SMC Applicative-vs-Monad tiering** — a clean test for which sub-proofs parallelise (static children)
   vs which must suspend-and-resume (children discovered mid-search).
6. **Dilworth width** — a *principled* fan-out width for the proof DAG's independent sub-lemmas and the
   developer's own coding workflows (replacing the guessed `min(16, cores-2)`).

> **Ordering matters (revised after review).** Items 1–4 are *correctness/soundness* changes; items 5–6
> are *throughput*. **Do correctness first.** Parallelism makes a correct scheduler *faster*; it does not
> make an incorrect one *safe* — so Dilworth/fan-out (item 6) lands **after** the suspending fix, the
> total lifecycle, the split-keyed memo, and the adversarial verifier.

**Leave:** the entire charting product (pixels, wgpu, Svelte, camera transforms); the ~30 charting UI
FSMs *as domain content* (only their *authoring discipline* transfers); `forge-codegen`/wasm glue; the
pixel-fixture gates; and — most importantly — **Forge's pure-AND scheduling has no analogue for
MathAgent's choose-among-decompositions / backtracking.** And a calibration: Forge's "~90% generic
orchestration" describes *Forge*; MathAgent's hard part is **maintaining elementary soundness under
informal proof search**, so these imports **harden the scaffold around the proof system — they do not
solve the proof-system architecture.**

---

## 2. What Forge is (the detailed structure)

**Product.** Forge is a **Rust/WASM local replication of TradingView's charting platform** whose
end-state invariant is *pixel-and-interaction parity* against fixtures captured from real TradingView. It
is a 9-crate Cargo workspace:

| Crate | Role |
|---|---|
| `forge-core` | The pure `ChartViewport` camera: logical↔screen↔NDC transforms, a `Copy` value type, fail-closed on degenerate/non-finite input (no FSM). |
| `forge-state` | The retained object registry, snap spatial index, and **~30 per-feature finite-state machines** holding ALL chart interaction/lifecycle state. |
| `forge-render` | wgpu/WebGPU pixels. |
| `forge-drawings`, `forge-studies` | Drawing tools and indicators/studies. |
| `forge-server` | Serves `dist` at `127.0.0.1:8765`. |
| `forge-wasm` | The typed `wasm-bindgen` boundary. |
| `forge-feature` | A grep-visible `forge_feature!` registration marker. |
| `forge-codegen` | Scans those markers via `syn` to emit the registry / wasm getters / Svelte bridge (never the machines themselves). |

The load-bearing architectural boundary: **Svelte owns chrome only; Rust/WASM owns all chart state +
pixels** — which is what makes rendered output *hashable* and the parity oracle tractable.

**The part that matters for MathAgent — the build/orchestration substrate (~90% of the engineering).**
Forge builds *itself* via an **"ephemeral-swarm + content-addressed-cache"** methodology
(`forge-architecture-guide.md`, `AGENTS.md`):

- A **feature DAG** (`docs/parity/tv_feature_graph.json`) of ~900 capability nodes whose edges are
  `precedence_deps` (pure AND — all prerequisites required; **no OR/alternative nodes**).
- A ~200-line **supervisor loop** spawns a *fresh* agent per ready node in a throwaway worktree; the
  agent produces an **artifact + receipt** and dies.
- The **orchestrator (not the worker)** recomputes canonical truth via a 9-gate verification contract
  executed as a *pure function* of `(artifact, fixtures)`.
- Concrete engine = pure-stdlib Python in `scripts/orchestrator/`:
  - `proof_cache.py` — content-addressed early cutoff.
  - `scheduler.py` — Dilworth-width concurrency + blocker classification.
  - `graph_growth.py` — Pearce-Kelly incremental topo-sort, online cycle detection, transitive reduction.
  - `gate_runner.py` — the gate-as-pure-function.
  - `supervisor.py` — composes all four into plan/gate/record/integrity + fail-closed status invariants.

The whole thing **terminates and is idempotent**: on restart it re-derives the frontier and skips any
node whose `node_proof_hash` is already in `.forge/proof_cache.json`.

---

## 3. How Forge uses each of the four concepts (with evidence)

### 3.1 DAGs
The build is a single **AND-DAG** of capability nodes, maintained *incrementally* (never
wholesale-regenerated — regen had leaked inherited over-claims). `graph_growth.IncrementalDAG`
(`scripts/orchestrator/graph_growth.py:94-262`) keeps a dense per-node order index; `add_edge` re-sorts
only the affected window (Pearce-Kelly) with **online cycle rejection** (a forward DFS reaching the
source proves the edge would close a cycle → rejected). `insert_discovered` (`:331-423`) is the
"Monadic" growth op: an agent that discovers a sub-dependency mid-build reports it at exit, and the
orchestrator splices the new node + edges one at a time, **clamping any worker-supplied `implemented`
status to `missing`** (`:426-455`) so a discovered node can never be born proven. `transitive_reduction`
(`:267-325`) strips shortcut edges so rank computation stays correct.

Scheduling is gated by a **three-guard readiness predicate**
`enabled(n) = precedenceReady(n) ∧ artifactsPresent(n) ∧ oracleProvable(n)`
(`scripts/schedule_analysis.py:131-139`), where each guard is independently evaluable and a *blocked*
node is classified by *which* guard failed (the remedy differs per guard).

### 3.2 Dilworth's theorem
`scheduler.py:310-342` `dilworth_width()` computes the **largest antichain** of the *residual*
(non-implemented) precedence DAG as `n − max_bipartite_matching` over the **full transitive closure**
(edge `u→v` iff `v` strictly reachable from `u`). By **König's theorem** this equals the **minimum chain
(path) cover = the Dilworth number = the true maximum set of nodes that can run fully in parallel**.
Matching is an *iterative* (non-recursive) Hopcroft-Karp (`:206-307`, with a 3000-rung-ladder self-test
proving it won't blow Python's recursion limit); the closure is a bitmask DP. It **refuses a cyclic
residual graph** (Dilworth is undefined off a poset). This feeds
`concurrency_cap(width, budget) = max(0, min(width, budget))` (`:77-85`), replacing a hardcoded
`CONCURRENCY_CAP=3`. The dual, **Mirsky's theorem** (longest chain = minimum antichain partition = the
*irreducible sequential depth* / critical path) is realized as the precomputed **`upward_rank`** (longest
path to a sink), used as scheduling priority so the critical path leads (`schedule_analysis.py:235`).
`next_batch` (`scheduler.py:628-670`) returns the ranked, cap-bounded, mutually-independent antichain to
fan out.

### 3.3 Finite-state machines
FSMs are the **dominant structural pattern of `forge-state`** (~30 of ~44 files). Every interaction/
lifecycle feature is a **pure total transition** `fn X_transition(state, event) -> (next_state, effect)`
whose body is a `match (state, event)` enumerating **every** pair explicitly — **no `_` wildcard**
(ignored pairs are explicit named no-op arms citing their statechart row; the gate *rejects* a receipt
whose Rust still contains a `_` arm). Adding a state/event becomes a *compile error*, not a silent
fall-through. The pattern is three enums + one function + a thin driver:

- **State** enum (e.g. `PanState::{Idle, Panning{anchor}}` — `chart_area_pan.rs:80-92`),
- **Event** enum (pointer/keyboard/data events),
- **Effect** enum — *effects are DATA the impure shell enacts* (e.g. `PanEffect::PanBy(ScreenDelta)`); the
  transition never mutates the world (pure core / impure shell — `chart_area_pan.rs:171-184` + the
  `ChartState` wrapper at `lib.rs:4339-4357`),
- a thin **Driver** struct whose `apply(event)->effect` is the only stateful wrapper.

Key disciplines:
- **Typestate** — per-state payload lives *inside* the active variant, so illegal states are
  unrepresentable (`PanState::Panning{anchor}`, `ToolPlacementState::Placing{tool,anchors}` with a
  proptest that a completing click always commits → `basic_tools.rs:362-378`).
- **Terminal + recovery states that BLOCK further work** — e.g. `HistoryState::NoDataTerminal`
  (`historical_bars.rs:444-451`) guarantees no infinite paging loop; `SubscriptionState::Unsubscribed`
  absorbs any event.
- **Statechart-first contract + totality proptests** — a `docs/parity/statecharts/<NODE>.md` must exist
  *before* implementation; every machine carries a `*_transition_is_total_and_event_determined` proptest
  plus invariant proptests ("disabled absorbs any event stream", "cancel always → Idle"). (`grep
  'no `_` wildcard'` = 134 hits / 42 files; `forge-architecture-guide.md:675-727`.)
- **`forge-codegen` generates registration glue, NOT machines** — `apply_fn` is optional *metadata*,
  "never a dispatch target" (`forge-codegen/src/lib.rs:46-58`), preventing silent effect-dropping.

### 3.4 Category theory
Two genuinely categorical uses + one incidental-naming place (`math_and_algo.md:109-115`, explicitly
"reason-with, not implemented-as-code"):

- **SMC scheduling algebra.** Symmetric monoidal categories with `∘` (sequential) and `⊗` (parallel) are
  *"the exact algebra of parallel-but-sometimes-sequential."* The operational shadows: `∘` = the Mirsky
  longest chain / `(max,+)` critical-path depth (the overlapped capture→build→verify pipeline,
  `docs/goals/00-orchestrator-loop.md:55-111`); `⊗` = the Dilworth maximum-antichain `next_batch`.
- **Applicative-vs-Monad per-node tiering.** From *Build Systems à la Carte*: `Applicative` task
  abstraction = **static deps (parallelisable)**; `Monad` = **dynamic deps (needs the suspending
  scheduler)**. The per-node tier router *is* `classify_blocker` + `graph_growth.insert_discovered`
  ("the Monadic growth operation", `graph_growth.py:329`).
- **Sheaf / H⁰ check** (the one *compiled* categorical structure). `check_interfaces.py:349-354`
  `h0_merge_check()` is the computable H⁰/0-cocycle shadow of the merge gate: all worktrees' local views
  of the shared interface registry must *agree on overlaps* before a merge — agreement-on-overlaps is the
  0-cocycle condition; passing it = "a global section exists" (H⁰ nonempty) = safe to merge.
- **`(max,+)` tropical algebra** = the timing algebra over the same poset; `upward_rank` is its
  operational shadow.
- *Incidental:* the "composition" in `forge-drawings`/`study_engine` is painter's-algorithm z-ordering
  and serde flatten; the `ToolKind↔DrawingKind` "functor-like" pair is a pinned bijection for catalog
  totality — **not** functors/monads. Do not mine it.

---

## 4. The relevance map — what to import into MathAgent

> Grouped by value. "Forge" cites Forge files; MathAgent targets are named explicitly. Effort is for the
> MathAgent-side change.

### 🥇 HIGH value, LOW effort — the standout

**Suspending-vs-restarting scheduler → fix the `NEEDS_REVIEW`-stuck bug.**
Forge's `multi-agent-dependency-graph-orchestration.md:60-72` diagnoses a multi-agent stall as a
**restarting scheduler** that *aborted* (an agent hit an unbuilt prereq, marked itself "blocked") with
**no producer scheduled to build the dep, so it never restarts** — verbatim: *"this is the failure mode
your agents exhibited… they stayed aborted forever."* Forge's prescribed discipline is the **suspending
scheduler**: a blocked agent *posts the missing dep to the frontier* (`classify_blocker` →
`producible_build` → `route_to:<producer>`, `scheduler.py:505-616`) and moves on, rather than dying.

This is **exactly** MathAgent's confirmed bug at `agent/orchestrator/dag_driver.py:229-231`:
```python
if res.exhausted:
    self.dag.mark_failed(goal)
    return False        # ← terminates BEFORE the decomposition branch at line 233
```
A direct-attempt `res.exhausted` caused by `NEEDS_REVIEW`-with-no-judge is *not* a real gap — it means
"this goal needs a producer." **Fix:** only `mark_failed` when exhausted **and** there is genuinely no
producer (`decomposer is None` and budget gone); otherwise **fall through to the decomposition loop** —
and adopt Forge's invariant *"a node never vanishes from every surface"*: every give-up must be
classified (`FAILED_GAP` / `FAILED_ELEMENTARY` / `EXHAUSTED`-starved) and surfaced, never silently
swallowed. Forge also distinguishes **`starved`** (a precedence producer that is *not* an in-scope
buildable node → escalate) from `producible_build` — MathAgent should likewise separate "real gap" from
"depth/budget-exhausted, retryable elsewhere."

### 🥈 HIGH value, MEDIUM effort

| Forge idea | MathAgent application |
|---|---|
| **Total FSM node lifecycle** (`fn node_transition(state,event)->(state,Action)`, no `_` wildcard; effects-as-data; totality + invariant proptests). Templates: `chart_area_pan.rs:197-225`, `datafeed.rs:1027-1084`, proptests `chart_area_pan.rs:412-471`. | MathAgent's advance logic is scattered inline branches in `dag_driver.py` (`state.py` defines only the enum). Refactor into **one explicit table** returning an `Action` enum (`AttemptDirect / Decompose / AcceptProven / MarkFailedElementary / MarkFailedGap / Exhaust`) the orchestrator executes. The bug becomes a *visible* arm `(IN_PROGRESS, DirectExhaustedNoJudge) -> Decompose`; any unhandled `(state,outcome)` is a compile/test error. Add the two invariant proptests: **"a NEEDS_REVIEW-no-judge node ALWAYS attempts decomposition before EXHAUSTED"** (would have caught the bug) and **"EXHAUSTED/FAILED_* absorbs any event stream"** (no late child result or memo re-entry resurrects a closed node = termination *as a property of the table*). |
| **Content-addressed proof-hash cache + early cutoff.** `node_proof_hash = sha256(node_id ‖ build_hash_proof + sha256(built wasm bytes) ‖ sorted(fixture CONTENT hashes) ‖ scope ‖ sorted(interface name=version))`; `early_cutoff` skips only `implemented` nodes whose recomputed hash hits the cache; fails *open* to a cold cache on corruption (`proof_cache.py:93-376`). | MathAgent already has the prove-once-reuse memo (`dag.py::goal_hash`, soundness-biased against false merges, `EXHAUSTED` deliberately not memoized). Forge sharpens the design — **but do NOT collapse everything into one `goal_hash`** (that would destroy cross-branch sharing of identical goals). Keep **two identifiers**: **`semantic_goal_hash`** (normalised statement identity → sub-goal dedup + fan-in, the existing role) and a separate **`proof_context_hash`** = (elementary-axiom/toolkit version + gate-config version + citable-lemma set + allowed axioms + denylist/allowlist state). **Key the memo on the pair `(semantic_goal_hash, proof_context_hash)`**: a goal stays shared across branches, yet a toolkit/gate/lemma change **invalidates only the stale certificate**, never the goal identity. Store a receipt recording *exactly which context a proof was accepted under* (like Forge's receipt_ref); steal `_canon`'s length-prefixed encoding (avoid goal-string delimiter collisions); keep caching `PROVEN` only; copy fail-open-to-cold-cache. *(This two-hash split is sharper than the original single-hash recommendation, adopted after independent review.)* |
| **Dilworth width = principled fan-out** (`scheduler.py:310` `dilworth_width` + iterative Hopcroft-Karp + transitive closure; ~120 lines of dependency-free stdlib; runs the cycle check first). **Sequence: *after* the suspending fix + lifecycle + memo (correctness before throughput).** | **(a)** For the dev's coding Workflow, replace the guessed `min(16, cores-2)` with `min(true_antichain_width, cores-2)`: the legal width is the max set of mutually-independent ready tasks — for code edits, the max set touching **disjoint files** (a Design-Structure-Matrix coupling antichain). Over-spawning past the antichain provably wastes workers; under-spawning starves the critical path. Feed **rate limits, API/model budget, and shared-memo wake-ups** into the cap, not just CPU cores. **(b)** Inside the proof DAG: the committed children of an AND-node are **precedence**-independent (the acyclicity guard ensures it) — but **precedence-independent ≠ assumption-independent**: two children may rest on inconsistent normalisations, hidden hypotheses, or different lemma versions. So fanning them out is safe **only when paired with the H⁰ child-consistency check below** before composing the parent. Compute Dilworth width over the open all-deps-met sub-lemmas, fan them out (a deep chain correctly returns ≈1 → don't over-spawn), order by `upward_rank`. **Use this only for committed AND children — never to choose among OR alternatives** (those are competing strategies, not mutually-required prerequisites). |
| **SMC Applicative-vs-Monad tiering.** Applicative = static, known children (parallelisable); Monad = children discovered on direct-attempt failure (suspend-resume). | Tier every OR-node. The **Monad tier is the disciplined statement of the bug fix** (suspend the parent, build the discovered decomposition, resume — never abort). The **Applicative tier unlocks the Dilworth fan-out** (committed children are static, parallelisable). Pairs naturally with the lifecycle-FSM refactor: the tier is a function of whether the arm is "children known" vs "children to discover." |
| **Untrusted-worker + adversarial-verifier + downgrade-don't-discard.** The orchestrator recomputes truth; every promotion needs an *independent* verifier that tries to **refute**; an over-claim is *downgraded* (`implemented→partial`) with a logged gap, never discarded (`forge-architecture-guide.md:213-238`; `PROGRESS.md` is full of downgrades). Plus **anti-vacuity traps**: a focused test matching "running 0 tests" *fails loud* (`gate_runner.py:510-520`); a missing artifact is a FAIL not a skip. | Directly relevant to the deep-audit finding that the soundness guarantee can fail open. **(1)** Make `NEEDS_REVIEW` route to an explicitly **adversarial** elementary-certifier (distinct from the prover; the producer never self-certifies) rather than terminate. **(2)** Adopt **downgrade-don't-discard**: a proof that fails elementary-certification but is otherwise valid becomes a logged `FAILED_ELEMENTARY` recording the *specific* non-elementary step (Forge's `GAP_MATRIX`), preserving the partial result + the exact gap. **(3)** Steal the anti-vacuity traps for the certifier: a check that inspected nothing / matched no rule / saw an empty proof must FAIL loud, never vacuously PASS. |

### 🥉 MEDIUM value

- **HEFT heterogeneous scheduling** (rank by critical path, assign each task to the model giving earliest
  finish). *Note: Forge only **reasons** about this (`forge-architecture-guide.md` Lever 2); it does not
  code it.* MathAgent's OpenEvolve Sonnet/Opus ensemble is actually **ahead** here. The transferable
  *principle*: make the depth/breadth weight a **function of `upward_rank`** — Opus weight rises on the
  critical path (deepest decompositions, lowest-confidence elementary steps); Sonnet handles the wide
  shallow frontier — instead of a fixed 0.8/0.2. Emit per-call cost/time telemetry to tune from data.
  Also: content-addressed early-cutoff can **dedup the evolutionary population** (score a ledger once per
  content hash, skip structurally-identical mutants).
- **Sheaf / H⁰ interface check — the prerequisite for safe AND-node composition (and thus for the
  Dilworth fan-out above).** Before an AND-node declares `PROVEN` by composing children, check that the
  children's **assumption/definition/lemma signatures agree on their overlaps**: variable domains,
  hypotheses, normalisation conventions, and shared lemma statements. A child proven under a
  hypothesis/definition that contradicts a sibling's is a 0-cocycle violation (no global section) → the
  composition is unsound even though each child passed locally → `REJECTED`/route-to-review, never
  promote. *Implement it as a concrete signature-compatibility gate — the category-theory label is
  optional; the operational check is what matters.* (Also the right merge-safety model if the worktree
  fan-out ever produces parallel proof fragments.) **As built (honest scope):** what landed is exactly
  this *surface signature-compatibility gate* — a narrow, computable shadow of the H⁰ 0-cocycle check
  (a hardcoded ~6-family exclusivity table over `given`/`assumption`/`lemma` step signatures), **not** a
  sheaf / global-section computation. It catches literal family clashes (e.g. even/odd) and frequently
  inspects 0 overlaps; read the "sheaf/H⁰" wording as a *label* for that lexical check, not implemented
  cohomology.
- **Workflow-net / Petri-net soundness** as the *formal name* for "everything terminates": no reachable
  state where a node sits `IN_PROGRESS` with no enabled transition (the bug was exactly a missing
  transition), and every run reaches a terminal state because Budget strictly decreases and
  `EXHAUSTED`/`FAILED_*` are absorbing. This is the spec the totality proptest discharges operationally.
- **`next_producers` leverage map.** Because `goal_hash` memoization makes one proven lemma reusable
  across *all* branches, a high-fan-in shared sub-lemma is exactly Forge's high-leverage producer
  (`supervisor.py:98-134` ranks producers by #dependents unblocked). When several decomposition branches
  are open, rank open sub-lemmas by in-degree (how many parents cite this `semantic_goal_hash`) **combined
  with `upward_rank`** — fan-in alone lets a shallow high-fan-in lemma starve a deep critical-path chain,
  so weight leverage by critical-path depth — and prove the highest-leverage one first; make the "all
  children proven?" re-check **event-driven over the shared memo** (wake every parent depending on the
  same `semantic_goal_hash` on each lemma completion) rather than only via the DFS return path.

### LOW value / convergent-design confirmation
- **Pearce-Kelly incremental topo-order + `insert_discovered` + online cycle rejection + clamp-to-missing.**
  MathAgent already embodies this correctly (`commit_decomposition` splices via `get_or_create`; a
  commit-time `would_create_cycle/reaches` guard rejects cyclic edges online — *arguably stronger* than
  Forge's per-tick detection; a new `OrNode` is born OPEN never PROVEN). Mostly convergent-design
  confirmation, plus a caution (don't wholesale-regenerate the proof tree on backtrack) and a marginal
  perf idea (a stored dense ord-index for O(1) "is u before v" if the DAG ever gets large).

---

## 5. What does NOT transfer

- **The shipped charting product** — pixel/interaction TradingView-parity, the wgpu/WebGPU renderer, the
  Svelte chrome boundary, the camera transforms — domain-specific to charting; transfers nothing beyond
  the generic "pure deterministic core, thin I/O shell" discipline.
- **The ~30 charting-UI interaction FSMs as *domain content*** (pan, axis-scale, hover, wheel, tool
  placement, snap). Only the **authoring discipline** (total match, no `_`, effects-as-data, typestate,
  totality proptests) transfers — captured above. Do **not** mine the gesture/datafeed machines for
  prover logic.
- **Drawings/studies "domain composition"** — incidental categorical naming (z-ordering, serde flatten);
  not functors/monads.
- **`forge-codegen` + the wasm-bindgen boundary** — build glue for a Rust/WASM web app. Only meta-lesson:
  "reserve codegen for wiring glue, never for the soundness-critical transition."
- **The pixel/fixture-bound gates** (served-wasm-sha == built-dist-sha, fixture provenance, the vision
  loops) — presuppose captured pixel fixtures a proof harness has no analogue for. The fail-closed
  *philosophy* transfers; the specific gates do not.
- **⚠️ The AND-vs-OR divergence (the most important boundary).** Forge's feature graph is a **pure
  AND-DAG**: exactly one way to build each feature, so **no choose-among-alternatives, no backtracking
  over failed alternatives, no "try the next decomposition."** Do **not** look to Forge's scheduler for
  how to pick/order/backtrack among alternative decompositions — **MathAgent's OR-structure is precisely
  its value-add over Forge, and the `NEEDS_REVIEW` bug is a failure to *use* that OR-structure** (a
  problem Forge never had to solve).
- **Deep reasoning-only theory Forge itself doesn't implement** (full sheaf cohomology beyond H⁰,
  Petri-net reachability beyond the soundness statement, poly functors, directed topology). Only the
  cheap computable shadows (Dilworth, `(max,+)` rank, H⁰, workflow-soundness-as-a-proptest) are worth it.

---

## 6. Forge ↔ MathAgent correspondence (quick reference)

| Forge | MathAgent analogue | Status |
|---|---|---|
| Feature AND-DAG of capability nodes | AND-OR proof DAG (`agent/orchestrator/dag.py`) | analogue + OR-extension is MathAgent's value-add |
| `node_proof_hash` content-addressed cache | `goal_hash` deep-hash memo | **sharpen** — split into `semantic_goal_hash` (sharing) + `proof_context_hash` (invalidation); key on the pair |
| Three-guard `enabled(n)` predicate | implicit in `_prove()` | **make explicit** |
| Restarting → **Suspending** scheduler | the `res.exhausted → mark_failed` bug | **direct fix** |
| `classify_blocker` (`producible_build` vs `starved`) | flat `FAILED_*` / `EXHAUSTED` | **enrich** (action-oriented kinds) |
| Total `match (state,event)` + proptests | `NodeState` enum + inline branches | **refactor into a total table** |
| Dilworth width / `concurrency_cap` | guessed `min(16, cores-2)` fan-out | **ported but aspirational** — no concurrent executor exists (children proved serially); flat AND ⇒ width==child_count, cap rarely binds |
| Mirsky / `upward_rank` (`(max,+)`) | none (DFS order) | **ported but decorative** — only a 3rd tie-break sort key; 0 for flat plans; ordering is outcome-irrelevant |
| Applicative/Monad tiering | none | **label only** (reasoning aid; no SMC/monad code) |
| H⁰ merge check | none | **adopted as a surface signature-compat gate** — a narrow computable shadow of H⁰, **not** a sheaf/global-section computation |
| Untrusted-worker + adversarial verifier + downgrade | fail-closed gate + faithfulness panel | **harden** (per deep-audit) |
| Commit-time acyclicity guard | `would_create_cycle` | convergent (MathAgent ≥ Forge) |
| Charting product / UI FSMs / codegen | — | **not relevant** |

---

## 7. Recommended next moves (for MathAgent) — prioritised (correctness before throughput)

**P0 — stop the known bad transition.** Implement the **suspending-scheduler fix** at
`dag_driver.py:229-231` — route a `NEEDS_REVIEW`-no-judge exhaustion to decomposition; reserve
`EXHAUSTED` for genuine starvation; never silently swallow a give-up. Add the explicit regression test
*"a direct attempt that exhausts because review is unavailable → decomposition is attempted."*

**P1 — make the lifecycle explicit, auditable, and resumable** (all soundness, not throughput):
- **Total `node_transition` table** returning an `Action` enum (`ATTEMPT_DIRECT / ROUTE_REVIEW /
  DECOMPOSE / WAIT_CHILDREN / ACCEPT_PROVEN / MARK_FAILED_GAP / MARK_FAILED_ELEMENTARY / EXHAUST_STARVED`).
  Python has **no Rust-style exhaustiveness check**, so enforce totality with an **enum-cartesian
  proptest** over every `(state × event)` pair, plus invariants: *no stuck non-terminal state*, *terminal
  states absorb every event* (no resurrection from `FAILED_*`), and *"NEEDS_REVIEW-no-judge always
  decomposes before EXHAUSTED."*
- **Explicit blocker reason codes** replacing flat failed/exhausted branches:
  `needs_review_no_reviewer`, `decomposer_absent`, `budget_starved`, `depth_limit`,
  `cyclic_decomposition`, `elementary_violation`, `formalization_failed`, `gap_found`,
  `unknown_tool_error` — so suspension/resumption and user reporting are clean and nothing gives up
  unclassified.
- **Split-keyed memo:** `semantic_goal_hash` (sharing) + `proof_context_hash` (toolkit/gate/lemma/axiom
  versions); cache only successful artifacts under the context they were accepted in.
- **Adversarial verifier + downgrade-don't-discard** (promoted from "stretch" — it directly protects the
  elementary-certification product): route `NEEDS_REVIEW` to an independent refuting certifier; a failed
  elementary check becomes a logged `FAILED_ELEMENTARY` with the exact offending step, never a silent
  discard or a cache success; **empty/vacuous verifier inspection must FAIL loud.**

**P2 — throughput, once correctness holds:** event-driven AND-node scheduling over a ready queue (wake
parents on shared-memo hits); then **Dilworth width + `upward_rank`** to cap the *safe* parallel fan-out
over committed AND children (never OR alternatives), with the **H⁰ child-consistency gate** before any
composition; budget/rate-limits as scheduler inputs, not just CPU cores.

**P3 — adaptivity:** rank-aware model routing (instrument cost/win curves first, then adapt the
Sonnet/Opus weights by `upward_rank`/uncertainty); `next_producers` leverage = fan-in **×** critical-path
depth.

**Extra regression tests worth adding** (Forge-style totality, specialised to MathAgent): (1)
`NEEDS_REVIEW`-no-reviewer never directly terminal while a decomposer exists; (2) every `EXHAUSTED`
carries a reason code; (3) terminal states absorb child-result / memo-hit / reviewer / retry events; (4)
a stale `proof_context_hash` cannot satisfy a new context; (5) no child born proven (all promotions
recomputed by the orchestrator); (6) empty verifier inspection fails loud; (7) parallel child completion
is order-independent (same parent status regardless of completion order); (8) an OR-alternative failure
does not poison sibling alternatives (unless the goal itself is context-wide impossible).

**Bottom line:** Forge's `math_and_algo.md` reads almost like a theory companion MathAgent's proof-DAG
orchestrator could adopt wholesale — they are two instantiations of the *same* dependency-graph
scheduling problem. Import the orchestration shadows; ignore the charting and the reasoning-only theory.

---

*Study performed read-only against `D:/Projects/forge`. No Forge files were modified. Citations are into
Forge unless prefixed with a MathAgent path.*
