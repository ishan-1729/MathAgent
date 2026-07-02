# MathAgent — Build Status

> **Snapshot:** `2026-06-14T18:58:00+05:30` · base commit `05cb1c3` · stamp `8f849a0d603b1807`
> (`stamp = sha256(timestamp | full-commit-sha)[:16]` — identifies the exact state this doc describes;
> regenerate on every edit so a stale doc is detectable.) See the canonical
> **[`system_design.md`](system_design.md)** for the architecture this status tracks against.
>
> **Refreshed 2026-07-03** (bounded truth-up; see the [2026-07-03 addendum](#8-2026-07-03-addendum) for
> what landed since the base snapshot). The **§5** figure of "380 tests" is the 2026-06-14 state; the
> offline suite has since grown to **1117 collected / 11 skipped** (the ~380→1117 growth is the P0–P3
> roadmap remediation, the modular RunProfile control plane, per-node Lean, and the ArXivMath/UI/registry
> suites). The counts and gap statuses below are annotated inline where they moved.

> Status vocabulary: **data/docs** (curated, non-executable) · **built** (code present, no test asserted here) · **unit-tested** (offline deterministic tests pass) · **LIVE-validated** (exercised against real Lean/Mathlib/Codex) · **partial** (wired but not end-to-end run) · **planned** (placeholder only).

---

## 1. What it is

MathAgent is a **training-free agentic harness** that attempts to prove **elementary number-theory** theorems and — crucially — to *prove they are elementary*. Its defining mechanism is a dual gate: a deterministic + soft check on an informal "step-ledger," backed by a non-gameable Lean proof-term **dependency/axiom audit** that enforces the load-bearing insight **"Lean-verified ≠ elementary."** The repository is a role-based scaffold (knowledge base + benchmarks + agent harness + Lean formal layer + research dossiers) tied together by a skeleton checker and a CLI prover.

### Locked v1 decisions

| Decision | Commitment |
|---|---|
| **Harness** | Training-free first; Codex (GPT-5.5 @ xHigh) stands in for AlphaProof. No model training in v1. |
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
| **HIGH 3** | HARD gate = dependency audit + restricted env + V_leg AST legality | **Partial.** Dependency-closure + axiom-whitelist audit **built + live**; restricted-import env **intentionally skipped** (memo: coarse/brittle, backstopped by the audit); **V_leg AST legality deferred** → gap #8. |
| **HIGH 4** | Self-correction repair + Autoreason incumbent tournament | **Built.** Repair loop **live**; **Autoreason tournament now built + wired** (`tournament.py`; PUCT + Bradley-Terry; `max_replan_depth` consumed). |
| **MED 5** | Retrieval bias to an elementary index | **Built.** Loogle + BM25 + **neural bi-encoder (`bge-small-en-v1.5`)** via `HybridRetriever`. |
| **MED 6** | Construction/witness finder (Axplorer) | **Partial** — `numeric.py` grounding only. A population search-as-tool now exists as an **optional** path: the OpenEvolve ledger-evolution backend (`tools/openevolve_bridge.py`, gate-scored, no-exec), but it is a `Decomposer`, not a numeric-construction finder. |
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
 │ PROVER ROLE (Codex GPT-5.5-xHigh)  →  JSON step-ledger                 │
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
 ┌──────────────── CODEX HARNESS (agent/orchestrator) ───────────────────┐
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
        AUTHORITATIVE VERDICT  =  compiled  AND  audit.passed  AND  faithful
                       (the only place "elementary" is enforced)
```

---

## 3. Component status table

### 3.1 Knowledge & benchmarks (`knowledge/`, `benchmarks/`)

| Component | File | Role | Status |
|---|---|---|---|
| Root README | `README.md` | v1 elementary mission, role-based repo map, examples-vs-problems rationale | data/docs |
| AGENTS.md | `AGENTS.md` | Durable Codex guidance; forbidden final-step list; method-file authoring contract | data/docs |
| Methods README + TEMPLATE | `knowledge/methods/README.md` | Methods contract; indexes 14 method files | data/docs |
| Method: descent | `knowledge/methods/descent.md` | Gate-integrated; 3 descent obligations + JSON shape; Lean lemmas (status: draft) | data/docs |
| Method: Vierzahlensatz | `knowledge/methods/vierzahlensatz.md` | Four-numbers parameterization pipeline (status: seed) | data/docs |
| Library README + 8 files | `knowledge/library/README.md` | Compact identities; `untriaged_tagebuch/` inbox (empty) | data/docs |
| Examples (Vierzahlensatz) | `knowledge/examples/vierzahlensatz/kieren_mse_x2_plus_1_eq_y3.md` | Worked method pipeline, no full proof (source of contamination mark) | data/docs |
| Problems README + TEMPLATE | `benchmarks/problems/README.md` | Per-problem folder contract | data/docs |
| Problem: x2_plus_1_eq_y3 | `benchmarks/problems/x2_plus_1_eq_y3/problem.md` | Only fully-specified target; status: contaminated/example-adjacent | data/docs |
| 5 other problem folders | `benchmarks/problems/imo_1988_finite_descent/README.md` (+4) | Placeholder READMEs only — no problem.md/inputs/attempts | **planned** |
| Evaluation metrics | `benchmarks/evaluation/metrics.md` | 5 scoring axes | data/docs |
| Evaluation rubric | `benchmarks/evaluation/rubric.yaml` | Binary elementary gate → weighted (corr .30/nov .25/step .25/gen .20); Lean track 1.0 | data/docs |
| Run-record template | `benchmarks/evaluation/run_record_TEMPLATE.md` | Run-record fields (no real records yet) | data/docs |
| Skeleton checker | `scripts/check_repo_skeleton.py` | Validates 21 dirs / 41 key files + {name,type,status} frontmatter | **built+LIVE-validated** (PASS) |
| Makefile | `Makefile` | check/test/demo/all/tree targets | built+LIVE-validated |
| CONTRIBUTING.md | `CONTRIBUTING.md` | Branch-per-experiment workflow; proof-status vocabulary | data/docs |

### 3.2 Elementary gate — Layers 1–3 (`agent/gates/`)

| Component | File | Role | Status |
|---|---|---|---|
| Gate README | `agent/gates/README.md` | Dual-gate rationale; 4 ranked hard gates | data/docs |
| allowed_toolkit.yaml | `agent/gates/allowed_toolkit.yaml` | ~40-key closed justification vocab + boundary_rulings | data/docs |
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
| Tools README | `agent/tools/README.md` | Tools are deterministic advisers; status table is stale | data/docs |
| numeric | `agent/tools/numeric.py` | Exact-integer SymPy witness search (L3 grounding) | built+unit-tested |
| Codex roles | `agent/tools/codex_prover.py` | Prover/Decomposer/Reviewer/Comparator/Faithfulness via `codex exec` | built+unit-tested (model stubbed) |
| Formalizer | `agent/tools/formalizer.py` | Ledger → Lean 4 (fresh + repair prompts) | built+unit-tested |
| Loogle retrieval | `agent/tools/retrieval.py` | Mathlib lemma names via Loogle HTTP; graceful degrade | built+unit-tested |
| Semantic retrieval | `agent/tools/semantic_retrieval.py` | Local BM25 + HybridRetriever over elementary Mathlib subset | built+unit-tested |
| Neural retrieval | `agent/tools/neural_retrieval.py` | Dense bi-encoder (`bge-small-en-v1.5`) + optional cross-encoder reranker; graceful degrade; offline `HashingEmbedder` for tests | built+unit-tested (model stubbed); **live opt-in** |
| Answer checker | `agent/tools/answer_check.py` | SymPy answer-equivalence (symbolic / numeric / set-tuple / string) for final-answer benchmarks | built+unit-tested |
| OpenEvolve bridge | `agent/tools/openevolve_bridge.py` | Evolve proof-sketch **ledgers** (MAP-Elites) scored by the deterministic gate as fitness oracle; mutations driven by a real **AlphaEvolve-style LLM ensemble** via the `claude` CLI — **Sonnet = breadth** (fast, high sampling weight, many candidates) + **Opus = depth** (stronger, low weight, occasional high-quality), mirroring AlphaEvolve's Flash/Pro split; reads ledger as text — **never exec/eval/imports** the artifact; `OpenEvolveBackend` is a `Decomposer` for `DagDriver`. **Optional dep** (`pip install mathagent[evolve]`); `available()` probes without importing; offline-testable via `StubEvolveLLM`. Ranks/filters only — does **not** certify elementary (only Layer 4 does). | built+unit-tested *(optional-dep)* |

### 3.5 Orchestrator / harness (`agent/orchestrator/`)

| Component | File | Role | Status |
|---|---|---|---|
| Liveness (NodeState + Budget) | `agent/orchestrator/state.py` | State machine + hard caps (150 calls / 6 repairs / depth 2) | built+unit-tested |
| RunTrace | `agent/orchestrator/trace.py` | JSONL event log + run-record render | built+unit-tested |
| FlatDriver | `agent/orchestrator/driver.py` | Phase-1 linear plan→prove→gate→repair→judges | built+unit-tested |
| ProofDAG | `agent/orchestrator/dag.py` | AND-OR DAG, **semantic** deep-hash memoization (`canonical_form`), acyclicity, assemble | built+unit-tested |
| RalphLoop | `agent/orchestrator/ralph.py` | Per-goal multi-episode loop, gate-rejects → lessons | built+unit-tested (indirect) |
| DagDriver | `agent/orchestrator/dag_driver.py` | direct→decompose→review→recurse; terminal gate hook; **refiner hook + `max_replan_depth` consumed + PUCT/BT candidate selection** | built+unit-tested |
| Population / Elo | `agent/orchestrator/population.py` | Candidate Elo, tournament, PUCT, Bradley-Terry MLE (now driven by the driver + tournament) | built+unit-tested |
| Revision tournament | `agent/orchestrator/tournament.py` | **Autoreason incumbent tournament**: critic→author→synth→blind panel; do-nothing wins ties; k=2 stop; margin gate; PUCT + Bradley-Terry; elementary-admissibility guard | built+unit-tested |
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
| prove.py | `scripts/prove.py` | Prover CLI; full flag surface (`--neural`/`--rerank` added); exit-2 if codex absent | built |
| run_benchmark.py | `scripts/run_benchmark.py` | Non-contaminative ArXivMath CLI; vanilla `CodexAnswerSolver` + `--harness` (Codex Autoreason tournament) solver; per-item error isolation | **built+LIVE-validated** (real GPT-5.5-xHigh run) |
| ArXivMath adapter | `agent/benchmarks/arxivmath.py` | Loader (statement-only `Problem` + held-out `oracle`) + `run_benchmark` + run records | built+unit-tested |
| ArXivMath dataset dir | `benchmarks/datasets/arxivmath/` | README (provenance/license/CC BY-SA 4.0), `manifest.yaml`, synthetic `fixtures/sample.jsonl` | data/docs |
| CI workflow | `.github/workflows/ci.yml` | Offline job (skeleton + suite, py3.11/3.12) always-on; **live-Lean suite on `workflow_dispatch`** with `.lake` cache | built |
| check_repo_skeleton.py | `scripts/check_repo_skeleton.py` | `make check` validator | built |
| Makefile | `Makefile` | check/test/demo/all/tree | built |
| pyproject.toml | `pyproject.toml` | py≥3.11; pyyaml/sympy/jsonschema; pytest config | built |
| conftest.py | `conftest.py` | sys.path bootstrap for `import agent` | built |
| tests/ (33 files) | `tests/` | Full-stack suite (32 test modules + conftest) | built+unit-tested |
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
| **Full certification pipeline authoritative on real NT theorems** (2026-06-13) | `prove.py --terminal-gate --server --faithfulness --retrieval --repair 3` (GPT-5.5-xHigh): **`n²≡0,1 mod 4`** and **`√2 irrational` (x²=2y², infinite descent)** both went prove→formalize→compile→**Layer-4 audit PASS (0 rejects)**→faithfulness 4/4→`authoritative_elementary=True`. **2/3** of the ladder certified; `3∣a²+b²⇒3∣a∧3∣b` was proven but its `decide`-based formalization failed to compile → honestly `authoritative=False` (gap #1). See [`live_certification_runs.md`](live_certification_runs.md). |
| **Vanilla vs harness on ArXivMath NT (final-answer)** | Live GPT-5.5-xHigh: vanilla 4/5 (80%); harness {7,15,17} 3/3 — no regression. The Codex Autoreason tournament (PUCT+BT+admissibility) ran end-to-end. See [`runs/2026-06-13_nt_vanilla_vs_harness.md`](../../benchmarks/datasets/arxivmath/runs/2026-06-13_nt_vanilla_vs_harness.md). |

**Caveat:** most live paths are still opt-in (env-gated). **This session DID re-execute** the certification pipeline and the ArXivMath comparison live with GPT-5.5-xHigh + Lean/Mathlib (rows above); the remaining "LIVE-validated" claims rest on persisted real-toolchain fixtures + prior validated runs.

---

## 5. How to run

**Validate the repo & run the suite:**

```bash
make check     # scripts/check_repo_skeleton.py — PASS (21 dirs, 41 key files, frontmatter)
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

> **2026-07-03 update:** the offline suite is now **1117 collected / 11 opt-in skipped** (measured
> `python -m pytest` on 2026-07-03; the 11 skips are the same env-gated live paths). The clean-tree pass
> count is ~1099; the working tree was mid-refactor at measurement time (uncommitted registry/run-profile
> WIP), which introduces test-ordering-sensitive failures under random ordering — those are WIP artifacts,
> not documented-behavior regressions, and do not reflect a committed red suite. Growth ~380→1117 is the
> P0–P3 remediation, RunProfile control plane, per-node Lean (P0–P4 + `LEAN_VERIFIED`), ablation harness,
> and the ArXivMath/UI/registry/supervisor suites.

**Run the prover (requires the external `codex` CLI on PATH; `--server`/Mathlib audits need a built ~5 GB `.lake`):**

```bash
python scripts/prove.py --terminal-gate --server --retrieval --repair 3 --faithfulness "<goal>"
```

Key flags: `--model gpt-5.5` · `--effort xhigh` · `--direct` (single Ralph loop) vs DAG · `--max-depth/--max-decomp/--episodes/--budget/--timeout` · `--formalize` · `--out` (ledger/trace JSONL). Hard-fails exit 2 if `CodexProver.available()` is false. Returns 0 if proven, else 1.

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
  for `--terminal-gate`/`--formalize` (`--no-faithfulness` = explicitly audited-only/non-authoritative).
- **Lean Layer-4 audit integrity:** nonce-bound `#audit` sentinel + duplicate-sentinel rejection +
  forbidden-command stripping + theorem-name cross-check (defeats self-reported / stale / injected audits);
  prefix-anchored allow/deny precedence; persistent-server teardown + error isolation.
- **RCE removed** from the numeric gate and the answer grader (no `sympify`/`eval`; locked `parse_expr`).
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
Keep flagged as a documented limitation. FlatDriver
informal `claim==problem` and ledger `conclusion==claim` remain conditional (robust binding is in the
orchestrator via `goal_hash`); a shared `canonical_form` between gates and orchestrator is the clean
refactor; a regression test pinning the Lean shadow-elaborator class is advisable.

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
| **AlphaProof (substitute)** | Codex GPT-5.5-xHigh focused prover — `agent/tools/codex_prover.py` |

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
- ✅ **Semantic `goal_hash` (was gap #6)** — `canonical_form` folds notation/synonyms/spacing
  (`n²−n` ≡ `n^2 - n`, `∀`≡`for all`, `∣`≡`divides`) **conservatively**: it never renames variables or
  reorders operands (a false memo hit would be *unsound*), with soundness guards tested
  (`x∣y` ≠ `y∣x`).
- ✅ **CI + Layer-4 sync (was gap #7)** — `.github/workflows/ci.yml` runs the offline suite on every
  push (py3.11/3.12) and the **live-Lean suite on manual `workflow_dispatch`** (cached `.lake`);
  `test_layer4_sync.py` ties the `Audit.lean` JSON contract ↔ Python parser ↔ denylist YAML together
  and pins that `gate.evaluate` does **not** invoke Layer 4 (the intentional separation).
- ✅ **ArXivMath first live run (advances gap #2)** — non-contaminative adapter + SymPy grader, run
  **live with GPT-5.5-xHigh** on the hand-classified NT subset of `arxivmath-0326`. **Vanilla 4/5
  (80%, 1 timeout); harness {7,15,17} 3/3 = 100%; apples-to-apples 3/3 vs 3/3 — no regression.** The
  Codex Autoreason tournament (PUCT + Bradley-Terry + admissibility gate) ran end-to-end on real
  research problems. Record: [`benchmarks/datasets/arxivmath/runs/2026-06-13_nt_vanilla_vs_harness.md`](../../benchmarks/datasets/arxivmath/runs/2026-06-13_nt_vanilla_vs_harness.md).
  *No lift shown — the base model is at ceiling on this tiny NT subset (the dataset, not the harness,
  is the limiter).*

### Remaining

| # | Gap | Next step |
|---|---|---|
| 1 | **Autoformalization wall moved (was the T1-rung miss; now IMO-hard multi-step).** The specific 2026-06-14 miss (`3∣a²+b²⇒3∣a∧3∣b`, brittle `decide`-over-`Fin`/`ZMod` → `Decidable`-synthesis failure) is **CLOSED**: `ClaudeFormalizer` produces a compiling proof using `decide` on the finite `ZMod 3` core only, bridged via `ZMod.intCast_zmod_eq_zero_iff_dvd`, and the winning tactic discipline is encoded in `Formalizer._RULES`. The reach map (live) shows residue casework **and** infinite descent reliably covered at the T1 rung. See [`live_certification_runs.md`](live_certification_runs.md) (2026-06-28 reach map). | Genuine frontier is now **IMO-hard multi-step** proofs (Vieta jumping, coprime-factorization Diophantine, sum-of-two-squares descent), which stress *proof-finding*, not just formalization. Scale the ladder there. |
| 2 | **No *lift* measured yet** — first live run done (vanilla 80% / harness 3/3 non-regression), but vanilla is at ceiling on this tiny NT subset so the harness had no headroom. | Sweep vanilla across the full 30 (or the harder aggregate items) to find **failures**, then run the harness only on those — the only place lift can show. |
| 4 | **Statement-faithfulness same-model caveat** — judges/prover can be the same model; no cross-model independence. | Use an independent judge model; widen the lens panel. |
| 8 | **V_leg AST-legality gate deferred** (LongCat HARD #2: statement-mutation / `unsafe`/`macro`/redefinition / smuggled `import`) — a deterministic AST legality check distinct from the dependency audit. **Deliberately deferred.** | Build the V_leg-style AST legality pass as a coarse first filter backstopping the Layer-4 dependency audit (PLAN §5 Layer 4 item 3). |
| 9 | **Restricted-import Lean environment not built** — intentionally skipped (memo: coarse/brittle, backstopped by the audit). | Optional later filter; the dependency audit remains the authoritative gate. |
| 10 | **Train-a-prover is explicitly v2.** | Keep the training-free harness as the v1 baseline; revisit after benchmark runs exist. |

> Numbering note: #3/#5/#6/#7 are resolved above; #2 is narrowed; #8 is the newly-tracked deferred
> V_leg gate; #9/#10 are carried forward. Gap #1 (autoformalization) + #2 (real run records) are the
> two that actually block a first headline result — the search/retrieval/gate machinery is now in place.

---

## 8. 2026-07-03 addendum

Bounded truth-up. The base snapshot (§1–§7) is 2026-06-14; the items below landed since and are
grounded in current code. This section adds pointers rather than rewriting the history above.

**Landed since the base snapshot:**

- **P0–P3 roadmap remediation — done.** Both report roadmaps (forge §7 + OpenEvolve §9) built across
  6 remediation rounds; board [`docs/goals/roadmap-p0-p3/state.yaml`](../../docs/goals/roadmap-p0-p3/state.yaml)
  reports `status: done` with oracle conditions 1+2 holding (the one residual is an *accepted*,
  Layer-4-bounded, search-signal-only trivial-cover spoof that never yields a false certificate).
- **Modular RunProfile control plane.** One declarative `RunProfile` (config-as-data) +
  fail-closed supervisor (`validate_profile`) + Claude-default role registry drives the pipeline;
  the elementarity toggle is `{none, soft, authoritative}` (`ElementarityLevel` in
  `agent/orchestrator/run_profile.py`; level→gate-wiring via `elementarity_policy.policy_for`). Ablation
  harness (`profiles/ablation/`, `scripts/ablate.py`) shipped.
- **Per-node Lean (P0–P4) + first-class `LEAN_VERIFIED`.** Opt-in per-leaf verifier and AND-node
  sketch-composition check are live-validated; the P5 `NodeState.LEAN_VERIFIED` hard-success state has
  landed (`agent/orchestrator/state.py:18`; set by `dag.mark_proven_direct(..., lean_verified=True)` and
  the P4 composition rule at `dag.py:390-395`; reported by `scripts/prove.py:606`). See
  [`live_certification_runs.md`](live_certification_runs.md).
- **Robustness: model-call exceptions no longer crash `DagDriver.run()`.** All live prover/decomposer/
  reviewer/verifier call sites are wrapped and classify a raise as a failed attempt (`unknown_tool_error`);
  regression-tested (`test_ralph.py`, `test_dag_driver.py`). See
  [`live_certification_runs.md`](live_certification_runs.md) ("Robustness gaps surfaced").
- **Denylist `GaussianInt`/`Zsqrtd` fix.** The 2026-06-28 adversarial probe (`x²+1=y³` → an LLM reached
  for non-elementary `ℤ[i]` UFD, which Layer-4 *admitted*) exposed a denylist gap; `GaussianInt`, `Zsqrtd`,
  `Mathlib.NumberTheory.Zsqrtd` are now in `lean_denylist_decls`
  ([`agent/gates/denylist.yaml:67-69`](../../agent/gates/denylist.yaml)) and the `ℤ[i]` proof now REJECTS.
- **Target-theory framing documented.** The Layer-4 certificate is now stated as "footprint ⊆ a stipulated,
  versioned fragment T," not a canonical "elementary" property — see
  [`agent/gates/README.md`](../../agent/gates/README.md) ("Target theory T") and
  [`constraint_induction_2026-06-28.md`](constraint_induction_2026-06-28.md) (open question #1).

**Still open (unchanged from §7):** #2 (no measured lift / held-out eval set), #4 (faithfulness cross-model
independence), #8 (V_leg AST-legality gate), #9 (restricted-import env), #10 (v2 training), and the §5
`Toolkit.ruling()`/`boundary_rulings` spec-only follow-up.