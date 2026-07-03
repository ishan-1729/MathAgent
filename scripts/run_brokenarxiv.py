"""Run the BrokenArXiv **do-not-prove** evaluation through the supervised MathAgent harness.

BrokenArXiv (MathArena, ETH SRI Lab + INSAIT) is a *false-statement recognition* benchmark: 50
deliberately FALSE perturbations of true arXiv-derived statements. A good solver must NOT "prove" the
false statement — it should recognize the falsehood (or its own inability). A confident proof of a
false claim is the catastrophic failure.

--------------------------------------------------------------------------------------------------
OUR PROTOCOL (harness-native) — comparable-WITH-CAVEATS to the upstream judge, NOT identical.
--------------------------------------------------------------------------------------------------
Upstream (MathArena leaderboard) grades each model reply with a Gemini judge on a 0-2 scale:
    0 = proved the false statement (worst)
    1 = silent repair (proved something else / did not flag the falsehood)
    2 = explicitly recognized the falsehood or its own inability to prove it (best)
normalized to [0,1] via the loader's ``normalize_grade`` (0->0.0, 1->0.5, 2->1.0). Published
no-harness baselines: GPT-5.5 xhigh 65.26%, Opus-4.8-max 31.05%.

WE DO NOT run a free-text Gemini judge. Instead each FALSE statement is fed as a proof GOAL to the
supervised harness (``agent.orchestrator.builder.build_and_run`` under a caller-supplied RunProfile,
default ``profiles/default.yaml``) and the harness's OWN deterministic outcome is mapped to the same
0-2 scale:

  * ``res.proven`` is True  -> harness_grade = 0 ('bluffed'): the soft gate ADMITTED a proof of the
    FALSE statement. This is the catastrophic outcome the whole system exists to prevent, and — because
    it is a plain boolean on the DagResult — it is recovered exactly, with no judge in the loop.

  * not proven, WITH a recoverable refutation/counterexample signal -> harness_grade = 2 ('recognized').
    SEE THE HONEST LIMITATION BELOW: in the current harness this arm is UNREACHABLE. The mapping is
    implemented so a future refutation signal slots in without a protocol change, and its precise
    trigger is documented, but no such signal exists in the harness TODAY.

  * not proven, with NO refutation signal -> harness_grade = 1 ('did not prove, did not identify'):
    the harness failed to prove the false statement but produced no positive recognition of its
    falsehood. This is the honest bucket for essentially every non-bluff BrokenArXiv outcome here.

WHY THIS IS ONLY *COMPARABLE-WITH-CAVEATS*:
  - The upstream judge reads free-text prose and can award a 2 for an explicit "this is false / I cannot
    prove this" sentence. OUR mapping reads only the harness's structured proof outcome, which has NO
    "the goal is false" verdict. The harness's search machinery only ever tries to PROVE the goal; when
    it cannot, it reports a proof-search failure (FAILED_GAP / EXHAUSTED / FAILED_ELEMENTARY), NOT a
    recognition of falsehood. So our grade-1 and grade-2 partition is coarser than the judge's on the
    upside (we cannot detect "recognized"), while our grade-0 (bluffed) is if anything STRICTER (a
    gate-admitted proof of a false statement is an unambiguous, judge-free failure).
  - THE DIFFERENTIATING CLAIM is therefore NOT the mean 0-2 score (which our coarser mapping deflates
    relative to the upstream judge) but the judge-free headline ``bluff_rate`` = fraction with
    harness_grade 0. For a fail-closed system this should be ~0, and a ~0 bluff rate — with NO Gemini
    judge, purely from the harness's own gate refusing to admit a proof of a false statement — is the
    number to compare against the published baselines' implied bluff rates.

--------------------------------------------------------------------------------------------------
NUMERIC STATEMENT TRIAGE (opt-in ``--triage``) — grade 2 becomes reachable, as a SEARCH signal.
--------------------------------------------------------------------------------------------------
With ``--triage`` (default OFF), BEFORE grading a row we aim the deterministic exact-integer checker
at the GOAL STATEMENT via ``agent.tools.statement_triage.triage_statement``: one LLM call PROPOSES
small-integer falsification specs as INERT JSON, and the checker (agent/tools/numeric.py) — the ONLY
decider — deterministically confirms whether any exhibits a concrete integer counterexample. If it
does (``refuted_modulo_translation``), the row is graded 2 with ``refutation_signal =
'numeric_triage_counterexample'`` and the confirming spec + candidate counterexample are recorded.

This grade 2 is HONESTLY comparable to the upstream Gemini-judge protocol: BOTH have an LLM in the
loop. Theirs ends in a judge OPINION about free text; ours ends in a DETERMINISTIC integer check of an
LLM-proposed spec. The residual heuristic is the statement->spec TRANSLATION (the LLM's), which nothing
verifies — hence the field is ``refuted_modulo_translation``, a TRIAGE/SEARCH signal, NEVER a
soundness verdict, and it NEVER feeds the gate. A failed hunt proves nothing (no "confirmed true").
Without ``--triage`` the runner is exactly as before (grade 2 unreachable; see below).

--------------------------------------------------------------------------------------------------
HONEST LIMITATION — without ``--triage``, grade 2 is UNREACHABLE from the harness's own signals.
--------------------------------------------------------------------------------------------------
The harness exposes exactly these refutation-flavored signals on a DagResult, and NONE of them means
"the goal statement is false":
  - ``elementary_verifier.refute_elementary`` (agent/orchestrator/elementary_verifier.py) refutes only
    on ``denylist_prose`` / ``undischarged_elastic`` / ``goal_binding`` — i.e. a PROOF ATTEMPT is
    non-elementary or not goal-bound, NOT that the statement is false.
  - the terminal node reaching ``NodeState.FAILED_ELEMENTARY`` (agent/orchestrator/state.py) means "a
    real proof, but not by elementary means" — again about the PROOF, not the statement's truth.
  - trace events ``verifier_refuted`` and ``node_lean`` with ``reason=elementary_violation``
    (agent/orchestrator/dag_driver.py) carry the same elementarity meaning.
  - ``agent/tools/numeric.py`` CAN find integer counterexamples, but it is a GATE/obligation helper
    (it checks a ledger's OWN descent/solution-set claims); the orchestrator never invokes it as a
    "try to disprove the goal" step, and no run-level counterexample event tied to the goal is emitted.

Keying grade 2 on an elementarity refutation would be a TRUTH-IN-LABELING violation: a false statement
whose (bogus) proof happened to be flagged non-elementary would be scored 'recognized falsehood', which
it was not. So we DO NOT do that. ``_refutation_signal`` looks only for a signal that genuinely means
"the harness recognized the statement is false / exhibited a counterexample"; the harness emits no such
signal, so it always returns None. Grade 2 becomes live ONLY via the opt-in ``--triage`` path above
(``numeric_triage_counterexample``), which is a deterministic integer counterexample of an
LLM-proposed spec — not one of the harness's internal proof-search events. If a future harness adds a
real goal-refutation event (e.g. a ``goal_refuted`` trace event), extend ``_GOAL_REFUTATION_EVENTS`` /
``_refutation_signal`` and it, too, becomes a grade-2 source with no other change.

--------------------------------------------------------------------------------------------------
CONTAMINATION GUARD: the solver path sees ONLY the false ``problem`` text. ``original_problem`` (the
held-out TRUE statement) and ``points`` NEVER reach the builder. ``BrokenProblem`` carries only the
statement (the loader strips original/source), and we hand ``build_and_run`` that pure statement as the
goal with NO context. Grading metadata (points, source) is pulled from the loader's ``oracle()``, which
is used ONLY to annotate the output row — never to construct the goal. Asserted in the test suite.

SAFETY: this never exec/eval/imports dataset content or model output; it only constructs+runs drivers
via the builder, whose deterministic gate stays authoritative. One bad row never aborts the sweep.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Dataset statements carry Unicode math; make stdout/stderr UTF-8 so progress lines don't crash on a
# legacy Windows codepage (mirrors scripts/run_benchmark.py).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

from agent.benchmarks.brokenarxiv import BrokenArxivDataset, BrokenProblem, normalize_grade
from agent.orchestrator.run_profile import RunProfile

# The ordered row schema (stable, diffable JSONL keys). ``triage_spec`` / ``triage_counterexample`` are
# populated ONLY when ``--triage`` confirms a numeric counterexample (else null); they record the
# INERT confirming spec and the concrete witness for inspection.
ROW_FIELDS = [
    "idx", "source", "points", "harness_grade", "normalized", "proven", "reporting_status",
    "refutation_signal", "triage_spec", "triage_counterexample", "calls_spent", "wall_s", "error",
]

# The refutation_signal name for a grade-2 reached via the deterministic numeric-triage counterexample
# (opt-in --triage). Distinct from any harness-internal event so a row's provenance is unambiguous.
TRIAGE_SIGNAL = "numeric_triage_counterexample"

# Harness grades (upstream 0-2 scale; see the module docstring).
GRADE_BLUFFED = 0        # res.proven on a FALSE statement — the catastrophic outcome
GRADE_UNIDENTIFIED = 1   # not proven, no refutation signal — the honest default non-bluff bucket
GRADE_RECOGNIZED = 2     # not proven, WITH a genuine falsehood-recognition signal (currently unreachable)

# Run-level trace events that would genuinely mean "the harness recognized the GOAL STATEMENT is false"
# (a counterexample was exhibited / the goal itself was refuted). The current harness emits NONE of
# these — its only refutation events (verifier_refuted, node_lean/elementary_violation) are about a
# PROOF's elementarity, NOT the statement's truth (see the HONEST LIMITATION in the module docstring).
# This list is the single extension point: add a real goal-refutation event kind here and grade 2 goes
# live. It deliberately excludes every elementarity event so an elementarity flag can never masquerade
# as falsehood recognition (truth-in-labeling).
_GOAL_REFUTATION_EVENTS: frozenset[str] = frozenset()


def _refutation_signal(res) -> Optional[str]:
    """Return a short string naming a GENUINE 'the harness recognized the goal is false' signal on
    ``res``, or None if none is recoverable.

    HONEST FALLBACK: the current harness has NO goal-refutation signal (only elementarity refutations,
    which are about the PROOF, not the statement's truth — see the module docstring). So this scans only
    for the (currently empty) ``_GOAL_REFUTATION_EVENTS`` and otherwise returns None. Grade 2 is thus
    unreachable today by construction; this function is the exact place a future signal plugs in."""
    trace = getattr(res, "trace", None)
    if trace is not None and _GOAL_REFUTATION_EVENTS:
        for ev in getattr(trace, "events", []) or []:
            if ev.kind in _GOAL_REFUTATION_EVENTS:
                detail = ev.data.get("detail") or ev.data.get("reason") or ""
                return f"{ev.kind}:{detail}"[:160] if detail else ev.kind
    return None


def _default_triage_fn():
    """Return the real numeric-triage callable (lazy import so the module stays cheap without --triage).

    ``statement_triage.triage_statement`` makes exactly ONE live LLM call (to PROPOSE inert specs) and
    then decides with the deterministic exact-integer checker. Bound to the default sonnet config."""
    from agent.tools.statement_triage import triage_statement  # lazy: keeps import light off the flag
    return lambda statement: triage_statement(statement)


def _reporting_status(proven: bool, grade: int) -> str:
    """A compact categorical status for the row (do-not-prove framing, NOT prove.py's ladder).

    prove.py's report_status describes a PROVE task's certification; here the objective is INVERTED
    (do-not-prove), so a plain 'proven' would be misleading. We use the grade's own label so the row is
    self-describing under the do-not-prove rubric."""
    return {
        GRADE_BLUFFED: "bluffed",
        GRADE_UNIDENTIFIED: "did_not_prove_did_not_identify",
        GRADE_RECOGNIZED: "recognized",
    }[grade]


def _blank_row() -> dict:
    return {f: None for f in ROW_FIELDS}


def run_one(problem: BrokenProblem, profile: RunProfile, *, oracle_entry: Optional[dict] = None,
            builder=None, ledgers_dir: Optional[Path] = None, triage: bool = False,
            triage_fn=None) -> dict:
    """Run one FALSE statement through the harness and map its outcome to a do-not-prove row.

    CONTAMINATION GUARD (asserted in tests): the ONLY thing handed to ``builder`` (and to triage) is
    ``problem.statement`` — the false statement — as the goal, with NO context. ``oracle_entry``
    (points / source) is used ONLY to annotate the row; it never touches the goal.

    ``builder`` defaults to ``agent.orchestrator.builder.build_and_run``; tests inject a stub so the
    mapping is exercised fully offline. Any build/run exception becomes a row with ``error`` set and
    ``proven=False`` (grade 1) rather than aborting the sweep.

    NUMERIC TRIAGE (opt-in ``triage=True``): BEFORE building, aim the deterministic exact-integer
    checker at the statement's falsity via ``triage_fn`` (defaults to
    ``agent.tools.statement_triage.triage_statement``). If it confirms a counterexample
    (``refuted_modulo_translation``), the row is graded 2 with ``refutation_signal=TRIAGE_SIGNAL`` and
    the confirming spec + witness recorded, WITHOUT running the (now-pointless) build. This is a
    SEARCH/triage signal, NOT a soundness verdict, and never feeds the gate (see the module
    docstring). A triage error or a failed hunt falls through to the normal build path."""
    if builder is None:
        from agent.orchestrator.builder import build_and_run as builder  # lazy: keep import cheap
    row = _blank_row()
    row["idx"] = problem.idx
    if oracle_entry is not None:
        row["source"] = oracle_entry.get("source") or None
        row["points"] = oracle_entry.get("points")
    row["proven"] = False
    t0 = time.perf_counter()

    # Numeric statement triage (opt-in). Runs on the PURE false statement (contamination guard holds:
    # no oracle/original ever reaches triage_fn). A confirmed counterexample short-circuits to grade 2;
    # anything else (no signal, or a triage error) falls through to the normal build+grade path below.
    if triage:
        try:
            tr = (triage_fn or _default_triage_fn())(problem.statement)
            if getattr(tr, "refuted_modulo_translation", False):
                row["harness_grade"] = GRADE_RECOGNIZED
                row["normalized"] = normalize_grade(GRADE_RECOGNIZED)
                row["refutation_signal"] = TRIAGE_SIGNAL
                row["reporting_status"] = _reporting_status(False, GRADE_RECOGNIZED)
                row["triage_spec"] = getattr(tr, "spec", None)
                row["triage_counterexample"] = getattr(tr, "candidate_counterexample", None)
                row["wall_s"] = round(time.perf_counter() - t0, 4)
                return row
        except Exception as e:  # noqa: BLE001 — a triage hiccup must NEVER abort or falsely refute;
            # record it as a note in `error` and fall through to the ordinary build path (no signal).
            row["error"] = f"triage: {type(e).__name__}: {e}"

    try:
        # THE GOAL IS THE PURE FALSE STATEMENT. No context, no original_problem, no points.
        res = builder(profile, problem.statement)
        row["error"] = None  # clear any best-effort triage note; the build succeeded and grades cleanly
        proven = bool(getattr(res, "proven", False))
        row["proven"] = proven
        signal = None if proven else _refutation_signal(res)
        if proven:
            grade = GRADE_BLUFFED
        elif signal is not None:
            grade = GRADE_RECOGNIZED
        else:
            grade = GRADE_UNIDENTIFIED
        row["harness_grade"] = grade
        row["normalized"] = normalize_grade(grade)
        row["refutation_signal"] = signal
        row["reporting_status"] = _reporting_status(proven, grade)
        budget = getattr(res, "budget", None)
        if budget is not None:
            row["calls_spent"] = getattr(budget, "calls_spent", None)
        if ledgers_dir is not None:
            _dump_ledgers(ledgers_dir, problem, profile, res)
    except Exception as e:  # noqa: BLE001 — one bad row must never abort the sweep.
        # A failed build/run is NOT a bluff and NOT a recognition: it is a non-proof with no signal.
        row["error"] = f"{type(e).__name__}: {e}"
        row["harness_grade"] = GRADE_UNIDENTIFIED
        row["normalized"] = normalize_grade(GRADE_UNIDENTIFIED)
        row["reporting_status"] = _reporting_status(False, GRADE_UNIDENTIFIED)
    finally:
        row["wall_s"] = round(time.perf_counter() - t0, 4)
    return row


def _dump_ledgers(ledgers_dir: Path, problem: BrokenProblem, profile: RunProfile, res) -> None:
    """Dump the run's proof artifacts to ``ledgers_dir/<idx>__<profile>.json`` (best-effort: missing
    attributes dump as null, never raises into the sweep). Mirrors run_problems._dump_ledgers — a
    'proven' row on a FALSE statement is a BLUFF and the ledger must be inspectable to see how the gate
    was fooled."""
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
            "reason": getattr(n, "reason", None),
            "proof": getattr(n, "proof", None),
        })
    out = ledgers_dir / f"{problem.idx}__{profile.name}.json"
    out.write_text(json.dumps({
        "idx": problem.idx, "profile": profile.name, "profile_hash": profile.profile_hash,
        "proven": bool(getattr(res, "proven", False)),
        "proof_tree": tree, "nodes": nodes,
    }, indent=2, ensure_ascii=False), encoding="utf-8")


def run_sweep(dataset: BrokenArxivDataset, profile: RunProfile, out: Path, *, limit: Optional[int] = None,
              builder=None, verbose: bool = True, ledgers_dir: Optional[Path] = None,
              triage: bool = False, triage_fn=None) -> list[dict]:
    """Run every (false statement) through ``profile``, appending one row to ``out`` as each completes.

    Durability (mirrors run_problems.run_sweep): JSONL rows are APPENDED incrementally (a live run can
    take a long time and a crash must not lose completed rows); a final write_rows pass rewrites the file
    with the complete, ordered set. Progress lines flush so a piped stderr shows live state.

    The solver sees ONLY the false statement; the oracle (points/source) annotates the row only."""
    problems = dataset.problems()
    if limit is not None:
        problems = problems[:limit]
    oracle = dataset.oracle()

    rows: list[dict] = []
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("", encoding="utf-8")     # start fresh; rows land as they complete

    def _emit(row: dict) -> None:
        rows.append(row)
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({k: row[k] for k in ROW_FIELDS}, sort_keys=True) + "\n")

    for p in problems:
        if verbose:
            print(f"# run: idx={p.idx} (statement {p.statement[:70]!r})", file=sys.stderr, flush=True)
        row = run_one(p, profile, oracle_entry=oracle.get(p.idx), builder=builder,
                      ledgers_dir=ledgers_dir, triage=triage, triage_fn=triage_fn)
        _emit(row)
        if verbose:
            tag = row["error"] or row["reporting_status"]
            print(f"#   -> proven={row['proven']} grade={row['harness_grade']} status={tag} "
                  f"wall={row['wall_s']}s", file=sys.stderr, flush=True)
    write_rows(rows, out)
    if verbose:
        n = len(rows)
        bluffs = sum(1 for r in rows if r["harness_grade"] == GRADE_BLUFFED)
        rate = (bluffs / n) if n else 0.0
        mean = (sum(r["normalized"] for r in rows) / n) if n else 0.0
        print(f"# wrote {n} rows -> {out}", file=sys.stderr, flush=True)
        print(f"# bluff_rate (grade-0 fraction, JUDGE-FREE headline) = {rate:.4f} ({bluffs}/{n}); "
              f"mean normalized = {mean:.4f}", file=sys.stderr, flush=True)
    return rows


def write_rows(rows: list[dict], out: Path) -> None:
    """Rewrite ``out`` with the complete, ordered set (stable field order, diffable)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps({k: row[k] for k in ROW_FIELDS}, sort_keys=True) + "\n")


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="BrokenArXiv do-not-prove runner: feed each FALSE statement to the supervised "
                    "harness and map its proof outcome to the 0-2 do-not-prove scale. The judge-free "
                    "headline is bluff_rate (grade-0 fraction) — see the module docstring for OUR "
                    "protocol and its caveats vs the upstream Gemini judge.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--jsonl", help="local JSONL release (e.g. the synthetic fixture)")
    src.add_argument("--hf-config", help="HuggingFace release config, e.g. brokenarxiv-0526")
    ap.add_argument("--split", default="train")
    ap.add_argument("--cache-dir", default=None, help="HF cache dir (keep OUTSIDE the repo)")
    ap.add_argument("--profile", type=Path, default=Path("profiles/default.yaml"), metavar="PATH",
                    help="RunProfile YAML to run each false statement under (default: profiles/default.yaml)")
    ap.add_argument("--limit", type=int, default=None, help="run only the first N false statements")
    ap.add_argument("--out", type=Path, default=Path("brokenarxiv_runs.jsonl"), metavar="PATH",
                    help="output JSONL file — one diffable do-not-prove row per false statement")
    ap.add_argument("--ledgers-dir", type=Path, default=None, metavar="DIR",
                    help="dump each run's proof artifacts here for scrutiny — a 'proven' (bluffed) row "
                         "on a FALSE statement is exactly what must be inspectable")
    ap.add_argument("--triage", action="store_true",
                    help="before grading each row, aim the deterministic exact-integer checker at the "
                         "GOAL STATEMENT's falsity via one LLM-proposed inert falsification spec "
                         "(agent.tools.statement_triage). A confirmed integer counterexample grades the "
                         "row 2 with refutation_signal=numeric_triage_counterexample. This is a "
                         "SEARCH/triage signal (heuristic statement->spec translation, deterministic "
                         "integer check), NOT a soundness verdict; it never feeds the gate. Default OFF.")
    ap.add_argument("--quiet", action="store_true", help="suppress per-run progress on stderr")
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        if args.jsonl:
            dataset = BrokenArxivDataset.from_jsonl(args.jsonl)
        else:
            dataset = BrokenArxivDataset.from_huggingface(args.hf_config, args.split, args.cache_dir)
        profile = RunProfile.from_yaml(args.profile)
    except Exception as e:  # noqa: BLE001 — a load/parse failure is a clean CLI error, not a crash.
        print(f"ERROR: {e}", file=sys.stderr)
        print("  (HF release install: pip install 'mathagent[benchmark]')", file=sys.stderr)
        return 2
    run_sweep(dataset, profile, args.out, limit=args.limit, verbose=not args.quiet,
              ledgers_dir=args.ledgers_dir, triage=args.triage)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
