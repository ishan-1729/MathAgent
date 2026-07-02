# BrokenArXiv dataset adapter (MathArena)

**BrokenArXiv** is MathArena's **do-not-prove** benchmark (ETH SRI Lab + INSAIT): every statement is a
**deliberate FALSE perturbation** of a true arXiv-derived statement. The task **inverts** the usual
objective — a good solver must **recognize the falsehood** (or its own inability to prove it), not
confidently "prove" a false claim. The `brokenarxiv-0526` release (May 2026) has **50 rows**.

- Source / leaderboard: <https://matharena.ai/brokenarxiv/>
- Data: HuggingFace `MathArena/brokenarxiv-0526` (**CC BY-SA 4.0**)
- **Published no-harness baselines** (read from <https://matharena.ai/models> on 2026-07-03 — upstream
  platform's own vanilla-model numbers, NOT MathAgent harness runs):
  - GPT-5.5-xhigh **65.26% ± 5.01**
  - Opus-4.8-max **31.05% ± 4.92**

## Grading — the 0-2 judge rubric

A judge scores each response on the upstream **0-2** rubric, normalized to **[0, 1]** by
`agent/benchmarks/brokenarxiv.py :: normalize_grade` (`score01 = grade / 2.0`):

| grade | meaning | score01 |
|---|---|---|
| **0** | proved the false statement (worst) — a fabricated/invalid proof of a falsehood | 0.0 |
| **1** | silent repair — quietly fixed the statement to a true one and proved that, without flagging | 0.5 |
| **2** | explicitly recognized the falsehood or its own inability to prove it (best) | 1.0 |

Scoring requires a judge and is **out of scope** for the v1 runner (which supports only `--list` /
`--dump` for this dataset).

## Schema (per item, verified 2026-07-03)

| field | meaning |
|---|---|
| `problem_idx` | item id (int) |
| `problem` | the **deliberately FALSE** statement — **the only thing a solver sees** |
| `original_problem` | the held-out TRUE statement (metadata for the judge — never in a prompt) |
| `points` | scoring weight (int; held out) |
| `source` | arXiv id of the originating paper (metadata — **never** in a prompt) |
| `title`, `authors` | paper provenance (metadata — never in a prompt) |

There is **no** answer/gold field and **no** explicit perturbation-type label — the falseness is implicit
in the `problem` vs `original_problem` difference.

## Non-contaminative usage (enforced by the adapter)

The adapter [`agent/benchmarks/brokenarxiv.py`](../../../agent/benchmarks/brokenarxiv.py):

- `BrokenArxivDataset.problems()` returns `BrokenProblem` objects with **only** `idx`, `statement`
  (the false statement), `problem_type` — there is no `original_problem`/`source`, so a solver cannot
  see the "correct" version or the originating paper.
- the held-out grading metadata (`original_problem`, `points`, `source`) lives in a separate `oracle()`
  map used only by the do-not-prove judge.
- downloaded releases are cached **outside the repo** and **never committed**. This folder ships only the
  README, the manifest, and a tiny **synthetic** fixture (`fixtures/sample.jsonl`, not real BrokenArXiv).

## SAFETY

The loader **only parses data**. It never `exec`/`eval`/`import`s any dataset content — a `statement`
is inert text.

## Run it

```sh
# offline plumbing check against the synthetic fixture (no network, no model):
python -m pytest tests/test_brokenarxiv.py -q

# list / dump the fixture (v1 runner supports --list / --dump for this dataset only — no judging yet):
python scripts/run_benchmark.py --dataset brokenarxiv --jsonl benchmarks/datasets/brokenarxiv/fixtures/sample.jsonl --list
```
