# AGENTS.md

These instructions are durable guidance for Codex sessions in MathAgent.

Preserve the separation of `methods/`, `library/`, `examples/`, `problems/`, `instructions/`, `evaluation/`, and `formal/`.

Do not add full proofs to target problem folders unless explicitly requested. Solved demonstrations belong in `examples/`.

Treat final proof attempts as elementary unless a problem-specific file says otherwise. Final attempts must avoid reliance on UFDs, algebraic number theory, class groups, elliptic curves, modular forms, Catalan/Mihailescu, Baker theory, or heavy computational black boxes.

It is allowed to study standard or non-elementary methods for inspiration. Any final proof must be translated into permitted elementary language.

Prefer concise Markdown with structured headings over long prose. Do not expand placeholders with invented theorems.

When adding a method, include trigger patterns, canonical transformations, examples, downstream moves, failure modes, and Lean-relevant lemmas.

When editing Lean files, keep them minimal and compilable if Lean is available.

Run `make check` after modifying repo structure or templates.
