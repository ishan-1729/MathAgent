# MathAgent harness console (local web UI)

A zero-dependency (stdlib only) web UI that drives the harness. Type a problem, pick the levers, and it
runs `scripts/prove.py` in the backend and streams the output live.

```sh
python ui/server.py        # then open http://127.0.0.1:8765
# or:
make ui
```

It binds to `127.0.0.1` only and builds the subprocess argv as a list (the typed problem is passed
after `--`), so a typed problem can't inject shell commands or flags. It runs the **real** harness, so
each run makes live Codex calls (and, when certifying, Lean/Mathlib compiles) — `codex` must be on PATH.

## The levers (each maps to a `prove.py` flag)

- **Model / effort / mode** — `--model`, `--effort {low,medium,high,xhigh}`, Direct vs DAG (`--direct`).
- **Formalize + Layer-4 certify** (the headline checkbox) — off = informal proof only; on = formalize to
  Lean → compile → **proof-term dependency/axiom audit** → faithfulness (`--terminal-gate`/`--formalize`,
  `--server`, `--faithfulness`, `--repair N`). The badge shows `certified-elementary: yes/no`.
- **Search / revision** — `--refine` (Autoreason tournament), `--population K`, `--judges N`.
- **Retrieval** — `--retrieval` (Loogle+BM25), `--neural` (bi-encoder), `--rerank`.
- **Budget** — `--max-depth`, `--max-decomp`, `--episodes`, `--budget`, `--max-replan`, `--timeout`.

`GET /selftest` streams a stub subprocess to confirm the SSE plumbing without invoking Codex.

Tests: `python -m pytest tests/test_ui_server.py` (argv mapping + streaming, no model calls).
