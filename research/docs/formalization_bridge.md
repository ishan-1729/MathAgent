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

## Honest caveats / what's next

- **Autoformalization is the wall.** Codex handled the trivial target; harder NT statements will often
  fail to compile or be mis-stated. The bridge reports those honestly; raising the formalization
  success rate (better prompts, retrieval of Mathlib lemma names, a repair loop on Lean errors, feeding
  the ledger steps as a Lean proof skeleton) is the main open work.
- **Statement faithfulness** is unchecked — a compiling `ma_target` could formalize the *wrong*
  statement. Needs an equivalence/expert check (PLAN §3.4, §8.3) before "authoritative" is trustworthy.
- **Speed:** each `import Mathlib` audit loads all of Mathlib (~40-60s). Fine for validation; a
  persistent Lean server or narrower imports would speed a real loop.
- The formalize/audit loop is **not yet wired into the DAG driver** (it runs on a final ledger); making
  Layer-4 the terminal gate of a DAG run is the natural next step.
