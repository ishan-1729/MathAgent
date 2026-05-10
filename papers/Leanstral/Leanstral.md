# Leanstral

Type: product + docs dossier

## What it is

Leanstral is Mistral AI's Lean-focused coding and proof-engineering model. The official framing is not generic mathematical reasoning but Lean 4 work inside realistic repositories: inspecting diagnostics and goals, editing declarations, repairing broken proofs, and using Lean as the final verifier. It is designed for proof engineering in context, not merely for benchmark-style theorem completion from isolated statements. ([Mistral release](https://mistral.ai/news/leanstral), [Leanstral model docs](https://docs.mistral.ai/models/leanstral-26-03))

## Status

Leanstral is publicly surfaced through Mistral Vibe, the Labs API model id `labs-leanstral-2603`, and downloadable weights. The model page lists 119B total parameters, 6.5B active parameters, and 256k context, while the release note says the weights are Apache 2.0. Mistral also says a technical report and FLTEval release are forthcoming; I did not find that report among the official sources checked here, so the public product story is currently richer than the technical paper trail. ([Mistral release](https://mistral.ai/news/leanstral), [Leanstral model docs](https://docs.mistral.ai/models/leanstral-26-03))

## Core design

Leanstral is best understood as the model layer in a three-part stack:

- Leanstral generates edits and proof steps.
- `lean-lsp-mcp` exposes structured access to the Lean project and language-server state.
- Lean itself checks whether the resulting code is valid.

That design is important because Lean proof engineering is not just next-token generation over source text. The hard part is interacting with current goals, diagnostics, imports, declaration boundaries, and local project structure. `lean-lsp-mcp` exposes precisely those surfaces: goal inspection, diagnostic messages, hover information, code actions, multi-attempt tactic search, and local search over the repository. Leanstral is explicitly trained to work well with that tool layer rather than acting as a free-floating model with only raw file text. ([Mistral release](https://mistral.ai/news/leanstral), [lean-lsp-mcp](https://github.com/oOo0oOo/lean-lsp-mcp))

## How it works in practice

In practical use, Leanstral behaves less like a standalone prover and more like a repo-aware proof engineer. A typical loop is:

1. inspect the current file, declaration, or broken proof
2. query Lean state through MCP tools
3. propose a local edit, tactic sequence, or declaration change
4. run Lean again to see the updated goals or errors
5. iterate until the proof or definition compiles cleanly

This loop is why Leanstral is better suited to proof repair, declaration shaping, migration work, and incremental formalization than to unconstrained theorem discovery. It has a short feedback cycle against a formal checker and is designed to exploit that feedback repeatedly. ([Mistral release](https://mistral.ai/news/leanstral), [lean-lsp-mcp](https://github.com/oOo0oOo/lean-lsp-mcp))

## Why the design helps

The design helps because it narrows the gap between model output and actual Lean semantics. Many failures in Lean workflows happen because a model can write something that looks plausible but has lost track of the current goal state, local namespace, import environment, or declaration context. A tool-aware model paired with the language server can work against the real project state instead of guessing from text alone. That makes the system more useful for day-to-day proof maintenance and local formalization work, where correctness depends on many small context-sensitive decisions. ([Mistral release](https://mistral.ai/news/leanstral), [lean-lsp-mcp](https://github.com/oOo0oOo/lean-lsp-mcp))

## Interfaces and usage surface

The public entry points are Mistral Vibe, the Labs API, and self-hosted weights. In practice, useful operation also depends on having a working Lean project and an MCP-capable client connected to `lean-lsp-mcp`. The MCP server documents tools such as `lean_goal`, `lean_diagnostic_messages`, `lean_hover_info`, `lean_code_actions`, `lean_multi_attempt`, and `lean_local_search`, which together define much of Leanstral's real operating surface. ([Mistral release](https://mistral.ai/news/leanstral), [Leanstral model docs](https://docs.mistral.ai/models/leanstral-26-03), [lean-lsp-mcp](https://github.com/oOo0oOo/lean-lsp-mcp))

## Strengths

- Built for Lean 4 proof engineering rather than generic mathematical chat.
- Explicitly optimized for tool-augmented repository work.
- Strong fit for proof repair, migration, local refactoring, and declaration-level iteration.
- Open-weight release and public API surface make it easier to study or integrate than many product-only systems.

## Limitations / risks

- Public technical details are still thinner than the product framing.
- The strongest evidence currently comes from Mistral's own release materials and case studies.
- Real value depends heavily on surrounding tooling: Lean project hygiene, language-server health, and MCP integration.
- Repo-level strength does not automatically imply strong open-ended theorem discovery or autonomous long-range proving.

## Sources

- Mistral AI. "Leanstral: Open-Source foundation for trustworthy vibe-coding." Mar. 16, 2026. [https://mistral.ai/news/leanstral](https://mistral.ai/news/leanstral)
- Mistral AI Docs. "Leanstral v26.03." [https://docs.mistral.ai/models/leanstral-26-03](https://docs.mistral.ai/models/leanstral-26-03)
- Oliver Dressler. `lean-lsp-mcp`. [https://github.com/oOo0oOo/lean-lsp-mcp](https://github.com/oOo0oOo/lean-lsp-mcp)
