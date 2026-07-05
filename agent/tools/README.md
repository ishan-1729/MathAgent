# agent/tools/

Adapters the swarm can call as tools. Tools are deterministic, side-effect-scoped, and return structured
results the orchestrator can act on.

Planned tools (see `../PLAN.md` §4.3 and §6):

| Tool | Purpose | Phase |
| --- | --- | --- |
| **Numeric / witness search** | Enumerate integer solutions, residues, descent seeds, Pell solutions, small-case checks (a Python/SymPy sandbox). Grounds conjectures and kills false statements early. | v1 |
| **Codex focused prover** (`codex_prover.py`) | GPT-5.5-xHigh via `codex exec` as the OPTIONAL focused prover / decomposer / reviewer (the registry default is Claude/opus — see `../orchestrator/registry.py`). See `research/docs/codex_harness.md`. | built |
| **Elementary auditor** | The hard gate (see `../gates/`): for the informal track, check the proof's method ledger against the denylist; for the Lean track, run the proof-term dependency audit + AST legality. | built (both tracks; Lean Layer-4 is the authoritative gate) |
| **Retrieval** | Loogle + BM25 (default lexical path; optional neural leg inert without `mathagent[neural]`) over Mathlib + `knowledge/` (elementary corpus only). | built (`retrieval.py`) |
| **Lean bridge** | Compile + `#audit` extraction (`../gates/lean_bridge.py`) and the persistent Mathlib REPL server (`../gates/lean_server.py`). LOAD-BEARING: this is the authoritative Layer-4 path, not advisory. | built + live |
| **CAS check** | Symbolic sanity checks of identities/algebra used in a proof. | v1 (optional) |

> Tool results are advice, not proof. Only the hard gate in `../gates/` accepts or rejects a final artifact.
