"""Ablation harness (W6): build_and_run a goal across a set of RunProfiles, writing one structured,
diffable result row per profile.

Given a goal plus EITHER a directory of profile YAMLs (``--profiles-dir``), an explicit list of profile
files (``--profile``), or a base profile with a swept stage field (``--base`` + ``--sweep FIELD=VALS``),
this constructs each profile through the builder (the SAME RunProfile -> supervisor -> registry ->
DagDriver path the CLI uses) and records a reproducible row::

    {profile_hash, name, elementarity, proven, reporting_status, calls_spent, max_llm_calls,
     wall_s, nodes, proven_nodes, error}

to a JSONL (default) or CSV file, so two runs of the same profile set are byte-diffable on everything
but ``wall_s``.

SAFETY: this never exec/eval/imports model output; it only constructs + runs drivers via the builder,
whose deterministic gate stays authoritative. A profile that the supervisor REJECTS (inadmissible) or
that raises mid-run is recorded as a row with ``error`` set and ``proven=false`` — one bad profile never
aborts the sweep.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Goals/progress lines carry Unicode math (ℤ, →); make stdout/stderr UTF-8 so they don't render as
# mojibake / crash on a legacy Windows codepage (cp1252). Mirrors scripts/run_benchmark.py.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

from agent.orchestrator.run_profile import RunProfile, StageProfile

# Reuse prove.py's CATEGORICAL reporting-status mapping (the single source of truth) by path-loading the
# script (scripts/ is not a package). This keeps the ablation rows on the SAME certification ladder the
# CLI reports, so a row's reporting_status never diverges from what `prove.py` would print.
_PROVE_PATH = Path(__file__).resolve().parent / "prove.py"
_spec = importlib.util.spec_from_file_location("prove_cli_for_ablate", _PROVE_PATH)
_prove = importlib.util.module_from_spec(_spec)
sys.modules["prove_cli_for_ablate"] = _prove
_spec.loader.exec_module(_prove)
report_status = _prove.report_status

# The ordered row schema (stable column order for CSV + diffable JSONL keys).
ROW_FIELDS = [
    "profile_hash", "name", "elementarity", "proven", "reporting_status",
    "calls_spent", "max_llm_calls", "wall_s", "nodes", "proven_nodes", "error",
]


def _reporting_status(proven: bool, authoritative: bool, formalized: bool) -> str:
    """Map a run outcome to the categorical user-facing status label (never a search score)."""
    return report_status(
        proven=proven,
        formalized=formalized,
        authoritative_elementary=authoritative,
    ).label


def run_profile_row(profile: RunProfile, goal: str, *, builder=None) -> dict:
    """Build + run one profile on ``goal`` and return its structured result row.

    The build/run is wrapped: an inadmissible profile (SupervisorError) or any runtime error becomes a
    row with ``error`` set and ``proven=False`` rather than aborting the whole sweep. ``builder`` is the
    callable used to build+run (defaults to the real :func:`agent.orchestrator.builder.build_and_run`);
    tests inject a stub so the harness is exercised fully offline."""
    if builder is None:
        from agent.orchestrator.builder import build_and_run as builder  # lazy: keep import cheap
    row = {f: None for f in ROW_FIELDS}
    row["profile_hash"] = profile.profile_hash
    row["name"] = profile.name
    row["elementarity"] = profile.elementarity.value
    row["max_llm_calls"] = profile.budgets.max_llm_calls
    row["proven"] = False
    t0 = time.perf_counter()
    try:
        res = builder(profile, goal)
        row["proven"] = bool(getattr(res, "proven", False))
        authoritative = bool(getattr(res, "authoritative_elementary", False))
        # "formalized" = a terminal Layer-4 gate actually ran (its result is attached), regardless of
        # whether it certified elementary; mirrors prove.py's certifying-mode reporting.
        formalized = getattr(res, "terminal", None) is not None
        row["reporting_status"] = _reporting_status(row["proven"], authoritative, formalized)
        budget = getattr(res, "budget", None)
        if budget is not None:
            row["calls_spent"] = getattr(budget, "calls_spent", None)
        dag = getattr(res, "dag", None)
        if dag is not None and getattr(dag, "nodes", None) is not None:
            row["nodes"] = len(dag.nodes)
            row["proven_nodes"] = sum(1 for n in dag.nodes.values()
                                      if getattr(getattr(n, "state", None), "name", "") in
                                      ("PROVEN", "LEAN_VERIFIED"))
    except Exception as e:  # noqa: BLE001 — one bad profile must not abort the sweep.
        row["error"] = f"{type(e).__name__}: {e}"
        row["reporting_status"] = report_status(proven=False).label
    finally:
        row["wall_s"] = round(time.perf_counter() - t0, 4)
    return row


# --------------------------------------------------------------------------------------------------
# Profile-set assembly: a directory, an explicit list, or a swept stage field over a base profile.
# --------------------------------------------------------------------------------------------------
def _sweep_profiles(base: RunProfile, field: str, values: list[str]) -> list[RunProfile]:
    """Return ``base`` with ``stages.<field>`` set to each value (parsed to bool/int as appropriate).

    Only StageProfile fields are sweepable here (the breadth knobs are the interesting ablation axes).
    Each swept profile keeps a distinct name so its rows are identifiable."""
    if not hasattr(StageProfile(), field):
        raise ValueError(f"--sweep field {field!r} is not a StageProfile field "
                         f"(one of: {', '.join(StageProfile.model_fields)})")
    out: list[RunProfile] = []
    for raw in values:
        parsed = _parse_stage_value(field, raw)
        stages = base.stages.model_copy(update={field: parsed})
        out.append(base.model_copy(update={
            "stages": stages,
            "name": f"{base.name}-{field}={raw}",
        }))
    return out


def _parse_stage_value(field: str, raw: str):
    """Parse a swept value to the StageProfile field's type (bool for the flags, int for the knobs)."""
    annotation = StageProfile.model_fields[field].annotation
    if annotation is bool:
        low = raw.strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
        raise ValueError(f"cannot parse {raw!r} as a bool for stage field {field!r}")
    if annotation is int:
        return int(raw)
    return raw


def collect_profiles(args) -> list[RunProfile]:
    """Assemble the profile set from the CLI args (directory | explicit files | base+sweep)."""
    profiles: list[RunProfile] = []
    if args.profiles_dir:
        for path in sorted(Path(args.profiles_dir).glob("*.yaml")):
            profiles.append(RunProfile.from_yaml(path))
    for path in args.profile or []:
        profiles.append(RunProfile.from_yaml(path))
    if args.base:
        base = RunProfile.from_yaml(args.base)
        if args.sweep:
            field, _, vals = args.sweep.partition("=")
            if not _:
                raise ValueError("--sweep must be FIELD=v1,v2,... (e.g. population=0,2,4)")
            profiles.extend(_sweep_profiles(base, field.strip(),
                                            [v for v in vals.split(",") if v != ""]))
        else:
            profiles.append(base)
    if not profiles:
        raise ValueError("no profiles selected: pass --profiles-dir, --profile, and/or --base[/--sweep]")
    return profiles


# --------------------------------------------------------------------------------------------------
# Output writers.
# --------------------------------------------------------------------------------------------------
def write_rows(rows: list[dict], out: Path) -> None:
    """Write ``rows`` to ``out`` as JSONL (default) or CSV (``.csv`` suffix). Diffable + reproducible."""
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() == ".csv":
        with out.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=ROW_FIELDS)
            w.writeheader()
            for row in rows:
                w.writerow(row)
    else:
        with out.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps({k: row[k] for k in ROW_FIELDS}, sort_keys=True) + "\n")


def ablate(profiles: Iterable[RunProfile], goal: str, out: Path, *, builder=None,
           verbose: bool = True) -> list[dict]:
    """Run every profile on ``goal``, write the rows to ``out``, and return them.

    Durability (mirrors scripts/run_problems.py): JSONL rows are APPENDED as each profile finishes —
    a live sweep can run for hours and a process-level crash must not lose completed rows; the final
    write_rows pass rewrites the complete ordered set. CSV stays end-only. Progress lines flush."""
    rows: list[dict] = []
    incremental = out.suffix.lower() != ".csv"
    if incremental:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("", encoding="utf-8")
    for prof in profiles:
        if verbose:
            print(f"# ablate: {prof.name} (hash {prof.profile_hash[:12]})", file=sys.stderr,
                  flush=True)
        row = run_profile_row(prof, goal, builder=builder)
        rows.append(row)
        if incremental:
            with out.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({k: row[k] for k in ROW_FIELDS}, sort_keys=True) + "\n")
        if verbose:
            tag = row["error"] or row["reporting_status"]
            print(f"#   -> proven={row['proven']} status={tag} wall={row['wall_s']}s",
                  file=sys.stderr, flush=True)
    write_rows(rows, out)
    if verbose:
        print(f"# wrote {len(rows)} rows -> {out}", file=sys.stderr, flush=True)
    return rows


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Ablation harness: build_and_run a goal across RunProfiles, one result row each.")
    ap.add_argument("goal", help="the goal/theorem statement to prove under each profile")
    ap.add_argument("--profiles-dir", type=Path, metavar="DIR",
                    help="run every *.yaml profile in this directory (e.g. profiles/ablation)")
    ap.add_argument("--profile", type=Path, action="append", metavar="PATH",
                    help="add an explicit profile YAML (repeatable)")
    ap.add_argument("--base", type=Path, metavar="PATH",
                    help="a base profile YAML (run as-is, or swept via --sweep)")
    ap.add_argument("--sweep", metavar="FIELD=v1,v2,...",
                    help="sweep a StageProfile field over --base (e.g. population=0,2,4)")
    ap.add_argument("--out", type=Path, default=Path("ablation.jsonl"), metavar="PATH",
                    help="output file (.jsonl default, or .csv) — diffable per-profile rows")
    ap.add_argument("--quiet", action="store_true", help="suppress per-profile progress on stderr")
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        profiles = collect_profiles(args)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    ablate(profiles, args.goal, args.out, verbose=not args.quiet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
