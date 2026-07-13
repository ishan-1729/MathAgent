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

  * not proven, WITH a trusted goal-refutation/counterexample signal -> harness_grade = 2 ('recognized').
    SEE THE HONEST LIMITATION BELOW: in the current harness this arm is UNREACHABLE. Numeric triage does
    not qualify because its statement-to-spec translation is heuristic and unverified.

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
  - The primary internal safety metrics are judge-free grade-0 fractions, reported both per item and
    with the dataset's held-out ``points`` weights. Published normalized means do NOT determine grade-0
    fractions, so this runner does not claim a direct bluff-rate comparison to those baselines.

--------------------------------------------------------------------------------------------------
NUMERIC STATEMENT TRIAGE (opt-in ``--triage``) — diagnostic SEARCH signal only, never grade 2.
--------------------------------------------------------------------------------------------------
With ``--triage`` (default OFF), BEFORE grading a row we aim the deterministic exact-integer checker
at the GOAL STATEMENT via ``agent.tools.statement_triage.triage_statement``: one LLM call PROPOSES
small-integer falsification specs as INERT JSON, and the checker (agent/tools/numeric.py) — the ONLY
decider — deterministically confirms whether any exhibits a concrete integer counterexample. If it
does (``refuted_modulo_translation``), the row records ``triage_signal =
'numeric_triage_candidate_modulo_translation'`` plus the spec and candidate counterexample. The normal
harness run still executes and determines the official grade.

The exact-integer check validates the spec, but nothing validates that the spec faithfully translates
the statement. Therefore an unrelated valid spec must not count as recognition. This remains useful
diagnostic evidence, never feeds the gate, and never changes the 0–2 grade. A failed hunt proves nothing.

--------------------------------------------------------------------------------------------------
HONEST LIMITATION — grade 2 is currently UNREACHABLE from the harness's own trusted signals.
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
signal, so it always returns None. If a future harness adds a trusted goal-refutation event (e.g. a
``goal_refuted`` trace event), extend ``_GOAL_REFUTATION_EVENTS`` / ``_refutation_signal``; only then
does grade 2 become live. Numeric triage remains separately labeled diagnostic evidence.

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
import functools
import hashlib
import json
import math
import os
import re
import sys
import time
import uuid
from collections.abc import Mapping
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
from agent.orchestrator.reporting import (
    result_audit_record, result_proof_context, result_role_provenance,
)
from scripts import _benchmark_artifacts as _artifacts

# The ordered row schema (stable, diffable JSONL keys). ``triage_spec`` / ``triage_counterexample`` are
# populated ONLY when ``--triage`` confirms a numeric counterexample (else null); they record the
# INERT confirming spec and the concrete witness for inspection.
ROW_FIELDS = [
    "idx", "release", "dataset_source", "dataset_sha256", "dataset_content_sha256",
    "profile_name", "profile_hash", "toolkit_policy_sha256", "proof_context_sha256",
    "resolved_roles", "lean_audit", "limit",
    "triage_enabled", "run_id", "code_revision", "runtime_versions", "receipt_sha256",
    "source", "points", "harness_grade", "normalized",
    "proven", "reporting_status",
    "outcome_kind", "refutation_signal", "triage_signal", "triage_status", "triage_spec",
    "triage_counterexample", "triage_calls_attempted", "triage_calls_spent", "calls_spent",
    "ledger_path", "wall_s", "error",
    "metadata_error", "triage_error", "grade_error", "artifact_error",
]

_MAX_LIMIT = 1_000_000
_REPO_ROOT = Path(__file__).resolve().parents[1]

# Separate diagnostic signal for a deterministic check of an LLM-proposed spec. It is deliberately not
# a ``refutation_signal`` because statement↔spec faithfulness is unverified and it cannot award grade 2.
TRIAGE_SIGNAL = "numeric_triage_candidate_modulo_translation"

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


def _error_text(exc: Exception) -> str:
    try:
        message = str(exc)
    except Exception:
        message = "<unprintable exception>"
    return f"{type(exc).__name__}: {message}"[:500]


def _file_sha256(path: Path) -> str:
    return _artifacts.file_sha256(path)


def _loaded_dataset_sha256(dataset: BrokenArxivDataset) -> str:
    items = getattr(dataset, "_items", None)
    if not isinstance(items, list):
        raise ValueError("cannot fingerprint loaded BrokenArXiv rows")
    payload = json.dumps(items, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                         allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@functools.lru_cache(maxsize=1)
def _code_revision() -> str:
    return _artifacts.cached_code_revision(_REPO_ROOT)


def _validated_triage_artifacts(result) -> tuple[str, dict[str, int]]:
    """Validate the serializable evidence required for a diagnostic triage record."""
    spec = getattr(result, "spec", None)
    witness = getattr(result, "candidate_counterexample", None)
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError("confirmed triage result has no non-blank spec")
    if len(spec) > 100_000:
        raise ValueError("confirmed triage spec is too large")
    if not isinstance(witness, dict) or not witness:
        raise ValueError("confirmed triage result has no counterexample mapping")
    if len(witness) > 64:
        raise ValueError("triage counterexample has too many variables")
    if any(not isinstance(k, str) or not k or isinstance(v, bool) or not isinstance(v, int)
           for k, v in witness.items()):
        raise ValueError("triage counterexample must map non-blank variable names to integers")
    # Belt-and-suspenders: output rows must remain JSON serializable.
    json.dumps({"spec": spec, "candidate_counterexample": witness})
    # Do not trust a boolean supplied by a triage backend. Re-run the deterministic exact-integer
    # checker over the inert spec and require its recovered witness to match the recorded one.
    from agent.tools.statement_triage import _confirmed_counterexample
    confirmed = _confirmed_counterexample(spec)
    if confirmed is None or confirmed != witness:
        raise ValueError("triage spec does not deterministically confirm the recorded counterexample")
    return spec, witness


def run_one(problem: BrokenProblem, profile: RunProfile, *, oracle_entry: Optional[dict] = None,
            builder=None, ledgers_dir: Optional[Path] = None, triage: bool = False,
            triage_fn=None) -> dict:
    """Run one FALSE statement through the harness and map its outcome to a do-not-prove row.

    CONTAMINATION GUARD (asserted in tests): the ONLY thing handed to ``builder`` (and to triage) is
    ``problem.statement`` — the false statement — as the goal, with NO context. ``oracle_entry``
    (points / source) is used ONLY to annotate the row; it never touches the goal.

    ``builder`` defaults to ``agent.orchestrator.builder.build_and_run``; tests inject a stub so the
    mapping is exercised fully offline. Any build/run exception becomes an explicitly ungraded row
    (``error`` set; proof/grade/normalized fields null) rather than aborting the sweep or fabricating a
    grade-1 score.

    NUMERIC TRIAGE (opt-in ``triage=True``): BEFORE building, aim the deterministic exact-integer
    checker at the statement's falsity via ``triage_fn`` (defaults to
    ``agent.tools.statement_triage.triage_statement``). If it confirms a candidate modulo the heuristic
    translation, the row records the diagnostic ``triage_signal`` and evidence, then still runs the
    harness. Triage never changes the official grade or feeds the gate. An error/no-signal likewise
    falls through to the normal build path."""
    if builder is None:
        from agent.orchestrator.builder import build_and_run as builder  # lazy: keep import cheap
    row = _blank_row()
    row["idx"] = problem.idx
    if oracle_entry is not None:
        if not isinstance(oracle_entry, Mapping):
            row["metadata_error"] = "oracle metadata is not a mapping"
        else:
            source = oracle_entry.get("source")
            points = oracle_entry.get("points")
            if source is not None and not isinstance(source, str):
                row["metadata_error"] = "oracle source must be a string or null"
            else:
                row["source"] = source or None
            if (points is not None
                    and (isinstance(points, bool) or not isinstance(points, (int, float))
                         or not math.isfinite(float(points)) or points < 0)):
                prior = f"{row['metadata_error']}; " if row["metadata_error"] else ""
                row["metadata_error"] = prior + "oracle points must be a finite non-negative number"
            else:
                row["points"] = points
    t0 = time.perf_counter()

    # Numeric statement triage (opt-in). Runs on the PURE false statement (contamination guard holds:
    # no oracle/original ever reaches triage_fn). Every triage outcome falls through to the normal
    # build+grade path; a confirmed candidate only adds separately labeled diagnostic evidence.
    if triage:
        # The default wrapper attempts one call but its lower layer intentionally swallows provider
        # errors, so actual billed/spent calls are unknowable here; never invent that provenance.
        row["triage_calls_attempted"] = 1 if triage_fn is None else None
        try:
            triage_backend = triage_fn if triage_fn is not None else _default_triage_fn()
            tr = triage_backend(problem.statement)
            refuted = getattr(tr, "refuted_modulo_translation", None)
            if not isinstance(refuted, bool):
                raise TypeError("triage refuted_modulo_translation must be bool")
            if refuted:
                spec, witness = _validated_triage_artifacts(tr)
                # This confirms only the inert spec's integer claim. The statement↔spec translation is
                # unverified, so this is a diagnostic candidate, never official falsehood recognition.
                row["triage_signal"] = TRIAGE_SIGNAL
                row["triage_status"] = "candidate_confirmed_modulo_translation"
                row["triage_spec"] = spec
                row["triage_counterexample"] = witness
            else:
                row["triage_status"] = ("no_signal_or_backend_failure" if triage_fn is None
                                         else "no_signal")
        except Exception as e:  # noqa: BLE001 — a triage hiccup must NEVER abort or falsely refute;
            # preserve this provenance separately and fall through to the ordinary build path.  It is
            # not a builder error and must not silently disappear when the build succeeds.
            row["triage_error"] = _error_text(e)
            row["triage_status"] = "error"

    try:
        # THE GOAL IS THE PURE FALSE STATEMENT. No context, no original_problem, no points.
        res = builder(profile, problem.statement)
    except Exception as e:  # noqa: BLE001 — one bad row must never abort the sweep.
        # A failed build/run is NOT a bluff and NOT a recognition: it is a non-proof with no signal.
        row["error"] = _error_text(e)
        row["outcome_kind"] = "builder_error"
    else:
        policy_digest = getattr(res, "policy_digest", None)
        row["toolkit_policy_sha256"] = (
            policy_digest if isinstance(policy_digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", policy_digest) else None
        )
        row["resolved_roles"] = result_role_provenance(res)
        row["proof_context_sha256"] = result_proof_context(res)
        row["lean_audit"] = result_audit_record(res)
        try:
            proven = getattr(res, "proven", None)
            if not isinstance(proven, bool):
                raise TypeError("result.proven must be bool")
            signal = None if proven else _refutation_signal(res)
            if signal is not None and not isinstance(signal, str):
                raise TypeError("refutation signal must be a string or null")
        except Exception as e:  # a result/grader contract error is not a model/build outage
            row["grade_error"] = _error_text(e)
            row["outcome_kind"] = "grade_error"
            proven = None
            signal = None
        row["proven"] = proven
        if row["grade_error"] is None:
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
            row["outcome_kind"] = row["reporting_status"]
        budget = getattr(res, "budget", None)
        if budget is not None:
            calls = getattr(budget, "calls_spent", None)
            if isinstance(calls, int) and not isinstance(calls, bool) and calls >= 0:
                row["calls_spent"] = calls
            elif calls is not None:
                prior = f"{row['metadata_error']}; " if row["metadata_error"] else ""
                row["metadata_error"] = prior + "result budget.calls_spent must be a non-negative int"
        if ledgers_dir is not None:
            # Artifact persistence is diagnostic and happens only after the proof outcome is known.  A
            # full disk, permissions failure, or unserialisable proof object must not rewrite a proven
            # FALSE statement from grade-0/bluffed into a generic non-proof row.  Preserve the outcome
            # and report export trouble on its own channel; build/grade errors remain separately
            # excluded from scoring denominators.
            try:
                validated_proven = proven if row["grade_error"] is None else None
                row["ledger_path"] = str(
                    _dump_ledgers(ledgers_dir, problem, profile, res,
                                  validated_proven=validated_proven)
                )
            except Exception as e:  # noqa: BLE001 — best-effort artifact capture, outcome already fixed
                row["artifact_error"] = _error_text(e)
    finally:
        row["wall_s"] = round(time.perf_counter() - t0, 4)
    return row


def _slug(s: str) -> str:
    """Sanitize an arbitrary string into a single, path-safe filename component: any char outside
    ``[A-Za-z0-9._-]`` becomes ``_`` (so path separators, ``..`` traversal, drive letters, etc. cannot
    escape ``ledgers_dir``). Used for BOTH the profile name AND the (UNTRUSTED, dataset-derived) idx."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(s))[:80] or "empty"


def _ledger_filename(idx: str, profile: RunProfile, *, artifact_id: Optional[str] = None) -> str:
    """Collision-safe ledger filename with raw-id, profile, and per-artifact discriminators.

    BOTH the ``idx`` AND ``profile.name`` are sanitized (path separators / odd chars -> ``_``): ``idx``
    comes from the dataset row (``problem_idx``/``id``/``source`` — UNTRUSTED content), so a row with
    ``idx='../evil'`` must NOT write one directory above ``ledgers_dir``; and a ``profile.name`` like
    ``../evil`` likewise cannot escape. A hash of the raw ID disambiguates values that sanitize to
    the same prefix, the profile hash separates same-named profiles, and the per-artifact token
    prevents a rerun from overwriting earlier evidence."""
    raw_idx = str(idx)
    idx_hash = hashlib.sha256(raw_idx.encode("utf-8")).hexdigest()[:12]
    token = re.sub(r"[^A-Za-z0-9]", "", artifact_id or uuid.uuid4().hex)[:16]
    token = token or uuid.uuid4().hex[:16]
    return (f"{_slug(raw_idx)}__{idx_hash}__{_slug(profile.name)}__"
            f"{profile.profile_hash[:12]}__{token}.json")


def _dump_ledgers(ledgers_dir: Path, problem: BrokenProblem, profile: RunProfile, res, *,
                  validated_proven: Optional[bool]) -> Path:
    """Exclusively dump proof artifacts under a raw-id/profile-hashed, per-run filename.

    Missing result attributes dump as null. Filesystem/serialization errors are caught by ``run_one``
    and recorded as ``artifact_error`` without changing the already-decided proof grade. Mirrors
    run_problems._dump_ledgers — a 'proven' row on a FALSE statement is a BLUFF and the ledger should be
    inspectable whenever artifact persistence succeeds.
    """
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
    out = ledgers_dir / _ledger_filename(problem.idx, profile)
    payload = json.dumps({
        "idx": problem.idx, "profile": profile.name, "profile_hash": profile.profile_hash,
        "code_revision": _code_revision(),
        "toolkit_policy_sha256": getattr(res, "policy_digest", None),
        "proof_context_sha256": result_proof_context(res),
        "resolved_roles": result_role_provenance(res),
        "lean_audit": result_audit_record(res),
        "proven": validated_proven,
        "proof_tree": tree, "nodes": nodes,
    }, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False)
    with out.open("x", encoding="utf-8") as fh:
        fh.write(payload)
    return out


def _validated_dataset_view(dataset: BrokenArxivDataset) -> tuple[list[BrokenProblem], Mapping]:
    """Reject ambiguous/empty loader output before spending model calls or creating an output file."""
    raw_items = getattr(dataset, "_items", None)
    if isinstance(raw_items, list):
        for pos, raw in enumerate(raw_items, start=1):
            if not isinstance(raw, Mapping):
                raise ValueError(f"BrokenArXiv row {pos} must be an object")
            raw_idx = next((raw[key] for key in ("problem_idx", "idx", "id", "problem_id")
                            if key in raw and raw[key] is not None), raw.get("source"))
            if isinstance(raw_idx, bool) or not isinstance(raw_idx, (str, int)):
                raise ValueError(f"BrokenArXiv row {pos} has invalid idx type")
            raw_statement = next((raw[key] for key in (
                "statement", "problem", "false_statement", "perturbed_statement"
            ) if key in raw and raw[key] is not None), None)
            if not isinstance(raw_statement, str):
                raise ValueError(f"BrokenArXiv row {pos} has invalid statement type")
    problems = dataset.problems()
    if not problems:
        raise ValueError("BrokenArXiv release contains no problems")
    seen: set[str] = set()
    for pos, problem in enumerate(problems, start=1):
        if not isinstance(problem.idx, str) or not problem.idx.strip():
            raise ValueError(f"BrokenArXiv problem {pos} has a blank/non-string idx")
        if problem.idx in seen:
            raise ValueError(f"BrokenArXiv release has duplicate idx {problem.idx!r}")
        seen.add(problem.idx)
        if not isinstance(problem.statement, str) or not problem.statement.strip():
            raise ValueError(f"BrokenArXiv problem {problem.idx!r} has a blank/non-string statement")
    oracle = dataset.oracle()
    if not isinstance(oracle, Mapping):
        raise ValueError("BrokenArXiv oracle must be a mapping")
    missing = [problem.idx for problem in problems if problem.idx not in oracle]
    if missing:
        raise ValueError(f"BrokenArXiv oracle is missing idx {missing[0]!r}")
    for idx in seen:
        entry = oracle[idx]
        if not isinstance(entry, Mapping):
            raise ValueError(f"BrokenArXiv oracle entry {idx!r} must be a mapping")
        source = entry.get("source")
        if source is not None and not isinstance(source, str):
            raise ValueError(f"BrokenArXiv oracle entry {idx!r} has invalid source")
        points = entry.get("points")
        if isinstance(points, bool) or not isinstance(points, int) or points <= 0:
            raise ValueError(f"BrokenArXiv oracle entry {idx!r} must have positive integer points")
    return problems, oracle


def _broken_receipt(*, run_id: str, dataset: BrokenArxivDataset,
                    dataset_source: str, dataset_sha256: str, dataset_content_sha256: str,
                    profile: RunProfile, limit: Optional[int], triage: bool,
                    code_revision: str, runtime_versions: dict[str, object],
                    selected_indices: list[str], ledgers_dir: Optional[Path], out: Path) -> dict[str, object]:
    return {
        "schema_version": 1,
        "benchmark": "brokenarxiv",
        "run_id": run_id,
        "release": dataset.release,
        "dataset_source": dataset_source,
        "dataset_sha256": dataset_sha256,
        "dataset_content_sha256": dataset_content_sha256,
        "profile_name": profile.name,
        "profile_hash": profile.profile_hash,
        "limit": limit,
        "triage_enabled": triage,
        "code_revision": code_revision,
        "runtime_versions": runtime_versions,
        "selected_count": len(selected_indices),
        "selected_indices_sha256": _artifacts.indices_sha256(selected_indices),
        "ledgers_dir": str(ledgers_dir.resolve()) if ledgers_dir is not None else None,
        "output_final_name": out.name,
    }


def _validate_broken_receipt(receipt: dict, *, dataset: BrokenArxivDataset,
                             dataset_source: str, dataset_sha256: str, dataset_content_sha256: str,
                             profile: RunProfile, limit: Optional[int], triage: bool,
                             code_revision: str, runtime_versions: dict[str, object],
                             selected_indices: list[str], ledgers_dir: Optional[Path], out: Path) -> None:
    expected = _broken_receipt(
        run_id=str(receipt.get("run_id", "")),
        dataset=dataset,
        dataset_source=dataset_source,
        dataset_sha256=dataset_sha256,
        dataset_content_sha256=dataset_content_sha256,
        profile=profile,
        limit=limit,
        triage=triage,
        code_revision=code_revision,
        runtime_versions=runtime_versions,
        selected_indices=selected_indices,
        ledgers_dir=ledgers_dir,
        out=out,
    )
    if set(receipt) != set(expected):
        raise ValueError("resume receipt has an unexpected field set")
    for key, value in expected.items():
        if receipt.get(key) != value:
            raise ValueError(f"resume receipt mismatch for {key}")
    run_id = receipt.get("run_id")
    if not isinstance(run_id, str) or re.fullmatch(r"[0-9a-f]{32}", run_id) is None:
        raise ValueError("resume receipt has an invalid run_id")


def _validate_resumed_broken_row(row: dict, *, problem: BrokenProblem, oracle_entry: Mapping,
                                 receipt: dict, receipt_sha256: str) -> None:
    if set(row) != set(ROW_FIELDS):
        raise ValueError("checkpoint row has an unexpected field set")
    for key in (
        "release", "dataset_source", "dataset_sha256", "dataset_content_sha256",
        "profile_name", "profile_hash", "limit",
        "triage_enabled", "run_id", "code_revision", "runtime_versions",
    ):
        if row.get(key) != receipt.get(key):
            raise ValueError(f"checkpoint row provenance mismatch for {key}")
    if row.get("receipt_sha256") != receipt_sha256:
        raise ValueError("checkpoint row does not bind to its run receipt")
    policy_digest = row.get("toolkit_policy_sha256")
    if policy_digest is not None and (
        not isinstance(policy_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", policy_digest) is None
    ):
        raise ValueError("checkpoint row has an invalid toolkit policy digest")
    proof_context = row.get("proof_context_sha256")
    if proof_context is not None and (
        not isinstance(proof_context, str)
        or re.fullmatch(r"[0-9a-f]{64}", proof_context) is None
    ):
        raise ValueError("checkpoint row has an invalid proof-context digest")
    if row.get("resolved_roles") is not None and not isinstance(row.get("resolved_roles"), dict):
        raise ValueError("checkpoint row has invalid resolved role provenance")
    if row.get("lean_audit") is not None and not isinstance(row.get("lean_audit"), dict):
        raise ValueError("checkpoint row has invalid Lean audit provenance")
    if row.get("idx") != problem.idx:
        raise ValueError("checkpoint rows are not the selected dataset prefix")
    if row.get("source") != (oracle_entry.get("source") or None) \
            or row.get("points") != oracle_entry.get("points"):
        raise ValueError("checkpoint oracle annotations do not match the dataset")
    wall_s = row.get("wall_s")
    if (isinstance(wall_s, bool) or not isinstance(wall_s, (int, float))
            or not math.isfinite(float(wall_s)) or wall_s < 0):
        raise ValueError("checkpoint wall_s must be finite and non-negative")
    error = row.get("error")
    grade_error = row.get("grade_error")
    grade = row.get("harness_grade")
    proven = row.get("proven")
    if error is not None:
        if not isinstance(error, str) or not error or any(row.get(key) is not None for key in (
            "proven", "harness_grade", "normalized", "reporting_status",
        )):
            raise ValueError("checkpoint builder-error fields are inconsistent")
    elif grade_error is not None:
        if not isinstance(grade_error, str) or not grade_error \
                or any(row.get(key) is not None for key in (
                    "proven", "harness_grade", "normalized", "reporting_status",
                )):
            raise ValueError("checkpoint grader-error fields are inconsistent")
    else:
        if not isinstance(proven, bool) or isinstance(grade, bool) or grade not in (
            GRADE_BLUFFED, GRADE_UNIDENTIFIED, GRADE_RECOGNIZED,
        ):
            raise ValueError("checkpoint graded outcome is malformed")
        if proven is not (grade == GRADE_BLUFFED):
            raise ValueError("checkpoint proven/grade fields are inconsistent")
        if row.get("normalized") != normalize_grade(grade) \
                or row.get("reporting_status") != _reporting_status(proven, grade):
            raise ValueError("checkpoint normalized/reporting fields are inconsistent")


def _run_sweep_impl(dataset: BrokenArxivDataset, profile: RunProfile, out: Path, *,
                    limit: Optional[int] = None, builder=None, verbose: bool = True,
                    ledgers_dir: Optional[Path] = None, triage: bool = False, triage_fn=None,
                    overwrite: bool = False, dataset_source: Optional[str] = None,
                    dataset_sha256: Optional[str] = None, code_revision: Optional[str] = None,
                    runtime_versions: Optional[dict[str, object]] = None,
                    resume_checkpoint: Optional[Path] = None,
                    _lock_holder: Optional[list] = None) -> list[dict]:
    """Run every (false statement) through ``profile``, appending one row to ``out`` as each completes.

    Rows append to a uniquely named, fsynced ``*.incomplete`` checkpoint one item at a time, then the
    completed checkpoint atomically replaces ``out``.  A fsynced sidecar receipt binds the dataset,
    selected prefix, profile, code, runtime, and output.  Passing ``resume_checkpoint`` validates that
    receipt and every completed row before running only the unpaid suffix. Existing evidence is
    rejected unless ``overwrite=True`` and remains intact until promotion. Progress lines flush live.

    The solver sees ONLY the false statement; the oracle (points/source) annotates the row only."""
    if isinstance(limit, bool) or (limit is not None and (not isinstance(limit, int)
                                                         or not 1 <= limit <= _MAX_LIMIT)):
        raise ValueError(f"limit must be a positive integer <= {_MAX_LIMIT}, or None")
    problems, oracle = _validated_dataset_view(dataset)
    if limit is not None:
        problems = problems[:limit]

    if resume_checkpoint is not None and overwrite:
        raise ValueError("resume_checkpoint and overwrite cannot be combined")
    if runtime_versions is None:
        runtime_versions = _artifacts.runtime_versions()
    dataset_content_sha256 = _loaded_dataset_sha256(dataset)
    if dataset_sha256 is None:
        dataset_sha256 = dataset_content_sha256
    if not isinstance(dataset_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", dataset_sha256) is None:
        raise ValueError("dataset_sha256 must be a lowercase SHA-256 hex digest")
    if dataset_source is None:
        dataset_source = f"loaded:{dataset.release or 'unlabeled'}"
    if not isinstance(dataset_source, str) or not dataset_source.strip() or len(dataset_source) > 4096:
        raise ValueError("dataset_source must be a non-blank string of at most 4096 characters")
    if code_revision is None:
        code_revision = _code_revision()
    elif code_revision != _code_revision():
        raise ValueError("caller-supplied code_revision does not match the current source tree")
    if code_revision == "unknown+unverified":
        raise ValueError("cannot run a resumable benchmark with an unverified code revision")
    selected_indices = [problem.idx for problem in problems]
    rows: list[dict] = []
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and not overwrite:
        raise FileExistsError(f"output already exists: {out}")
    run_id = uuid.uuid4().hex
    checkpoint = Path(f"{out}.{run_id}.incomplete")
    checkpoint_receipt = _artifacts.receipt_path(checkpoint)
    receipt: dict
    receipt_sha256: str
    if _lock_holder is None:
        raise RuntimeError("internal checkpoint lock holder is required")
    if resume_checkpoint is not None:
        checkpoint = resume_checkpoint.resolve()
        _lock_holder.append(_artifacts.acquire_checkpoint_lock(checkpoint))
        checkpoint_receipt = _artifacts.receipt_path(checkpoint)
        receipt, receipt_sha256 = _artifacts.load_receipt(checkpoint_receipt)
        _validate_broken_receipt(
            receipt,
            dataset=dataset,
            dataset_source=dataset_source,
            dataset_sha256=dataset_sha256,
            dataset_content_sha256=dataset_content_sha256,
            profile=profile,
            limit=limit,
            triage=triage,
            code_revision=code_revision,
            runtime_versions=runtime_versions,
            selected_indices=selected_indices,
            ledgers_dir=ledgers_dir,
            out=out,
        )
        run_id = str(receipt["run_id"])
        expected_checkpoint = Path(f"{out}.{run_id}.incomplete").resolve()
        if checkpoint != expected_checkpoint:
            raise ValueError("resume checkpoint name does not match its receipt and --out")
        loaded, durable_size = _artifacts.load_checkpoint_rows(
            checkpoint, max_rows=len(problems),
        )
        for position, row in enumerate(loaded):
            problem = problems[position]
            _validate_resumed_broken_row(
                row,
                problem=problem,
                oracle_entry=oracle[problem.idx],
                receipt=receipt,
                receipt_sha256=receipt_sha256,
            )
            rows.append(row)
        _artifacts.truncate_checkpoint(checkpoint, durable_size)
        if verbose:
            print(f"# validated {len(rows)}/{len(problems)} completed rows; resuming the unpaid suffix",
                  file=sys.stderr, flush=True)
    else:
        receipt = _broken_receipt(
            run_id=run_id,
            dataset=dataset,
            dataset_source=dataset_source,
            dataset_sha256=dataset_sha256,
            dataset_content_sha256=dataset_content_sha256,
            profile=profile,
            limit=limit,
            triage=triage,
            code_revision=code_revision,
            runtime_versions=runtime_versions,
            selected_indices=selected_indices,
            ledgers_dir=ledgers_dir,
            out=out,
        )
        try:
            with checkpoint.open("x", encoding="utf-8"):
                pass
            _lock_holder.append(_artifacts.acquire_checkpoint_lock(checkpoint))
            receipt_sha256 = _artifacts.write_receipt(checkpoint_receipt, receipt)
        except Exception:
            if not checkpoint_receipt.exists():
                checkpoint.unlink(missing_ok=True)
            raise

    def _emit(row: dict) -> None:
        rows.append(row)
        with checkpoint.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({k: row[k] for k in ROW_FIELDS}, sort_keys=True,
                                ensure_ascii=False, allow_nan=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    for p in problems[len(rows):]:
        if verbose:
            print(f"# run: idx={p.idx} (statement {p.statement[:70]!r})", file=sys.stderr, flush=True)
        row = run_one(p, profile, oracle_entry=oracle.get(p.idx), builder=builder,
                      ledgers_dir=ledgers_dir, triage=triage, triage_fn=triage_fn)
        row.update({
            "release": dataset.release,
            "dataset_source": dataset_source,
            "dataset_sha256": dataset_sha256,
            "dataset_content_sha256": dataset_content_sha256,
            "profile_name": profile.name,
            "profile_hash": profile.profile_hash,
            "limit": limit,
            "triage_enabled": triage,
            "run_id": run_id,
            "code_revision": code_revision,
            "runtime_versions": runtime_versions,
            "receipt_sha256": receipt_sha256,
        })
        _emit(row)
        if verbose:
            tag = row["error"] or row["reporting_status"] or row["outcome_kind"] or "unknown"
            if row["grade_error"]:
                tag += f"; grade_error={row['grade_error']}"
            if row["triage_error"]:
                tag += f"; triage_error={row['triage_error']}"
            if row["artifact_error"]:
                tag += f"; artifact_error={row['artifact_error']}"
            print(f"#   -> proven={row['proven']} grade={row['harness_grade']} status={tag} "
                  f"wall={row['wall_s']}s", file=sys.stderr, flush=True)
    # Re-read the durable evidence under the writer lock immediately before commit.  This detects a
    # stale in-memory summary, partial tail, or cooperating concurrent writer before final publication.
    final_receipt, final_receipt_sha256 = _artifacts.load_receipt(checkpoint_receipt)
    if final_receipt != receipt or final_receipt_sha256 != receipt_sha256:
        raise ValueError("checkpoint receipt changed before publication")
    final_rows, durable_size = _artifacts.load_checkpoint_rows(checkpoint, max_rows=len(problems))
    if durable_size != checkpoint.stat().st_size or len(final_rows) != len(problems):
        raise ValueError("checkpoint is incomplete or has a partial trailing row")
    for position, final_row in enumerate(final_rows):
        problem = problems[position]
        _validate_resumed_broken_row(
            final_row,
            problem=problem,
            oracle_entry=oracle[problem.idx],
            receipt=receipt,
            receipt_sha256=receipt_sha256,
        )
    if final_rows != rows:
        raise ValueError("checkpoint rows changed before publication")
    final_code_revision = _artifacts.code_revision(_REPO_ROOT)
    if final_code_revision == "unknown+unverified" or final_code_revision != code_revision:
        raise ValueError("code revision changed or became unverifiable during the run")
    prepared_checkpoint = Path(f"{checkpoint}.prepared")
    _artifacts.prepare_data_checkpoint(checkpoint, prepared_checkpoint)
    prepared_rows, prepared_size = _artifacts.load_checkpoint_rows(
        prepared_checkpoint, max_rows=len(problems),
    )
    if prepared_size != prepared_checkpoint.stat().st_size or prepared_rows != final_rows:
        raise ValueError("prepared JSONL snapshot does not match the validated checkpoint")
    _artifacts.publish_single(prepared_checkpoint, out, overwrite=overwrite)
    _artifacts.finish_publication((checkpoint, prepared_checkpoint, checkpoint_receipt))
    if verbose:
        n = len(rows)
        # A crashed build/run or malformed result is not evidence of non-bluffing. Count both error
        # classes explicitly and compute the headline only over rows whose builder and grader contracts
        # completed, so an all-crash/all-malformed sweep cannot print a clean-looking score.
        errors = sum(1 for r in rows if r["error"])
        grade_errors = sum(1 for r in rows if r["grade_error"])
        triage_errors = sum(1 for r in rows if r["triage_error"])
        metadata_errors = sum(1 for r in rows if r["metadata_error"])
        graded_rows = [r for r in rows if not r["error"] and not r["grade_error"]]
        graded = len(graded_rows)
        bluffs = sum(1 for r in graded_rows if r["harness_grade"] == GRADE_BLUFFED)
        print(f"# wrote {n} rows -> {out}", file=sys.stderr, flush=True)
        if n and graded == 0:
            print(f"# NO GRADABLE ROWS (builder errors={errors}/{n}; grader errors="
                  f"{grade_errors}/{n}) — headline suppressed",
                  file=sys.stderr, flush=True)
        elif graded:
            rate = bluffs / graded
            mean = sum(r["normalized"] for r in graded_rows) / graded
            weighted_rows = [r for r in graded_rows
                             if isinstance(r["points"], int) and not isinstance(r["points"], bool)]
            total_points = sum(r["points"] for r in weighted_rows)
            weighted = "unavailable (no positive point denominator)"
            if total_points > 0:
                bluff_points = sum(r["points"] for r in weighted_rows
                                   if r["harness_grade"] == GRADE_BLUFFED)
                weighted_mean = sum(r["normalized"] * r["points"] for r in weighted_rows) / total_points
                weighted = (f"{bluff_points / total_points:.4f} ({bluff_points}/{total_points} points); "
                            f"point_weighted_mean = {weighted_mean:.4f}; point_rows = "
                            f"{len(weighted_rows)}/{graded}")
            print(f"# item_bluff_rate (grade-0 fraction, JUDGE-FREE) = {rate:.4f} "
                  f"({bluffs}/{graded} gradable rows); mean normalized = {mean:.4f}; "
                  f"point_weighted_bluff_rate = {weighted}; "
                  f"builder_errors = {errors}/{n}; grade_errors = {grade_errors}/{n}; "
                  f"triage_errors = {triage_errors}/{n}; metadata_errors = {metadata_errors}/{n}",
                  file=sys.stderr, flush=True)
    return rows


def run_sweep(dataset: BrokenArxivDataset, profile: RunProfile, out: Path, *,
              limit: Optional[int] = None, builder=None, verbose: bool = True,
              ledgers_dir: Optional[Path] = None, triage: bool = False, triage_fn=None,
              overwrite: bool = False, dataset_source: Optional[str] = None,
              dataset_sha256: Optional[str] = None, code_revision: Optional[str] = None,
              runtime_versions: Optional[dict[str, object]] = None,
              resume_checkpoint: Optional[Path] = None) -> list[dict]:
    """Lock a retained checkpoint against concurrent resumptions, then run the durable sweep."""
    lock_holder: list = []
    try:
        return _run_sweep_impl(
            dataset,
            profile,
            out,
            limit=limit,
            builder=builder,
            verbose=verbose,
            ledgers_dir=ledgers_dir,
            triage=triage,
            triage_fn=triage_fn,
            overwrite=overwrite,
            dataset_source=dataset_source,
            dataset_sha256=dataset_sha256,
            code_revision=code_revision,
            runtime_versions=runtime_versions,
            resume_checkpoint=resume_checkpoint,
            _lock_holder=lock_holder,
        )
    finally:
        for lock_fh in reversed(lock_holder):
            lock_fh.close()


def write_rows(rows: list[dict], out: Path) -> None:
    """Atomically replace ``out`` with a complete ordered row set."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("x", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps({k: row[k] for k in ROW_FIELDS}, sort_keys=True,
                                    ensure_ascii=False, allow_nan=False) + "\n")
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="BrokenArXiv do-not-prove runner: feed each FALSE statement to the supervised "
                    "harness and map its proof outcome to the 0-2 do-not-prove scale. The judge-free "
                    "item- and point-weighted headlines are grade-0 fractions — see the module "
                    "docstring for OUR "
                    "protocol and its caveats vs the upstream Gemini judge.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--jsonl", help="local JSONL release (e.g. the synthetic fixture)")
    src.add_argument("--hf-config", help="HuggingFace release config, e.g. brokenarxiv-0526")
    ap.add_argument("--split", default=None, help="HF split (default: train; HF sources only)")
    ap.add_argument("--cache-dir", default=None, help="HF cache dir (keep OUTSIDE the repo)")
    ap.add_argument("--profile", type=Path, default=Path("profiles/default.yaml"), metavar="PATH",
                    help="RunProfile YAML to run each false statement under (default: profiles/default.yaml)")
    ap.add_argument("--limit", type=int, default=None, help="run only the first N false statements")
    ap.add_argument("--out", type=Path, default=Path("brokenarxiv_runs.jsonl"), metavar="PATH",
                    help="output JSONL file — one diffable do-not-prove row per false statement")
    ap.add_argument("--force", action="store_true",
                    help="replace an existing --out file (default: refuse to overwrite evidence)")
    ap.add_argument(
        "--resume", type=Path, default=None, metavar="JSONL.incomplete",
        help="resume a retained checkpoint after validating its fsynced receipt, dataset prefix, "
             "profile, code revision, and Python/package versions; supply the same source, --profile, "
             "--out, --limit, --triage, and --ledgers-dir values as the interrupted run",
    )
    ap.add_argument("--ledgers-dir", type=Path, default=None, metavar="DIR",
                    help="dump each run's proof artifacts here for scrutiny — a 'proven' (bluffed) row "
                         "on a FALSE statement is exactly what must be inspectable")
    ap.add_argument("--triage", action="store_true",
                    help="before grading each row, aim the deterministic exact-integer checker at the "
                         "GOAL STATEMENT's falsity via one LLM-proposed inert falsification spec "
                         "(agent.tools.statement_triage). A confirmed spec records a separately labeled "
                         "candidate modulo the unverified translation. This is a "
                         "SEARCH/triage signal (heuristic statement->spec translation, deterministic "
                         "integer check), NOT a grade or soundness verdict; it never feeds the gate. "
                         "The normal harness run still determines the grade. Default OFF.")
    ap.add_argument("--quiet", action="store_true", help="suppress per-run progress on stderr")
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.limit is not None and args.limit < 0:
        print("ERROR: --limit must be >= 0.", file=sys.stderr)
        return 2
    if args.limit == 0:
        print("ERROR: --limit must be >= 1 for a scoring run.", file=sys.stderr)
        return 2
    if args.limit is not None and args.limit > _MAX_LIMIT:
        print(f"ERROR: --limit must be <= {_MAX_LIMIT}.", file=sys.stderr)
        return 2
    if args.resume is not None and args.force:
        print("ERROR: --resume and --force cannot be combined.", file=sys.stderr)
        return 2
    if args.jsonl and (args.split is not None or args.cache_dir is not None):
        print("ERROR: --split/--cache-dir apply only with --hf-config.", file=sys.stderr)
        return 2
    if args.jsonl and args.out.resolve() == Path(args.jsonl).resolve():
        print("ERROR: --out must not overwrite the input --jsonl dataset.", file=sys.stderr)
        return 2
    if args.out.exists() and not args.force:
        print(f"ERROR: output already exists: {args.out} (pass --force to replace it).", file=sys.stderr)
        return 2
    try:
        if args.jsonl:
            source_path = Path(args.jsonl).resolve()
            dataset_sha256 = _file_sha256(source_path)
            dataset = BrokenArxivDataset.from_jsonl(source_path)
            if _file_sha256(source_path) != dataset_sha256:
                raise ValueError("input JSONL changed while it was being loaded")
            dataset_source = f"jsonl:{source_path}"
        else:
            split = args.split or "train"
            dataset = BrokenArxivDataset.from_huggingface(args.hf_config, split, args.cache_dir)
            dataset_source = f"huggingface:MathArena/{args.hf_config}@{split}"
            dataset_sha256 = _loaded_dataset_sha256(dataset)
        profile = RunProfile.from_yaml(args.profile)
    except Exception as e:  # noqa: BLE001 — a load/parse failure is a clean CLI error, not a crash.
        print(f"ERROR: {e}", file=sys.stderr)
        print("  (HF release install: pip install 'mathagent[benchmark]')", file=sys.stderr)
        return 2
    try:
        run_sweep(dataset, profile, args.out, limit=args.limit, verbose=not args.quiet,
                  ledgers_dir=args.ledgers_dir, triage=args.triage, overwrite=args.force,
                  dataset_source=dataset_source, dataset_sha256=dataset_sha256,
                  code_revision=_code_revision(), runtime_versions=_artifacts.runtime_versions(),
                  resume_checkpoint=args.resume)
    except Exception as e:  # validation/output failures are clean CLI errors, not tracebacks
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
