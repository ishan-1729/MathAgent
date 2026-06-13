# ArXivMath NT run record — 2026-06-13

First live run on MathArena **ArXivMath** (`arxivmath-0326`, 30 problems). Grader: SymPy
answer-equivalence ([`agent/tools/answer_check.py`](../../../../agent/tools/answer_check.py)).
Model: **GPT-5.5 @ xHigh** via the Codex CLI.

> **Non-contaminative:** problem statements and gold answers are deliberately omitted here — only
> per-item correctness is recorded. The NT subset is hand-classified because the dataset ships empty
> `problem_type` labels (categories live only on matharena.ai).

## A. Vanilla GPT-5.5-xHigh (single-shot) — NT subset {7, 15, 17, 23, 28}

| idx | result |
|---|---|
| 7 | correct |
| 15 | correct |
| 17 | correct |
| 28 | correct |
| 23 | timeout (>1500 s/call, even solo) |

**Accuracy: 4/5 = 80.0%** (4/4 of completed; 1 timeout).

## B. GPT-5.5-xHigh + MathAgent harness — {7, 15, 17}

Harness = the Codex Autoreason incumbent tournament (critic → author → synthesizer → judge panel,
with PUCT + Bradley-Terry and an elementary-admissibility gate), 1 judge, 1 pass, refining the initial
Codex solution before the final answer is extracted.

| idx | result |
|---|---|
| 7 | correct |
| 15 | correct |
| 17 | correct |

**Accuracy: 3/3 = 100.0%.**

## C. Apples-to-apples on {7, 15, 17}

**Vanilla 3/3 vs harness 3/3 — tie (no regression).**

## Reading

- The full Codex tournament machinery (with PUCT + Bradley-Terry + admissibility gate) runs
  **end-to-end on live research problems** and preserved every correct answer — its **no-regression**
  property held.
- The base model is **at ceiling** on this NT subset (4/4 completed correct), so the harness had **no
  accuracy headroom** to demonstrate lift. The limiter is the dataset, not the harness: this release
  has very few number-theory items and vanilla solves them.
- A genuine "how much does the harness add" measurement requires a subset where **vanilla fails** —
  next step: sweep vanilla across the full 30 (or the harder aggregate items), then run the harness
  only on vanilla's failures.

## Caveats

- Our grader is SymPy-only; MathArena adds an LLM-judge fallback, so our absolute numbers may
  *undercount* equivalent-but-unparsed answers. The vanilla-vs-harness delta on the same grader is the
  reliable signal.
- The published GPT-5.5-xHigh **77.5%** is the *full 30-problem* number, not NT-only.
- #23 (1/n! in the Cantor set) is a hard timeout (>25 min/call) and was excluded from the harness run.
