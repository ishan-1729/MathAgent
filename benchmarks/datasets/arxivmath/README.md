# ArXivMath dataset adapter (MathArena)

**ArXivMath** is MathArena's arXiv-derived **final-answer** benchmark (ETH SRI Lab + INSAIT). Problems
are reverse-engineered from very recent arXiv papers and refreshed monthly, so the set is
**contamination-resistant** and postdates most training cutoffs. Vanilla **GPT-5.5-xHigh scores ~77.50%**
on the `arxivmath-0326` (March 2026) release — a non-saturated target for final-answer baselines and the
optional answer-refinement loop. The current runner does **not** execute the typed-ledger/Layer-4 proof
harness, so no full-harness lift is claimed from these records.

- Source / leaderboard: <https://matharena.ai/arxivmath/>
- Platform paper: *Beyond Benchmarks: MathArena …* — [arXiv:2605.00674](https://arxiv.org/abs/2605.00674)
- Code: <https://github.com/eth-sri/matharena> (MIT) · Data: HuggingFace `MathArena/*` (**CC BY-SA 4.0**)

## Schema (per item)

| field | meaning |
|---|---|
| `problem_idx` | item id |
| `problem` | the statement (LaTeX) — **the only thing a solver sees** |
| `answer` | gold final answer (held out for grading) |
| `problem_type` | category labels, e.g. `["Number Theory"]` / `Algebra` / `Geometry` / `Combinatorics` |
| `source` | arXiv id of the originating paper (metadata — **never** put in the prompt) |

There is **no worked-solution field** — only the final `answer` ships.

## Non-contaminative usage (enforced by the adapter)

The adapter [`agent/benchmarks/arxivmath.py`](../../../agent/benchmarks/arxivmath.py):

- `ArxivMathDataset.problems()` returns `Problem` objects with **only** `idx`, `statement`,
  `problem_type` — there is no `answer`/`source` on a `Problem`, so a solver cannot see them.
- gold answers come from a separate `oracle()` map used only by the SymPy grader
  ([`agent/tools/answer_check.py`](../../../agent/tools/answer_check.py)).
- downloaded releases (which contain answers) are cached **outside the repo** (HF cache or a chosen
  `--cache-dir`) and are **never committed**. This folder ships only the adapter, this README, the
  manifest, and a tiny **synthetic** fixture (`fixtures/sample.jsonl`, not real ArXivMath data) for
  offline tests.

## Run it

```sh
# offline plumbing check against the synthetic fixture (no network, no model):
python -m pytest tests/test_arxivmath.py -q

# live: download a release from HuggingFace and solve with Codex (needs codex on PATH +
# mathagent[benchmark]); NT subset only, write a run record:
python scripts/run_benchmark.py --hf-config arxivmath-0326 --nt-only --out benchmarks/datasets/arxivmath/runs
```

Runs written with `--out` checkpoint every completed row and a separate fsynced receipt. If the
process stops, stderr prints the retained `*.jsonl.incomplete` path. Resume with the same dataset,
filter, and solver flags plus that path, for example:

```sh
python scripts/run_benchmark.py --hf-config arxivmath-0326 --nt-only \
  --resume benchmarks/datasets/arxivmath/runs/arxivmath_0326_<stamp>.jsonl.incomplete
```

`--resume` fails before any model call unless the receipt, dataset hash and row prefix, solver mode,
Git tree fingerprint, and Python/package versions all match. It reruns only the unpaid suffix. At
completion the Markdown summary and JSONL are both prepared and fsynced; the summary is published
first and the JSONL is the commit point, so this runner never publishes a final JSONL without its
summary.

The receipt and row checks detect accidental corruption and inconsistent rewrites; they are not a
cryptographic signature against a filesystem owner who deliberately rewrites the receipt and every
dependent row. The configured model/mode and Python/package versions are recorded, but the external
Codex executable's build version is not currently machine-attested.

See `manifest.yaml` for the default release and subset.
