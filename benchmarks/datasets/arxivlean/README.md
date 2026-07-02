# ArXivLean dataset adapter (MathArena)

**ArXivLean** is MathArena's arXiv-derived **Lean-proof** benchmark — the formal sibling of ArXivMath
(ETH SRI Lab + INSAIT). Each item ships a Lean 4 `formal_statement`, and the task is to **produce a Lean
proof that closes it**. The `arxivlean-0326` release (March 2026) has **41 problems**.

- Source / leaderboard: <https://matharena.ai/arxivlean/>
- Data: HuggingFace `MathArena/arxivlean-0326` (**CC BY-SA 4.0**)
- **Published no-harness baseline:** GPT-5.5-xhigh **17.07% ± 11.52** (read from
  <https://matharena.ai/models> on 2026-07-03 — this is the upstream platform's own vanilla-model number,
  NOT a MathAgent harness run).

## Toolchain / compile-compatibility

Upstream `arxivlean-0326` targets **Lean v4.29.0**; our toolchain is **Lean 4.30**. We do **not** assume
proofs compile cross-version. Compile-compatibility is a **per-problem** fact the runner records for each
attempt (did it compile under our 4.30 toolchain?), never a dataset-wide assumption (`manifest.yaml`
→ `toolchain.compile_compat: per-problem`).

## Schema (per item, verified 2026-07-03)

| field | meaning |
|---|---|
| `problem_idx` | item id (int) |
| `problem` | natural-language statement (metadata; solver sees the formal one) |
| `formal_statement` | the Lean 4 statement to close — **the thing a solver is handed** |
| `answer` | gold column (held out for grading); in this release it holds the target Lean `theorem …` |
| `source` | arXiv id of the originating paper (metadata — **never** in a prompt) |
| `title`, `authors` | paper provenance (metadata — never in a prompt) |

There is **no** `problem_type` category field, so the NT-subset filter simply matches nothing on this
dataset (harmless).

## Non-contaminative usage (enforced by the adapter)

The adapter [`agent/benchmarks/arxivlean.py`](../../../agent/benchmarks/arxivlean.py):

- `ArxivLeanDataset.problems()` returns `LeanProblem` objects with **only** `idx`, `formal_statement`,
  `problem_type` — there is no `answer`/`source`/`title`/`authors`, so a solver cannot see the intended
  answer or the originating paper.
- gold answers come from a separate `oracle()` map; source ids from `sources()` — both held out.
- downloaded releases are cached **outside the repo** and **never committed**. This folder ships only the
  README, the manifest, and a tiny **synthetic** fixture (`fixtures/sample.jsonl`, not real ArXivLean).

## SAFETY

The loader **only parses data**. It never `exec`/`eval`/`import`s any dataset content — a
`formal_statement` is inert Lean text, handed to the (out-of-band) Lean checker as data.

## Run it

```sh
# offline plumbing check against the synthetic fixture (no network, no model):
python -m pytest tests/test_arxivlean.py -q

# list / dump the fixture (v1 runner supports --list / --dump for this dataset only — no proving yet):
python scripts/run_benchmark.py --dataset arxivlean --jsonl benchmarks/datasets/arxivlean/fixtures/sample.jsonl --list
```

Grading (Lean compilation) is out of scope for the v1 runner; see `manifest.yaml`.
