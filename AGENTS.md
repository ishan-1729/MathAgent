# AGENTS.md

These instructions are durable guidance for Codex sessions in MathAgent.

Preserve the role-based top-level grouping (see `README.md` for the map):

- `agent/` — the system: `orchestrator/`, `roles/`, `gates/`, `tools/`, `instructions/`, `workflows/`.
- `knowledge/` — `methods/`, `library/`, `examples/`.
- `benchmarks/` — `problems/`, `evaluation/`.
- `formal/` — Lean. `research/` — `papers/`, `docs/`. `scripts/`, `.agents/` — infra/skills.

The build plan is `agent/PLAN.md`. The elementary-constraint enforcement design lives in `agent/gates/`.

Treat `research/papers/` as systems literature for brainstorming agent architectures, Lean tooling, workflow configurations, and evaluation designs. Do not treat it as part of the default mathematical context for benchmark proof attempts.

Do not add full proofs to target problem folders unless explicitly requested. Solved demonstrations belong in `knowledge/examples/`.

Treat final proof attempts as elementary unless a problem-specific file says otherwise. Final attempts must avoid reliance on UFDs, algebraic number theory, class groups, elliptic curves, modular forms, Catalan/Mihailescu, Baker theory, or heavy computational black boxes.

It is allowed to study standard or non-elementary methods for inspiration. Any final proof must be translated into permitted elementary language.

It is allowed to use `research/papers/` to design coherent workflow permutations, such as combinations of search, refinement, Lean feedback, proof-state tooling, and evaluator loops. Record which papers or software systems inspired a workflow, but keep the resulting workflow specification concise and testable.

Prefer concise Markdown with structured headings over long prose. Do not expand placeholders with invented theorems.

When adding a method, include trigger patterns, canonical transformations, examples, downstream moves, failure modes, and Lean-relevant lemmas.

When editing Lean files, keep them minimal and compilable if Lean is available.

Run `make check` after modifying repo structure or templates.
