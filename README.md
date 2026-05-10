# MathAgent

MathAgent is a research repository for agentic discovery of elementary Diophantine proofs and later Lean formalization. It is a collaboration between Ishan and Kieren, with room for multiple AI workflows to explore separate branches.

The repo is organized around four core artifact types:

- `methods/`: reusable proof techniques, one method per file.
- `library/`: small high-impact identities, lemmas, and transformations.
- `problems/`: target theorem folders with statements, allowed inputs, attempts, Lean notes, and evaluation records.
- `examples/`: solved demonstrations that teach how a method is used.

Supporting areas include `workflows/` for agent protocols, `evaluation/` for scoring, `formal/` for Lean-facing artifacts, and `papers/` for systems literature about theorem-proving agents, Lean tooling, search, refinement, and research automation.

Examples and problems are intentionally separated. Examples may contain solved demonstrations and method skeletons. Problems should usually avoid full standard proofs, so workflow evaluations are not contaminated by copied solutions.

The `papers/` folder is for brainstorming coherent software/workflow configurations and benchmarking plans. It should influence workflow design and evaluation infrastructure, not silently expand the mathematical facts available to a problem run.

**First Tasks**
1. Fill method files.
2. Add high-impact Tagebuch identities.
3. Create target problem statements.
4. Run workflows on separate branches.
5. Score attempts using evaluation metrics.
6. Use `papers/` to design and compare workflow configurations.

Run the skeleton check with:

```sh
make check
```
