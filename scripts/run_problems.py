"""Benchmark problem-runner: drive the authored ``benchmarks/problems/*`` folders end to end.

Until now ``benchmarks/problems/`` was inert data — no code loaded ``problem.md`` / ``allowed_inputs.md``,
so a benchmark run meant hand-pasting goal strings into ``scripts/prove.py``. This script closes that
gap for Phase-4 evaluation: it LOADS each authored problem folder, then for every (problem x profile)
pair builds+runs the SAME supervised path the ablation harness uses
(:func:`agent.orchestrator.builder.build_and_run`) and writes one structured, diffable result row.

Flow (mirrors scripts/ablate.py, the sibling sweep harness)::

    load_problem(folder) -> Problem{slug, statement, tier, status, allowed_inputs_note, ...}
      -> (skip non-ready folders as skip rows; unparseable folders as error rows — never a guess)
      -> for each --profile:  build_and_run(profile, goal)  [supervisor fail-CLOSED first]
      -> one JSONL/CSV row per (problem, profile), reusing prove.py's categorical report_status.

Per-problem context injection (Fix 2): when a problem declares per-problem citable inputs (e.g.
Ljunggren's quartic-residue whitelist, the triangular-number three-squares theorem), the runner passes
them through the harness's dedicated seed-context CHANNEL — ``build_and_run(profile, goal, context=...)``
threads the clause as a standing prover/decomposer lesson WITHOUT touching goal identity. Crucially the
goal string handed to the builder stays the PURE statement, so goal-binding hashes the mathematics only
(previously the clause was APPENDED to the goal string, which made the goal's hash include the clause
while the prover restated only the mathematics — every context-injected problem then died at 1 call on a
spurious goal_binding refutation). The row records ``injected_context=True`` when a clause was passed.
It does NOT build a new gate-config channel — the GLOBAL gate stays authoritative; the clause is only
prover-facing prompt context.

SAFETY: like ablate.py, this never exec/eval/imports model output; it only constructs + runs drivers via
the builder, whose deterministic gate stays authoritative. A folder that fails to parse, a profile the
supervisor REJECTS, or a run that raises mid-flight becomes a row with ``error`` set — one bad
(problem, profile) never aborts the sweep.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.orchestrator.run_profile import RunProfile

# Reuse prove.py's CATEGORICAL reporting-status mapping (the single source of truth) by path-loading the
# script (scripts/ is not a package), the SAME trick ablate.py uses. This keeps problem-runner rows on
# the identical certification ladder the CLI and the ablation harness report.
_PROVE_PATH = Path(__file__).resolve().parent / "prove.py"
_spec = importlib.util.spec_from_file_location("prove_cli_for_run_problems", _PROVE_PATH)
_prove = importlib.util.module_from_spec(_spec)
sys.modules["prove_cli_for_run_problems"] = _prove
_spec.loader.exec_module(_prove)
report_status = _prove.report_status

# The ordered row schema (stable column order for CSV + diffable JSONL keys).
ROW_FIELDS = [
    "problem", "tier", "profile_name", "profile_hash", "elementarity", "proven", "reporting_status",
    "authoritative_elementary", "calls_spent", "max_llm_calls", "wall_s", "nodes", "proven_nodes",
    "injected_context", "contaminated", "error",
]


# --------------------------------------------------------------------------------------------------
# Loader: benchmarks/problems/<slug>/problem.md (+ allowed_inputs.md) -> Problem.
# --------------------------------------------------------------------------------------------------
@dataclass
class Problem:
    slug: str                       # folder name (stable id)
    statement: str                  # the EXACT goal string to hand the prover (from frontmatter)
    tier: Optional[str]             # T1 / T2 / T3-hard / calibration / ... (frontmatter, may be None)
    status: str                     # ready | draft | contaminated/... (frontmatter `status`)
    allowed_inputs_note: Optional[str]  # per-problem citable additions (from allowed_inputs.md), or None
    source_folder: Path

    @property
    def is_ready(self) -> bool:
        """Status is exactly ``ready``."""
        return self.status.strip().lower() == "ready"

    @property
    def contaminated(self) -> bool:
        """Status carries the contamination flag (``contaminated/...``): the repo contains
        example-adjacent solution material for this problem, so results are capability checks,
        not clean-eval evidence. Runnable, but every row is labeled ``contaminated=True``."""
        return self.status.strip().lower().startswith("contaminated")

    @property
    def is_runnable(self) -> bool:
        """A folder runs iff it is ``ready`` or ``contaminated/...`` (labeled). Anything else —
        draft, blocked — is skipped rather than guessed at (fail-closed)."""
        return self.is_ready or self.contaminated


class ProblemLoadError(ValueError):
    """A folder that cannot be parsed into a Problem (missing frontmatter, no statement, ...). Surfaced
    as an error ROW by the sweep, never a silent guess."""


def _read_frontmatter(path: Path) -> dict[str, str]:
    """Parse the leading ``---``-delimited YAML-ish frontmatter as flat ``key: value`` lines.

    Matches scripts/check_repo_skeleton.py's reader (line-based, ``#`` comments skipped) so the two agree
    on what the frontmatter says. Quotes around a value are stripped so ``statement: "..."`` yields the
    inner string. Returns {} when there is no frontmatter block."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = i
            break
    if end is None:
        return {}
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        fields[key.strip()] = value
    return fields


def _parse_allowed_inputs_note(folder: Path) -> Optional[str]:
    """Extract the per-problem citable-input note from ``allowed_inputs.md``, or None if the folder has
    none.

    The authored files put per-problem additions under a header whose text contains "Per-problem"
    (e.g. "## Per-problem whitelist ..." or "Per-problem citable input ..."). We collect the bullet
    lines under that header until the next header / the "Disallowed" section, and flatten them to a
    short one-line note. A folder that explicitly says "No ... per-problem ... needed" (imo_1988,
    mordell) has no such header and returns None. Returns None if allowed_inputs.md is absent."""
    ai = folder / "allowed_inputs.md"
    if not ai.is_file():
        return None
    lines = ai.read_text(encoding="utf-8").splitlines()

    def _is_section_header(s: str) -> bool:
        """A real section header in these files is a non-bullet line that ENDS with ':' (e.g.
        "Allowed:", "Per-problem whitelist ... :", "Disallowed as final proof tools ... :"). Wrapped
        prose continuation lines of a bullet never end in ':', so this distinguishes a new section from a
        bullet's overflow — the key to not truncating a multi-line bullet."""
        return bool(s) and not s.startswith("-") and s.rstrip().endswith(":")

    # State machine: SEEK the per-problem header -> skip the rest of that (possibly multi-line) header
    # up to its terminating ':' -> COLLECT bullets -> stop at the next real section header.
    seeking, in_header = True, False
    bullets: list[str] = []       # each entry is one bullet, possibly reassembled from wrapped lines
    for line in lines:
        stripped = line.strip()
        low = stripped.lower()
        if seeking:
            # The per-problem header STARTS on a non-bullet line mentioning "per-problem". The header
            # itself may wrap across lines; it ends on the line whose text ends with ':'. A DISCLAIMER
            # sentence ("No per-problem ... required/needed", "No special per-problem ...") also mentions
            # "per-problem" but introduces NO block — it is a full sentence ending in '.', so we require
            # the header to end (this line or a wrapped continuation) in ':' and reject a leading "no ".
            if (stripped and not stripped.startswith("-") and "per-problem" in low
                    and not low.startswith("no ") and not stripped.rstrip().endswith(".")):
                seeking = False
                in_header = not stripped.rstrip().endswith(":")
            continue
        if in_header:
            if stripped.rstrip().endswith(":"):   # end of the wrapped header
                in_header = False
            continue
        if _is_section_header(stripped):     # the next real section (incl. "Disallowed:") ends the block
            break
        if stripped.startswith("-"):         # a new bullet
            item = stripped.lstrip("-").strip()
            bullets.append(item)
        elif stripped and bullets:           # a wrapped continuation line of the current bullet
            bullets[-1] = f"{bullets[-1]} {stripped}"

    cleaned = []
    for item in bullets:
        item = item.replace("**", "").replace("`", "")
        item = re.sub(r"\s+", " ", item).strip()
        if item:
            cleaned.append(item)
    if not cleaned:
        return None
    note = "; ".join(cleaned)
    # Keep the note compact — it becomes a single prompt clause, not a paragraph.
    if len(note) > 500:
        note = note[:497].rstrip() + "..."
    return note


def load_problem(folder: Path) -> Problem:
    """Load one ``benchmarks/problems/<slug>/`` folder into a :class:`Problem`.

    The goal string is read from the ``statement:`` frontmatter key of ``problem.md`` — an explicit,
    unambiguous one-line pin authored per folder (the ``## Statement`` prose is split across Statement /
    Variables / Target-conclusion sections and is not machine-parseable without rewording the math, so
    the frontmatter key is the single source of truth). Raises :class:`ProblemLoadError` if problem.md is
    missing, has no frontmatter, or has no non-empty ``statement``."""
    folder = Path(folder)
    pmd = folder / "problem.md"
    if not pmd.is_file():
        raise ProblemLoadError(f"{folder.name}: no problem.md")
    fm = _read_frontmatter(pmd)
    if not fm:
        raise ProblemLoadError(f"{folder.name}: problem.md has no frontmatter block")
    status = fm.get("status", "").strip()
    if not status:
        raise ProblemLoadError(f"{folder.name}: problem.md frontmatter has no `status`")
    statement = fm.get("statement", "").strip()
    # A non-runnable folder is legitimately statement-less (drafts/blocked); a RUNNABLE folder
    # (ready or contaminated-but-labeled) must carry a statement (else it is malformed and must
    # surface as an error, never a guess).
    low = status.strip().lower()
    if (low == "ready" or low.startswith("contaminated")) and not statement:
        raise ProblemLoadError(
            f"{folder.name}: runnable problem.md has no `statement:` frontmatter key")
    return Problem(
        slug=folder.name,
        statement=statement,
        tier=fm.get("tier") or None,
        status=status,
        allowed_inputs_note=_parse_allowed_inputs_note(folder),
        source_folder=folder,
    )


def discover_problems(problems_dir: Path) -> list[tuple[str, Optional[Problem], Optional[str]]]:
    """Scan ``problems_dir`` for problem folders, returning ``(slug, problem_or_None, error_or_None)``
    triples in sorted-slug order.

    ``TEMPLATE`` (the authoring skeleton) is skipped entirely. A folder that raises
    :class:`ProblemLoadError` yields ``(slug, None, error_message)`` so the sweep can emit an error row
    for it rather than crashing (fail-closed)."""
    out: list[tuple[str, Optional[Problem], Optional[str]]] = []
    for folder in sorted(Path(problems_dir).iterdir()):
        if not folder.is_dir() or folder.name == "TEMPLATE":
            continue
        if not (folder / "problem.md").is_file():
            continue
        try:
            out.append((folder.name, load_problem(folder), None))
        except ProblemLoadError as e:
            out.append((folder.name, None, str(e)))
    return out


# --------------------------------------------------------------------------------------------------
# Per-problem context injection (Fix 2): the citable-inputs clause travels the seed-context CHANNEL —
# it is NOT appended to the goal string (which would corrupt goal-binding), so goal identity stays pure.
# --------------------------------------------------------------------------------------------------
def context_for(problem: Problem) -> tuple[str, Optional[str]]:
    """Return ``(statement, context_clause_or_None)`` for a problem.

    The FIRST element is ALWAYS the PURE ``problem.statement`` — the goal string handed to the builder,
    whose hash must include only the mathematics (goal-binding depends on this).

    The SECOND element is the seed-context CLAUSE: when the problem declares a per-problem
    ``allowed_inputs_note``, a compact "Per-problem citable inputs: ..." clause the caller forwards via
    ``build_and_run(..., context=clause)`` (a standing prover/decomposer lesson), or ``None`` when the
    problem declares no per-problem inputs. The clause is prover-facing prompt context only; the global
    gate stays authoritative and is NOT reconfigured.

    (Previously this was ``goal_for`` and APPENDED the clause to the goal string; that made the goal's
    hash include the clause while the prover restated only the mathematics, so every context-injected
    problem died at 1 call on a spurious goal_binding refutation. Fix 2 routes the clause off the goal.)"""
    if problem.allowed_inputs_note:
        clause = (f"Per-problem citable inputs (admitted for this problem only): "
                  f"{problem.allowed_inputs_note}")
        return problem.statement, clause
    return problem.statement, None


# --------------------------------------------------------------------------------------------------
# Runner: one (problem x profile) -> one structured row (mirrors ablate.run_profile_row).
# --------------------------------------------------------------------------------------------------
def _blank_row() -> dict:
    return {f: None for f in ROW_FIELDS}


def run_one(problem: Problem, profile: RunProfile, *, builder=None,
            ledgers_dir: Optional[Path] = None) -> dict:
    """Build + run one ``profile`` on one ``problem`` and return its structured result row.

    The build/run is wrapped: an inadmissible profile (SupervisorError) or any runtime error becomes a
    row with ``error`` set and ``proven=False`` rather than aborting the sweep. ``builder`` is the
    callable used to build+run (defaults to :func:`agent.orchestrator.builder.build_and_run`); tests
    inject a stub so the harness is exercised fully offline. When ``ledgers_dir`` is given, the run's
    proof artifacts (proof tree + per-node winning ledgers) are dumped there for scrutiny — a
    soft_proven row is NOT a certificate, so the ledger must be inspectable."""
    if builder is None:
        from agent.orchestrator.builder import build_and_run as builder  # lazy: keep import cheap
    # Fix 2: the goal handed to the builder is the PURE statement; the per-problem citable-inputs clause
    # (if any) travels the dedicated seed-context CHANNEL (context=...) so it never enters goal identity.
    statement, context_clause = context_for(problem)
    injected = context_clause is not None
    row = _blank_row()
    row["problem"] = problem.slug
    row["tier"] = problem.tier
    row["profile_name"] = profile.name
    row["profile_hash"] = profile.profile_hash
    row["elementarity"] = profile.elementarity.value
    row["max_llm_calls"] = profile.budgets.max_llm_calls
    row["proven"] = False
    row["authoritative_elementary"] = False
    row["injected_context"] = injected
    row["contaminated"] = problem.contaminated
    t0 = time.perf_counter()
    try:
        # PURE statement as the goal; the citable-inputs clause via the keyword-only context channel.
        res = builder(profile, statement, context=context_clause)
        row["proven"] = bool(getattr(res, "proven", False))
        authoritative = bool(getattr(res, "authoritative_elementary", False))
        row["authoritative_elementary"] = authoritative
        # "formalized" = a terminal Layer-4 gate actually ran (its result is attached), regardless of
        # whether it certified elementary; mirrors prove.py / ablate.py reporting.
        formalized = getattr(res, "terminal", None) is not None
        row["reporting_status"] = report_status(
            proven=row["proven"], formalized=formalized,
            authoritative_elementary=authoritative).label
        budget = getattr(res, "budget", None)
        if budget is not None:
            row["calls_spent"] = getattr(budget, "calls_spent", None)
        dag = getattr(res, "dag", None)
        if dag is not None and getattr(dag, "nodes", None) is not None:
            row["nodes"] = len(dag.nodes)
            row["proven_nodes"] = sum(1 for n in dag.nodes.values()
                                      if getattr(getattr(n, "state", None), "name", "") in
                                      ("PROVEN", "LEAN_VERIFIED"))
        if ledgers_dir is not None:
            _dump_ledgers(ledgers_dir, problem, profile, res)
    except Exception as e:  # noqa: BLE001 — one bad (problem, profile) must not abort the sweep.
        row["error"] = f"{type(e).__name__}: {e}"
        row["reporting_status"] = report_status(proven=False).label
    finally:
        row["wall_s"] = round(time.perf_counter() - t0, 4)
    return row


def _dump_ledgers(ledgers_dir: Path, problem: Problem, profile: RunProfile, res) -> None:
    """Dump the run's proof artifacts to ``ledgers_dir/<slug>__<profile>.json`` (best-effort:
    missing attributes dump as null — never raises into the sweep)."""
    ledgers_dir.mkdir(parents=True, exist_ok=True)
    tree = None
    pt = getattr(res, "proof_tree", None)
    if callable(pt):
        try:
            tree = pt()
        except Exception:  # noqa: BLE001 — artifact capture must never break the sweep
            tree = None
    nodes = []
    dag = getattr(res, "dag", None)
    for n in (getattr(dag, "nodes", {}) or {}).values():
        nodes.append({
            "goal": getattr(n, "goal", None),
            "state": getattr(getattr(n, "state", None), "name", None),
            "proof": getattr(n, "proof", None),
        })
    # Capture the terminal Layer-4 gate result when a terminal gate actually ran, so a certified-vs-not
    # verdict is inspectable alongside the ledger (best-effort: never raises into the sweep).
    terminal = None
    try:
        term = getattr(res, "terminal", None)
        if term is not None:
            summ = getattr(term, "summary", None)
            terminal = {
                "summary": summ() if callable(summ) else str(term),
                "authoritative_elementary": getattr(res, "authoritative_elementary", None),
            }
    except Exception:  # noqa: BLE001 — artifact capture must never break the sweep
        terminal = None
    out = ledgers_dir / f"{problem.slug}__{profile.name}.json"
    out.write_text(json.dumps({
        "problem": problem.slug, "profile": profile.name,
        "profile_hash": profile.profile_hash,
        "proven": bool(getattr(res, "proven", False)),
        "terminal": terminal,
        "proof_tree": tree, "nodes": nodes,
    }, indent=2, ensure_ascii=False), encoding="utf-8")


def _skip_row(slug: str, problem: Optional[Problem], reason: str) -> dict:
    """A row for a folder that was NOT run (non-ready status, or a load error). ``error`` carries the
    reason; ``proven`` is False and the status is ``rejected`` (no admissible proof was produced)."""
    row = _blank_row()
    row["problem"] = slug
    row["tier"] = problem.tier if problem is not None else None
    row["proven"] = False
    row["authoritative_elementary"] = False
    row["injected_context"] = False
    row["contaminated"] = problem.contaminated if problem is not None else None
    row["reporting_status"] = report_status(proven=False).label
    row["error"] = reason
    return row


def run_sweep(problems: list[tuple[str, Optional[Problem], Optional[str]]],
              profiles: list[RunProfile], out: Path, *, builder=None,
              verbose: bool = True, ledgers_dir: Optional[Path] = None) -> list[dict]:
    """Run every (ready problem x profile) pair, plus one skip/error row per non-ready or unparseable
    folder, write the rows to ``out``, and return them.

    A load-error folder (``problem is None``) emits ONE error row (not one per profile — there is no
    profile-independent thing to sweep). A non-ready folder emits ONE skip row. A ready folder emits one
    run row per profile.

    Durability: JSONL rows are APPENDED to ``out`` as each run finishes (a live run can take minutes to
    hours, and a process-level crash must not lose completed rows); the final write_rows pass then
    rewrites the file atomically-in-effect with the complete, ordered set. CSV stays end-only (no
    incremental append with a header). Progress lines are flushed so a piped stderr shows live state."""
    rows: list[dict] = []
    incremental = out.suffix.lower() != ".csv"
    if incremental:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("", encoding="utf-8")     # start fresh; rows land as they complete

    def _emit(row: dict) -> None:
        rows.append(row)
        if incremental:
            with out.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({k: row[k] for k in ROW_FIELDS}, sort_keys=True) + "\n")

    for slug, problem, err in problems:
        if err is not None:                       # unparseable folder -> a single error row
            if verbose:
                print(f"# {slug}: LOAD ERROR ({err})", file=sys.stderr, flush=True)
            _emit(_skip_row(slug, problem, f"load error: {err}"))
            continue
        assert problem is not None
        if not problem.is_runnable:               # draft/blocked folder -> a single skip row
            if verbose:
                print(f"# {slug}: SKIP (status={problem.status!r})", file=sys.stderr, flush=True)
            _emit(_skip_row(slug, problem, f"skipped: status={problem.status!r} (not runnable)"))
            continue
        for prof in profiles:
            if verbose:
                print(f"# run: {slug} x {prof.name} (hash {prof.profile_hash[:12]})",
                      file=sys.stderr, flush=True)
            row = run_one(problem, prof, builder=builder, ledgers_dir=ledgers_dir)
            _emit(row)
            if verbose:
                tag = row["error"] or row["reporting_status"]
                print(f"#   -> proven={row['proven']} status={tag} "
                      f"injected={row['injected_context']} wall={row['wall_s']}s",
                      file=sys.stderr, flush=True)
    write_rows(rows, out)
    if verbose:
        print(f"# wrote {len(rows)} rows -> {out}", file=sys.stderr, flush=True)
    return rows


# --------------------------------------------------------------------------------------------------
# Output writer (JSONL default; CSV by .csv suffix) — stable field order, diffable. Mirrors ablate.py.
# --------------------------------------------------------------------------------------------------
def write_rows(rows: list[dict], out: Path) -> None:
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


# --------------------------------------------------------------------------------------------------
# CLI.
# --------------------------------------------------------------------------------------------------
def _select_problems(problems: list[tuple[str, Optional[Problem], Optional[str]]],
                     wanted: Optional[list[str]]
                     ) -> list[tuple[str, Optional[Problem], Optional[str]]]:
    """Filter discovered problems to the ``--problem SLUG`` set (all if unset). An unknown slug is an
    error (fail-closed: don't silently run nothing)."""
    if not wanted:
        return problems
    by_slug = {slug: triple for triple in problems for slug in (triple[0],)}
    missing = [s for s in wanted if s not in by_slug]
    if missing:
        raise ValueError(f"unknown --problem slug(s): {', '.join(missing)} "
                         f"(available: {', '.join(sorted(by_slug))})")
    return [by_slug[s] for s in wanted]


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Benchmark problem-runner: build_and_run each authored problem folder across "
                    "RunProfiles, one result row per (problem, profile).")
    ap.add_argument("--problems-dir", type=Path, default=Path("benchmarks/problems"), metavar="DIR",
                    help="directory of authored problem folders (default: benchmarks/problems)")
    ap.add_argument("--problem", action="append", metavar="SLUG",
                    help="restrict to this problem folder (repeatable; default: all ready folders)")
    ap.add_argument("--profile", type=Path, action="append", required=True, metavar="PATH",
                    help="a RunProfile YAML to run each problem under (repeatable)")
    ap.add_argument("--out", type=Path, default=Path("runs.jsonl"), metavar="PATH",
                    help="output file (.jsonl default, or .csv) — diffable per-(problem,profile) rows")
    ap.add_argument("--timeout-s", type=int, default=None, metavar="N",
                    help="per-run wall-clock cap (seconds); overrides each profile's roles' timeout_s")
    ap.add_argument("--quiet", action="store_true", help="suppress per-run progress on stderr")
    ap.add_argument("--ledgers-dir", type=Path, default=None, metavar="DIR",
                    help="dump each run's proof artifacts (proof tree + per-node winning ledgers) "
                         "here for scrutiny — a soft_proven row is NOT a certificate")
    return ap


def _apply_timeout(profile: RunProfile, timeout_s: Optional[int]) -> RunProfile:
    """Return ``profile`` with every role's ``timeout_s`` capped to ``timeout_s`` (a per-run wall cap),
    or the profile unchanged when ``timeout_s`` is None. A distinct copy so the cap is reproducible in
    the recorded profile_hash."""
    if timeout_s is None:
        return profile
    roles = profile.roles
    updated = {name: getattr(roles, name).model_copy(update={"timeout_s": timeout_s})
               for name in roles.model_fields}
    return profile.model_copy(update={"roles": roles.model_copy(update=updated)})


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        discovered = discover_problems(args.problems_dir)
        selected = _select_problems(discovered, args.problem)
        profiles = [_apply_timeout(RunProfile.from_yaml(p), args.timeout_s) for p in args.profile]
    except (ValueError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if not selected:
        print(f"ERROR: no problem folders found under {args.problems_dir}", file=sys.stderr)
        return 2
    run_sweep(selected, profiles, args.out, verbose=not args.quiet,
              ledgers_dir=args.ledgers_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
