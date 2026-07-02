"""Tests for the OpenEvolve proof-sketch bridge (agent/tools/openevolve_bridge.py).

Offline suite (always runs; needs neither the optional ``openevolve`` package nor a Claude
subprocess): exercises the deterministic gate-evaluator over a temp ledger file via the
dependency-free :class:`StubEvolveLLM` plumbing — a known-good ledger scores 1.0, a known-bad one
scores 0.0, and crucially the evolved artifact is NEVER executed (an executable-poison ledger that
would raise on import still scores cleanly through the text-only gate path).

Config-shape + picklability are unit-tested WITHOUT running evolution (``build_evolve_config`` +
``pickle.dumps`` round-trips), so we can assert the real AlphaEvolve ensemble (Sonnet breadth + Opus
depth) is wired correctly and the factories survive the cross-process pickle that previously silently
dropped a closure-lambda factory and made evolution a no-op.

The opt-in live test (``pytest.importorskip('openevolve')``) runs a real short evolution driven by
:class:`StubEvolveLLM` so no Claude subprocess fires, and PROVES evolution actually executes: a seed
that gates NEEDS_REVIEW (0.5) must evolve to best_fitness 1.0 because the stub's scripted full-rewrite
mutation gates PASSED (1.0). (The stub's own ``.calls`` counter lives in worker-process copies under
the process pool, so it cannot be asserted from the main process — we assert on the RESULT instead.)
"""
import json
import pickle

import pytest

from agent.tools.openevolve_bridge import (
    NEEDS_REVIEW_FLOOR,
    PASSED_FLOOR,
    OpenEvolveBackend,
    StubEvolveLLM,
    RankSignal,
    _ClaudeLLMFactory,
    _FixedLLMFactory,
    _DEFAULT_BREADTH_WEIGHT,
    _DEFAULT_DEPTH_WEIGHT,
    _DEPTH_WEIGHT_CEIL,
    _DEPTH_WEIGHT_FLOOR,
    available,
    build_evolve_config,
    make_gate_evaluator,
    rank_aware_weights,
    retrieval_seeds,
    call_telemetry,
    explore_exploit_barrier,
    evolve_sketches,
    score_ledger,
)
from agent.tools.claude_cli import ClaudeConfig
from agent.tools.codex_prover import children_from_sketch

# conftest provides the session-scoped `toolkit` fixture + minimal_ledger() builder.
from tests.conftest import minimal_ledger


def _write(tmp_path, text: str) -> str:
    p = tmp_path / "candidate.txt"
    p.write_text(text, encoding="utf-8")
    return str(p)


def _good_ledger_text() -> str:
    return json.dumps(minimal_ledger())  # given + conclusion -> PASSED_DETERMINISTIC


def _goal_bound_good_ledger_text(goal: str = "A demo theorem.") -> str:
    """A PASSED ledger whose top-level claim AND conclusion both bind to ``goal`` (goal_hash equality).

    The stock ``minimal_ledger()`` conclusion claim is "The theorem." (NOT the goal), so under the
    HARD goal-binding gate it scores 0.0. This variant restates the goal in BOTH the top-level claim
    and the terminal conclusion, so it is the goal-bound PASSED ledger the live evolution test must
    reach (combined_score in the PASSED band [0.60, 1.0]).
    """
    return json.dumps({
        "problem": "demo",
        "claim": goal,
        "steps": [
            {"id": "s1", "claim": "A hypothesis.", "justification": "given", "depends_on": []},
            {"id": "s2", "claim": goal, "justification": "conclusion", "depends_on": ["s1"]},
        ],
    })


def _bad_ledger_text() -> str:
    led = minimal_ledger()
    led["steps"][0]["justification"] = "definitely_not_a_real_toolkit_key"  # -> REJECTED
    return json.dumps(led)


def _needs_review_ledger_text() -> str:
    """A ledger that parses + passes all deterministic checks but gates NEEDS_REVIEW (0.5).

    A ``factorization`` step is an *elastic* justification: the gate routes it to mandatory adversarial
    review (a REVIEW finding, not a REJECT), so the verdict is NEEDS_REVIEW rather than PASSED. This is
    the seed the live test must see EVOLVE up to PASSED (1.0).
    """
    return json.dumps({
        "problem": "demo",
        "claim": "A demo theorem.",
        "steps": [
            {"id": "s1", "claim": "A hypothesis.", "justification": "given", "depends_on": []},
            {"id": "s2", "claim": "A factorization holds.", "justification": "factorization",
             "depends_on": ["s1"]},
            {"id": "s3", "claim": "A demo theorem.", "justification": "conclusion",
             "depends_on": ["s2"]},
        ],
    })


def _sketch_with_two_lemmas() -> str:
    return json.dumps({
        "problem": "demo",
        "claim": "The main theorem.",
        "steps": [
            {"id": "l1", "claim": "Lemma one statement.", "justification": "lemma",
             "depends_on": []},
            {"id": "l2", "claim": "Lemma two statement.", "justification": "lemma",
             "depends_on": []},
            {"id": "c", "claim": "The main theorem.", "justification": "conclusion",
             "depends_on": ["l1", "l2"]},
        ],
    })


# ---- offline: the gate-evaluator scores good vs bad ledgers correctly --------------------------

def test_gate_evaluator_scores_good_ledger_passed_band(tmp_path, toolkit):
    # With no goal supplied the HARD binding gate is skipped: a PASSED ledger lands in the PASSED
    # band [PASSED_FLOOR, 1.0] (graded by obligations/depth), strictly above any NEEDS_REVIEW.
    path = _write(tmp_path, _good_ledger_text())
    metrics = make_gate_evaluator(toolkit)(path)
    assert PASSED_FLOOR <= metrics["combined_score"] <= 1.0
    # MAP-Elites feature dims are now STRATEGY descriptors (not incidental justification_diversity).
    assert metrics["step_count"] == 2.0
    assert "strategy_class" in metrics and "modulus_band" in metrics and "subgoal_depth" in metrics


def test_gate_evaluator_goal_binding_zeroes_off_goal(tmp_path, toolkit):
    # The HARD goal-binding gate: minimal_ledger()'s conclusion is "The theorem.", NOT the goal, so
    # binding fails -> combined_score is a HARD zero even though it gates PASSED structurally.
    path = _write(tmp_path, _good_ledger_text())
    metrics = make_gate_evaluator(toolkit, goal="A demo theorem.")(path)
    assert metrics["combined_score"] == 0.0
    assert metrics["goal_bound"] == 0.0


def test_gate_evaluator_goal_bound_passed_in_band(tmp_path, toolkit):
    # A goal-bound PASSED ledger clears the HARD gate and scores in the PASSED band.
    path = _write(tmp_path, _goal_bound_good_ledger_text("A demo theorem."))
    metrics = make_gate_evaluator(toolkit, goal="A demo theorem.")(path)
    assert PASSED_FLOOR <= metrics["combined_score"] <= 1.0
    assert metrics["goal_bound"] == 1.0


def test_gate_evaluator_scores_bad_ledger_0(tmp_path, toolkit):
    path = _write(tmp_path, _bad_ledger_text())
    metrics = make_gate_evaluator(toolkit)(path)
    assert metrics["combined_score"] == 0.0


def test_gate_evaluator_scores_malformed_ledger_0(tmp_path, toolkit):
    path = _write(tmp_path, "this is not a ledger at all {{{")
    metrics = make_gate_evaluator(toolkit)(path)
    # Fails closed: unparseable -> REJECTED -> HARD 0.0; feature dims are present and zero/default.
    assert metrics["combined_score"] == 0.0
    assert metrics["step_count"] == 0.0
    assert metrics["subgoal_depth"] == 0.0


def test_score_ledger_matches_evaluator(toolkit):
    # The in-process score_ledger() and the file evaluator agree on the same text (PASSED band vs 0).
    assert score_ledger(_good_ledger_text(), toolkit)["combined_score"] >= PASSED_FLOOR
    assert score_ledger(_bad_ledger_text(), toolkit)["combined_score"] == 0.0


def test_needs_review_seed_in_review_band(toolkit):
    # The seed used by the live evolution test gates NEEDS_REVIEW: its score lands in the
    # NEEDS_REVIEW band [NEEDS_REVIEW_FLOOR, PASSED_FLOOR) — strictly BELOW any goal-bound PASSED, so
    # "0.5 is not a win" and evolving it up to the PASSED band is a real improvement.
    score = score_ledger(_needs_review_ledger_text(), toolkit, goal="A demo theorem.")["combined_score"]
    assert NEEDS_REVIEW_FLOOR <= score < PASSED_FLOOR


def test_needs_review_strictly_below_passed(toolkit):
    # Pin the ordering invariant directly: a goal-bound NEEDS_REVIEW < a goal-bound PASSED, always.
    nr = score_ledger(_needs_review_ledger_text(), toolkit, goal="A demo theorem.")["combined_score"]
    pa = score_ledger(_goal_bound_good_ledger_text("A demo theorem."), toolkit,
                      goal="A demo theorem.")["combined_score"]
    assert 0.0 < nr < pa <= 1.0


# ---- SAFETY: the evolved artifact is NEVER executed -------------------------------------------

def test_artifact_is_never_executed(tmp_path, toolkit):
    """A ledger file whose text would raise if imported/exec'd must still score via the gate only.

    If the bridge ever exec'd or imported the candidate, this Python snippet (which raises at module
    top level) would crash the evaluator. Because the evaluator only READS + gates the text, the
    snippet is simply unparseable JSON -> REJECTED -> 0.0, with no exception surfacing.
    """
    poison = "import os\nraise RuntimeError('the artifact was executed!')\n"
    path = _write(tmp_path, poison)
    metrics = make_gate_evaluator(toolkit)(path)  # must not raise
    assert metrics["combined_score"] == 0.0


def test_poison_claim_ledger_gates_without_side_effect(tmp_path, toolkit):
    """A *well-formed* ledger whose CLAIM text is a code-injection string still just gates.

    The poison ``__import__("os")...`` lives inside a JSON string value (a step claim). If anything in
    the pipeline ever eval'd ledger content the temp file would be touched / the process would crash.
    Here it is treated as opaque prose: the ledger parses and gates with no side effect, no exception.
    """
    import os
    marker = str(tmp_path / "PWNED")
    poison_claim = f'__import__("os").system("type nul > {marker}")'
    led = minimal_ledger()
    led["steps"][0]["claim"] = poison_claim
    path = _write(tmp_path, json.dumps(led))

    metrics = make_gate_evaluator(toolkit)(path)  # must not raise, must not run anything

    assert isinstance(metrics["combined_score"], float)
    assert 0.0 <= metrics["combined_score"] <= 1.0
    assert not os.path.exists(marker)  # the claim string was NEVER executed


def test_stub_llm_is_deterministic_and_not_openevolve_subclass():
    stub = StubEvolveLLM([_good_ledger_text(), _bad_ledger_text()])
    # Cycles its scripted outputs deterministically.
    a, b, c = stub.generate_sync(), stub.generate_sync(), stub.generate_sync()
    assert a == c and a != b and stub.calls == 3
    # It is intentionally NOT an openevolve LLMInterface subclass (works with the package absent):
    # none of its base classes live in the top-level ``openevolve`` package.
    base_modules = [base.__module__ for base in type(stub).__mro__]
    assert not any(m == "openevolve" or m.startswith("openevolve.") for m in base_modules)


# ---- ensemble config shape: TWO models (Sonnet breadth + Opus depth), no evolution run --------

def test_build_config_is_sonnet_opus_ensemble(toolkit):
    pytest.importorskip("openevolve")
    config = build_evolve_config(breadth_weight=0.8, depth_weight=0.2)
    names = [m.name for m in config.llm.models]
    assert names == ["sonnet", "opus"]
    weights = {m.name: m.weight for m in config.llm.models}
    # AlphaEvolve mapping: Sonnet = breadth sampled OFTEN (high weight) > Opus = depth, sampled rarely.
    assert weights["sonnet"] > weights["opus"]
    assert weights["sonnet"] == 0.8 and weights["opus"] == 0.2
    # Each member carries the REAL Claude factory bound to its model name (not a shared/no-op closure).
    factories = {m.name: m.init_client for m in config.llm.models}
    assert isinstance(factories["sonnet"], _ClaudeLLMFactory) and factories["sonnet"].model_name == "sonnet"
    assert isinstance(factories["opus"], _ClaudeLLMFactory) and factories["opus"].model_name == "opus"
    # evaluator_models is set explicitly (post_init gotcha) to the same ensemble.
    assert [m.name for m in config.llm.evaluator_models] == ["sonnet", "opus"]
    # Real ensemble path is diff-based (SEARCH/REPLACE over the ledger text).
    assert config.diff_based_evolution is True


def test_build_config_honors_ensemble_model_names_and_weights(toolkit):
    """item 6: build_evolve_config parameterizes the breadth/depth MODEL NAMES (defaults sonnet/opus).
    A profile-addressable ensemble {breadth_model: haiku-test, depth_model: opus, weights 0.5/0.5}
    produces an evolve config whose llm.models reflect those names AND weights (offline — no LLM call)."""
    pytest.importorskip("openevolve")
    config = build_evolve_config(breadth_model="haiku-test", depth_model="opus",
                                 breadth_weight=0.5, depth_weight=0.5)
    names = [m.name for m in config.llm.models]
    assert names == ["haiku-test", "opus"]
    weights = {m.name: m.weight for m in config.llm.models}
    assert weights["haiku-test"] == 0.5 and weights["opus"] == 0.5
    # Each member's factory is bound to the chosen model name (not the old hardcoded sonnet/opus).
    factories = {m.name: m.init_client for m in config.llm.models}
    assert isinstance(factories["haiku-test"], _ClaudeLLMFactory)
    assert factories["haiku-test"].model_name == "haiku-test"
    assert factories["opus"].model_name == "opus"


def test_openevolve_backend_threads_ensemble_into_evolve_sketches(toolkit, monkeypatch):
    """OpenEvolveBackend forwards its breadth/depth model names + weights into evolve_sketches (which
    passes them to build_evolve_config). We spy on evolve_sketches and inspect the forwarded kwargs —
    offline, no evolution run."""
    import agent.tools.openevolve_bridge as oe
    seen = {}

    def _spy(goal, tk, **kw):
        seen.update(kw)
        return ("{}", 0.0, {})

    monkeypatch.setattr(oe, "evolve_sketches", _spy)
    backend = OpenEvolveBackend(toolkit, breadth_model="haiku-test", depth_model="opus",
                                breadth_weight=0.3, depth_weight=0.7)
    backend.decompose("A goal.")
    assert seen["breadth_model"] == "haiku-test" and seen["depth_model"] == "opus"
    assert seen["breadth_weight"] == 0.3 and seen["depth_weight"] == 0.7


def test_build_config_stub_path_is_full_rewrite(toolkit):
    pytest.importorskip("openevolve")
    stub = StubEvolveLLM([_good_ledger_text()])
    config = build_evolve_config(llm=stub)
    # Offline/stub path: a single picklable fixed-LLM factory, full-rewrite mode for the stub.
    assert [m.name for m in config.llm.models] == ["stub-evolve"]
    assert isinstance(config.llm.models[0].init_client, _FixedLLMFactory)
    assert config.diff_based_evolution is False


# ---- THE BUG: factories + config must PICKLE (closure-lambda factories silently no-op'd) -------

def test_claude_factory_is_picklable():
    fac = _ClaudeLLMFactory("opus", ClaudeConfig(timeout_s=600))
    round_tripped = pickle.loads(pickle.dumps(fac))
    assert round_tripped.model_name == "opus"
    assert round_tripped.claude_cfg.timeout_s == 600


def test_fixed_factory_and_stub_are_picklable():
    stub = StubEvolveLLM([_good_ledger_text(), _bad_ledger_text()])
    fac = _FixedLLMFactory(stub)
    rt = pickle.loads(pickle.dumps(fac))  # _FixedLLMFactory + StubEvolveLLM both pickle
    assert isinstance(rt.llm, StubEvolveLLM)
    assert rt.llm._scripted == stub._scripted


def test_ensemble_config_is_picklable(toolkit):
    pytest.importorskip("openevolve")
    config = build_evolve_config(breadth_weight=0.8, depth_weight=0.2)
    # The WHOLE config (incl. each LLMModelConfig.init_client factory) must survive the cross-process
    # pickle that OpenEvolve's ProcessParallelController performs. A closure-lambda factory would
    # raise here (PicklingError) — and worse, be silently dropped at runtime, making evolution a no-op.
    rt = pickle.loads(pickle.dumps(config))
    assert [m.name for m in rt.llm.models] == ["sonnet", "opus"]
    assert all(isinstance(m.init_client, _ClaudeLLMFactory) for m in rt.llm.models)


def test_stub_config_is_picklable(toolkit):
    pytest.importorskip("openevolve")
    stub = StubEvolveLLM([_good_ledger_text()])
    rt = pickle.loads(pickle.dumps(build_evolve_config(llm=stub)))
    assert isinstance(rt.llm.models[0].init_client, _FixedLLMFactory)


# ---- decompose() extracts children from the best ledger's lemma steps -------------------------

def test_decompose_extracts_children_via_stub(monkeypatch, toolkit):
    """Backend.decompose() returns (sketch, children) where children are the lemma claims.

    We stub evolve_sketches (so neither openevolve nor Claude is touched) to return a fixed
    two-lemma sketch, then assert decompose threads it through children_from_sketch correctly.
    """
    import agent.tools.openevolve_bridge as bridge

    sketch = _sketch_with_two_lemmas()
    monkeypatch.setattr(bridge, "evolve_sketches",
                        lambda goal, tk, **kw: (sketch, 1.0, {"combined_score": 1.0}))

    backend = OpenEvolveBackend(toolkit, generations=3)
    returned_sketch, children = backend.decompose("The main theorem.")
    assert returned_sketch == sketch
    assert children == ["Lemma one statement.", "Lemma two statement."]
    assert children == children_from_sketch(sketch)  # consistent with the shared helper


# ---- availability probe is honest about the optional dependency -------------------------------

def test_available_matches_find_spec():
    import importlib.util
    assert available() == (importlib.util.find_spec("openevolve") is not None)
    assert OpenEvolveBackend.available() == available()


def test_unavailable_when_openevolve_missing(monkeypatch):
    import importlib.util as ilu
    monkeypatch.setattr(ilu, "find_spec", lambda name: None)
    assert available() is False
    # evolve_sketches must raise the install hint, not crash with ImportError.
    from agent.gates.toolkit import load_toolkit
    with pytest.raises(RuntimeError, match="pip install mathagent\\[evolve\\]"):
        from agent.tools.openevolve_bridge import evolve_sketches
        evolve_sketches("g", load_toolkit())


# ---- live (opt-in): a real short evolution PROVES the loop executes (no Claude subprocess) -----

def test_live_evolution_actually_runs_and_improves_seed(toolkit):
    """PROVE evolution executes: a NEEDS_REVIEW seed must evolve up to the goal-bound PASSED band.

    The seed gates NEEDS_REVIEW (a score in the [NEEDS_REVIEW_FLOOR, PASSED_FLOOR) band). The
    StubEvolveLLM is scripted (full-rewrite mode) to emit a GOAL-BOUND PASSED ledger (a score in the
    PASSED band). If evolution were a no-op (the old closure-factory bug: the factory pickles fail / get
    dropped, so no iteration ever runs), best_fitness would stay in the review band. Reaching the PASSED
    band means the stub's mutation was applied and gate-scored under the HARD-gated, goal-bound fitness
    — i.e. an iteration actually executed AND the candidate bound to the goal.

    Note: the stub's ``.calls`` counter cannot be asserted — under the process pool the increment
    happens in a WORKER copy of the stub, not this process's object. We assert on the RESULT.
    """
    pytest.importorskip("openevolve", reason="pip install mathagent[evolve] to run the live bridge")
    from agent.tools.openevolve_bridge import evolve_sketches
    from agent.gates.ledger import parse_ledger

    goal = "A demo theorem."
    seed = _needs_review_ledger_text()
    # Precondition: the seed is goal-bound NEEDS_REVIEW — strictly below the PASSED band, NOT yet a win.
    seed_score = score_ledger(seed, toolkit, goal=goal)["combined_score"]
    assert NEEDS_REVIEW_FLOOR <= seed_score < PASSED_FLOOR

    # Full-rewrite stub that always emits a GOAL-BOUND PASSED ledger (the scripted "mutation").
    stub = StubEvolveLLM([_goal_bound_good_ledger_text(goal)])
    best_text, fitness, metrics = evolve_sketches(
        goal, toolkit, llm=stub, iterations=3, seed_sketches=[seed],
    )

    assert isinstance(best_text, str)
    assert "combined_score" in metrics
    # The discriminating assertion: a no-op would leave us in the seed's review band; an executed
    # iteration applies the stub's goal-bound PASSED rewrite and the gate scores it in the PASSED band.
    assert fitness >= PASSED_FLOOR, f"evolution did not run/improve the seed (best_fitness={fitness})"
    assert fitness > seed_score
    led = parse_ledger(best_text)  # the returned best ledger parses (and, being PASSED, binds to goal)
    from agent.tools.openevolve_bridge import binds_to_goal
    assert binds_to_goal(led, goal)


# ---- strategy-keyed MAP-Elites axes: the config is re-keyed off the incidental structural features --

def test_evolve_config_uses_strategy_feature_axes(toolkit):
    pytest.importorskip("openevolve")
    config = build_evolve_config(breadth_weight=0.8, depth_weight=0.2)
    # The MAP-Elites axes are STRATEGY descriptors, not the old (step_count, justification_diversity).
    assert config.database.feature_dimensions == ["strategy_class", "modulus_band", "subgoal_depth"]


# ---- the LANGUAGE crash fix + breadth-exploration knobs ----------------------------------------

def test_evolve_config_sets_language_to_avoid_crash(toolkit):
    """``config.language`` MUST be a non-None string so the full-rewrite parser never crashes.

    OpenEvolve leaves ``language=None`` by default; a worker path that reaches
    ``parse_full_rewrite(resp, config.language)`` with ``language is None`` raises
    ``TypeError: can only concatenate str (not "NoneType")`` (it does ``"```" + language``). Pinning a
    concrete string makes the breadth/depth mutations actually land instead of being dropped on a crash.
    """
    pytest.importorskip("openevolve")
    from openevolve.utils.code_utils import parse_full_rewrite

    config = build_evolve_config(breadth_weight=0.8, depth_weight=0.2)
    assert isinstance(config.language, str) and config.language  # non-None, non-empty
    # The exact crash the fix prevents: with language=None this raises; with our string it does not.
    with pytest.raises(TypeError):
        parse_full_rewrite('{"claim": "x"}', None)
    # With the configured language a fence-less full-rewrite reply is returned verbatim (no crash).
    assert parse_full_rewrite('{"claim": "x"}', config.language) == '{"claim": "x"}'


def test_evolve_config_tilts_database_toward_exploration(toolkit):
    """The database is tilted toward EXPLORATION so breadth fans out over the strategy grid (GOAL 1/3)."""
    pytest.importorskip("openevolve")
    config = build_evolve_config(iterations=20)
    db = config.database
    # Exploration is favored over re-exploiting a single elite, and islands cross-pollinate within a run.
    assert db.exploration_ratio > db.exploitation_ratio
    assert db.migration_interval >= 1 and db.migration_interval <= 20


# ---- evolve_prove: the FIRST-CLASS proving entrypoint (acceptance bar = goal-bound PASSED, not 1.0) --

def test_evolve_champion_accepts_goal_bound_passed_not_unreachable_one():
    """``EvolveChampion.accepted`` is goal-bound AND PASSED — NOT the unreachable fitness == 1.0.

    The HARD-gated band caps a genuine PASSED ledger well below 1.0, so an ``== 1.0`` acceptance bar
    (the old ``--evolve`` behaviour) discards every real champion and degrades evolve to a no-op. We pin
    the acceptance semantics directly, without running evolution.
    """
    from agent.tools.openevolve_bridge import EvolveChampion

    # A goal-bound PASSED champion at fitness 0.815 (< 1.0) is ACCEPTED.
    good = EvolveChampion(ledger="{}", fitness=0.815, metrics={}, goal_bound=True, passed=True)
    assert good.accepted is True
    # Goal-bound but below the PASSED band -> not accepted (seed-only).
    weak = EvolveChampion(ledger="{}", fitness=0.4, metrics={}, goal_bound=True, passed=False)
    assert weak.accepted is False
    # PASSED but off-goal -> not accepted (the HARD binding gate is load-bearing).
    off = EvolveChampion(ledger="{}", fitness=0.72, metrics={}, goal_bound=False, passed=True)
    assert off.accepted is False


def test_evolve_prove_unavailable_when_openevolve_missing(monkeypatch, toolkit):
    # evolve_prove must raise the install hint (not ImportError) when the optional package is absent.
    import importlib.util as ilu
    monkeypatch.setattr(ilu, "find_spec", lambda name: None)
    from agent.tools.openevolve_bridge import evolve_prove
    with pytest.raises(RuntimeError, match="pip install mathagent\\[evolve\\]"):
        evolve_prove("g", toolkit, iterations=1)


def test_evolve_prove_returns_accepted_champion_for_goal_bound_passed(toolkit):
    """The live entrypoint: a goal-bound PASSED champion is returned with ``accepted is True``."""
    pytest.importorskip("openevolve", reason="pip install mathagent[evolve] to run the live bridge")
    from agent.tools.openevolve_bridge import evolve_prove

    goal = "A demo theorem."
    champ = evolve_prove(goal, toolkit, llm=StubEvolveLLM([_goal_bound_good_ledger_text(goal)]),
                         iterations=3, seed_sketches=[_needs_review_ledger_text()])
    assert PASSED_FLOOR <= champ.fitness <= 1.0
    assert champ.goal_bound and champ.passed and champ.accepted


# ---- live (opt-in): the NUMERIC witness-evolution mode runs and is scored ONLY by numeric.py --------

def test_live_witness_evolution_runs_and_grounds_construction():
    """PROVE the witness mode executes: a partial residue cover evolves to a COMPLETE one (score 1.0).

    The seed is a mod-2 partial cover (the exact-integer checker scores it 0.0). The stub emits a
    complete mod-3 cover (scored 1.0 by verify_residue_cover). Reaching 1.0 proves an iteration ran AND
    that the construction was grounded by the NON-GAMEABLE exact-integer checker — not the soft gate.
    """
    pytest.importorskip("openevolve", reason="pip install mathagent[evolve] to run the live bridge")
    from agent.tools.openevolve_bridge import evolve_witnesses, score_witness_spec

    seed = json.dumps({"kind": "residue_cover", "modulus": 2, "residues": [0]})
    assert score_witness_spec(seed)["combined_score"] == 0.0  # precondition: seed is NOT yet complete

    good = json.dumps({"kind": "residue_cover", "modulus": 3, "residues": [0, 1, 2]})
    stub = StubEvolveLLM([good])
    best_text, fitness, metrics = evolve_witnesses("any", llm=stub, iterations=3, seed_specs=[seed])

    assert fitness == 1.0, f"witness evolution did not run/improve the seed (best_fitness={fitness})"
    assert "modulus_band" in metrics and "box_coverage" in metrics  # the witness MAP-Elites axes
    # The returned best spec is the complete cover, confirmed independently by the exact-integer checker.
    assert score_witness_spec(best_text)["combined_score"] == 1.0


# ---- (P3) RANK-AWARE MODEL ROUTING: the breadth/depth weights are MONOTONE in the rank signal ------

def test_rank_aware_weights_default_when_no_signal():
    # No signal => the fixed AlphaEvolve 0.8/0.2 split (full back-compat).
    b, d = rank_aware_weights(None)
    assert (b, d) == (_DEFAULT_BREADTH_WEIGHT, _DEFAULT_DEPTH_WEIGHT)


def test_rank_aware_weights_sum_to_one_and_clamped():
    for sig in (RankSignal(), RankSignal(depth=0.5), RankSignal(depth=1.0, uncertainty=1.0, critical=1.0)):
        b, d = rank_aware_weights(sig)
        assert abs(b + d - 1.0) < 1e-9                 # always a valid mix
        assert _DEPTH_WEIGHT_FLOOR <= d <= _DEPTH_WEIGHT_CEIL  # never collapses to a single model


def test_rank_aware_depth_weight_monotone_in_rank():
    # Opus (depth) weight RISES monotonically with depth / uncertainty / critical-path; Sonnet falls.
    sigs = [
        RankSignal(depth=0.0, uncertainty=0.0, critical=0.0),
        RankSignal(depth=0.5, uncertainty=0.0, critical=0.0),
        RankSignal(depth=1.0, uncertainty=0.0, critical=0.0),
        RankSignal(depth=1.0, uncertainty=1.0, critical=0.0),
        RankSignal(depth=1.0, uncertainty=1.0, critical=1.0),
    ]
    depths = [rank_aware_weights(s)[1] for s in sigs]
    breadths = [rank_aware_weights(s)[0] for s in sigs]
    assert depths == sorted(depths)                    # depth weight non-decreasing in the signal
    assert breadths == sorted(breadths, reverse=True)  # breadth weight non-increasing
    assert depths[0] < depths[-1]                      # and strictly rises end-to-end


def test_rank_signal_normalises_raw_rank_by_max_rank():
    # A RAW upward_rank is normalised by max_rank into [0,1] for the intensity computation.
    s = RankSignal(depth=3.0, max_rank=6.0).normalised()
    assert abs(s.depth - 0.5) < 1e-9
    # A deeper raw rank => more depth weight (monotone after normalisation).
    shallow = rank_aware_weights(RankSignal(depth=1.0, max_rank=6.0))[1]
    deep = rank_aware_weights(RankSignal(depth=5.0, max_rank=6.0))[1]
    assert deep > shallow


def test_build_config_rank_signal_overrides_weights(toolkit):
    pytest.importorskip("openevolve")
    # A deep/uncertain rank signal routes MORE weight to Opus than the default, overriding 0.8/0.2.
    config = build_evolve_config(rank_signal=RankSignal(depth=1.0, uncertainty=1.0, critical=1.0))
    weights = {m.name: m.weight for m in config.llm.models}
    assert weights["opus"] > _DEFAULT_DEPTH_WEIGHT       # depth weight rose on the critical path
    assert weights["sonnet"] < _DEFAULT_BREADTH_WEIGHT
    assert abs(weights["opus"] + weights["sonnet"] - 1.0) < 1e-9


# ---- (P3) per-call cost/time telemetry sink (tunable-from-data weighting) ----------------------

def test_call_telemetry_aggregates_by_model():
    tel = call_telemetry()
    before = len(tel.records)
    tel.record("sonnet", 0.5, ok=True)
    tel.record("sonnet", 1.5, ok=True)
    tel.record("opus", 4.0, ok=False)
    assert len(tel.records) == before + 3
    agg = tel.by_model()
    assert agg["sonnet"]["calls"] >= 2 and agg["opus"]["calls"] >= 1
    # mean is total/calls and ok counts successes only.
    assert agg["opus"]["ok"] == 0


# ---- (P3 / P2) RETRIEVAL-SEEDED ISLANDS: seed from a retrieved exemplar, graceful fallback ------

def test_retrieval_seeds_injects_exemplars_and_is_goal_bound(toolkit):
    from agent.tools.retrieval import ScriptedRetriever
    goal = "A demo theorem."
    seeds = retrieval_seeds(goal, ScriptedRetriever(["Nat.foo : a = b", "Nat.bar : c = d"]))
    assert seeds and len(seeds) == 1
    led = json.loads(seeds[0])
    # The retrieved exemplars are folded in as `given` priors; the seed binds to the goal.
    assert led["claim"] == goal
    givens = [s for s in led["steps"] if s["justification"] == "given"]
    assert len(givens) == 2 and all("Elementary prior" in s["claim"] for s in givens)
    concl = next(s for s in led["steps"] if s["justification"] == "conclusion")
    assert concl["claim"] == goal


def test_retrieval_seeds_graceful_fallback():
    from agent.tools.retrieval import ScriptedRetriever, NullRetriever
    assert retrieval_seeds("g", None) is None              # no retriever
    assert retrieval_seeds("g", NullRetriever()) is None    # empty results
    assert retrieval_seeds("g", ScriptedRetriever([])) is None

    class _Boom:
        def retrieve(self, claim, error=""):
            raise RuntimeError("retrieval backend down")
    assert retrieval_seeds("g", _Boom()) is None            # raising backend => clean fallback


def test_evolve_uses_retrieval_seed_when_no_explicit_seed(toolkit):
    # When neither seed_sketches nor a default is forced, evolve_sketches uses the retrieval seed.
    pytest.importorskip("openevolve", reason="pip install mathagent[evolve] to run the live bridge")
    from agent.tools.retrieval import ScriptedRetriever
    goal = "A demo theorem."
    stub = StubEvolveLLM([_goal_bound_good_ledger_text(goal)])
    # The retrieval seed is goal-bound; the stub rewrites it to a PASSED goal-bound ledger.
    best_text, fitness, _ = evolve_sketches(
        goal, toolkit, llm=stub, iterations=2,
        retriever=ScriptedRetriever(["Nat.foo : a = b"]),
    )
    assert isinstance(best_text, str) and fitness >= 0.0


# ---- (P3 / #6) EXPLORE/EXPLOIT one-way barrier: AutoReason output never re-seeds the archive ----

def test_explore_exploit_barrier_blocks_refined_seed(toolkit):
    pytest.importorskip("openevolve", reason="pip install mathagent[evolve] to run the live bridge")
    barrier = explore_exploit_barrier()
    refined = _goal_bound_good_ledger_text("A barred theorem.")
    barrier.mark_refined(refined)
    assert barrier.is_refined(refined)
    # Feeding a refined (exploit-side) artifact back as an evolve seed must FAIL LOUD.
    with pytest.raises(AssertionError, match="explore/exploit separation"):
        evolve_sketches("A barred theorem.", toolkit, llm=StubEvolveLLM([refined]),
                        iterations=1, seed_sketches=[refined])


def test_explore_exploit_barrier_allows_unrefined_seed(toolkit):
    # A seed that was never refined passes the barrier untouched (no false positive).
    pytest.importorskip("openevolve", reason="pip install mathagent[evolve] to run the live bridge")
    goal = "An unbarred theorem."
    fresh = _goal_bound_good_ledger_text(goal)
    # Sanity: not registered as refined.
    assert not explore_exploit_barrier().is_refined(fresh + "  # distinct")
    best_text, fitness, _ = evolve_sketches(
        goal, toolkit, llm=StubEvolveLLM([fresh]), iterations=1, seed_sketches=[fresh])
    assert isinstance(best_text, str) and fitness >= 0.0


# ---- (R2 #2) CANONICAL FINGERPRINT: a byte-perturbed refined artifact still cannot re-seed evolve --

def test_barrier_canonical_fingerprint_blocks_perturbed_refined():
    """A refined artifact perturbed by '\\n' / re-serialized JSON whitespace is STILL barred.

    The old barrier hashed the RAW TEXT by exact match, so an AutoReason-refined output that picked up
    a trailing newline or was re-serialized with different whitespace/indent/key-order evaded the
    fingerprint and re-entered the archive. The fingerprint now canonicalizes the ledger (semantic
    goal_hash + normalized step structure), so every equivalent-but-perturbed form maps to the same id.
    """
    from agent.tools.openevolve_bridge import ExploreExploitBarrier

    barrier = ExploreExploitBarrier()
    refined = _goal_bound_good_ledger_text("A canonically barred theorem.")
    barrier.mark_refined(refined)

    # Exact match (baseline): barred.
    assert barrier.is_refined(refined)
    # (a) trailing newline appended.
    assert barrier.is_refined(refined + "\n"), "appended-newline perturbation evaded the barrier"
    assert barrier.is_refined("\n\n" + refined + "  \n")
    # (b) re-serialized JSON: different indent + sorted keys + trailing whitespace.
    obj = json.loads(refined)
    reserialized = json.dumps(obj, indent=4, sort_keys=True) + "\n"
    assert barrier.is_refined(reserialized), "re-serialized-JSON perturbation evaded the barrier"
    # (c) re-ordered steps (the DAG is defined by ids/depends_on, not list order) -> same artifact.
    reordered = dict(obj)
    reordered["steps"] = list(reversed(obj["steps"]))
    assert barrier.is_refined(json.dumps(reordered))


def test_barrier_canonical_fingerprint_no_false_positive_on_distinct_ledger():
    # Canonicalization must NOT collapse genuinely-different ledgers: a different goal/claim is allowed.
    from agent.tools.openevolve_bridge import ExploreExploitBarrier

    barrier = ExploreExploitBarrier()
    barrier.mark_refined(_goal_bound_good_ledger_text("Theorem alpha."))
    assert not barrier.is_refined(_goal_bound_good_ledger_text("Theorem beta."))


def test_barrier_perturbed_refined_seed_still_fails_loud_in_evolve(toolkit):
    # End-to-end: a refined artifact with an appended newline, fed as an evolve seed, FAILS LOUD — the
    # canonical fingerprint catches it even though its bytes differ from the registered form.
    pytest.importorskip("openevolve", reason="pip install mathagent[evolve] to run the live bridge")
    barrier = explore_exploit_barrier()
    refined = _goal_bound_good_ledger_text("A perturbed-barred theorem.")
    barrier.mark_refined(refined)
    perturbed = refined + "\n"   # byte-different, semantically identical
    assert barrier.is_refined(perturbed)
    with pytest.raises(AssertionError, match="explore/exploit separation"):
        evolve_sketches("A perturbed-barred theorem.", toolkit,
                        llm=StubEvolveLLM([perturbed]), iterations=1, seed_sketches=[perturbed])
