# AutoResearch

Type: repo dossier

## What it is

`karpathy/autoresearch` is a compact autonomous experiment loop for machine-learning research. It is not a Lean theorem prover and does not claim to be Lean-native. Its core idea is to give an agent a small, fast, measurable training setup, let it modify a narrow part of the code, run a short experiment, keep the change only if the metric improves, and repeat. The specific training setup in the repository is a simplified single-GPU implementation of `nanochat`, so the repo is both a method demo and a concrete working example. Its importance is methodological: it packages a reusable outer loop for optimization and ablation. ([AutoResearch repo](https://github.com/karpathy/autoresearch), [`program.md`](https://github.com/karpathy/autoresearch/blob/master/program.md))

## Status

AutoResearch is public as a GitHub repo with a deliberately small code surface. The README documents Python 3.10+, `uv`, and a single NVIDIA GPU, and says the setup was tested on H100. I did not find a formal paper or technical report in the official materials checked here; the repository itself is the primary source of truth. ([AutoResearch repo](https://github.com/karpathy/autoresearch))

## Core design

The repo is organized around a narrow edit boundary. `prepare.py` handles fixed constants, data preparation, and utilities; `train.py` is the file the agent is allowed to modify; `program.md` is the human-written instruction document that defines the objective. The training run is intentionally short and standardized: five minutes of wall-clock time, with `val_bpb` as the main metric. This design is not incidental. It exists to make the feedback loop cheap enough that an agent can run many iterations and compare them meaningfully. ([AutoResearch repo](https://github.com/karpathy/autoresearch), [`program.md`](https://github.com/karpathy/autoresearch/blob/master/program.md))

## How it works in practice

The practical workflow is:

1. initialize the small training environment
2. give the agent a human-authored objective in `program.md`
3. let the agent edit only `train.py`
4. run a fixed-budget experiment
5. compare the resulting metric against the previous best
6. keep or discard the change, then repeat

This is a very deliberate reduction of the search space. By keeping the codebase small, the mutable surface narrow, and the metric explicit, AutoResearch makes autonomous iteration far more tractable than it would be in a large unconstrained repository. ([AutoResearch repo](https://github.com/karpathy/autoresearch), [`program.md`](https://github.com/karpathy/autoresearch/blob/master/program.md))

## Why the design helps

The design helps because autonomous optimization fails easily when the objective is vague, the experiment is slow, or the editable surface is too large. AutoResearch addresses all three failure modes at once:

- a small codebase keeps the agent from drowning in context
- a single primary metric provides a clear accept/reject signal
- a short experiment budget makes iteration cheap
- a stable instruction file separates human intent from agent edits

That is why the repo is useful beyond its immediate example. It offers a concrete pattern for "agent + measurable loop" systems even outside language-model training, provided the target domain can be reduced to fast repeatable experiments. ([AutoResearch repo](https://github.com/karpathy/autoresearch), [`program.md`](https://github.com/karpathy/autoresearch/blob/master/program.md))

## Setup / usage notes

The official quick start is `uv sync`, then `uv run prepare.py`, then `uv run train.py`, followed by launching an agent in the repo and pointing it at `program.md`. The repo assumes a single NVIDIA GPU and is intentionally narrow in scope; portability is not the main goal. The goal is to maintain a clean, repeatable optimization loop. ([AutoResearch repo](https://github.com/karpathy/autoresearch))

## Strengths

- Extremely clear edit boundary and experiment loop.
- Small enough to study as a method rather than a large engineering artifact.
- Good reference design for optimization systems with a fast scalar metric.
- Separates human objective specification from agent code modification cleanly.

## Limitations / risks

- Not Lean-native and not theorem-proving-specific.
- Assumes fast experiments and a scalar metric.
- GPU-specific setup limits portability.
- Autonomous hill-climbing is only as good as the metric being optimized.
- Many interesting research problems cannot be reduced to a five-minute measurable loop this cleanly.

## Sources

- Andrej Karpathy. `autoresearch`. [https://github.com/karpathy/autoresearch](https://github.com/karpathy/autoresearch)
- Andrej Karpathy. `program.md` in `autoresearch`. [https://github.com/karpathy/autoresearch/blob/master/program.md](https://github.com/karpathy/autoresearch/blob/master/program.md)
