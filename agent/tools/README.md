# agent/tools/

Adapters used by the live roles and orchestrator. Model adapters are not deterministic; they are
subprocess-scoped, return structured results, and remain downstream of deterministic admission checks.
Numeric, parsing, retrieval-index, and Lean-audit components are deterministic for fixed inputs/toolchains.

Current adapters (see `../PLAN.md` §4.3 and §6):

| Tool | Purpose | Phase |
| --- | --- | --- |
| **Numeric / witness search** | Bounded exact-integer checks for witnesses, residue covers, descent decreases, and small cases. Untrusted expressions are converted from a small allowlisted AST; no model text is executed. | built |
| **Codex focused prover** (`codex_prover.py`) | GPT-5.5-xHigh via `codex exec` as an optional focused prover / decomposer / reviewer. `RolesProfile` and the shipped YAML profiles default the prover to Claude/opus; the bare legacy CLI instead constructs Codex role specs. The registry resolves whichever spec it receives. See [`../../research/docs/codex_harness.md`](../../research/docs/codex_harness.md). | built |
| **Elementary auditor** | The layered gate (see `../gates/`): validate the informal method ledger, conservatively validate model-authored Lean source, then run the proof-term dependency/axiom audit. The full elaborated-AST `V_leg` pass is not built. | built, with the documented AST defense deferred |
| **Retrieval** | Loogle + BM25 (default lexical path; optional neural leg inert without `mathagent[neural]`) over a curated elementary **Mathlib** subset only. `knowledge/` and `agent/instructions/` are **never** loaded into any prompt or index. | built (`retrieval.py`) |
| **Lean bridge** | Compile + `#audit` extraction (`../gates/lean_bridge.py`) and the persistent Mathlib REPL server (`../gates/lean_server.py`). LOAD-BEARING: this is the authoritative Layer-4 path, not advisory. | built + live |
| **Final-answer checker** | Conservatively compare bounded LaTeX-ish scalar and collection answers without `eval`, unbounded `simplify`, or greedy set matching. | built (`answer_check.py`) |

> Search scores and ordinary tool results are not certificates. Only a trusted terminal path that compiles,
> passes the Layer-4 dependency/axiom audit, and passes statement faithfulness may emit
> `authoritative_elementary`.
