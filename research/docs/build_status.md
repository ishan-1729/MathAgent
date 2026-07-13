# MathAgent — Historical Build-Status Log

> **Historical, not a current manifest.** The sections below preserve dated evidence and counts from
> the 2026-06/07 snapshots. Section 8 records current implementation semantics but is not a fresh live-run
> attestation. For current architecture use [`system_design.md`](system_design.md); for current validation
> run `make check`, the offline `pytest` suite, and `make demo`. Do not infer present test counts or live
> capability from this log.

> **Snapshot:** `2026-06-14T18:58:00+05:30` · base commit `05cb1c3` · stamp `8f849a0d603b1807`
> (`stamp = sha256(timestamp | full-commit-sha)[:16]` — identifies the exact state this doc describes;
> regenerate on every edit so a stale doc is detectable.) See the canonical
> **[`system_design.md`](system_design.md)** for the architecture this status tracks against.
>
> **Refreshed 2026-07-03** (bounded truth-up; see the [2026-07-03 addendum](#8-2026-07-03-addendum)).
> Numeric test counts in later sections are retained as historical measurements, not continuously
> updated claims.

> Status vocabulary: **data/docs** (curated, non-executable) · **built** (code present, no test asserted here) · **unit-tested** (offline deterministic tests pass) · **LIVE-validated** (exercised against real Lean/Mathlib/Codex) · **partial** (wired but not end-to-end run) · **planned** (placeholder only).

---

## 1. What it is

MathAgent is a **training-free agentic harness** that attempts to prove **elementary number-theory** theorems and — crucially — to *prove they are elementary*. Its defining mechanism is a dual gate: a deterministic + soft check on an informal "step-ledger," backed by a non-gameable Lean proof-term **dependency/axiom audit** that enforces the load-bearing insight **"Lean-verified ≠ elementary."** The repository is a role-based scaffold (knowledge base + benchmarks + agent harness + Lean formal layer + research dossiers) tied together by a skeleton checker and a CLI prover.

### Locked v1 decisions

| Decision | Commitment |
|---|---|
| **Harness** | Training-free first; a frontier general-purpose LLM stands in for AlphaProof. Shipped profiles default the prover to Claude/opus; the bare legacy CLI constructs Codex (GPT-5.5 @ xHigh) role specs. No model training in v1. |
| **Domain** | Elementary number theory **only** — no UFDs/ANT, class groups, Dedekind domains, elliptic curves, modular/cyclotomic theory, Catalan–Mihăilescu, or Baker theory as final steps. |
| **Lean role** | Soft/advisory in the docs era → **now the authoritative Layer 4**: a dependency + axiom audit is the only non-gameable gate. |
| **Tiering** | Targets staged IMO → research-grade NT problems. |
| **Elementary enforcement** | Binary admit/reject **GATE** applied *before* any weighted scoring. |

---

## 1b. Implemented vs. the design memo (adoption-list scorecard)

Measured against the prioritized adoption list in
[`literature_design_implications.md`](literature_design_implications.md) §4 — the cleanest yardstick for
"are we building what the literature synthesis prescribed." (The framing — *one LEAP spine + grafted
components + the elementary gate as the product* — is in [`system_design.md`](system_design.md) §2.)

| Memo priority | Item | Status |
|---|---|---|
| **HIGH 1** | Pantograph as Lean substrate | **Substituted** — own `lean_bridge` + community-REPL server + `Audit.lean` (proof-term closure + `collectAxioms`); Pantograph not depended on. |
| **HIGH 2** | LEAP AND-OR DAG + pre-commit reviewer | **Built + tested** (`dag.py`, `dag_driver.py`). |
| **HIGH 3** | HARD gate = dependency audit + restricted env + V_leg AST legality | **Partial.** Dependency-closure + axiom-whitelist audit **built + live**; restricted-import env intentionally skipped; a conservative lexer-level source boundary is now built, while the full elaborated-AST V_leg pass remains deferred → gap #8. |
| **HIGH 4** | Self-correction repair + Autoreason incumbent tournament | **Built.** Repair loop **live**; **Autoreason tournament now built + wired** (`tournament.py`; PUCT + Bradley-Terry; `max_replan_depth` consumed). |
| **MED 5** | Retrieval bias to an elementary index | **Built.** Loogle + BM25 + **neural bi-encoder (`bge-small-en-v1.5`)** via `HybridRetriever`. |
| **MED 6** | Construction/witness finder (Axplorer) | **Built as bounded grounding, not proof authority.** `numeric.py` performs exact-integer checks; optional `stages.evolve_witness` searches exact-integer construction specifications and reports the best diagnostic result, but never proves a theorem by itself. |
| **MED 7** | Skill cards + immutable objective spec | **Partial** — toolkit/denylist YAML + method files; immutability not architecturally enforced. |
| **MED 8** | AXLE/LeanDojo artifact cleanup + verified-lemma cache | **Not built.** |
| **LOW 9–12** | Trained prover, LeanProgress reranker, data-gen flywheel, orchestration shells | **Deferred to v2** (a cross-encoder reranker *hook* exists in the neural retriever). |

**MathAgent-original (not on the menu), built:** the typed **step-ledger** gate; the adversarial
**faithfulness panel**; the **persistent Lean server**; the conservative **semantic `goal_hash`**; and the
non-contaminative **ArXivMath benchmark** adapter + harness.

---

## 2. End-to-end pipeline

```
                          informal claim / goal
                                   │
                                   ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │ PROVER ROLE (Claude / Codex opt.)  →  JSON step-ledger                 │
 │   • justified steps, depends-on DAG, exactly one conclusion           │
 └──────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
 ┌──────────────── DETERMINISTIC + SOFT GATE (agent/gates) ──────────────┐
 │ L1a structural: schema-validate, dup-id, dangling/cyclic deps,        │
 │     one connected terminal; closed allowed_toolkit vocabulary         │
 │ L1a/L3 obligations: case-cover completeness, descent strict-decrease  │
 │     (numeric spot-check), coprime-split ancestry  → fail closed       │
 │ L1b soft scanner: prose denylist → REVIEW only (never REJECT)         │
 │  ⇒ GateReport: REJECTED | NEEDS_REVIEW | PASSED_DETERMINISTIC         │
 └──────────────────────────────────────────────────────────────────────┘
        REJECT → repair          │ NEEDS_REVIEW → Layer-2 LLM judge panel
                                   ▼
 ┌──────────────── MODEL HARNESS (agent/orchestrator) ───────────────────┐
 │ (1) FlatDriver: plan→prove→gate→repair→judges (Phase-1)               │
 │ (2) DagDriver: direct→decompose→review→recurse (AND-OR DAG)           │
 │       • deep-hash memoization (cache_hits), acyclicity guard, DFS BT  │
 │       • RalphLoop per node (episodes, gate-rejects → lessons-learned) │
 │       • Population/Elo: rank K candidate decompositions, best-first   │
 │     Hard liveness: NodeState machine + Budget caps → always terminates│
 └──────────────────────────────────────────────────────────────────────┘
                                   │ assembled informal proof
                                   ▼
 ┌──────────────── AUTOFORMALIZATION (agent/tools + formalize_bridge) ────┐
 │ Formalizer: ledger → Lean 4 (fresh prompt)                            │
 │ compile (lean / lake env lean)                                        │
 │   └─ on error → REPAIR LOOP: inject compiler errors                   │
 │        + retrieved Mathlib lemmas (Loogle HTTP + local BM25 hybrid)   │
 └──────────────────────────────────────────────────────────────────────┘
                                   │ compiles
                                   ▼
 ┌──────────────── LAYER 4 — AUTHORITATIVE AUDIT (the only real gate) ────┐
 │ Audit.lean #audit: transitive const closure + collectAxioms          │
 │ lean_audit.py judges:                                                 │
 │   • axioms ⊆ {propext, Classical.choice, Quot.sound}  (catches sorry) │
 │   • no closure constant matches content denylist (class groups,      │
 │     Dedekind, elliptic curves, number fields, modular/cyclotomic)    │
 │     unless infrastructure/elementary-by-fiat allowlist wins          │
 │ (persistent LeanServer: Mathlib loaded once → sub-second per audit)   │
 └──────────────────────────────────────────────────────────────────────┘
                                   │ audit.passed
                                   ▼
 ┌──────────────── FAITHFULNESS PANEL (agent/orchestrator) ──────────────┐
 │ adversarial lenses: back_translation / quantifiers_domain /           │
 │ vacuity / strength — each defaults to "unfaithful" if unsure          │
 │ (max_unfaithful tolerance, default 0 = any objection rejects)         │
 └──────────────────────────────────────────────────────────────────────┘
                                   ▼
        AUTHORITATIVE VERDICT  =  compiled AND audit.passed AND faithful
                                  AND certification_trusted
                       (the only path that certifies "elementary")
```

---

## 3. Component status table

### 3.1 Knowledge & benchmarks (`knowledge/`, `benchmarks/`)

| Component | File | Role | Status |
|---|---|---|---|
| Root README | `README.md` | v1 elementary mission, role-based repo map, examples-vs-problems rationale | data/docs |
| AGENTS.md | `AGENTS.md` | Tool-discovery pointer to the canonical repository instructions in `CLAUDE.md` | data/docs |
| Methods README + TEMPLATE | `knowledge/methods/README.md` | Methods contract; indexes 12 method files (excl. README/TEMPLATE) | data/docs |
| Method: descent | `knowledge/methods/descent.md` | Gate-integrated; 3 descent obligations + JSON shape; Lean lemmas (status: draft) | data/docs |
| Method: Vierzahlensatz | `knowledge/methods/vierzahlensatz.md` | Four-numbers parameterization pipeline (status: seed) | data/docs |
| Library README + 8 files | `knowledge/library/README.md` | Compact identities; `untriaged_tagebuch/` inbox (empty) | data/docs |
| Examples (Vierzahlensatz) | `knowledge/examples/vierzahlensatz/kieren_mse_x2_plus_1_eq_y3.md` | Worked method pipeline, no full proof (source of contamination mark) | data/docs |
| Problems README + TEMPLATE | `benchmarks/problems/README.md` | Per-problem folder contract | data/docs |
| Problem: x2_plus_1_eq_y3 | `benchmarks/problems/x2_plus_1_eq_y3/problem.md` | Calibration target; status: contaminated/example-adjacent | data/docs |
| 8 other problem folders | `benchmarks/problems/imo_1988_finite_descent/README.md` (+7) | All authored — 9 problem folders total (8 problems + 1 negative control), statements present, no longer placeholder-only | data/docs |
| Evaluation metrics | `benchmarks/evaluation/metrics.md` | 5 scoring axes | data/docs |
| Evaluation rubric | `benchmarks/evaluation/rubric.yaml` | Binary elementary gate → weighted (corr .30/nov .25/step .25/gen .20); Lean track 1.0 | data/docs |
| Run-record template | `benchmarks/evaluation/run_record_TEMPLATE.md` | Run-record fields plus persisted live run records | data/docs |
| Skeleton checker | `scripts/check_repo_skeleton.py` | Validates 21 dirs / 41 key files + {name,type,status} frontmatter | **built+LIVE-validated** (PASS) |
| Makefile | `Makefile` | check/test/demo/all/tree targets | built+LIVE-validated |
| CONTRIBUTING.md | `CONTRIBUTING.md` | Branch-per-experiment workflow; proof-status vocabulary | data/docs |

### 3.2 Elementary gate — Layers 1–3 (`agent/gates/`)

| Component | File | Role | Status |
|---|---|---|---|
| Gate README | `agent/gates/README.md` | Layered-gate rationale, versioned target theory, implemented and deferred defenses | data/docs |
| allowed_toolkit.yaml | `agent/gates/allowed_toolkit.yaml` | 34-key closed justification vocab + boundary_rulings | data/docs |
| denylist.yaml | `agent/gates/denylist.yaml` | Soft prose terms; Lean denylist/allowlist/axiom-whitelist | data/docs |
| ledger.schema.json | `agent/gates/ledger.schema.json` | Draft-07 DAG + 4 obligation shapes | data/docs |
| toolkit.py | `agent/gates/toolkit.py` | Loads/validates toolkit + denylist; requires a conclusion | built+unit-tested |
| report.py | `agent/gates/report.py` | Finding/Severity types, 5 layer ids | built+unit-tested |
| ledger.py | `agent/gates/ledger.py` | L1a parse + structural checks (Kahn cycle detection) | built+unit-tested |
| obligations.py | `agent/gates/obligations.py` | L1a/3 content checks; numeric re-checks; fail closed | built+unit-tested |
| scanner.py | `agent/gates/scanner.py` | L1b soft scan → REVIEW only | built+unit-tested |
| gate.py | `agent/gates/gate.py` | Composes parse+1a+1b+3 → GateReport; does NOT certify elementary | built+unit-tested |

### 3.3 Lean Layer 4 + persistent server (`agent/gates/`, `formal/lean/`)

| Component | File | Role | Status |
|---|---|---|---|
| lean_audit.py | `agent/gates/lean_audit.py` | Decision logic: axiom-whitelist + denylist-over-closure, allowlist wins | built+unit-tested |
| lean_bridge.py | `agent/gates/lean_bridge.py` | Runs lean / lake env lean; parses audit JSON; PATH/elan discovery | **built+LIVE-validated** |
| lean_server.py | `agent/gates/lean_server.py` | Persistent REPL: load Mathlib + #audit once, reuse | built+unit-tested |
| Audit.lean | `agent/gates/lean/Audit.lean` | `#audit` extractor: transitive closure + collectAxioms → JSON | **built+LIVE-validated** |
| add_zero_report.json | `agent/gates/lean/examples/add_zero_report.json` | Real fixture, toolchain v4.30.0 stamp | data/docs |
| mathagent_formal (lake) | `formal/lean/mathagent_formal/lakefile.toml` | Mathlib lake project, repl.exe built | **built+LIVE-validated** |

### 3.4 Tools (`agent/tools/`)

| Component | File | Role | Status |
|---|---|---|---|
| Tools README | `agent/tools/README.md` | Current adapter inventory and certification boundary | data/docs |
| numeric | `agent/tools/numeric.py` | Exact-integer SymPy witness search (L3 grounding) | built+unit-tested |
| Model roles (shipped profiles: Claude) | `agent/tools/claude_roles.py` + `agent/orchestrator/registry.py`; `agent/tools/codex_prover.py` | Prover/Decomposer/Reviewer/Judge/Comparator/Refiner/Formalizer/Faithfulness. `RolesProfile` supplies Claude opus/sonnet defaults; the bare legacy CLI supplies Codex specs; the registry resolves the declared provider. | built+unit-tested (model stubbed) |
| Formalizer | `agent/tools/formalizer.py` | Ledger → Lean 4 (fresh + repair prompts) | built+unit-tested |
| Loogle retrieval | `agent/tools/retrieval.py` | Mathlib lemma names via Loogle HTTP; graceful degrade | built+unit-tested |
| Semantic retrieval | `agent/tools/semantic_retrieval.py` | Local BM25 + HybridRetriever over elementary Mathlib subset | built+unit-tested |
| Neural retrieval | `agent/tools/neural_retrieval.py` | Dense bi-encoder (`bge-small-en-v1.5`) + optional cross-encoder reranker; graceful degrade; offline `HashingEmbedder` for tests | built+unit-tested (model stubbed); **live opt-in** |
| Answer checker | `agent/tools/answer_check.py` | Bounded allowlisted parsing plus typed scalar/collection comparison, exact rationals, finite tolerance, maximum set matching, and structural SymPy equality | built+unit-tested |
| OpenEvolve bridge | `agent/tools/openevolve_bridge.py` | Gate-scored, no-exec MAP-Elites over proof ledgers or exact-integer witness specs, driven by the Claude Sonnet-breadth/Opus-depth ensemble. Supervised modes are prover seeding (`evolve`), diagnostic construction search (`evolve_witness`), and last-resort decomposition (`evolve_fallback`); all are iteration-bounded candidate generation and none certifies. | built+unit-tested *(optional-dep)* |

### 3.5 Orchestrator / harness (`agent/orchestrator/`)

| Component | File | Role | Status |
|---|---|---|---|
| Liveness (NodeState + Budget) | `agent/orchestrator/state.py`, `run_profile.py` | State machine + hard caps; profile default is 60 orchestrator-metered search/review calls, max_depth 3, max_decomp 2, max_replan 2, 3 episodes; terminal/evolution internals are separately bounded | built+unit-tested |
| RunTrace | `agent/orchestrator/trace.py` | JSONL event log + run-record render | built+unit-tested |
| FlatDriver | `agent/orchestrator/driver.py` | Phase-1 linear plan→prove→gate→repair→judges | built+unit-tested |
| ProofDAG | `agent/orchestrator/dag.py` | AND-OR DAG, **semantic** deep-hash memoization (`canonical_form`), acyclicity, assemble | built+unit-tested |
| RalphLoop | `agent/orchestrator/ralph.py` | Per-goal multi-episode loop, gate-rejects → lessons | built+unit-tested (indirect) |
| DagDriver | `agent/orchestrator/dag_driver.py` | direct→decompose→review→recurse; terminal gate hook; **refiner hook + `max_replan_depth` consumed + PUCT/BT candidate selection** | built+unit-tested |
| Population / Elo | `agent/orchestrator/population.py` | Candidate Elo, tournament, PUCT, Bradley-Terry MLE (now driven by the driver + tournament) | built+unit-tested |
| Revision tournament | `agent/orchestrator/tournament.py` | **Autoreason incumbent tournament**: critic→author→synth→blind preference panel; do-nothing wins ties; k=2 stop; margin gate; PUCT + Bradley-Terry; independent proof-judge `no_gaps` admission; deterministic + configured budgeted-Lean guard; disabled without proof review | built+unit-tested |
| Faithfulness panel | `agent/orchestrator/faithfulness.py` | 4-lens adversarial check; default-unfaithful | built+unit-tested |
| Formalize bridge | `agent/orchestrator/formalize_bridge.py` | Terminal L4 gate: formalize→compile→audit→faithfulness | built+unit-tested |

### 3.6 Autoformalization (cross-cutting)

| Component | File | Role | Status |
|---|---|---|---|
| Repair loop | `agent/orchestrator/formalize_bridge.py` + `agent/tools/formalizer.py` | Lean-error repair: inject errors + retrieved lemmas, re-formalize | built+unit-tested; **LIVE recovery of n²−n** |
| Retrieval feed | `agent/tools/retrieval.py`, `semantic_retrieval.py` | Loogle + BM25 hybrid supplies real Mathlib lemma names | built+unit-tested; **LIVE (Loogle + BM25)** |

### 3.7 Literature & docs (`research/`, `agent/roles/`)

| Component | File | Role | Status |
|---|---|---|---|
| Papers INDEX | `research/papers/INDEX.md` | Index of 20-dossier corpus | data/docs |
| Paper dossiers | `research/papers/` | 20 folders (AlphaProof_Nexus, LEAP, Pantograph, GoedelProverV2, LeanAgent, …) | data/docs |
| Design implications | `research/docs/literature_design_implications.md` | Component menu; HARD/SOFT toolbox; audit-first | data/docs |
| Plan red-team | `research/docs/plan_redteam.md` | 5-lens plan review; binary elementary gate | data/docs |
| Engine review | `research/docs/engine_review.md` | 5-lens engine review; landed fixes | data/docs |
| L4 + population build record | `research/docs/lean_layer4_and_population.md` | Authoritative gate + Elo build record | data/docs |
| Formalization bridge doc | `research/docs/formalization_bridge.md` | ledger→Lean→compile→audit→faithfulness | data/docs |
| Codex harness doc | `research/docs/codex_harness.md` | Codex stands in for AlphaProof (earlier snapshot) | data/docs |
| Autoformalization repair doc | `research/docs/autoformalization_repair.md` | Repair loop + Mathlib retrieval | data/docs |
| Prover prompt | `agent/roles/prover.md` | Live Prover role: emits JSON step-ledger | data/docs |
| Critic/Judge prompt | `agent/roles/critic_judge.md` | Live Layer-2 critic; not final authority | data/docs |

### 3.8 Run surface (`scripts/`, root)

| Component | File | Role | Status |
|---|---|---|---|
| prove.py | `scripts/prove.py` | Supervised profile/legacy prover CLI; validates only the providers and Lean capabilities active for the selected run | built |
| run_benchmark.py | `scripts/run_benchmark.py` | Non-contaminative ArXivMath CLI; dry or vanilla `CodexAnswerSolver`, with optional answer-refinement tournament; per-item error isolation | **built+LIVE-validated** (real GPT-5.5-xHigh run) |
| ArXivMath adapter | `agent/benchmarks/arxivmath.py` | Loader (statement-only `Problem` + held-out `oracle`) + `run_benchmark` + run records | built+unit-tested |
| ArXivMath dataset dir | `benchmarks/datasets/arxivmath/` | README (provenance/license/CC BY-SA 4.0), `manifest.yaml`, synthetic `fixtures/sample.jsonl` | data/docs |
| CI workflow | `.github/workflows/ci.yml` | Offline job (skeleton + suite, py3.11/3.12) always-on; **live-Lean suite on `workflow_dispatch`** with `.lake` cache | built |
| check_repo_skeleton.py | `scripts/check_repo_skeleton.py` | `make check` validator | built |
| Makefile | `Makefile` | check/test/demo/all/tree | built |
| pyproject.toml | `pyproject.toml` | py≥3.11; pydantic/pyyaml/sympy/jsonschema; pytest config | built |
| conftest.py | `conftest.py` | sys.path bootstrap for `import agent` | built |
| tests/ (snapshot: 33 files) | `tests/` | Historical 2026-06-14 suite shape; do not treat as the current module count | built+unit-tested |
| Live-gated tests | `tests/test_lean_mathlib_live.py` (+ bridge/server/formalize/codex) | Opt-in via env flags | **built+LIVE-validated** |
| demo.py | `agent/demo.py` | `make demo` no-LLM/no-network end-to-end | built |
| Lean env README | `formal/lean/README.md` | Lean 4.30.0 + Mathlib v4.30.0 build/audit env | data/docs |
| lakefile.toml | `formal/lean/mathagent_formal/lakefile.toml` | Mathlib + repl @ v4.30.0 | built |
| lean-toolchain | `formal/lean/mathagent_formal/lean-toolchain` | Pins leanprover/lean4:v4.30.0 | built |

---

## 4. What is LIVE-validated

| Claim | Evidence |
|---|---|
| **Lean 4.30.0 + Mathlib v4.30.0 toolchain** | `add_zero_report.json` carries a genuinely extracted `leanprover/lean4:v4.30.0` stamp; lake project + built `repl.exe` present under `.lake/packages/repl`. |
| **Persistent server ≈ sub-second per audit** | `LeanServer` loads Mathlib + `#audit` once and reuses the base env; subsequent audits return ~0.1s. |
| **Denylist rejects non-elementary content** | Live test: an `IsDedekindDomain` proof is rejected by the closure denylist even though it compiles. |
| **`full_verify` authoritative on `n+0=n`** | Opt-in live path (`test_formalize_live.py`): ledger→gate→Codex formalize→lake/lean compile→L4 audit ⇒ `authoritative_elementary`. |
| **Repair loop recovered a formalization (`n²−n`)** | Lean-error repair loop re-formalized using injected compiler errors + retrieved lemmas (documented in `autoformalization_repair.md`). |
| **Retrieval live** | Loogle HTTP API + local BM25 over an elementary Mathlib subset both produce real Mathlib lemma names. |
| **Faithfulness panel 4/4 lenses** | back_translation / quantifiers_domain / vacuity / strength each default to "unfaithful"; aggregation rejects on any objection (tolerance 0). |
| **Core elementary proof passes; `sorry` rejected** | Live: core proof passes; a `sorry` proof is rejected via `sorryAx`. |
| **Full certification pipeline authoritative on real NT theorems** (2026-06-13) | `prove.py --terminal-gate --server --faithfulness --retrieval --repair 3` (GPT-5.5-xHigh): **`n²≡0,1 mod 4`** and **`√2 irrational` (x²=2y², infinite descent)** both went prove→formalize→compile→**Layer-4 audit PASS (0 rejects)**→faithfulness 4/4→`authoritative_elementary=True`. **Two of three historical attempts** certified; `3∣a²+b²⇒3∣a∧3∣b` was proven but its `decide`-based formalization failed to compile → honestly `authoritative=False` (gap #1). See [`live_certification_runs.md`](live_certification_runs.md). |
| **Vanilla vs answer refinement on ArXivMath NT (final-answer)** | Live GPT-5.5-xHigh: vanilla 4/5 (80%); answer-refinement tournament {7,15,17} 3/3 — no regression on the overlap. This answer-only experiment did not run the typed-ledger/Layer-4 proof harness and measures no full-harness lift. Historical record: [`runs/2026-06-13_nt_vanilla_vs_harness.md`](../../benchmarks/datasets/arxivmath/runs/2026-06-13_nt_vanilla_vs_harness.md). |

**Caveat:** most live paths are still opt-in (env-gated). **This session DID re-execute** the certification pipeline and the ArXivMath comparison live with GPT-5.5-xHigh + Lean/Mathlib (rows above); the remaining "LIVE-validated" claims rest on persisted real-toolchain fixtures + prior validated runs.

---

## 5. How to run

**Validate the repo & run the suite:**

```bash
make check     # scripts/check_repo_skeleton.py (layout/frontmatter validation)
make test      # python -m pytest
make all       # check + test (CI target)
make demo      # agent/demo.py — gate + flat driver, no LLM / no network
```

Aggregate offline result (this snapshot): **380 tests passed, 8 opt-in skipped** — all deterministic
(model & Lean stubbed). New since the prior snapshot: `test_dag` semantic-canonicalization +
soundness guards, `test_tournament`, `test_dag_driver` refiner/replan, `test_layer4_sync`,
`test_neural_retrieval`, `test_answer_check`, `test_arxivmath`, plus the **2026-06-14 security/soundness
hardening** suites (see below). The 8 skips are the env-gated live
paths (`MATHAGENT_LEAN_TESTS` / `MATHAGENT_CODEX_TESTS` / `MATHAGENT_NEURAL_TESTS`).

> **Current-count policy:** do not copy a later collection/pass number into this historical section. The
> suite changes with every hardening pass, and a count from a dirty intermediate tree is not validation
> evidence. Use the commands above and report their actual result for the commit under review.

**Run the prover (the active profile determines which provider and Lean capabilities are required):**

```bash
python scripts/prove.py --profile profiles/default.yaml "<goal>"
python scripts/prove.py --terminal-gate --server --retrieval --repair 3 "<goal>"
python scripts/prove.py --direct --formalize "<goal>"
```

The shipped default profile resolves Claude roles; the bare legacy CLI constructs a Codex profile. The
supervisor probes only active providers and the exact Lean transport. `--formalize` requires direct mode and
prints Lean source; `--terminal-gate` works for DAG or direct execution. A certifying invocation requires
faithfulness and returns 0 only for `authoritative_elementary`; an informal proof with a failed/absent
certificate returns 1. Configuration/capability errors return 2. `--server` requests the persistent REPL and
fails closed if it cannot start; an authoritative terminal-only profile may instead use one-shot compilation
with `lean.server=false`.

`--budget` / `budgets.max_llm_calls` caps orchestrator-metered search/review calls, not terminal
formalization/repair/faithfulness or OpenEvolve's internal calls. Terminal verification reports its own
provider cost as `FormalizeAuditResult.model_calls`; evolution is bounded/reported by the applicable stage
iteration count and `ensemble.timeout_s`; per-node verification has the optional `max_node_verify_calls`
sub-cap. Role-level `effort` is Codex-only and the supervisor rejects it for any provider chain that can
select Claude.

**Opt-in live tests (skipped by default):**

| Env var | Unlocks |
|---|---|
| `MATHAGENT_CODEX_TESTS=1` (+ codex on PATH) | Live Codex prompt/role tests |
| `MATHAGENT_LEAN_TESTS=1` (+ built Mathlib project) | Live Lean bridge / Mathlib audit / semantic build |
| both | `test_formalize_live.py` full end-to-end (`n+0=n`) |

### Security / soundness hardening (2026-06-14)

A deep multi-agent audit (5 Opus code workers + 3 consistency workers + 2 adversarial Codex GPT-5.5-xHigh
agents + skeptic verification; report in [`audits/2026-06-14_deep_audit.md`](audits/2026-06-14_deep_audit.md))
found, and a follow-up fix campaign closed, a set of soundness/security defects. **Closed:**

- **Faithfulness fails open → fail-closed.** `FormalizeAuditResult.faithful` now requires a panel that
  *actually ran and passed*; `authoritative_elementary` requires it. The CLI defaults faithfulness **on**
  for `--terminal-gate`/`--formalize` and now rejects `--no-faithfulness` in any certifying invocation.
- **Certification trust is explicit.** Authority requires both the formalizer and faithfulness checker to
  advertise `certification_trusted=True`; generic/scripted fixtures default to untrusted, live panels are
  trusted only with unanimity, and alternate persistent servers must expose the trusted audit interface.
- **Lean Layer-4 audit integrity:** nonce-bound `#audit` sentinel + duplicate-sentinel rejection +
  forbidden-command stripping + theorem-name cross-check (defeats self-reported / stale / injected audits);
  prefix-anchored allow/deny precedence; persistent-server teardown + error isolation.
- **RCE removed** from the numeric gate and answer grader: untrusted expressions are converted from small,
  bounded allowlisted ASTs rather than `eval`/`sympify`/`parse_expr` execution paths.
- **`NEEDS_REVIEW` no longer admitted as authoritative** (requires `PASSED_DETERMINISTIC`); RalphLoop and
  DAG decomposition fail closed on an unreviewed `NEEDS_REVIEW`.
- **Goal binding** on the direct `--formalize` path (ledger claim *and* terminal conclusion bound to the
  goal via `goal_hash`); depth-limit cache poisoning, decomposition rollback, greedy verdict regex, and
  answer-grading mis-classification fixed. New suites: `test_faithfulness_gate`, `test_ralph_review`, plus
  hardening tests across `test_lean_*`, `test_numeric`, `test_obligations`, `test_dag_driver`, `test_answer_check`.

**Tracked follow-ups (non-blocking):** `Toolkit.ruling()` / `boundary_rulings`
([`agent/gates/toolkit.py:63`](../../agent/gates/toolkit.py); populated from
[`agent/gates/allowed_toolkit.yaml`](../../agent/gates/allowed_toolkit.yaml) `boundary_rulings`) remain
**spec-only** — declared and loaded, but *never consulted by a gate to admit or reject anything*. The
`ruling()` accessor has no callers; the only reader of `boundary_rulings` is `dag.py:147-148`, which folds
the rulings into the certificate-invalidation context fingerprint (so a tool moving allowed↔disallowed
invalidates a stale `PROVEN` cert) — that is context-hashing, not a contested-tool ruling being enforced.
Keep flagged as a documented limitation. Goal binding is now enforced across the flat and DAG/direct paths:
the requested problem, ledger claim, and terminal conclusion must conservatively hash to the same goal.

---

## 6. Paper → component mapping

| Paper / system | Mapped component |
|---|---|
| **LEAP** | AND-OR DAG with memoization — `agent/orchestrator/dag.py`, `dag_driver.py` |
| **AlphaProof_Nexus** | Per-goal Ralph loop + **population/Elo candidate search** (Elo + Bradley-Terry + PUCT) — `agent/orchestrator/ralph.py`, `population.py` |
| **Autoreason** | Incumbent / "do-nothing" revision tournament (k=2 stop, margin gate) — **built**: `agent/orchestrator/tournament.py`, wired into `DagDriver(refiner=…)` |
| **AlphaEvolve** | Two narrow lessons (**not** the population machinery — that is AlphaProof_Nexus): the **cheap-first evaluation cascade** (`agent/gates/gate.py` layer ordering) + the **"no hard projection for elementarity"** lesson (→ Layer 4 is a verification gate, not a projection). |
| **OpenEvolve** (AlphaEvolve OSS) | **Optional** evolutionary backend: MAP-Elites population search over proof-sketch **ledgers**, gate-scored fitness, no-exec, driven by a real **AlphaEvolve-style Sonnet-breadth + Opus-depth ensemble** (via the `claude` CLI) — `agent/tools/openevolve_bridge.py` (`OpenEvolveBackend` is a `DagDriver` `Decomposer`). |
| **AXLE** | Adversarial statement-faithfulness panel — `agent/orchestrator/faithfulness.py` |
| **Loogle / LeanSearch / LeanExplore** | Mathlib lemma retrieval — `agent/tools/retrieval.py` (Loogle) + BM25 (`semantic_retrieval.py`) + neural bi-encoder (`neural_retrieval.py`) |
| **MathArena ArXivMath** | Non-contaminative final-answer benchmark adapter + SymPy grader — `agent/benchmarks/arxivmath.py`, `agent/tools/answer_check.py` |
| **Pantograph / LeanDojo** | Lean compile/audit bridge + persistent REPL server — `agent/gates/lean_bridge.py`, `lean_server.py`, `lean/Audit.lean` |
| **AlphaProof (substitute)** | Model-agnostic focused-prover slot — shipped profiles declare Claude/opus through `RolesProfile`; the registry resolves that spec or an optional Codex GPT-5.5-xHigh spec (`agent/tools/codex_prover.py`), and the bare legacy CLI uses Codex. |

---

## 7. Honest gaps & next steps

### Resolved in this snapshot

- ✅ **Retrieval neural backend (was gap #3)** — `neural_retrieval.py` adds a dense bi-encoder
  (`bge-small-en-v1.5`, the LeanExplore recipe) + optional cross-encoder reranker, hybridized with
  Loogle + BM25. Closes the abbreviation/paraphrase gap (`gcd` ↔ "greatest common divisor").
  *Caveat:* unit-tested with a stub embedder; the semantic win is asserted only in the **opt-in** live
  test (`MATHAGENT_NEURAL_TESTS=1`) — not yet run here.
- ✅ **Autoreason tournament + PUCT + Bradley-Terry + `max_replan_depth` (was gap #5)** —
  `tournament.py` (do-nothing first-class, k=2 stop, margin gate, **elementary-admissibility guard so a
  non-elementary revision can't displace an elementary incumbent**); wired as `DagDriver(refiner=…)`;
  the population path now uses Bradley-Terry strengths + PUCT selection; the decomposition loop consumes
  the global replan budget.
- ✅ **Strict `goal_hash` (was gap #6)** — full SHA-256 over NFC + whitespace normalization only.
  Authority-bearing identity never folds notation, synonyms, punctuation, variable names, or operands
  because a false memo hit would be *unsound* (`A × B` is not assumed equal to `A * B`). Lossy
  canonicalization is confined to non-authoritative H0 signature analysis.
- ✅ **CI + Layer-4 sync (was gap #7)** — `.github/workflows/ci.yml` runs the offline suite on every
  push (py3.11/3.12) and the **live-Lean suite on manual `workflow_dispatch`** (cached `.lake`);
  `test_layer4_sync.py` ties the `Audit.lean` JSON contract ↔ Python parser ↔ denylist YAML together
  and pins that `gate.evaluate` does **not** invoke Layer 4 (the intentional separation).
- ✅ **ArXivMath first live answer-only run (advances baseline plumbing for gap #2)** — the
  non-contaminative adapter + SymPy grader ran live with GPT-5.5-xHigh on the hand-classified NT subset
  of `arxivmath-0326`. **Vanilla 4/5 (80%, 1 timeout); answer refinement {7,15,17} 3/3; the common
  items were 3/3 vs 3/3.** The Codex Autoreason answer tournament (PUCT + Bradley-Terry + prose filter)
  ran end-to-end, but it was not the typed-ledger/Layer-4 proof harness and therefore measures no
  full-harness lift. Historical record:
  research problems. Record: [`benchmarks/datasets/arxivmath/runs/2026-06-13_nt_vanilla_vs_harness.md`](../../benchmarks/datasets/arxivmath/runs/2026-06-13_nt_vanilla_vs_harness.md).
  *No proof-harness lift has been measured; the answer-only overlap was at ceiling.*

### Remaining

| # | Gap | Next step |
|---|---|---|
| 1 | **Autoformalization wall moved (was the T1-rung miss; now IMO-hard multi-step).** The specific 2026-06-14 miss (`3∣a²+b²⇒3∣a∧3∣b`, brittle `decide`-over-`Fin`/`ZMod` → `Decidable`-synthesis failure) is **CLOSED**: `ClaudeFormalizer` produces a compiling proof using `decide` on the finite `ZMod 3` core only, bridged via `ZMod.intCast_zmod_eq_zero_iff_dvd`, and the winning tactic discipline is encoded in `Formalizer._RULES`. The reach map (live) shows residue casework **and** infinite descent reliably covered at the T1 rung. See [`live_certification_runs.md`](live_certification_runs.md) (2026-06-28 reach map). | Genuine frontier is now **IMO-hard multi-step** proofs (Vieta jumping, coprime-factorization Diophantine, sum-of-two-squares descent), which stress *proof-finding*, not just formalization. Scale the ladder there. |
| 2 | **No full-harness lift measured yet** — the first live run exercised vanilla and answer refinement only; their tiny overlap was at ceiling. | Sweep vanilla across the full set to find failures, then implement/run a real typed-ledger/Layer-4 `Solver` on those items. |
| 4 | **Statement-faithfulness same-model caveat** — judges/prover can be the same model; no cross-model independence. | Use an independent judge model; widen the lens panel. |
| 8 | **Full V_leg elaborated-AST legality gate deferred.** A conservative lexer-level source boundary is now implemented: fixed umbrella imports, local theorem binding, nonce-bound audit, and rejection of commands/attributes/quoted literals plus unsafe/macro/elaborator/evaluation/native tokens. This is not claimed to be a complete AST pass. | If warranted, add an elaborated-AST legality pass as defense-in-depth; keep the dependency/axiom audit authoritative. |
| 9 | **Restricted-import Lean environment not built** — intentionally skipped (memo: coarse/brittle, backstopped by the audit). | Optional later filter; the dependency audit remains the authoritative gate. |
| 10 | **Train-a-prover is explicitly v2.** | Keep the training-free harness as the v1 baseline; revisit after benchmark runs exist. |

> Numbering note: #3/#5/#6/#7 are resolved above; #2 is narrowed; #8 is the newly-tracked deferred
> V_leg gate; #9/#10 are carried forward. Gap #1 (autoformalization) + #2 (real run records) are the
> two that actually block a first headline result — the search/retrieval/gate machinery is now in place.

---

## 8. Current implementation-semantics addendum (2026-07-13)

The base snapshot (§1–§7) remains dated evidence. The points below describe current code contracts; they
do not claim that live provider/Lean tests were re-run on this date, and they intentionally contain no
copied offline test count.

- **One supervised control plane.** `RunProfile` → `validate_profile` → `build_driver` → registry →
  `DagDriver` is the profile path. The supervisor probes only active roles and the exact Lean transport,
  rejects invalid numeric/model/ensemble settings, fails closed if an explicitly requested persistent
  server cannot start, and cleans up a warmed server when construction fails.
- **Elementarity semantics are explicit.** `none` disables the elementarity objective but preserves goal
  binding, acyclicity, obligations, H0, and composition checks. `soft` enforces the objective in search but
  can report only `soft_proven`. `authoritative` attaches the terminal gate. Direct authoritative profiles
  are valid when DAG-only stages are disabled; CLI `--formalize` specifically requires direct mode and
  prints Lean source, while `--terminal-gate` is the general certification switch.
- **H0 cannot be ablated.** `StageProfile.h0_consistency` is `Literal[True]`; the historical no-H0 profile
  was removed and the sweep allowlist excludes it. H0 is a logical composition invariant, not a performance
  knob.
- **Review wiring is no longer ambiguous.** `stages.review=true` resolves both the DAG decomposition
  reviewer and the Ralph full-ledger judge. `review=false` disables both. `stages.judges` controls only the
  optional refinement tournament panel.
- **Certification trust is explicit and fail-closed.** `FormalizeAuditResult.authoritative` requires a
  compiling/passing dependency+axiom audit, a passing faithfulness result, and
  `certification_trusted=True` from both production components. Generic/scripted fixtures default to
  untrusted; live faithfulness wrappers are trusted only at unanimity; alternate persistent servers must
  expose the trusted audit interface. Certifying CLI modes reject `--no-faithfulness`.
- **Reporting is categorical.** The only labels are `rejected`, `candidate_incomplete`, `soft_proven`,
  `audited_not_certified`, and `authoritative_elementary`. The audited tier requires a proven result plus a
  completed audit; authority requires all lower logical prerequisites. Evolutionary fitness/Elo scores never
  promote reporting status. Certifying CLI invocations exit non-zero unless authority is true.
- **Budget scope is named precisely.** Profile `max_llm_calls` meters orchestrator search/review calls; it
  does not include nested terminal formalization/repair/faithfulness calls or OpenEvolve's internal
  generations. Terminal verification is separately bounded and reports `model_calls`; evolution is
  bounded/reported by its stage iteration count and per-subprocess ensemble timeout; and per-node Lean has
  an optional independent `max_node_verify_calls` cap. `max_replan_depth` is a distinct global cap from
  per-node `max_decomp_attempts`.
- **Evolution is supervised candidate generation.** `stages.evolve` feeds its champion into the ordinary
  prover pipeline as the first candidate; `evolve_witness` is diagnostic numeric construction search;
  `evolve_fallback` is a last-resort decomposer. None can mint `PROVEN` from fitness alone, and an enabled
  mode with a missing package/provider is rejected rather than mislabeled as an executed no-op. Each is
  bounded by its declared iterations, while `ensemble.timeout_s` bounds the Claude subprocesses.
- **Decorative/provider-incompatible controls are rejected.** `lean.server=true` must feed an authoritative
  terminal gate or per-node gate; `elementarity=none` requires every Lean flag to be false. Role
  `effort` is Codex-only and cannot coexist with a provider/fallback chain that might select Claude.
- **Lean/source and answer-grader boundaries are hardened.** The Lean bridge validates the requested local
  theorem, restricts imports/tokens, nonce-binds reports, revalidates packaged scaffolds, and constrains
  Windows process trees with aggregate-memory jobs. The answer checker uses a bounded allowlisted AST,
  typed collection semantics, exact rational comparison, finite numeric tolerance, maximum set matching,
  and structural SymPy equality; it does not run unbounded `simplify`/`equals` on untrusted answers.
- **Target-theory framing remains relative.** A certificate means dependency/axiom footprint ⊆ the
  stipulated, versioned fragment T, not a canonical mathematical definition of "elementary." See
  [`agent/gates/README.md`](../../agent/gates/README.md) and
  [`constraint_induction_2026-06-28.md`](constraint_induction_2026-06-28.md).

**Still open:** a measured held-out lift; default cross-model independence for faithfulness; a full
elaborated-AST `V_leg` pass beyond the implemented lexer-level boundary; an optional restricted-import
environment; v2 prover training; and enforcement of the declared `Toolkit.ruling()` boundary rulings.
