#!/usr/bin/env python3
"""Deterministic witness-check battery runner (Mode B; NO LLM, NO eval/exec).

Reads a directory of JSON witness-spec files and runs each through the exact-integer checker
:func:`agent.tools.numeric.check_witness_spec` (which parses every embedded expression with the
restricted no-eval integer AST). For each spec it writes one JSONL row recording the outcome:

    {"spec": <filename>, "valid_schema": bool, "confirmed": bool, "score": float, "error": str|null}

A REJECTED control (a spec encoding an object outside the integer-only AST — real coordinates,
complex entries) is a RECORDED outcome, not a crash: the checker returns ``ok=False`` with a set
``error``, and this runner records that row and moves on. Nothing here is ever eval/exec/import'd;
the spec is data handed to the checker.

Usage:
    python scripts/run_witness_checks.py --specs-dir benchmarks/evaluation/witness_specs \
        --out benchmarks/evaluation/witness_specs/RESULTS.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make the repo importable when run as a script (scripts/ is a sibling of agent/).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.tools.numeric import check_witness_spec  # noqa: E402
from agent.tools.openevolve_bridge import score_witness_spec  # noqa: E402


def check_spec_file(path: Path) -> dict:
    """Run one spec file through the checker and return its recorded JSONL row (never raises).

    ``valid_schema`` is True iff the checker did NOT record a schema/validation error (i.e. the spec
    was representable in the integer-only AST). ``confirmed`` is the checker's ``ok`` verdict, and
    ``score`` is the shared MAP-Elites fitness (1.0 on confirm, 0.0 otherwise). A spec whose text is
    itself unreadable/unparseable is recorded as an error row, not a crash."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001 - an unreadable file is a recorded outcome, not a crash
        return {"spec": path.name, "valid_schema": False, "confirmed": False,
                "score": 0.0, "error": f"could not read spec file: {e}"}

    result = check_witness_spec(text)
    # score_witness_spec re-runs the same checker but yields the shared combined_score (parity with the
    # in-harness Mode-B fitness); it never eval/exec's either. Guard it too so a scoring hiccup is a row.
    try:
        score = float(score_witness_spec(text).get("combined_score", 0.0))
    except Exception as e:  # noqa: BLE001
        score = 1.0 if result.ok else 0.0
        if result.error is None:
            result.error = f"scorer error: {e}"

    return {
        "spec": path.name,
        "valid_schema": result.error is None,     # no recorded validation error => schema was representable
        "confirmed": bool(result.ok),             # the checker CONFIRMED the exact-integer obligation
        "score": score,
        "error": result.error,                    # set iff the spec was malformed/unrepresentable
    }


def run(specs_dir: Path, out_path: Path) -> list[dict]:
    """Check every ``*.json`` spec in ``specs_dir`` (sorted) and write one JSONL row each to ``out_path``."""
    rows: list[dict] = []
    for path in sorted(specs_dir.glob("*.json")):
        rows.append(check_spec_file(path))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=True) + "\n")
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministic witness-check battery (Mode B, no LLM).")
    ap.add_argument("--specs-dir", required=True, type=Path,
                    help="Directory of JSON witness-spec files to check.")
    ap.add_argument("--out", required=True, type=Path,
                    help="Output JSONL path (one recorded row per spec).")
    args = ap.parse_args()

    if not args.specs_dir.is_dir():
        print(f"error: --specs-dir {args.specs_dir} is not a directory", file=sys.stderr)
        return 2

    rows = run(args.specs_dir, args.out)
    for row in rows:
        print(json.dumps(row, ensure_ascii=True))
    print(f"\nwrote {len(rows)} rows to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
