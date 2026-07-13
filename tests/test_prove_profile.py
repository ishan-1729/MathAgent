"""Offline tests for the W6 CLI<->profile delegation in scripts/prove.py.

No CLI/model/Lean is invoked: profiles are pure data, the supervisor's availability probes are mocked
available, and the only build exercised uses the SCRIPTED provider. We pin:

  * ``profile_from_args(default args)`` maps the default CLI flags to the expected RunProfile fields
    (legacy Codex roles, soft elementarity, decompose+review on, depth/budget/episodes mapped);
  * every structural flag maps to exactly one profile field (direct/terminal-gate/lean-strict/population/
    refine/evolve-fallback/budgets);
  * ``--dump-profile`` prints the effective profile and exits 0 WITHOUT a goal and without any backend;
  * ``--profile`` loads a base profile whose structural fields the CLI flags then OVERRIDE;
  * each shipped profiles/*.yaml loads + passes the supervisor (with probes mocked available);
  * the profile-driven DAG build goes through the builder (build_driver) end to end on scripted parts.
"""
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.orchestrator import supervisor as sup
from agent.orchestrator.run_profile import (
    ElementarityLevel,
    Mode,
    ProviderKey,
    RoleSpec,
    RolesProfile,
    RunProfile,
    StageProfile,
)

# scripts/ is not a package; load prove.py by path (mirrors the other prove.py tests).
_REPO = Path(__file__).resolve().parents[1]
_PROVE_PATH = _REPO / "scripts" / "prove.py"
_spec = importlib.util.spec_from_file_location("prove_cli", _PROVE_PATH)
prove_cli = importlib.util.module_from_spec(_spec)
sys.modules["prove_cli"] = prove_cli
_spec.loader.exec_module(prove_cli)

_PROFILES_DIR = _REPO / "profiles"
_SHIPPED = sorted(_PROFILES_DIR.glob("**/*.yaml"))


def _parse(argv):
    return prove_cli.build_arg_parser().parse_args(argv)


@pytest.fixture
def all_probes_available(monkeypatch):
    """Mock every supervisor capability probe to 'available' so shipped profiles can be vetted offline."""
    for name in (
        "_claude_available",
        "_codex_available",
        "_lean_compiler_available",
        "_lean_repl_available",
        "_openevolve_available",
    ):
        monkeypatch.setattr(sup, name, lambda: True)
    monkeypatch.setattr(sup, "_PROVIDER_PROBE",
                        {k: (lambda: True) for k in sup._PROVIDER_PROBE})


# ---- profile_from_args: the default flags map to the expected (legacy Codex) profile --------------

def test_profile_from_args_default_maps_legacy_codex_wiring():
    p = prove_cli.profile_from_args(_parse(["a goal"]))
    assert isinstance(p, RunProfile)
    assert p.mode is Mode.dag
    assert p.elementarity is ElementarityLevel.soft
    # Legacy CLI default: every role -> codex (the back-compat baseline).
    for role in ("prover", "decomposer", "reviewer", "comparator", "judge", "faithfulness", "refiner"):
        assert getattr(p.roles, role).provider is ProviderKey.codex
    # default stages: decompose + review ON; population/refine/evolve OFF; h0 on.
    assert p.stages.decompose is True and p.stages.review is True
    assert p.stages.population == 0 and p.stages.evolve == 0
    assert p.stages.evolve_witness == 0 and p.stages.evolve_fallback == 0
    assert p.stages.refine is False and p.stages.judges == 1
    assert p.stages.h0_consistency is True
    # default budgets mapped from the CLI defaults.
    assert p.budgets.max_llm_calls == 60
    assert p.budgets.max_depth == 3
    assert p.budgets.max_decomp_attempts == 2
    assert p.budgets.max_replan_depth == 2
    assert p.budgets.episodes == 3
    # default lean: all off.
    assert (p.lean.per_node, p.lean.terminal, p.lean.strict, p.lean.server) == \
        (False, False, False, False)


def test_profile_from_args_formalizer_model_claude_overrides_only_formalizer():
    p = prove_cli.profile_from_args(_parse(["g", "--formalizer-model", "claude"]))
    assert p.roles.formalizer.provider is ProviderKey.claude
    assert p.roles.prover.provider is ProviderKey.codex  # the rest stay Codex


# ---- every structural flag maps to exactly one profile field -------------------------------------

def test_direct_flag_maps_to_direct_mode_and_keeps_proof_review():
    p = prove_cli.profile_from_args(_parse(["g", "--direct"]))
    assert p.mode is Mode.direct
    assert p.stages.decompose is False
    assert p.stages.review is True  # direct-proof judge remains active


def test_terminal_gate_maps_to_authoritative_and_lean_terminal():
    p = prove_cli.profile_from_args(_parse(["g", "--terminal-gate"]))
    assert p.elementarity is ElementarityLevel.authoritative
    assert p.lean.terminal is True


def test_lean_strict_implies_per_node_and_server_in_profile():
    p = prove_cli.profile_from_args(_parse(["g", "--lean-strict"]))
    assert p.lean.strict is True and p.lean.per_node is True and p.lean.server is True


def test_lean_per_node_maps_and_implies_server():
    p = prove_cli.profile_from_args(_parse(["g", "--lean-per-node"]))
    assert p.lean.per_node is True and p.lean.server is True
    assert p.lean.strict is False


def test_population_refine_evolve_budgets_map_through():
    p = prove_cli.profile_from_args(_parse([
        "g", "--population", "4", "--refine", "--judges", "3",
        "--evolve", "6", "--evolve-witness", "5", "--evolve-fallback", "2",
        "--budget", "99", "--max-depth", "5", "--max-decomp", "3",
        "--max-replan", "4", "--episodes", "7",
    ]))
    assert p.stages.population == 4
    assert p.stages.refine is True
    assert p.stages.judges == 3
    assert p.stages.evolve == 6
    assert p.stages.evolve_witness == 5
    assert p.stages.evolve_fallback == 2
    assert p.budgets.max_llm_calls == 99
    assert p.budgets.max_depth == 5
    assert p.budgets.max_decomp_attempts == 3
    assert p.budgets.max_replan_depth == 4
    assert p.budgets.episodes == 7


# ---- _certifying: certification is derived from the effective PROFILE too, not only the flags (M5) --


def test_certifying_derives_from_authoritative_profile_without_flags():
    args = _parse(["g"])  # no --terminal-gate / --formalize
    assert prove_cli._certifying(args, RunProfile(elementarity=ElementarityLevel.soft)) is False
    # The documented `--profile authoritative` path: the terminal gate runs, so certifying must be True
    # even with no CLI flag (else a non-elementary result mislabels soft_proven and exits 0).
    assert prove_cli._certifying(args, RunProfile(elementarity=ElementarityLevel.authoritative)) is True


def test_certifying_true_when_flag_set_even_on_soft_profile():
    args = _parse(["g", "--terminal-gate"])
    assert prove_cli._certifying(args, RunProfile(elementarity=ElementarityLevel.soft)) is True


# ---- --profile base: loaded then OVERRIDDEN by structural flags ----------------------------------

def test_profile_base_roles_preserved_flags_override_structure(tmp_path):
    """A loaded --profile supplies roles/name/seed/notes; the CLI flags override mode/stages/lean."""
    base = RunProfile(name="my-base", seed=7,
                      roles=RolesProfile(prover=RoleSpec(provider=ProviderKey.scripted)))
    yaml_path = tmp_path / "base.yaml"
    import yaml
    yaml_path.write_text(yaml.safe_dump(base.model_dump(mode="json")), encoding="utf-8")

    args = _parse(["g", "--profile", str(yaml_path), "--population", "3"])
    p = prove_cli.profile_from_args(args, base=RunProfile.from_yaml(args.profile))
    # roles + identity come from the base.
    assert p.roles.prover.provider is ProviderKey.scripted
    assert p.name == "my-base" and p.seed == 7
    # the structural flag overrides the base.
    assert p.stages.population == 3


def test_profile_base_unset_flags_do_not_clobber_base():
    """Loading a profile whose lean/elementarity are ON must NOT be silently downgraded by the
    default-False CLI flags — only EXPLICITLY-set flags override the base."""
    base = RunProfile.from_yaml(_PROFILES_DIR / "authoritative.yaml")
    p = prove_cli.profile_from_args(_parse(["g"]), base=base)
    # No flags set -> the base is returned untouched (still authoritative + per-node Lean).
    assert p.elementarity is ElementarityLevel.authoritative
    assert p.lean.per_node is True and p.lean.terminal is True


def test_profile_explicit_formalizer_provider_overrides_effective_role():
    base = RunProfile.from_yaml(_PROFILES_DIR / "authoritative.yaml")
    p = prove_cli.profile_from_args(
        _parse(["g", "--profile", "profiles/authoritative.yaml",
                "--formalizer-model", "codex"]),
        base=base,
    )
    assert p.roles.formalizer.provider is ProviderKey.codex
    assert p.roles.formalizer.model == "gpt-5.5"
    assert p.roles.prover == base.roles.prover


@pytest.mark.parametrize("flag,value", [
    ("--model", "gpt-5.5"),
    ("--effort", "high"),
    ("--timeout", "90"),
])
def test_profile_rejects_ambiguous_legacy_provider_flags(flag, value):
    base = RunProfile.from_yaml(_PROFILES_DIR / "default.yaml")
    with pytest.raises(ValueError, match="cannot be combined with --profile"):
        prove_cli.profile_from_args(
            _parse(["g", "--profile", "profiles/default.yaml", flag, value]), base=base)


def test_profile_base_explicit_flag_overrides_only_that_field():
    base = RunProfile.from_yaml(_PROFILES_DIR / "codex.yaml")
    p = prove_cli.profile_from_args(_parse(["g", "--population", "5"]), base=base)
    assert p.stages.population == 5            # explicit override applied
    assert p.stages.decompose is True          # untouched base stage preserved
    assert p.roles.prover.provider is ProviderKey.codex  # base roles preserved
    assert p.elementarity is ElementarityLevel.soft       # base elementarity preserved


def test_profile_base_can_be_overridden_to_cli_default_values():
    """Explicitness is derived from option presence, not inequality with argparse defaults."""
    base = RunProfile(
        name="offline test",
        stages=StageProfile(population=5, refine=True),
    )
    p = prove_cli.profile_from_args(
        _parse(["g", "--population", "0", "--no-refine"]), base=base)
    assert p.stages.population == 0
    assert p.stages.refine is False


def test_profile_authority_can_be_explicitly_downgraded():
    base = RunProfile.from_yaml(_PROFILES_DIR / "authoritative.yaml")
    p = prove_cli.profile_from_args(
        _parse(["g", "--no-terminal-gate", "--no-lean-per-node", "--no-server"]),
        base=base,
    )
    assert p.elementarity is ElementarityLevel.soft
    assert not p.lean.terminal and not p.lean.per_node and not p.lean.server


def test_effective_profile_uses_profile_file(tmp_path):
    base = RunProfile(name="loaded", roles=RolesProfile(prover=RoleSpec(provider=ProviderKey.scripted)))
    yaml_path = tmp_path / "p.yaml"
    import yaml
    yaml_path.write_text(yaml.safe_dump(base.model_dump(mode="json")), encoding="utf-8")
    args = _parse(["g", "--profile", str(yaml_path)])
    eff = prove_cli.effective_profile(args)
    assert eff.name == "loaded"
    assert eff.roles.prover.provider is ProviderKey.scripted


# ---- --dump-profile: prints effective profile, exits 0, needs no goal/backend --------------------

def test_dump_profile_prints_yaml_and_exits_zero_without_goal(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["prove.py", "--dump-profile"])
    rc = prove_cli.main()
    assert rc == 0
    out = capsys.readouterr().out
    # It is valid YAML round-tripping to a RunProfile.
    import yaml
    data = yaml.safe_load(out)
    assert RunProfile.model_validate(data).mode is Mode.dag


def test_dump_profile_reflects_flags(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["prove.py", "--dump-profile", "--direct", "--population", "2", "g"])
    rc = prove_cli.main()
    assert rc == 0
    import yaml
    data = yaml.safe_load(capsys.readouterr().out)
    p = RunProfile.model_validate(data)
    assert p.mode is Mode.direct
    assert p.stages.population == 2


def test_dump_profile_missing_path_is_clean_usage_error(monkeypatch, capsys, tmp_path):
    missing = tmp_path / "missing.yaml"
    monkeypatch.setattr(
        sys, "argv", ["prove.py", "--dump-profile", "--profile", str(missing)])
    assert prove_cli.main() == 2
    captured = capsys.readouterr()
    assert "ERROR: could not load profile" in captured.err
    assert "Traceback" not in captured.err


def test_packaged_profile_spelling_resolves_outside_checkout(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    args = _parse(["--dump-profile", "--profile", "profiles/default.yaml"])
    assert prove_cli.effective_profile(args).name == "default"


@pytest.mark.parametrize("spelling", ["default.yaml", "ablation/no-review.yaml"])
def test_missing_bare_relative_profile_never_guesses_packaged_preset(monkeypatch, tmp_path, spelling):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError, match="RunProfile does not exist"):
        prove_cli.effective_profile(_parse(["--dump-profile", "--profile", spelling]))


def test_provider_banner_reports_configured_fallback_chain_without_claiming_primary():
    spec = RoleSpec(provider=ProviderKey.claude, fallback=ProviderKey.codex)
    label = prove_cli._role_chain_label(spec)
    assert label == "claude/(default) -> codex/(provider default)"
    assert "selected" not in label


def test_main_requires_a_goal_when_not_dumping(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["prove.py"])
    rc = prove_cli.main()
    assert rc == 2
    assert "goal is required" in capsys.readouterr().err


# ---- every shipped profile loads + passes the supervisor (probes mocked available) ---------------

def test_shipped_profiles_exist():
    names = {p.name for p in _SHIPPED}
    assert {"default.yaml", "codex.yaml", "solution-only.yaml", "authoritative.yaml"} <= names
    ablation = {p.name for p in (_PROFILES_DIR / "ablation").glob("*.yaml")}
    assert {"no-decompose.yaml", "no-review.yaml", "no-refine.yaml"} <= ablation
    assert "no-h0.yaml" not in ablation  # H0 is a logical invariant, not an ablation axis


@pytest.mark.parametrize("path", _SHIPPED, ids=lambda p: p.name)
def test_shipped_profile_loads_and_passes_supervisor(path, all_probes_available):
    p = RunProfile.from_yaml(path)
    # No raise == admitted. (Probes are mocked available so the offline test can vet authoritative.)
    sup.validate_profile(p)


def test_default_profile_is_claude_roles():
    p = RunProfile.from_yaml(_PROFILES_DIR / "default.yaml")
    assert p.roles.prover.provider is ProviderKey.claude
    assert p.elementarity is ElementarityLevel.soft


def test_codex_profile_is_all_codex():
    p = RunProfile.from_yaml(_PROFILES_DIR / "codex.yaml")
    for role in ("prover", "decomposer", "reviewer", "formalizer"):
        assert getattr(p.roles, role).provider is ProviderKey.codex


def test_solution_only_profile_is_elementarity_none():
    p = RunProfile.from_yaml(_PROFILES_DIR / "solution-only.yaml")
    assert p.elementarity is ElementarityLevel.none
    assert not p.lean.per_node and not p.lean.terminal and not p.lean.strict


def test_authoritative_profile_wires_terminal_and_per_node_lean():
    p = RunProfile.from_yaml(_PROFILES_DIR / "authoritative.yaml")
    assert p.elementarity is ElementarityLevel.authoritative
    assert p.lean.terminal is True and p.lean.per_node is True


# ---- the profile-driven DAG build goes through the builder end to end (scripted) -----------------

def _scripted_offline_profile() -> RunProfile:
    S = RoleSpec(provider=ProviderKey.scripted)
    return RunProfile(
        name="offline-test",
        roles=RolesProfile(prover=S, decomposer=S, reviewer=S, comparator=S, judge=S,
                           formalizer=S, faithfulness=S, refiner=S),
    )


def test_build_driver_from_a_loaded_scripted_profile(tmp_path):
    """A scripted profile written to YAML loads + builds a real DagDriver via the builder (no backend)."""
    from agent.orchestrator.builder import build_driver
    from agent.orchestrator.dag_driver import DagDriver

    prof = _scripted_offline_profile()
    yaml_path = tmp_path / "scripted.yaml"
    import yaml
    yaml_path.write_text(yaml.safe_dump(prof.model_dump(mode="json")), encoding="utf-8")

    loaded = RunProfile.from_yaml(yaml_path)
    driver = build_driver(loaded)
    assert isinstance(driver, DagDriver)
    # default-stage wiring: informal enforcement/review on, no Lean gates.
    assert driver.enforce_elementarity is True
    assert driver.decomposer is not None and driver.reviewer is not None
    assert driver.terminal_gate is None and driver.node_verifier is None


# ---- FIX 3: the --profile path CLOSES its driver (releases the warm LeanServer build_driver stored on
#      it) on BOTH success and a raising run() — mirroring build_and_run's try/finally. Without this a
#      lean.server --profile run leaks an orphaned repl.exe holding Mathlib. ------------------------------

class _SpyResult:
    """Just enough DagResult surface for prove.main()'s --profile post-run block."""
    class _Dag:
        nodes = {}
        def stats(self):
            return "nodes=0"
    def __init__(self):
        self.proven = False
        self.dag = self._Dag()
        self.terminal = None
        self.authoritative_elementary = False
        self.budget = SimpleNamespace(calls_spent=0, max_llm_calls=1)
    def proof_tree(self):
        return {}


class _SpyDriver:
    """Records close() calls; run() either returns a stub result or raises (parametrized by the test)."""
    def __init__(self, *, raise_on_run=False):
        self.closed = 0
        self._raise = raise_on_run
    def run(self, goal, *a, **k):
        if self._raise:
            raise RuntimeError("run blew up mid-proof")
        return _SpyResult()
    def close(self):
        self.closed += 1


def _run_profile_main(monkeypatch, tmp_path, *, raise_on_run, extra_argv=None):
    """Drive prove.main() down the REAL --profile branch with a scripted profile, intercepting
    build_driver to return a spy so the try/finally close() contract is observed. Returns the spy."""
    prof = _scripted_offline_profile()
    yaml_path = tmp_path / "prof.yaml"
    import yaml
    yaml_path.write_text(yaml.safe_dump(prof.model_dump(mode="json")), encoding="utf-8")

    spy = _SpyDriver(raise_on_run=raise_on_run)
    # build_driver is imported inside the branch as `from agent.orchestrator.builder import build_driver`,
    # so patch the SOURCE symbol (not prove_cli's namespace) to intercept the real construction site.
    import agent.orchestrator.builder as builder_mod
    monkeypatch.setattr(builder_mod, "build_driver", lambda *a, **k: spy)
    monkeypatch.setattr(
        sys, "argv",
        ["prove.py", "some goal", "--profile", str(yaml_path), *(extra_argv or [])],
    )
    return spy


def test_profile_path_closes_driver_on_success(monkeypatch, tmp_path, all_probes_available):
    spy = _run_profile_main(monkeypatch, tmp_path, raise_on_run=False)
    rc = prove_cli.main()
    assert rc in (0, 1)                     # NOT PROVEN stub -> exit 0/1, but the run completed
    assert spy.closed == 1, "the --profile path must close() its driver on success"


def test_profile_out_publishes_proof_and_trace_without_clobber(
        monkeypatch, tmp_path, all_probes_available):
    prefix = tmp_path / "profile-run"
    spy = _run_profile_main(
        monkeypatch, tmp_path, raise_on_run=False,
        extra_argv=["--out", str(prefix)],
    )
    assert prove_cli.main() == 1
    proof_path = Path(str(prefix) + ".proof.json")
    trace_path = Path(str(prefix) + ".trace.jsonl")
    proof_before = proof_path.read_bytes()
    trace_before = trace_path.read_bytes()
    payload = json.loads(proof_before)
    assert payload["goal"] == "some goal"
    assert payload["reporting_status"] == "rejected"
    assert payload["proof_tree"] == {}
    assert payload["effective_profile"]["name"] == payload["profile_name"]
    assert payload["execution_controls"] == {
        "formalization_repair_iterations": 0,
        "retrieval": {
            "retrieval_requested": False,
            "neural_requested": False,
            "rerank_requested": False,
            "retrieval_effective": False,
            "neural_active": False,
            "neural_degraded": False,
            "rerank_active": False,
            "retriever_chain": [],
        },
    }
    assert trace_before
    assert spy.closed == 1

    # A second invocation is rejected by the preflight before rebuilding or replacing either file.
    assert prove_cli.main() == 2
    assert proof_path.read_bytes() == proof_before
    assert trace_path.read_bytes() == trace_before
    assert spy.closed == 1


def test_legacy_direct_formalize_out_retains_posthoc_certificate(
        monkeypatch, tmp_path, all_probes_available):
    """The legacy post-hoc audit must be embedded, not discarded with the RalphResult."""
    from agent.gates.lean_audit import (
        ConstDep, DependencyReport, LeanAuditResult, LeanVerdict,
        DERIVED_PROVENANCE_SCHEMA,
    )
    from agent.orchestrator.ralph import RalphResult
    import agent.orchestrator.formalize_bridge as bridge
    import agent.orchestrator.ralph as ralph_module

    goal = "For all integers n, n + 0 = n."
    ledger = json.dumps({
        "problem": "p", "claim": goal,
        "steps": [
            {"id": "s1", "claim": goal, "justification": "given", "depends_on": []},
            {"id": "s2", "claim": goal, "justification": "conclusion",
             "depends_on": ["s1"]},
        ],
    })
    fake_result = RalphResult(
        success=True, ledger=ledger, report=None, episodes=1,
    )
    monkeypatch.setattr(ralph_module.RalphLoop, "run", lambda *_args, **_kwargs: fake_result)

    audit = LeanAuditResult(
        verdict=LeanVerdict.PASS,
        report=DependencyReport(
            theorem="ma_target", axioms=["propext"],
            constants=[ConstDep("ma_target")],
            toolchain="leanprover/lean4:v4.30.0",
            manifest="sha256:" + "a" * 64,
            provenance=DERIVED_PROVENANCE_SCHEMA,
        ),
        provenance_verified=True,
    )
    terminal = SimpleNamespace(
        authoritative=True, certification_trusted=True, compiled=True, audit=audit,
        lean_source="theorem ma_target : True := trivial",
        summary=lambda: "formalize: authoritative",
    )
    monkeypatch.setattr(bridge, "formalize_and_audit", lambda *_args, **_kwargs: terminal)

    prefix = tmp_path / "legacy-direct"
    monkeypatch.setattr(
        sys, "argv",
        ["prove.py", "--direct", "--formalize", "--out", str(prefix), goal],
    )
    assert prove_cli.main() == 0
    payload = json.loads(Path(str(prefix) + ".proof.json").read_text(encoding="utf-8"))
    assert payload["run_id"]
    assert payload["terminal_summary"] == "formalize: authoritative"
    assert payload["lean_audit"]["authoritative"] is True
    assert payload["lean_audit"]["report"]["manifest"] == "sha256:" + "a" * 64
    assert payload["direct_ledger"] == ledger


def test_retriever_chain_records_hybrid_wrapper_and_children():
    class Leaf:
        pass

    wrapper = SimpleNamespace(retrievers=[Leaf(), SimpleNamespace(retrievers=[Leaf()])])
    assert prove_cli._retriever_chain(wrapper) == [
        "types.SimpleNamespace",
        f"{Leaf.__module__}.{Leaf.__qualname__}",
        "types.SimpleNamespace",
        f"{Leaf.__module__}.{Leaf.__qualname__}",
    ]


def test_profile_main_loads_one_immutable_snapshot(monkeypatch, tmp_path, all_probes_available):
    """Certification setup and DAG construction must consume the same validated YAML snapshot."""
    real_effective_profile = prove_cli.effective_profile
    calls = {"n": 0}

    def counted(args):
        calls["n"] += 1
        return real_effective_profile(args)

    monkeypatch.setattr(prove_cli, "effective_profile", counted)
    spy = _run_profile_main(monkeypatch, tmp_path, raise_on_run=False)
    assert prove_cli.main() in (0, 1)
    assert calls["n"] == 1
    assert spy.closed == 1


def test_profile_path_closes_driver_when_run_raises(monkeypatch, tmp_path, all_probes_available):
    spy = _run_profile_main(monkeypatch, tmp_path, raise_on_run=True)
    with pytest.raises(RuntimeError, match="run blew up"):
        prove_cli.main()
    assert spy.closed == 1, "the --profile path must close() its driver even when run() raises (finally)"


def test_supervisor_is_sole_chokepoint_in_main(tmp_path, monkeypatch):
    """POINT 3: main() validates the EFFECTIVE profile via the supervisor BEFORE constructing any
    prover/driver, for EVERY path. An incoherent profile (elementarity=none but lean.per_node=true,
    supervisor guard S4) makes main() return 2 fail-closed, and no role is ever resolved/constructed —
    proving the supervisor is the sole pre-flight chokepoint, not the per-branch construction code."""
    import sys
    import scripts.prove as prove
    import agent.orchestrator.registry as reg
    bad = tmp_path / "incoherent.yaml"
    bad.write_text("elementarity: none\nlean:\n  per_node: true\n", encoding="utf-8")
    called = {"resolve": False}
    def boom(*a, **k):
        called["resolve"] = True
        raise AssertionError("resolve() ran before the supervisor validated the profile (chokepoint bypassed)")
    monkeypatch.setattr(reg, "resolve", boom)             # main() imports resolve lazily from this module
    monkeypatch.setattr(sys, "argv", ["prove", "some goal", "--profile", str(bad)])
    rc = prove.main()
    assert rc == 2, "incoherent profile must be rejected fail-closed (return 2)"
    assert called["resolve"] is False, "no prover/role may be constructed before the supervisor validates"


# ---- H2: the certifying exit code FAILS CLOSED on a crashed/absent terminal verdict. In certifying
#      mode `success = ok and (cert_authoritative is True)`, so a proven run whose terminal Layer-4
#      verdict is ABSENT (terminal=None — DagDriver.run swallows a crashed terminal gate) must exit 1,
#      exactly like a completed REJECT. If reverted to `success = ok`, a crashed certification would
#      exit 0 and mislead automation keyed on the exit code. ----

def _fake_dag_result(goal, *, terminal):
    """A real (minimal) DagResult with proven=True and a caller-supplied terminal verdict."""
    from agent.orchestrator.dag_driver import DagResult
    from agent.orchestrator.dag import ProofDAG
    from agent.orchestrator.trace import RunTrace
    from agent.orchestrator.state import Budget
    return DagResult(goal=goal, proven=True, dag=ProofDAG(), trace=RunTrace("h2-test"),
                     budget=Budget(), terminal=terminal)


class _TerminalVerdictDriver:
    """A fake driver whose run() returns a proven DagResult carrying a fixed terminal verdict; close()
    is a no-op counter. Substituted for the real build_driver so no model/Lean is invoked."""
    def __init__(self, terminal):
        self._terminal = terminal
        self.closed = 0
    def run(self, goal, *a, **k):
        return _fake_dag_result(goal, terminal=self._terminal)
    def close(self):
        self.closed += 1


def _drive_authoritative_profile(monkeypatch, terminal):
    """Run prove.main() down the authoritative --profile path with build_driver intercepted to return a
    fake whose terminal verdict is `terminal`. Returns (rc, driver)."""
    import agent.orchestrator.builder as builder_mod
    driver = _TerminalVerdictDriver(terminal)
    # main() imports build_driver locally as `from agent.orchestrator.builder import build_driver`, so
    # patch the SOURCE attribute (not prove_cli's namespace) to intercept the real construction site.
    monkeypatch.setattr(builder_mod, "build_driver", lambda *a, **k: driver)
    monkeypatch.setattr(sys, "argv",
                        ["prove.py", "--profile", str(_PROFILES_DIR / "authoritative.yaml"), "some goal"])
    rc = prove_cli.main()
    return rc, driver


def test_certifying_exit_fails_closed_on_absent_terminal_verdict(monkeypatch, all_probes_available):
    # terminal=None simulates the swallowed terminal-gate crash. Certification was requested
    # (authoritative profile => _certifying True), the verdict is absent => must exit 1.
    rc, driver = _drive_authoritative_profile(monkeypatch, terminal=None)
    assert rc == 1, "certifying mode + absent terminal verdict must fail closed (exit 1)"
    assert driver.closed == 1, "the --profile path must still close() its driver"


def test_certifying_exit_succeeds_when_terminal_authoritative(monkeypatch, all_probes_available):
    # CONTRAST (proves the guard is not always-1): the same authoritative path with a terminal verdict
    # whose .authoritative is True yields authoritative_elementary=True => exit 0.
    from agent.gates.lean_audit import (
        ConstDep, DependencyReport, LeanAuditResult, LeanVerdict,
        DERIVED_PROVENANCE_SCHEMA,
    )
    audit = LeanAuditResult(
        verdict=LeanVerdict.PASS,
        report=DependencyReport(
            theorem="ma_target", axioms=["propext"], constants=[ConstDep("ma_target")],
            toolchain="leanprover/lean4:v4.30.0",
            manifest="sha256:" + "a" * 64,
            provenance=DERIVED_PROVENANCE_SCHEMA,
        ),
        provenance_verified=True,
    )
    terminal = SimpleNamespace(
        authoritative=True, certification_trusted=True, compiled=True, audit=audit,
        summary=lambda: "ok",
    )
    rc, driver = _drive_authoritative_profile(monkeypatch, terminal=terminal)
    assert rc == 0, "an authoritative terminal verdict must exit 0"
    assert driver.closed == 1


def test_certifying_exit_rejects_untrusted_authoritative_duck(monkeypatch, all_probes_available):
    terminal = SimpleNamespace(authoritative=True, summary=lambda: "untrusted")
    rc, driver = _drive_authoritative_profile(monkeypatch, terminal=terminal)
    assert rc == 1
    assert driver.closed == 1


def test_proof_artifact_cannot_promote_authority_without_serialized_audit_receipt():
    from agent.orchestrator.reporting import ReportStatus
    from agent.orchestrator.trace import RunTrace

    terminal = SimpleNamespace(
        authoritative=True,
        certification_trusted=True,
        compiled=True,
        audit=SimpleNamespace(passed=True, authoritative=True),
        summary=lambda: "spoofed",
    )
    result = _fake_dag_result("some goal", terminal=terminal)
    artifact = prove_cli._proof_artifact(
        result,
        goal="some goal",
        profile=RunProfile.from_yaml(_PROFILES_DIR / "authoritative.yaml"),
        status=ReportStatus.AUTHORITATIVE_ELEMENTARY,
        trace=RunTrace("artifact-coherence"),
    )
    assert artifact["proven"] is True
    assert artifact["reporting_status"] == "soft_proven"
    assert artifact["lean_audit"] is None


def test_legacy_server_is_closed_when_post_start_setup_raises(monkeypatch, all_probes_available):
    """The top-level ownership guard closes a warm legacy REPL on every exceptional exit."""
    import agent.gates.lean_server as lean_server_mod
    import agent.orchestrator.registry as registry_mod
    from agent.orchestrator.run_profile import Role

    class FakeServer:
        instances = []

        def __init__(self):
            self.closed = 0
            self.instances.append(self)

        @classmethod
        def available(cls):
            return True

        def start(self):
            return self

        def close(self):
            self.closed += 1

    monkeypatch.setattr(lean_server_mod, "LeanServer", FakeServer)
    # Preserve the prover construction that occurs before server acquisition, then fail while
    # constructing the certification panel after acquisition.
    real_resolve = registry_mod.resolve

    def resolving(role, spec, deps=None):
        if role is Role.faithfulness:
            raise RuntimeError("faith panel construction failed")
        return real_resolve(role, spec, deps)

    monkeypatch.setattr(registry_mod, "resolve", resolving)
    monkeypatch.setattr(
        sys, "argv", ["prove.py", "--terminal-gate", "--server", "a goal"])
    with pytest.raises(RuntimeError, match="faith panel"):
        prove_cli.main()
    assert len(FakeServer.instances) == 1
    assert FakeServer.instances[0].closed == 1


# ---- M1: evolved candidates enter the ordinary supervised proof path, while the legacy direct
#      --formalize path resolves its formalizer FROM THE EFFECTIVE PROFILE. ----

def _source_block(src, start_marker, end_marker):
    i = src.index(start_marker)
    j = src.index(end_marker, i + len(start_marker))
    return src[i:j]


def test_evolve_uses_ordinary_gates_and_formalize_resolves_profile_role():
    src = _PROVE_PATH.read_text(encoding="utf-8")
    resolve_call = "resolve(Role.formalizer, profile.roles.formalizer"

    # Evolution must not have a parallel certification shortcut.  Its champion becomes the first
    # ordinary prover response and is therefore subject to judges, DAG routing, Lean, and budgets.
    evolve_block = _source_block(
        src,
        "if evolved_ledger is not None and not profile_driven_invocation:",
        "legacy_direct =",
    )
    assert "CodexFormalizer(" not in evolve_block, \
        "the evolved-candidate admission block must not hardcode a parallel formalizer"
    assert "_SeededProver(evolved_ledger, prover)" in evolve_block, \
        "the evolved champion must enter the ordinary prover/gate pipeline"

    # The --formalize (direct-mode) block.
    formalize_block = _source_block(src, "if posthoc_audit and winning_ledger:",
                                    "orchestrator search/review calls spent")
    assert "CodexFormalizer(" not in formalize_block, \
        "the --formalize block must not hardcode CodexFormalizer(...)"
    assert resolve_call in formalize_block, \
        "the --formalize block must resolve the formalizer from the profile"
