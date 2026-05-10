# Papers and Workflow Research

The `papers/` folder is a systems-literature area. It is for brainstorming how MathAgent should search, refine, verify, and evaluate proof attempts.

## What belongs here

- Lean-native theorem-proving systems and interfaces.
- Agentic research loops and evolutionary search systems.
- Informal-to-formal bridge designs.
- Evaluation, self-correction, and anti-cheating protocols.
- Notes on software that could be permuted into coherent workflow configurations.

## How to use it

Use paper notes to design workflows in `workflows/`, not as hidden mathematical facts in `problems/`.

Good workflow questions:

- Which component proposes informal strategies?
- Which component checks elementary compliance?
- Which component generates Lean statements?
- Which component reads Lean diagnostics?
- Which evaluator scores novelty, step complexity, generalizability, and Lean compilability?

## Guardrails

- Keep benchmark problem folders free of copied paper examples unless intentionally seeded.
- Record paper/software influences in run records.
- Compare workflows only when the problem, allowed methods, and metric weights are fixed.
- Treat paper claims as architecture references, not as verified MathAgent results.
