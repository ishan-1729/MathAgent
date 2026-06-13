# agent/tools/

Adapters the swarm can call as tools. Tools are deterministic, side-effect-scoped, and return structured
results the orchestrator can act on.

Planned tools (see `../PLAN.md` §4.3 and §6):

| Tool | Purpose | Phase |
| --- | --- | --- |
| **Numeric / witness search** | Enumerate integer solutions, residues, descent seeds, Pell solutions, small-case checks (a Python/SymPy sandbox). Grounds conjectures and kills false statements early. | v1 |
| **Codex focused prover** (`codex_prover.py`) | GPT-5.5-xHigh via `codex exec` as the focused prover / decomposer / reviewer — the AlphaProof substitute in the DAG harness. See `research/docs/codex_harness.md`. | built |
| **Elementary auditor** | The hard gate (see `../gates/`): for the informal track, check the proof's method ledger against the denylist; for the Lean track, run the proof-term dependency audit + AST legality. | v1 (informal) / later (Lean) |
| **Retrieval** | Embed a goal and fetch relevant `knowledge/methods/` and `knowledge/library/` entries (elementary corpus only). | v1 |
| **Lean bridge** | Persistent Lean REPL (Pantograph/LeanDojo-style): submit, read structured diagnostics + goal-at-error, extract `sorry` subgoals, export the proof term. | later (advisory) |
| **CAS check** | Symbolic sanity checks of identities/algebra used in a proof. | v1 (optional) |

> Tool results are advice, not proof. Only the hard gate in `../gates/` accepts or rejects a final artifact.
