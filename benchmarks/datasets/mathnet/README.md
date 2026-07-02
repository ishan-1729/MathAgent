# MathNet dataset adapter (MIT)

**MathNet** (`ShadenA/MathNet`, MIT) is a large topic-tagged final-answer math dataset. The `all` config
has **~27.8k rows**. For MathAgent v1 we take the **Number Theory** slice in **English**.

- Source / leaderboard: <https://mathnet.mit.edu>
- Data: HuggingFace `ShadenA/MathNet`, config `all` (**CC BY 4.0**)
- **Published no-harness baselines** (read from <https://mathnet.mit.edu> on 2026-07-03 — upstream
  project's own vanilla-model numbers, NOT MathAgent harness runs):
  - Gemini-3.1-Pro **78.4%**
  - GPT-5 **69.3%**
  - Opus-4.6 **45.7%**

## Filtering — Number Theory + English

The loader keeps a row iff:
- one of its `topics_flat` paths **starts with** `Number Theory` (head segment matched
  case-insensitively; paths use `" > "` as the separator, e.g. `"Number Theory > Divisibility > gcd"`),
  **and**
- its `language` is English. Upstream `language` is **nullable** — a row with a null/empty language is
  **not** dropped (we only drop a row whose language is set and differs). Pass `topic_prefix=None` or
  `language=None` to disable either filter.

## Schema (per item, verified 2026-07-03)

| field | meaning |
|---|---|
| `id` | item id (string slug) |
| `problem_markdown` | the statement — **the only thing a solver sees** |
| `final_answer` | gold final answer (held out for grading) |
| `topics_flat` | flat **list of strings**, each a `" > "`-delimited hierarchical topic path |
| `language` | language tag (string; **nullable**) |
| `problem_type` | e.g. `"proof and answer"` / `"answer"` |
| `solutions_markdown`, `images`, `country`, `competition` | metadata (never in a prompt) |

Grading is SymPy answer-equivalence (`agent/tools/answer_check.py`), matching ArXivMath.

## Non-contaminative usage (enforced by the adapter)

The adapter [`agent/benchmarks/mathnet.py`](../../../agent/benchmarks/mathnet.py):

- `MathNetDataset.problems()` returns `Problem` objects with **only** `idx`, `statement`, `topics`,
  `language` — there is no `final_answer`/`solutions_markdown`, so a solver cannot see the gold answer
  or a worked solution.
- gold answers come from a separate `oracle()` map, held out for the SymPy grader.
- downloaded data is cached **outside the repo** and **never committed**. This folder ships only the
  README, the manifest, and a tiny **synthetic** fixture (`fixtures/sample.jsonl`, not real MathNet).

## SAFETY

The loader **only parses data**. It never `exec`/`eval`/`import`s any dataset content.

## Run it

```sh
# offline plumbing check against the synthetic fixture (no network, no model):
python -m pytest tests/test_mathnet.py -q

# list / dump the NT+English fixture slice:
python scripts/run_benchmark.py --dataset mathnet --jsonl benchmarks/datasets/mathnet/fixtures/sample.jsonl --list
```
