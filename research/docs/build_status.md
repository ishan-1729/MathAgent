# MathAgent — Build Status

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

### 3.5 Orchestrator / harness (`agent/orchestrator/`)

| Component | File | Role | Status |
|---|---|---|---|
| Liveness (NodeState + Budget) | `agent/orchestrator/state.py` | State machine + hard caps (150 calls / 6 repairs / depth 2) | built+unit-tested |
| RunTrace | `agent/orchestrator/trace.py` | JSONL event log + run-record render | built+unit-tested |
| FlatDriver | `agent/orchestrator/driver.py` | Phase-1 linear plan→prove→gate→repair→judges | built+unit-tested |
| ProofDAG | `agent/orchestrator/dag.py` | AND-OR DAG, deep-hash memoization, acyclicity, assemble | built+unit-tested |
| RalphLoop | `agent/orchestrator/ralph.py` | Per-goal multi-episode loop, gate-rejects → lessons | built+unit-tested (indirect) |
| DagDriver | `agent/orchestrator/dag_driver.py` | direct→decompose→review→recurse; terminal gate hook | built+unit-tested |
| Population / Elo | `agent/orchestrator/population.py` | Candidate Elo, tournament, PUCT, Bradley-Terry MLE | built+unit-tested |
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
| prove.py | `scripts/prove.py` | Prover CLI; full flag surface; exit-2 if codex absent; pure wiring | built |
| check_repo_skeleton.py | `scripts/check_repo_skeleton.py` | `make check` validator | built |
| Makefile | `Makefile` | check/test/demo/all/tree | built |
| pyproject.toml | `pyproject.toml` | py≥3.11; pyyaml/sympy/jsonschema; pytest config | built |
| conftest.py | `conftest.py` | sys.path bootstrap for `import agent` | built |
| tests/ (24 files) | `tests/` | Full-stack suite (23 test modules + conftest) | built+unit-tested |
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

**Caveat:** the live Codex+Lean+Mathlib paths are **opt-in** (env-gated) and were **not re-executed** in the surveys; "LIVE-validated" rests on the persisted real-toolchain fixtures, the built REPL/Mathlib project, and prior validated runs documented in tests/docs — not a fresh run this session.

---

## 5. How to run

**Validate the repo & run the suite:**

```bash
make check     # scripts/check_repo_skeleton.py — PASS (21 dirs, 41 key files, frontmatter)
make test      # python -m pytest
make all       # check + test (CI target)
make demo      # agent/demo.py — gate + flat driver, no LLM / no network
```

Aggregate offline result across surveyed suites: **~210 tests passed, ~7 opt-in skipped** (gate/ledger/obligations/scanner/toolkit/lean_audit/terminal 68 + orchestrator/dag/dag_driver/population/faithfulness/trace 60 + numeric/retrieval/semantic/formalizer/codex set + lean_server/bridge — all deterministic, model & Lean stubbed).

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

---

## 6. Paper → component mapping

| Paper / system | Mapped component |
|---|---|
| **LEAP** | AND-OR DAG with memoization — `agent/orchestrator/dag.py`, `dag_driver.py` |
| **AlphaProof_Nexus** | Per-goal Ralph loop + population/Elo search — `agent/orchestrator/ralph.py`, `population.py` |
| **Autoreason** | Incumbent / "do-nothing" revision tournament (design-only; k=2 stop) — *planned in DagDriver* |
| **AlphaEvolve** | Population/Elo candidate-decomposition ranking — `agent/orchestrator/population.py` |
| **AXLE** | Adversarial statement-faithfulness panel — `agent/orchestrator/faithfulness.py` |
| **Loogle / LeanSearch** | Mathlib lemma retrieval for the repair loop — `agent/tools/retrieval.py` (+ BM25 `semantic_retrieval.py`) |
| **Pantograph / LeanDojo** | Lean compile/audit bridge + persistent REPL server — `agent/gates/lean_bridge.py`, `lean_server.py`, `lean/Audit.lean` |
| **AlphaProof (substitute)** | Codex GPT-5.5-xHigh focused prover — `agent/tools/codex_prover.py` |

---

## 7. Honest gaps & next steps

| # | Gap | Next step |
|---|---|---|
| 1 | **Autoformalization is the wall** — repair only recovered trivial targets (`n+0=n`, `n²−n`); no success demonstrated on hard NT. | Drive the formalize→repair loop on real benchmark problems; measure where formalization first fails. |
| 2 | **Benchmark suite is effectively one problem** — 5 of 6 folders are placeholders, and the live one (`x2_plus_1_eq_y3`) is self-marked contaminated/example-adjacent. **No run records exist.** | Flesh out the 5 placeholder problems; add a clean uncontaminated target; produce real scored run records. |
| 3 | **Retrieval is purely lexical** (BM25 + Loogle) — abbreviation/paraphrase gaps (e.g. "greatest common divisor" vs `gcd`). | Add neural / semantic embedding retrieval. |
| 4 | **Statement-faithfulness same-model caveat** — judges and prover can be the same model; no cross-model independence. | Use an independent judge model; widen lens panel. |
| 5 | **Incumbent / revision-control tournament not built** (Autoreason); PUCT + Bradley-Terry implemented but not wired into DagDriver selection; `max_replan_depth` tracked but unused. | Wire PUCT resampling and a first-class incumbent ("do nothing") tournament into the driver. |
| 6 | **`goal_hash` is lexical** (NFC + whitespace), not semantic — trivially-rephrased identical lemmas miss the memo cache. | Add semantic goal canonicalization. |
| 7 | **Live paths opt-in & not re-run**; `gate.py` deliberately does **not** invoke Layer 4 (chaining lives in `formalize_bridge`/`prove.py`); denylist/allowlist not asserted in sync with `Audit.lean` constant names. | Add a CI job that runs the live suite; add a sync test for denylist ↔ emitted constants. |
| 8 | **Train-a-prover is explicitly v2.** | Keep training-free harness as the v1 baseline; revisit after benchmark runs exist. |