"""Run the ArXivMath benchmark non-contaminatively and write a run record.

The solver sees ONLY each problem's statement; gold answers stay in the held-out oracle and are used
only by the SymPy grader. By default this uses a bare Codex answer-solver (a baseline ~ vanilla
GPT-5.5); the elementary-proof harness + Layer-4 gate can later be slotted in behind the `Solver`
protocol to measure how much the harness adds.

Examples:
  python scripts/run_benchmark.py --jsonl benchmarks/datasets/arxivmath/fixtures/sample.jsonl --dry
  python scripts/run_benchmark.py --hf-config arxivmath-0326 --nt-only --out benchmarks/datasets/arxivmath/runs
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Dataset statements carry Unicode math (e.g. `∣`, `≤`, Greek); make stdout/stderr UTF-8 so --list /
# --dump don't crash on a legacy Windows codepage (cp1252).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

from agent.benchmarks.arxivmath import ArxivMathDataset, run_benchmark


def _extract_final_answer(msg: str) -> str:
    m = re.search(r"FINAL ANSWER:\s*(.+?)\s*$", msg, re.IGNORECASE | re.MULTILINE)
    if m:
        return m.group(1).strip()
    lines = [ln.strip() for ln in msg.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


class NullSolver:
    """--dry: returns nothing (every item scored wrong). Smoke-tests the pipeline without a model."""

    def solve(self, statement: str) -> str:
        return ""


_SOLVE_PROMPT = (
    "Solve the following number-theory problem using ONLY elementary methods (the IMO toolkit: "
    "divisibility, congruences, gcd/coprimality, descent, bounding, parity, induction). Reason "
    "carefully, then end your reply with exactly one line:\nFINAL ANSWER: <answer>\nwhere <answer> is "
    "only the final value (a number, closed-form expression, or set).\n\nProblem:\n{statement}"
)


class CodexAnswerSolver:
    """Bare Codex final-answer solver — the **vanilla GPT-5.5-xHigh** baseline. Sees only the statement."""

    def __init__(self, cfg):
        self.cfg = cfg

    def solve(self, statement: str) -> str:
        from agent.tools.codex_prover import _run_codex
        return _extract_final_answer(_run_codex(_SOLVE_PROMPT.format(statement=statement), self.cfg))


class HarnessAnswerSolver:
    """**GPT-5.5-xHigh + the MathAgent harness.** Codex produces an elementary solution, then the Codex
    Autoreason incumbent tournament (critic → author → synthesizer → judge panel, with PUCT +
    Bradley-Terry) refines it under a **prose denylist filter** (a candidate whose text trips a
    non-elementary prose smell cannot displace the incumbent); we then extract the final answer.
    NOTE: this is only a lowercase substring filter over `prose_terms` — NOT the authoritative
    elementary gate (no typed ledger, no `gate.evaluate`, no Lean Layer-4 audit). It is a soft
    admissibility heuristic for this final-answer benchmark; proof certification lives in `prove.py`.
    Measures what the harness adds over the bare model. Still sees only the statement
    (non-contaminative)."""

    def __init__(self, cfg, *, n_judges: int = 1, passes: int = 2):
        from agent.gates.toolkit import load_toolkit
        from agent.tools.codex_prover import make_codex_refiner
        self.cfg = cfg
        self._deny = load_toolkit().prose_terms          # lowercased non-elementary prose smells
        self.refiner = make_codex_refiner(cfg, n_judges=n_judges, max_passes=passes)

    def _elementary_ok(self, candidate: str) -> bool:
        # Soft prose denylist filter only (substring scan) — see the class docstring; this is NOT the
        # authoritative Layer-4 elementary gate.
        low = candidate.lower()
        return not any(term in low for term in self._deny)

    def solve(self, statement: str) -> str:
        from agent.tools.codex_prover import _run_codex
        incumbent = _run_codex(_SOLVE_PROMPT.format(statement=statement), self.cfg)
        refined = self.refiner.refine(statement, incumbent, is_admissible=self._elementary_ok)
        return _extract_final_answer(refined.content)


def _run_list_dump(dataset_name: str, args) -> int:
    """--list / --dump support for the non-answer datasets (arxivlean, brokenarxiv, mathnet).

    These datasets are NOT runnable end-to-end yet: arxivlean needs a Lean checker, brokenarxiv needs a
    do-not-prove judge, and the MathNet answer-run path is not wired. So the v1 runner only inspects them
    — it never pretends to "run" what it cannot grade. Solver flags (--dry/--harness/model/effort) are
    rejected here so nothing is silently ignored."""
    for bad in ("dry", "harness"):
        if getattr(args, bad, False):
            print(f"ERROR: --{bad} is not supported for --dataset {dataset_name} "
                  f"(only --list / --dump; proof/Lean/false-statement running arrives in a later phase).",
                  file=sys.stderr)
            return 2
    if not (args.list or args.dump):
        print(f"ERROR: --dataset {dataset_name} supports only --list or --dump for now "
              f"(proof/Lean/false-statement running arrives in a later phase).", file=sys.stderr)
        return 2

    if dataset_name == "arxivlean":
        from agent.benchmarks.arxivlean import ArxivLeanDataset as _DS
        stmt_attr = "formal_statement"
    elif dataset_name == "brokenarxiv":
        from agent.benchmarks.brokenarxiv import BrokenArxivDataset as _DS
        stmt_attr = "statement"
    elif dataset_name == "mathnet":
        from agent.benchmarks.mathnet import MathNetDataset as _DS
        stmt_attr = "statement"
    else:  # pragma: no cover - guarded by argparse choices
        print(f"ERROR: unknown dataset {dataset_name}", file=sys.stderr)
        return 2

    if args.jsonl:
        dataset = _DS.from_jsonl(args.jsonl)
    else:
        try:
            if dataset_name == "mathnet":
                dataset = _DS.from_huggingface(args.hf_config or "all", args.split, args.cache_dir)
            else:
                dataset = _DS.from_huggingface(args.hf_config, args.split, args.cache_dir)
        except Exception as e:
            print(f"ERROR: could not load {dataset_name} ({args.hf_config}): {e}", file=sys.stderr)
            print("  (install with: pip install 'mathagent[benchmark]')", file=sys.stderr)
            return 2

    problems = dataset.problems()
    print(f"# {dataset_name} [{dataset.release}] — {len(dataset)} raw items, "
          f"{len(problems)} after default filtering (solver never sees held-out oracle fields)")
    for p in problems:
        text = getattr(p, stmt_attr, "")
        if args.dump:
            print(json.dumps({"idx": p.idx, stmt_attr: text}))
        else:
            print(f"  {p.idx}: {text[:100]!r}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Inspect / run MathAgent benchmark datasets (non-contaminative).",
        epilog="NOTE: only --dataset arxivmath runs end-to-end (final-answer solving + SymPy grading). "
               "arxivlean/brokenarxiv/mathnet support ONLY --list / --dump for now — Lean proving, "
               "do-not-prove judging, and MathNet answer-running arrive in a later phase.")
    ap.add_argument("--dataset", choices=["arxivmath", "arxivlean", "brokenarxiv", "mathnet"],
                    default="arxivmath",
                    help="which dataset to use (default: arxivmath — the only one that runs end-to-end; "
                         "the other three support only --list / --dump)")
    ap.add_argument("--list", action="store_true",
                    help="(non-answer datasets) list problem idx + statement and exit")
    ap.add_argument("--dump", action="store_true",
                    help="(non-answer datasets) dump problem idx + statement as JSONL and exit")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--jsonl", help="local JSONL release (e.g. the synthetic fixture)")
    src.add_argument("--hf-config", help="HuggingFace release config, e.g. arxivmath-0326")
    ap.add_argument("--split", default="train")
    ap.add_argument("--cache-dir", default=None, help="HF cache dir (keep OUTSIDE the repo)")
    ap.add_argument("--nt-only", action="store_true", help="number-theory subset only (v1 scope)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry", action="store_true", help="use a null solver (no model) to smoke-test")
    ap.add_argument("--model", default="gpt-5.5")
    ap.add_argument("--effort", default="xhigh")
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument("--out", default=None, help="directory to write the run record into")
    ap.add_argument("--harness", action="store_true",
                    help="use the full MathAgent harness solver (Codex + Autoreason tournament) "
                         "instead of the vanilla single-shot baseline")
    ap.add_argument("--n-judges", type=int, default=1, help="(harness) Codex judges per tournament pass")
    ap.add_argument("--passes", type=int, default=2, help="(harness) refinement passes")
    args = ap.parse_args()

    if args.dataset != "arxivmath":
        return _run_list_dump(args.dataset, args)

    if args.list or args.dump:
        print("ERROR: --list / --dump apply to the non-answer datasets "
              "(--dataset arxivlean|brokenarxiv|mathnet), not arxivmath.", file=sys.stderr)
        return 2

    if args.jsonl:
        dataset = ArxivMathDataset.from_jsonl(args.jsonl)
    else:
        try:
            dataset = ArxivMathDataset.from_huggingface(args.hf_config, args.split, args.cache_dir)
        except Exception as e:
            print(f"ERROR: could not load MathArena/{args.hf_config}: {e}", file=sys.stderr)
            print("  (install with: pip install 'mathagent[benchmark]')", file=sys.stderr)
            return 2

    if args.dry:
        solver, mode = NullSolver(), "null"
    else:
        from agent.tools.codex_prover import CodexConfig, CodexProver
        if not CodexProver.available():
            print("ERROR: codex CLI not found on PATH (use --dry to smoke-test).", file=sys.stderr)
            return 2
        cfg = CodexConfig(model=args.model, reasoning_effort=args.effort, timeout_s=args.timeout)
        if args.harness:
            solver = HarnessAnswerSolver(cfg, n_judges=args.n_judges, passes=args.passes)
            mode = f"harness({args.model}/{args.effort}, judges={args.n_judges}, passes={args.passes})"
        else:
            solver, mode = CodexAnswerSolver(cfg), f"vanilla({args.model}/{args.effort})"

    print(f"# ArXivMath [{dataset.release}] — {len(dataset)} items "
          f"({'NT only' if args.nt_only else 'all categories'}) — mode: {mode}")

    def _tick(item):
        mark = "ERR" if item.error else ("OK " if item.correct else "xx ")
        print(f"  {mark}{item.idx}: {item.predicted[:70]!r}", flush=True)

    report = run_benchmark(dataset, solver, number_theory_only=args.nt_only,
                           limit=args.limit, on_result=_tick)

    print("\n" + report.summary())

    if args.out:
        stamp = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
        outdir = Path(args.out)
        outdir.mkdir(parents=True, exist_ok=True)
        base = outdir / f"arxivmath_{dataset.release}_{stamp}"
        base.with_suffix(".jsonl").write_text(report.to_jsonl(), encoding="utf-8")
        summary = (f"# ArXivMath run record\n\nrelease: {dataset.release}\nmode: {mode}\n"
                   f"nt_only: {args.nt_only}\n"
                   f"accuracy: {report.accuracy:.4f} ({report.n_correct}/{report.total})\n"
                   f"timestamp: {stamp}\n\n{report.summary()}\n")
        base.with_suffix(".md").write_text(summary, encoding="utf-8")
        print(f"\n# wrote {base.with_suffix('.jsonl')} and {base.with_suffix('.md')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
