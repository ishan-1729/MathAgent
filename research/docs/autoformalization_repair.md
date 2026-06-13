# Autoformalization: Lean-error repair loop + Mathlib retrieval

Raises the autoformalization success rate (the formalization wall) by iterating on Lean compiler
feedback and grounding lemma names in real Mathlib declarations.

## Deep dive: should Codex's "goal" mode own the loop? (No.)

Codex CLI has a `/goal` long-horizon agentic mode (config `features.goals = true`) and
`codex exec resume` for session continuity. `/goal` *plans, executes, checks its own output, and
self-corrects until done* — so it could, in principle, run the formalization+repair loop itself
(write Lean, run `lean`, read errors, fix). We evaluated delegating to it and chose **not** to:

- **Compile speed is decisive.** Our **persistent Lean server** audits in **~0.1s** (Mathlib loaded
  once). If Codex ran `lake env lean` itself, each compile reloads Mathlib (~60s) — a repair loop would
  be ~600× slower per iteration.
- **Headless friction.** `/goal` is interactive-first; headless goal resumption needs a fake prompt
  ([codex#24016](https://github.com/openai/codex/issues/24016)).
- **Control.** A Python-owned loop lets us inject the **denylist guidance**, **retrieved lemmas**, and a
  **deterministic budget**, and run the **Layer-4 audit + faithfulness** as the authority — none of
  which we can guarantee inside Codex's opaque goal loop.

**Decision:** a **Python-orchestrated repair loop** with Codex as the per-turn formalize/repair
generator (stateless, full context in the prompt — robust, and avoids session/`--ephemeral` friction;
`codex exec resume` remains a viable alternative for cross-turn memory but is unnecessary).

Sources: [Codex CLI features](https://developers.openai.com/codex/cli/features),
[/goal overview](https://www.mindstudio.ai/blog/openai-codex-goal-command-autonomous-tasks),
[codex#24016](https://github.com/openai/codex/issues/24016).

## The loop (`formalize_and_audit(..., repair_iters=N, retriever=...)`)

```
formalize (Codex) ─► compile+audit (persistent server, ~0.1s)
                         │ fail (LeanBridgeError carries the diagnostics)
                         ▼
                     retrieve real Mathlib lemmas (Loogle) for the error + claim
                         ▼
                     re-formalize (Codex): prior attempt + Lean errors + retrieved lemmas
                         └─► repeat up to N, then audit + faithfulness on success
```

| Piece | File |
| --- | --- |
| Repair loop | [`orchestrator/formalize_bridge.py`](../../agent/orchestrator/formalize_bridge.py) (`repair_iters`, `retriever`; `FormalizeAuditResult.attempts`) |
| Repair prompt | [`tools/formalizer.py`](../../agent/tools/formalizer.py) (`CodexFormalizer.formalize(prior_source, errors, lemmas)`) |
| Mathlib retrieval | [`tools/retrieval.py`](../../agent/tools/retrieval.py) (`LoogleRetriever`: Loogle JSON API; extracts unknown-identifier names from Lean errors + claim keywords; graceful no-network degradation) |

CLI: `python scripts/prove.py --direct --formalize --server --retrieval --repair 3 "<goal>"`
(or `--terminal-gate --server --retrieval --repair 3` in DAG mode).

## Why Loogle (not LeanSearch)
Loogle exposes a documented HTTP JSON API (`/json?q=`) returning `{name, type, module}`. The
highest-value query is the **unknown-identifier** from a compile error (Codex's hallucinated lemma →
the real nearby name), plus concept keywords from the claim. LeanSearch has no documented public API,
so it isn't a dependency.

## Live evidence (Lean 4.30.0 + Mathlib, Codex gpt-5.5)
- **Loogle** returned real Mathlib declarations for an unknown-identifier + keyword query
  (`Nat.add_zero`, `Int.add_zero`, …).
- **The repair loop recovered a failed formalization.** `full_verify` on *"For every integer n,
  n² − n is even"* (persistent server + retrieval + faithfulness, `repair_iters=3`): attempt #1 (an
  `omega`-based proof) failed to compile (Lean error-recovered a `sorry`); the loop fed the error back
  and **attempt #2** — a clean `Int.even_or_odd` case split closed by `ring` — **compiled, audited PASS,
  faithfulness 4/4 lenses, `authoritative_elementary=True`** (`attempts: 2`).
- This also validated the two correctness fixes the live run surfaced: a proof whose errors
  error-recover into `sorry` is now treated as a **compile failure** (so the loop repairs it, instead of
  silently emitting a `sorryAx`-rejected "compiled" proof), and the loop now **repairs on an audit
  reject** (incomplete/non-elementary), not only on hard compile errors.

## Caveats
- Repair improves the rate but does not guarantee compilation on hard NT statements; the loop reports
  honest failure with the last Lean error after exhausting the budget.
- Loogle is name/type-pattern based; semantic NL retrieval (a local embedding index over Mathlib, or a
  LeanSearch backend if a stable API appears) would help for claims with no obvious identifier overlap.
