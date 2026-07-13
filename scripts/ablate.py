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
import json
import os
import re
import sys
import time
import uuid
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
from agent.orchestrator.reporting import (
    report_status, result_certification_state, result_has_candidate, result_proof_context,
    result_role_provenance,
)
from scripts import _benchmark_artifacts as _artifacts

# The ordered row schema (stable column order for CSV + diffable JSONL keys).
ROW_FIELDS = [
    "profile_hash", "name", "code_revision", "toolkit_policy_sha256", "proof_context_sha256",
    "resolved_roles",
    "lean_audit", "elementarity", "proven", "reporting_status",
    "calls_spent", "max_llm_calls", "wall_s", "nodes", "proven_nodes", "error",
]

# Explicit experiment knobs only.  Deriving this from every StageProfile field would silently make a
# newly-added soundness control sweepable before anyone reviewed whether disabling it preserves proof
# semantics (the historical H0 bug).  New axes must be consciously added here with tests.
SWEEPABLE_STAGE_FIELDS = (
    "decompose", "review", "population", "evolve_fallback", "refine", "memo",
)


def _reporting_status(proven: bool, authoritative: bool, audited: bool,
                      has_candidate: bool = False) -> str:
    """Map a run outcome to the categorical user-facing status label (never a search score)."""
    return report_status(
        proven=proven,
        has_candidate=has_candidate,
        audited=audited,
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
    row["code_revision"] = _artifacts.cached_code_revision(Path(__file__).resolve().parents[1])
    row["elementarity"] = profile.elementarity.value
    row["max_llm_calls"] = profile.budgets.max_llm_calls
    row["proven"] = False
    t0 = time.perf_counter()
    try:
        res = builder(profile, goal)
        policy_digest = getattr(res, "policy_digest", None)
        row["toolkit_policy_sha256"] = (
            policy_digest if isinstance(policy_digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", policy_digest) else None
        )
        row["resolved_roles"] = result_role_provenance(res)
        row["proof_context_sha256"] = result_proof_context(res)
        proven, audited, authoritative, audit_record = result_certification_state(res)
        row["lean_audit"] = audit_record
        row["proven"] = proven
        row["reporting_status"] = _reporting_status(
            row["proven"], authoritative, audited, result_has_candidate(res))
        budget = getattr(res, "budget", None)
        if budget is not None:
            row["calls_spent"] = getattr(budget, "calls_spent", None)
        dag = getattr(res, "dag", None)
        if dag is not None and getattr(dag, "nodes", None) is not None:
            row["nodes"] = len(dag.nodes)
            # Node.proven already == state.is_success (soft PROVEN OR hard LEAN_VERIFIED; see
            # agent/orchestrator/dag.py Node.proven / state.py NodeState.is_success), so read it
            # directly instead of re-deriving the success set by string-matching enum NAMES (which
            # silently drifts if a success state is renamed/added). Mirrors run_problems.py:349.
            row["proven_nodes"] = sum(1 for n in dag.nodes.values() if getattr(n, "proven", False))
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
    if field == "h0_consistency":
        raise ValueError("--sweep cannot vary h0_consistency: H0 is a mandatory soundness gate")
    if field not in SWEEPABLE_STAGE_FIELDS:
        raise ValueError(f"--sweep field {field!r} is not an allowed ablation field "
                         f"(sweepable fields: {', '.join(SWEEPABLE_STAGE_FIELDS)})")
    out: list[RunProfile] = []
    for raw in values:
        parsed = _parse_stage_value(field, raw)
        # Pydantic's model_copy(update=...) deliberately skips validation.  Sweep values are CLI input,
        # so using it here let population=-1 and population=100000 bypass StageProfile's ge/le bounds.
        # Reconstruct the complete RunProfile through model_validate so both the nested stage bounds and
        # any cross-profile validators run for every generated experiment.
        data = base.model_dump(mode="python")
        data["stages"][field] = parsed
        data["name"] = f"{base.name}-{field}={raw}"
        out.append(RunProfile.model_validate(data))
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
def _row_object(row: dict) -> dict:
    return {key: row[key] for key in ROW_FIELDS}


def _csv_row_object(row: dict) -> dict:
    return {
        key: (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
              if isinstance(value, (dict, list)) else value)
        for key, value in _row_object(row).items()
    }


def _jsonl_line(row: dict) -> str:
    return json.dumps(
        _row_object(row), sort_keys=True, ensure_ascii=False, allow_nan=False,
    ) + "\n"


def _checkpoint_path(out: Path) -> Path:
    return out.with_name(f"{out.name}.{uuid.uuid4().hex}.incomplete")


def _start_output(out: Path, *, overwrite: bool, incremental: bool) -> Path:
    """Reject existing evidence, then allocate a run-private recovery checkpoint."""
    out.parent.mkdir(parents=True, exist_ok=True)
    if (out.exists() or out.is_symlink()) and not overwrite:
        raise FileExistsError(f"output already exists: {out}")
    checkpoint = _checkpoint_path(out)
    if incremental:
        _artifacts.prepare_text_checkpoint(checkpoint, "")
    return checkpoint


def _append_jsonl_row(checkpoint: Path, row: dict) -> None:
    """Append one newline-committed row and fsync it before the next paid run starts."""
    with checkpoint.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(_jsonl_line(row))
        fh.flush()
        os.fsync(fh.fileno())


def _write_csv_checkpoint(rows: list[dict], checkpoint: Path) -> None:
    """Create a complete fsynced CSV checkpoint; the destination is published only afterward."""
    with checkpoint.open("x", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=ROW_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(_csv_row_object(row))
        fh.flush()
        os.fsync(fh.fileno())
    _artifacts.fsync_directory(checkpoint.parent)


def _validate_jsonl_checkpoint(checkpoint: Path, rows: list[dict]) -> None:
    loaded, durable_size = _artifacts.load_checkpoint_rows(checkpoint, max_rows=len(rows))
    expected = [json.loads(_jsonl_line(row)) for row in rows]
    if durable_size != checkpoint.stat().st_size or loaded != expected:
        raise ValueError("JSONL checkpoint changed or contains a partial row before publication")


def _publish_output(checkpoint: Path, out: Path, *, overwrite: bool) -> None:
    # Default publication is an atomic hard-link creation, so a destination that appears during the
    # sweep wins and is never clobbered.  --force uses atomic replacement only after the checkpoint is
    # complete and fsynced; a crash during the sweep therefore leaves old evidence untouched.
    _artifacts.publish_single(checkpoint, out, overwrite=overwrite)


def write_rows(rows: list[dict], out: Path, *, overwrite: bool = False) -> None:
    """Safely publish a complete JSONL/CSV row set without clobbering evidence by default."""
    out = Path(out)
    incremental = out.suffix.lower() != ".csv"
    checkpoint = _start_output(out, overwrite=overwrite, incremental=incremental)
    if incremental:
        for row in rows:
            _append_jsonl_row(checkpoint, row)
        _validate_jsonl_checkpoint(checkpoint, rows)
    else:
        _write_csv_checkpoint(rows, checkpoint)
    _publish_output(checkpoint, out, overwrite=overwrite)


def ablate(profiles: Iterable[RunProfile], goal: str, out: Path, *, builder=None,
           verbose: bool = True, overwrite: bool = False) -> list[dict]:
    """Run every profile on ``goal``, write the rows to ``out``, and return them.

    JSONL rows are fsynced to a unique ``*.incomplete`` checkpoint as each profile finishes.  On
    success that validated checkpoint is published without rewriting it.  CSV is written and fsynced
    as a complete private checkpoint before publication. Existing output is rejected by default and a
    destination created during the run is never clobbered. Progress lines flush."""
    out = Path(out)
    rows: list[dict] = []
    incremental = out.suffix.lower() != ".csv"
    checkpoint = _start_output(out, overwrite=overwrite, incremental=incremental)
    for prof in profiles:
        if verbose:
            print(f"# ablate: {prof.name} (hash {prof.profile_hash[:12]})", file=sys.stderr,
                  flush=True)
        row = run_profile_row(prof, goal, builder=builder)
        rows.append(row)
        if incremental:
            _append_jsonl_row(checkpoint, row)
        if verbose:
            tag = row["error"] or row["reporting_status"]
            print(f"#   -> proven={row['proven']} status={tag} wall={row['wall_s']}s",
                  file=sys.stderr, flush=True)
    if incremental:
        _validate_jsonl_checkpoint(checkpoint, rows)
    else:
        _write_csv_checkpoint(rows, checkpoint)
    _publish_output(checkpoint, out, overwrite=overwrite)
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
    ap.add_argument("--force", action="store_true",
                    help="atomically replace an existing --out file (default: preserve and reject)")
    ap.add_argument("--quiet", action="store_true", help="suppress per-profile progress on stderr")
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        profiles = collect_profiles(args)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    try:
        ablate(profiles, args.goal, args.out, verbose=not args.quiet, overwrite=args.force)
    except (OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
