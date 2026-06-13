# Ledger → Lean formalization bridge + Mathlib setup

Closes the loop: an informal, gate-passed step-ledger is formalized into Lean, compiled, and run
through the **Layer-4 dependency audit** — the only place "elementary" is *enforced*, not merely
pressured.

## Pipeline

```
step-ledger ──► gate (Layers 0-3) ──► CodexFormalizer ──► lake env lean (compile) ──► Layer-4 audit ──► verdict
 (informal)      pressured-elementary    informal→Lean        Mathlib project          dependency/axiom    authoritative
```

| Piece | File | Role |
| --- | --- | --- |
| Formalizer | [`agent/tools/formalizer.py`](../../agent/tools/formalizer.py) | `CodexFormalizer` asks GPT-5.5-xHigh for a Lean 4 theorem (named `ma_target`) + complete proof from the ledger; parses the fenced Lean block + declaration name. `ScriptedFormalizer` for tests. |
| Bridge | [`agent/orchestrator/formalize_bridge.py`](../../agent/orchestrator/formalize_bridge.py) | `formalize_and_audit` (formalize → compile → audit) and `full_verify` (informal gate **and** Lean audit → `authoritative_elementary`). Honest: a non-compiling proof → `compiled=False` with the diagnostics, never a silent pass. |
| Compile/audit env | [`agent/gates/lean_bridge.py`](../../agent/gates/lean_bridge.py) | Runs `lake env lean` inside the Mathlib project so `import Mathlib` resolves; hoists all imports to the file top (extractor's `import Lean` + the proof's `import Mathlib`). |
| Mathlib project | [`formal/lean/mathagent_formal/`](../../formal/lean/mathagent_formal/) | Lean 4.30.0 + Mathlib `v4.30.0` lake project (the audit environment). Only small files committed; `.lake/` (~5GB) git-ignored — run `lake exe cache get`. |
| CLI | [`scripts/prove.py`](../../scripts/prove.py) `--formalize` | `python scripts/prove.py --direct --formalize "<goal>"` proves a ledger then formalizes + audits it. |

## Live validation (Lean 4.30.0 + Mathlib)

- **Full end-to-end** `full_verify` on `For all integers n, n + 0 = n.`:
  `[informal] passed_deterministic | [lean] formalize: ok, compiled, lean-audit: pass | authoritative_elementary=True`.
  Codex produced `import Mathlib; theorem ma_target (n : Int) : n + 0 = n := by simp`, it compiled in the
  Mathlib project, and the dependency audit passed.
- **Denylist, live against Mathlib:** `import Mathlib; theorem ma_dedekind : IsDedekindDomain Int → True := fun _ => trivial`
  → **REJECT** (`denylisted_dependency: 'IsDedekindDomain'`). An elementary `import Mathlib` proof
  (`n+0=n by simp`) → **PASS** (the allowlists don't over-reject Mathlib's elementary plumbing).
- **Core (no Mathlib):** `n+0=n := Nat.add_zero n` → PASS; a `sorry` proof → REJECT (axiom gate).

## Added: faithfulness check, DAG terminal gate, persistent server

| Addition | File | What it does |
| --- | --- | --- |
| **Adversarial faithfulness panel** | [`faithfulness.py`](../../agent/orchestrator/faithfulness.py) + `CodexFaithfulnessChecker` | Several independent judges (back-translation, quantifiers/domain, vacuity, strength lenses) each told to FIND a discrepancy and default to *unfaithful*; the statement is accepted only if no lens objects. `authoritative` now requires `elementary_verified` AND `faithful`. |
| **Layer 4 as the DAG terminal gate** | `DagDriver(terminal_gate=...)` + `make_terminal_gate` + `ProofDAG.proof_bundle` | After the DAG proves the root, the assembled proof is formalized → compiled → Layer-4 audited → faithfulness-checked. `DagResult.authoritative_elementary` reflects it. |
| **Persistent Lean server** | [`lean_server.py`](../../agent/gates/lean_server.py) (drives leanprover-community/repl, built into the Mathlib project) | Loads Mathlib + `#audit` **once**, then reuses the environment. `lean_bridge`/`formalize_bridge` accept a `server=`. |

**Live evidence (Lean 4.30.0 + Mathlib):**
- Persistent server: startup (Mathlib load) **75.8s**, then `audit#1` (elementary) **0.13s**, `audit#2`
  (denylisted) **0.03s** — a >500× speedup vs per-call `lake env lean`.
- Full stack `full_verify` (with server + faithfulness panel) on `n+0=n`:
  `[informal] passed_deterministic | formalize ok, compiled, lean-audit pass, faithfulness[4/4 lenses faithful], authoritative=True`.
- CLI: `python scripts/prove.py --terminal-gate --server --faithfulness "<goal>"`.

## Honest caveats / what's next

- **Autoformalization is still the wall.** Codex handled trivial targets; harder NT statements will
  often fail to compile or be mis-stated. The bridge reports those honestly; raising the success rate
  (Lean-error repair loop, Mathlib lemma retrieval, feeding the ledger as a Lean proof skeleton) is the
  main open work.
- **Faithfulness judges share the prover's model family** (Codex) — the panel uses diverse lenses to
  mitigate shared blind spots, but a different-model judge would be stronger; the protocol allows it.
- The persistent server keeps **one** Mathlib env; a crash requires a restart (the wrapper raises and
  callers can fall back to per-call). Narrower imports would cut the 76s startup.
