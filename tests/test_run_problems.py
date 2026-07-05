"""Offline tests for scripts/run_problems.py (the benchmark problem-runner).

No CLI/model/Lean is invoked. We pin:

  * the loader parses all six authored folders: each READY folder yields a non-empty statement and the
    right tier; the ljunggren/triangular folders carry a per-problem ``allowed_inputs_note``;
  * the contaminated (x2_plus_1) folder loads but is NOT ready (skipped by the sweep);
  * a fake-builder sweep runs fully offline and writes well-formed rows carrying a profile_hash;
  * a blocked/malformed folder produces an error row (not a crash);
  * the per-problem citable-inputs clause appears in the goal handed to the builder EXACTLY when the
    problem declares per-problem inputs, and the row's ``injected_context`` flag matches.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from agent.orchestrator.run_profile import (
    ProviderKey,
    RoleSpec,
    RolesProfile,
    RunProfile,
    StageProfile,
)

# scripts/ is not a package; load run_problems.py by path (the same trick the module + test_ablate use).
_REPO = Path(__file__).resolve().parents[1]
_RP_PATH = _REPO / "scripts" / "run_problems.py"
_spec = importlib.util.spec_from_file_location("run_problems_cli", _RP_PATH)
rp = importlib.util.module_from_spec(_spec)
sys.modules["run_problems_cli"] = rp
_spec.loader.exec_module(rp)

_PROBLEMS_DIR = _REPO / "benchmarks" / "problems"


# --------------------------------------------------------------------------------------------------
# Stub build_and_run result (no driver constructed): just enough surface for run_one.
# --------------------------------------------------------------------------------------------------
class _StubBudget:
    def __init__(self, spent):
        self.calls_spent = spent


class _StubNode:
    class _State:
        name = "PROVEN"
    state = _State()
    proven = True   # Node.proven == state.is_success; run_one now counts proven_nodes via this attr.


class _StubDag:
    def __init__(self, n_nodes=1):
        self.nodes = {f"n{i}": _StubNode() for i in range(n_nodes)}


class _StubResult:
    def __init__(self, proven=True, *, authoritative=False, terminal=None, spent=4, nodes=1):
        self.proven = proven
        self.authoritative_elementary = authoritative
        self.terminal = terminal
        self.budget = _StubBudget(spent)
        self.dag = _StubDag(nodes)


def _scripted_profile(name="rp-offline", **kw):
    """A scripted-provider RunProfile: never touches a backend (used only for its identity/hash here)."""
    S = RoleSpec(provider=ProviderKey.scripted)
    kw.setdefault("roles", RolesProfile(prover=S, decomposer=S, reviewer=S, comparator=S, judge=S,
                                        formalizer=S, faithfulness=S, refiner=S))
    return RunProfile(name=name, **kw)


# --------------------------------------------------------------------------------------------------
# Loader: the six authored folders.
# --------------------------------------------------------------------------------------------------
_EXPECTED_TIER = {
    "ljunggren_equation": "T3-hard",
    "triangular_number_theorem": "T2",
    "imo_1988_finite_descent": "T1",
    "hardy_wright_theorem_120": "calibration",
    "mordell_curves": "T2",
}
_READY_SLUGS = set(_EXPECTED_TIER)
_WITH_NOTE = {"ljunggren_equation", "triangular_number_theorem"}


def test_loader_parses_all_ready_folders_with_statement_and_tier():
    for slug, tier in _EXPECTED_TIER.items():
        prob = rp.load_problem(_PROBLEMS_DIR / slug)
        assert prob.slug == slug
        assert prob.is_ready, f"{slug} should be ready"
        assert prob.statement and prob.statement.strip(), f"{slug} has an empty statement"
        assert prob.tier == tier, f"{slug}: tier {prob.tier!r} != {tier!r}"


def test_loader_contaminated_folder_is_runnable_and_labeled():
    prob = rp.load_problem(_PROBLEMS_DIR / "x2_plus_1_eq_y3")
    assert not prob.is_ready
    assert prob.contaminated and prob.is_runnable
    assert prob.statement and "X^2 + 1 = Y^3" in prob.statement
    assert "contaminated" in prob.status.lower()


def test_ljunggren_and_triangular_carry_allowed_inputs_note():
    lj = rp.load_problem(_PROBLEMS_DIR / "ljunggren_equation")
    tri = rp.load_problem(_PROBLEMS_DIR / "triangular_number_theorem")
    assert lj.allowed_inputs_note and "quartic" in lj.allowed_inputs_note.lower()
    assert tri.allowed_inputs_note and "three-squares" in tri.allowed_inputs_note.lower()


def test_problems_without_per_problem_inputs_have_no_note():
    for slug in ("imo_1988_finite_descent", "mordell_curves"):
        prob = rp.load_problem(_PROBLEMS_DIR / slug)
        assert prob.allowed_inputs_note is None, f"{slug} should have no per-problem note"


def test_discover_skips_template_and_finds_all_authored():
    discovered = {slug for slug, _p, _e in rp.discover_problems(_PROBLEMS_DIR)}
    assert "TEMPLATE" not in discovered
    assert _READY_SLUGS <= discovered
    assert "x2_plus_1_eq_y3" in discovered


# --------------------------------------------------------------------------------------------------
# Context injection (Fix 2): the citable-inputs clause travels the seed-context CHANNEL — the goal
# handed to the builder stays the PURE statement (never carries the clause), and the clause is the
# SECOND element returned by context_for(), forwarded as build_and_run(..., context=...).
# --------------------------------------------------------------------------------------------------
def test_context_for_returns_pure_statement_and_clause_only_when_note_present():
    lj = rp.load_problem(_PROBLEMS_DIR / "ljunggren_equation")
    statement, clause = rp.context_for(lj)
    # The goal string is the PURE statement — the clause is NOT appended to it (goal identity is clean).
    assert statement == lj.statement
    assert "Per-problem citable inputs" not in statement
    # The clause is the dedicated context channel's payload.
    assert clause is not None
    assert "Per-problem citable inputs" in clause
    assert lj.allowed_inputs_note in clause

    imo = rp.load_problem(_PROBLEMS_DIR / "imo_1988_finite_descent")
    statement2, clause2 = rp.context_for(imo)
    assert statement2 == imo.statement
    assert clause2 is None


def test_builder_receives_pure_goal_and_clause_via_context_channel(tmp_path):
    """Fix 2 (test spec c): the goal handed to the builder is the PURE statement even for a
    context-carrying problem, while the citable-inputs clause arrives via the keyword-only ``context``
    channel; the row's injected_context flag stays True. Captured via a stub builder."""
    seen_goal: dict[str, str] = {}
    seen_ctx: dict[str, object] = {}

    def builder(profile, goal, **kwargs):
        seen_goal[profile.name] = goal
        seen_ctx[profile.name] = kwargs.get("context")
        return _StubResult(proven=True)

    lj = rp.load_problem(_PROBLEMS_DIR / "ljunggren_equation")
    imo = rp.load_problem(_PROBLEMS_DIR / "imo_1988_finite_descent")
    prof = _scripted_profile("cap")

    row_lj = rp.run_one(lj, prof, builder=builder)
    lj_goal, lj_ctx = seen_goal["cap"], seen_ctx["cap"]
    seen_goal.clear(); seen_ctx.clear()
    row_imo = rp.run_one(imo, prof, builder=builder)
    imo_goal, imo_ctx = seen_goal["cap"], seen_ctx["cap"]

    # The GOAL handed to the builder is the PURE statement for BOTH problems (clause never in the goal).
    assert lj_goal == lj.statement
    assert "Per-problem citable inputs" not in lj_goal
    assert imo_goal == imo.statement
    # The ljunggren clause arrives via the dedicated context channel; imo gets None.
    assert lj_ctx is not None and "Per-problem citable inputs" in lj_ctx
    assert lj.allowed_inputs_note.split(";")[0][:20] in lj_ctx
    assert imo_ctx is None
    # The row still flags injected context for ljunggren (a clause was passed), not for imo.
    assert row_lj["injected_context"] is True
    assert row_imo["injected_context"] is False


# --------------------------------------------------------------------------------------------------
# Full offline sweep: well-formed rows with profile_hash; skip/error rows; no crash.
# --------------------------------------------------------------------------------------------------
def test_full_sweep_offline_writes_well_formed_rows(tmp_path):
    def builder(profile, goal, **kwargs):
        return _StubResult(proven=True, spent=5, nodes=2)

    discovered = rp.discover_problems(_PROBLEMS_DIR)
    prof = _scripted_profile("sweep")
    out = tmp_path / "runs.jsonl"
    rows = rp.run_sweep(discovered, [prof], out, builder=builder, verbose=False)

    written = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(written) == len(rows)
    for row in written:
        assert set(row) == set(rp.ROW_FIELDS)

    by_problem = {r["problem"]: r for r in written}
    # Every ready problem produced a run row with the profile identity + proven verdict.
    for slug in _READY_SLUGS:
        r = by_problem[slug]
        assert r["profile_hash"] == prof.profile_hash
        assert r["profile_name"] == "sweep"
        assert r["proven"] is True
        assert r["reporting_status"] == "soft_proven"
        assert r["calls_spent"] == 5
        assert r["nodes"] == 2
        assert r["error"] is None
    # The ljunggren row flags the injected context; imo does not.
    assert by_problem["ljunggren_equation"]["injected_context"] is True
    assert by_problem["imo_1988_finite_descent"]["injected_context"] is False
    # The contaminated folder RUNS (capability check) but every row is labeled contaminated=True.
    x2 = by_problem["x2_plus_1_eq_y3"]
    assert x2["error"] is None
    assert x2["contaminated"] is True
    assert x2["proven"] is True                      # scripted builder proves everything it runs
    # Ready folders are labeled clean.
    assert by_problem["imo_1988_finite_descent"]["contaminated"] is False


def test_malformed_folder_produces_error_row_not_crash(tmp_path):
    # A folder with a problem.md that has NO frontmatter -> a load error row.
    pdir = tmp_path / "problems"
    bad = pdir / "broken"
    bad.mkdir(parents=True)
    (bad / "problem.md").write_text("# no frontmatter here\n\njust prose.\n", encoding="utf-8")
    # A well-formed ready folder alongside it, to prove the sweep continues past the bad one.
    good = pdir / "good"
    good.mkdir()
    (good / "problem.md").write_text(
        '---\nname: G\ntype: problem\nstatus: ready\ntier: T1\n'
        'statement: "Prove 1 = 1."\n---\n# G\n', encoding="utf-8")

    discovered = rp.discover_problems(pdir)
    rows = rp.run_sweep(discovered, [_scripted_profile("m")], tmp_path / "o.jsonl",
                        builder=lambda p, g, **kw: _StubResult(proven=True), verbose=False)
    by_problem = {r["problem"]: r for r in rows}
    assert "load error" in by_problem["broken"]["error"]
    assert by_problem["broken"]["proven"] is False
    # The good folder still ran.
    assert by_problem["good"]["proven"] is True


def _write_ready_folder(pdir: Path, name: str) -> Path:
    """A minimal well-formed READY problem folder (helper for the UTF-8 tests)."""
    folder = pdir / name
    folder.mkdir(parents=True)
    (folder / "problem.md").write_text(
        '---\nname: G\ntype: problem\nstatus: ready\ntier: T1\n'
        'statement: "Prove 1 = 1."\n---\n# G\n', encoding="utf-8")
    return folder


def test_non_utf8_problem_md_is_error_row_not_sweep_abort(tmp_path):
    """FIX 1: a single non-UTF-8 problem.md becomes a per-folder ERROR ROW (UnicodeDecodeError caught and
    re-raised as ProblemLoadError), never a whole-sweep abort — the valid folder alongside still loads."""
    pdir = tmp_path / "problems"
    _write_ready_folder(pdir, "good")
    bad = pdir / "bad"
    bad.mkdir(parents=True)
    # A cp1252 right-single-quote byte (0x92) is not valid UTF-8.
    (bad / "problem.md").write_bytes(
        b'---\nname: B\nstatus: ready\ntier: T1\nstatement: "don\x92t"\n---\n')

    discovered = rp.discover_problems(pdir)
    by_slug = {slug: (prob, err) for slug, prob, err in discovered}
    # The bad folder is an error row (problem is None, error mentions the file); the good one loaded.
    assert by_slug["bad"][0] is None
    assert by_slug["bad"][1] is not None and "UTF-8" in by_slug["bad"][1]
    assert "problem.md" in by_slug["bad"][1]
    assert by_slug["good"][0] is not None and by_slug["good"][0].is_ready


def test_non_utf8_allowed_inputs_md_is_error_row_not_sweep_abort(tmp_path):
    """FIX 1: a non-UTF-8 allowed_inputs.md (read from inside load_problem) also routes to an ERROR ROW,
    not an abort. The sibling valid folder still loads."""
    pdir = tmp_path / "problems"
    _write_ready_folder(pdir, "good")
    bad = _write_ready_folder(pdir, "bad")
    (bad / "allowed_inputs.md").write_bytes(b"Per-problem whitelist:\n- Ljunggren\x92s quartic\n")

    discovered = rp.discover_problems(pdir)
    by_slug = {slug: (prob, err) for slug, prob, err in discovered}
    assert by_slug["bad"][0] is None
    assert by_slug["bad"][1] is not None and "UTF-8" in by_slug["bad"][1]
    assert "allowed_inputs.md" in by_slug["bad"][1]
    assert by_slug["good"][0] is not None


def test_ledger_filename_disambiguates_same_named_profiles_and_stays_in_dir(tmp_path):
    """FIX 2: two DIFFERENT profiles that share a name dump to DISTINCT ledger files (profile_hash
    discriminator), and a profile named ``../evil`` is sanitized so the file stays inside ledgers_dir."""
    imo = rp.load_problem(_PROBLEMS_DIR / "imo_1988_finite_descent")
    ldir = tmp_path / "ledgers"

    # Same name, different content -> different profile_hash -> two distinct files.
    p1 = _scripted_profile("dup", stages=StageProfile())
    p2 = _scripted_profile("dup", stages=StageProfile(decompose=False))
    assert p1.profile_hash != p2.profile_hash
    rp.run_one(imo, p1, builder=lambda p, g, **kw: _StubResult(proven=True), ledgers_dir=ldir)
    rp.run_one(imo, p2, builder=lambda p, g, **kw: _StubResult(proven=True), ledgers_dir=ldir)
    files = sorted(f.name for f in ldir.glob("*.json"))
    assert len(files) == 2, files
    assert files[0] != files[1]

    # A path-traversing name is sanitized: the file lands inside ledgers_dir, not a parent.
    evil = _scripted_profile("../evil")
    rp.run_one(imo, evil, builder=lambda p, g, **kw: _StubResult(proven=True), ledgers_dir=ldir)
    escaped = list(tmp_path.glob("*evil*.json"))  # would exist if the name escaped one level up
    assert not escaped
    inside = [f for f in ldir.glob("*.json") if "evil" in f.name]
    assert len(inside) == 1
    assert inside[0].parent == ldir


def test_ledger_filename_slug_is_sanitized_and_stays_in_dir(tmp_path):
    """FIX 4 (defense-in-depth): the folder SLUG is sanitized too (mirroring run_brokenarxiv.py's
    UNTRUSTED idx). A slug containing path-traversal cannot escape ledgers_dir even though today the
    slug is not attacker-injected — keeps the two mirrored helpers consistent."""
    ldir = tmp_path / "ledgers"
    evil = rp.Problem(slug="../pwned", statement="x", tier=None, status="ready",
                      allowed_inputs_note=None, source_folder=tmp_path)
    prof = _scripted_profile("ok")
    rp.run_one(evil, prof, builder=lambda p, g, **kw: _StubResult(proven=True), ledgers_dir=ldir)

    assert not list(tmp_path.glob("*pwned*.json")), "slug traversal escaped ledgers_dir"
    assert not (tmp_path / "pwned").exists()
    inside = list(ldir.glob("*.json"))
    assert len(inside) == 1 and inside[0].parent == ldir
    assert "pwned" in inside[0].name
    fname = rp._ledger_filename("../pwned", prof)
    assert fname.startswith(".._pwned__") and "/" not in fname and "\\" not in fname


def test_bad_builder_run_is_error_row_and_sweep_continues(tmp_path):
    def builder(profile, goal, **kwargs):
        if profile.name == "boom":
            raise RuntimeError("kaboom")
        return _StubResult(proven=True)

    imo = rp.load_problem(_PROBLEMS_DIR / "imo_1988_finite_descent")
    bad = _scripted_profile("boom")
    good = _scripted_profile("ok")
    rows = rp.run_sweep([(imo.slug, imo, None)], [bad, good], tmp_path / "o.jsonl",
                        builder=builder, verbose=False)
    by_prof = {r["profile_name"]: r for r in rows}
    assert by_prof["boom"]["error"] is not None and "kaboom" in by_prof["boom"]["error"]
    assert by_prof["boom"]["proven"] is False
    assert by_prof["ok"]["proven"] is True


def test_ledgers_dump_captures_terminal_gate_result(tmp_path):
    """When a terminal Layer-4 gate ran, _dump_ledgers records its summary + the run's
    authoritative_elementary verdict under a top-level ``terminal`` key (best-effort). When no
    terminal gate ran (terminal=None) the key is null — never a raise."""
    class _Terminal:
        def summary(self):
            return "lean-audit: reject (rejects=1)"

    imo = rp.load_problem(_PROBLEMS_DIR / "imo_1988_finite_descent")
    prof = _scripted_profile("dump")
    ldir = tmp_path / "ledgers"

    # A run whose terminal gate ran but did NOT certify elementary.
    rp.run_one(imo, prof, builder=lambda p, g, **kw: _StubResult(
        proven=True, authoritative=False, terminal=_Terminal()), ledgers_dir=ldir)
    fname = rp._ledger_filename(imo.slug, prof)
    dumped = json.loads((ldir / fname).read_text(encoding="utf-8"))
    assert dumped["terminal"] is not None
    assert dumped["terminal"]["summary"] == "lean-audit: reject (rejects=1)"
    assert dumped["terminal"]["authoritative_elementary"] is False

    # A run with NO terminal gate -> terminal key is null, still no crash.
    rp.run_one(imo, prof, builder=lambda p, g, **kw: _StubResult(proven=True, terminal=None),
               ledgers_dir=ldir)
    dumped2 = json.loads((ldir / fname).read_text(encoding="utf-8"))
    assert dumped2["terminal"] is None


def test_reporting_status_authoritative_when_terminal_gate_certifies(tmp_path):
    class _Terminal:
        authoritative = True

    imo = rp.load_problem(_PROBLEMS_DIR / "imo_1988_finite_descent")
    row = rp.run_one(imo, _scripted_profile("auth"),
                     builder=lambda p, g, **kw: _StubResult(proven=True, authoritative=True,
                                                            terminal=_Terminal()))
    assert row["reporting_status"] == "authoritative_elementary"
    assert row["authoritative_elementary"] is True


# --------------------------------------------------------------------------------------------------
# CSV output + reproducibility.
# --------------------------------------------------------------------------------------------------
def test_csv_output_has_header_and_rows(tmp_path):
    imo = rp.load_problem(_PROBLEMS_DIR / "imo_1988_finite_descent")
    out = tmp_path / "runs.csv"
    rp.run_sweep([(imo.slug, imo, None)], [_scripted_profile("a"), _scripted_profile("b")], out,
                 builder=lambda p, g, **kw: _StubResult(proven=True), verbose=False)
    lines = [ln for ln in out.read_text().splitlines() if ln]
    assert lines[0].split(",") == rp.ROW_FIELDS
    assert len(lines) == 3  # header + 2 rows


def test_rerun_is_diffable_except_wall(tmp_path):
    imo = rp.load_problem(_PROBLEMS_DIR / "imo_1988_finite_descent")
    prof = _scripted_profile("repro")
    triple = [(imo.slug, imo, None)]
    b = lambda p, g, **kw: _StubResult(proven=True, spent=7, nodes=1)
    r1 = rp.run_sweep(triple, [prof], tmp_path / "1.jsonl", builder=b, verbose=False)[0]
    r2 = rp.run_sweep(triple, [prof], tmp_path / "2.jsonl", builder=b, verbose=False)[0]
    r1.pop("wall_s"); r2.pop("wall_s")
    assert r1 == r2


# --------------------------------------------------------------------------------------------------
# CLI: main() end-to-end through the real builder on the scripted default profile written to a temp dir.
# --------------------------------------------------------------------------------------------------
def test_main_unknown_problem_slug_errors(tmp_path):
    prof = _scripted_profile("x")
    ppath = tmp_path / "p.yaml"
    import yaml
    ppath.write_text(yaml.safe_dump(prof.model_dump(mode="json")), encoding="utf-8")
    rc = rp.main(["--problems-dir", str(_PROBLEMS_DIR), "--problem", "does_not_exist",
                  "--profile", str(ppath), "--out", str(tmp_path / "o.jsonl"), "--quiet"])
    assert rc == 2


def test_main_selects_single_problem_and_writes_row(tmp_path, monkeypatch):
    # Route the real builder through a stub so main() is fully offline.
    import agent.orchestrator.builder as builder_mod
    monkeypatch.setattr(builder_mod, "build_and_run",
                        lambda profile, goal, **kwargs: _StubResult(proven=True), raising=True)
    prof = _scripted_profile("cli")
    ppath = tmp_path / "p.yaml"
    import yaml
    ppath.write_text(yaml.safe_dump(prof.model_dump(mode="json")), encoding="utf-8")
    out = tmp_path / "o.jsonl"
    rc = rp.main(["--problems-dir", str(_PROBLEMS_DIR), "--problem", "imo_1988_finite_descent",
                  "--profile", str(ppath), "--out", str(out), "--quiet"])
    assert rc == 0
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["problem"] == "imo_1988_finite_descent"
    assert rows[0]["tier"] == "T1"
    assert rows[0]["proven"] is True


def test_timeout_flag_changes_profile_hash(tmp_path):
    prof = _scripted_profile("t")
    capped = rp._apply_timeout(prof, 42)
    assert capped.profile_hash != prof.profile_hash
    assert all(getattr(capped.roles, n).timeout_s == 42 for n in capped.roles.model_fields)
