"""OpenEvolve bridge: evolve proof-sketch LEDGERS with MAP-Elites + the deterministic gate as oracle.

This adds a third candidate-generation path alongside the Ralph loop (CodexProver) and CodexDecomposer:
a population search over *proof-sketch ledgers* (JSON strings conforming to
``agent/gates/ledger.schema.json``). OpenEvolve's MAP-Elites population search mutates the ledger
*text*; the fitness oracle is a HARD-GATED + GRADED score built on :func:`agent.gates.gate.evaluate`
(a purely deterministic parse + structural + obligation + numeric-AST + prose check) **conjoined with a
deterministic goal-binding + vacuity gate and an obligation-debt penalty**.

The LLM backing the mutations is a real **AlphaEvolve-style ensemble** driven through the headless
``claude`` CLI (:func:`agent.tools.claude_cli._run_claude`): a FAST breadth model (**Sonnet**, sampled
often, high weight) plus a STRONGER depth model (**Opus**, sampled rarely, low weight). OpenEvolve
realizes the ensemble as a weighted ``config.llm.models`` list and samples one model per generation by
normalized weight.

FITNESS (hardened — see research/docs/openevolve_stacking_brief.md §9):
  * **HARD goal-binding gate (P0).** A candidate whose top-level claim AND terminal conclusion do not
    bind to the GOAL by DETERMINISTIC ``goal_hash`` equality (NOT an LLM vote), or that is vacuous (the
    conclusion is a trivial restatement of a hypothesis), gets ``combined_score = 0.0`` (a HARD zero).
    The goal is threaded into the serialised OpenEvolve evaluator as a generated ASCII-safe constant.
  * **NEEDS_REVIEW (0.5) is not a win.** A NEEDS_REVIEW verdict scores strictly below any goal-bound
    PASSED ledger and is never treated as success.
  * **Graded terms feed the score** (only once the hard gate is cleared): obligations discharged
    (re-checked deterministically where possible), subgoal-simplicity, MINUS obligation debt — every
    asserted nontrivial lemma that creates an unresolved typed obligation is penalised even when the
    ledger is structurally valid.

SAFETY (non-negotiable):
  * The evolved artifact is ALWAYS a JSON ledger string — never executable code. The evaluator
    READS the candidate file as text and passes it to ``parse_ledger`` + ``gate.evaluate``. It never
    ``exec``/``eval``/``import``s the evolved content, so no arbitrary-code-execution surface is added.
  * ``gate.evaluate`` fails closed: a malformed ledger or any internal exception yields verdict
    REJECTED -> fitness 0.0. There is no fail-open path.
  * The witness-evolution mode (:func:`evolve_witnesses`) scores a NUMERIC spec ONLY through
    :func:`agent.tools.numeric.check_witness_spec`, whose restricted no-eval integer AST parses the
    spec's expressions; the spec text is NEVER eval/exec'd.
  * The only subprocess is the audited ``_run_claude`` call (headless ``claude -p``, all tools
    disabled, throwaway cwd, timeout), wrapped in ``asyncio.to_thread``. It only *generates text*; the
    evolved ledger itself is never shelled out, exec'd, or imported.

OpenEvolve is an OPTIONAL dependency (``pip install mathagent[evolve]``). :func:`available` reports
its presence without importing it eagerly; everything in this module works offline against the
deterministic :class:`StubEvolveLLM` / :func:`make_gate_evaluator` plumbing, so the test suite needs
neither the package nor a Codex subprocess.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import time
from dataclasses import dataclass, field
from typing import Optional

from agent.gates.ledger import LedgerError, parse_ledger
from agent.tools.claude_cli import ClaudeConfig, _run_claude
from agent.tools.codex_prover import children_from_sketch


def goal_hash(statement: str) -> str:
    """Deterministic goal hash (the DAG memo key), imported lazily to keep this module decoupled.

    Goal-binding is SOUNDNESS-CRITICAL and must use the SAME canonicalization as the proof DAG's memo
    key, so we re-use ``agent.orchestrator.dag.goal_hash`` rather than re-implementing it (a divergent
    copy could merge distinct goals). The import is function-level so merely importing this bridge does
    not eagerly pull in the whole orchestrator package — the bridge stays disjoint from the orchestrator
    worker, and its in-process tests do not depend on the orchestrator package being import-clean.
    """
    from agent.orchestrator.dag import goal_hash as _gh

    return _gh(statement)

# Default AlphaEvolve ensemble weights: breadth (Sonnet) sampled OFTEN, depth (Opus) sampled RARELY.
_DEFAULT_BREADTH_WEIGHT = 0.8
_DEFAULT_DEPTH_WEIGHT = 0.2

# Clamp band for the DEPTH (Opus) share so a rank-aware weighting never collapses the ensemble to a
# single model: even on the deepest critical chain we keep SOME breadth, and even on the widest shallow
# frontier we keep SOME depth. (Forge §4.3 / P3: HEFT-style heterogeneous routing — Opus weight rises
# with critical-path depth / uncertainty; Sonnet handles the wide shallow frontier.)
_DEPTH_WEIGHT_FLOOR = 0.10   # the shallow, confident frontier still gets a little depth
_DEPTH_WEIGHT_CEIL = 0.80    # the deepest, least-confident goal still keeps a little breadth


@dataclass
class RankSignal:
    """The scheduling signal that tilts the breadth/depth ensemble for a node (P3 rank-aware routing).

    All fields are normalised to [0, 1] (or auto-normalised via :meth:`normalised`):
      * ``depth``       — node depth / ``upward_rank`` on the proof DAG, normalised by ``max_rank``;
        a DEEPER node (long critical chain) routes MORE weight to Opus (depth).
      * ``uncertainty`` — 1 - confidence in the current goal (e.g. repeated direct/decomposer failure);
        a LESS-confident goal routes MORE weight to Opus.
      * ``critical``    — 1.0 if the node is on the critical path (the deepest chain), else 0.0; a small
        additive nudge toward depth.

    A higher aggregate => more Opus (depth) weight, clamped so the ensemble never collapses. Defaulting
    every field to 0.0 reproduces the fixed 0.8/0.2 split (full back-compat when no signal is given)."""
    depth: float = 0.0
    uncertainty: float = 0.0
    critical: float = 0.0
    max_rank: float = 0.0   # if > 0, `depth` is treated as a RAW rank and divided by this

    def normalised(self) -> "RankSignal":
        d = self.depth
        if self.max_rank and self.max_rank > 0:
            d = self.depth / self.max_rank
        return RankSignal(
            depth=_clamp01(d),
            uncertainty=_clamp01(self.uncertainty),
            critical=_clamp01(self.critical),
            max_rank=0.0,
        )

    def intensity(self) -> float:
        """A single [0, 1] 'route-to-depth' intensity aggregating the normalised components."""
        n = self.normalised()
        # Depth and uncertainty are the primary drivers (equal weight); critical-path is a small nudge.
        raw = 0.45 * n.depth + 0.45 * n.uncertainty + 0.10 * n.critical
        return _clamp01(raw)


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else float(x))


def rank_aware_weights(signal: Optional[RankSignal]) -> tuple[float, float]:
    """Derive ``(breadth_weight, depth_weight)`` from a rank/uncertainty ``signal`` (P3).

    MONOTONE + CLAMPED: the depth (Opus) weight is a monotone-increasing function of the signal's
    route-to-depth intensity, linearly interpolated between the default depth share (at intensity 0)
    and ``_DEPTH_WEIGHT_CEIL`` (at intensity 1), then clamped to ``[_DEPTH_WEIGHT_FLOOR, _DEPTH_WEIGHT_CEIL]``.
    Breadth is the complement, so the two always sum to 1.0. ``signal is None`` reproduces the fixed
    default split exactly (back-compat).

    Returns weights summing to 1.0. Because the interpolation is monotone in ``intensity`` and
    ``intensity`` is monotone in each normalised component, a deeper / less-confident / critical-path
    node never gets LESS depth weight than a shallower / more-confident one."""
    if signal is None:
        return _DEFAULT_BREADTH_WEIGHT, _DEFAULT_DEPTH_WEIGHT
    t = signal.intensity()
    depth = _DEFAULT_DEPTH_WEIGHT + t * (_DEPTH_WEIGHT_CEIL - _DEFAULT_DEPTH_WEIGHT)
    depth = max(_DEPTH_WEIGHT_FLOOR, min(_DEPTH_WEIGHT_CEIL, depth))
    return 1.0 - depth, depth


# --------------------------------------------------------------------------------------------------
# Per-call cost/time telemetry. The rank-aware weighting (above) is a heuristic that should be TUNED
# FROM DATA (Forge §4.3: "emit per-call cost/time telemetry so the weighting is tunable"). This is an
# in-process, append-only ledger of (model, wall_seconds, ok) records; it never affects scoring. A
# process-wide default sink is provided so the Claude LLM can record without threading a handle through
# OpenEvolve's picklable factory boundary (the records live in whatever process made the call).
# --------------------------------------------------------------------------------------------------
@dataclass
class CallRecord:
    model: str
    seconds: float
    ok: bool


@dataclass
class CallTelemetry:
    """Append-only per-call cost/time ledger, aggregable by model for data-driven weight tuning."""
    records: list = field(default_factory=list)

    def record(self, model: str, seconds: float, ok: bool) -> None:
        self.records.append(CallRecord(model=model, seconds=float(seconds), ok=bool(ok)))

    def by_model(self) -> dict:
        """{model: {calls, total_seconds, mean_seconds, ok}} aggregate over recorded calls."""
        agg: dict = {}
        for r in self.records:
            a = agg.setdefault(r.model, {"calls": 0, "total_seconds": 0.0, "ok": 0})
            a["calls"] += 1
            a["total_seconds"] += r.seconds
            a["ok"] += 1 if r.ok else 0
        for a in agg.values():
            a["mean_seconds"] = a["total_seconds"] / a["calls"] if a["calls"] else 0.0
        return agg


_TELEMETRY = CallTelemetry()


def call_telemetry() -> CallTelemetry:
    """The process-wide call-telemetry sink (per-call cost/time for tuning the rank-aware weights)."""
    return _TELEMETRY


# --------------------------------------------------------------------------------------------------
# EXPLORE/EXPLOIT one-way barrier (P3 / openevolve_stacking_brief §9 #6 + anti-pattern: "Don't feed
# AutoReason's refined output back into the evolutionary archive"). AutoReason (exploit, monotone
# no-regression polish) runs AFTER OpenEvolve (explore) on the proven champion. Its refined output must
# stay OUT of the evolutionary archive: re-injecting a "better-sounding" synthesis into a
# diversity-maximising explore loop can let a non-elementary synthesis propagate. We make this an
# EXECUTABLE invariant: a refined artifact is registered here, and the evolve entry points ASSERT no
# seed they ingest was produced by refinement.
# --------------------------------------------------------------------------------------------------
def _canonical_form(text: str) -> str:
    """A CANONICALIZED, semantic form of a ledger artifact for fingerprinting (whitespace/JSON-stable).

    A raw-text EXACT match is trivially evaded by a byte perturbation that AutoReason's
    AutoReason-refined output picks up for free: appending a ``\\n``, re-serialising the JSON with
    different whitespace/indent/key order, or re-fencing it. So instead of hashing the bytes we hash
    the SEMANTIC content:

      * If the text parses as a ledger: ``goal_hash(claim)`` plus, for every step, a normalized tuple
        ``(goal_hash(claim), justification, sorted(depends_on), canonical(obligations))``. This folds
        all whitespace/indent/key-order/notation differences the goal_hash canonicalization already
        folds, so an equivalent-but-perturbed refined ledger maps to the SAME fingerprint.
      * Otherwise (non-ledger text): a whitespace/JSON-normalized fallback — parse as JSON and re-dump
        with sorted keys + no incidental whitespace if possible, else collapse runs of whitespace —
        so an appended newline or reflowed JSON still canonicalizes identically.

    Deterministic; NO LLM, NO exec/eval (only json.loads on DATA + the deterministic goal_hash)."""
    import json as _json

    raw = text or ""
    # Primary: a parsed ledger -> hash its semantic structure (immune to whitespace/serialization).
    try:
        led = parse_ledger(raw)
    except (LedgerError, Exception):  # any parse failure -> fall through to the text-normalized form
        led = None
    if led is not None:
        steps = []
        for s in led.steps:
            deps = sorted(s.depends_on)
            ob = ""
            if isinstance(s.obligations, dict) and s.obligations:
                try:
                    ob = _json.dumps(s.obligations, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True)
                except Exception:
                    ob = repr(sorted(s.obligations.items()))
            steps.append((goal_hash(s.claim), s.justification, tuple(deps), ob))
        # Step ORDER is not semantically load-bearing (the DAG is defined by ids/depends_on), so sort
        # the per-step tuples to make the fingerprint invariant to step reordering too.
        return "L|" + goal_hash(led.claim) + "|" + repr(sorted(steps))

    # Fallback: whitespace/JSON-normalized text (an appended newline / reflowed JSON canonicalizes).
    stripped = raw.strip()
    try:
        obj = _json.loads(stripped)
        return "J|" + _json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except Exception:
        import re as _re
        return "T|" + _re.sub(r"\s+", " ", stripped)


def _content_fingerprint(text: str) -> str:
    import hashlib
    return hashlib.sha256(_canonical_form(text).encode("utf-8")).hexdigest()[:16]


class ExploreExploitBarrier:
    """A registry of AutoReason-refined (exploit-side) artifacts that must NEVER seed evolve (explore)."""

    def __init__(self):
        self._refined: set[str] = set()

    def mark_refined(self, text: str) -> None:
        """Record that ``text`` is an AutoReason-refined (exploit) artifact — barred from the archive."""
        if text:
            self._refined.add(_content_fingerprint(text))

    def is_refined(self, text: str) -> bool:
        return bool(text) and _content_fingerprint(text) in self._refined

    def assert_not_refined(self, text: str) -> None:
        """Raise if ``text`` is an AutoReason-refined artifact being (mis)used as an evolve seed."""
        if self.is_refined(text):
            raise AssertionError(
                "explore/exploit separation violated: an AutoReason-refined artifact must NOT be "
                "fed back into the OpenEvolve evolutionary archive (one-directional explore->exploit)")


_BARRIER = ExploreExploitBarrier()


def explore_exploit_barrier() -> ExploreExploitBarrier:
    """The process-wide explore/exploit barrier (AutoReason-refined artifacts barred from evolve seeds)."""
    return _BARRIER

# --------------------------------------------------------------------------------------------------
# Fitness band layout. The HARD gate (goal-binding / vacuity) zeroes the score; the verdict sets a
# floor band, and graded terms add a bounded bonus WITHIN the band so ordering is preserved:
#
#   0.0                      : REJECTED, off-goal, vacuous, or any HARD-gate failure (a hard zero).
#   (0.0, 0.5)               : goal-bound but NEEDS_REVIEW (0.5 is NOT a win) — strictly below PASSED.
#   [PASSED_FLOOR, 1.0]      : goal-bound AND PASSED_DETERMINISTIC; graded terms lift it toward 1.0.
#
# Concretely: NEEDS_REVIEW occupies the band [NEEDS_REVIEW_FLOOR, NEEDS_REVIEW_FLOOR+BAND_WIDTH] and
# PASSED occupies [PASSED_FLOOR, 1.0]; PASSED_FLOOR > NEEDS_REVIEW band-top, so ANY goal-bound PASSED
# out-ranks ANY goal-bound NEEDS_REVIEW, which out-ranks anything that fails the hard gate.
# --------------------------------------------------------------------------------------------------
HARD_ZERO = 0.0
NEEDS_REVIEW_FLOOR = 0.30
PASSED_FLOOR = 0.60
_NEEDS_REVIEW_BAND = 0.20   # NEEDS_REVIEW graded bonus lives in [0.30, 0.50]
_PASSED_BAND = 0.40         # PASSED graded bonus lives in [0.60, 1.00]


# --------------------------------------------------------------------------------------------------
# Optional-dependency probe (mirrors neural_retrieval.available()): never imports openevolve eagerly.
# --------------------------------------------------------------------------------------------------
def available() -> bool:
    """True iff the optional ``openevolve`` package is importable (without importing it)."""
    try:
        return importlib.util.find_spec("openevolve") is not None
    except Exception:  # pragma: no cover - find_spec only raises on broken meta-paths
        return False


def _require_openevolve():
    if not available():
        raise RuntimeError("openevolve not installed; pip install mathagent[evolve]")


# --------------------------------------------------------------------------------------------------
# LLM backends.
# --------------------------------------------------------------------------------------------------
def _make_claude_evolve_llm_cls():
    """Build the ``ClaudeEvolveLLM`` class lazily (it subclasses an openevolve type, deferred import).

    Returns the class object. Raises RuntimeError via :func:`_require_openevolve` if the package is
    missing — callers should gate on :func:`available` first. The class is constructed *inside the
    worker process* by :class:`_ClaudeLLMFactory`, so the class object itself never has to pickle.
    """
    _require_openevolve()
    from openevolve.llm.base import LLMInterface

    class ClaudeEvolveLLM(LLMInterface):
        """An ``openevolve`` LLM backend that drives evolution via the headless Claude CLI.

        Parameterized by ``model_name`` (``"sonnet"`` for breadth / ``"opus"`` for depth). The
        ensemble's ``_sample_model`` reads ``vars(instance)['model']``, so ``self.model`` MUST exist
        and reflect the chosen model — it is set to ``model_name``. Both async methods offload the
        blocking subprocess to a worker thread via ``asyncio.to_thread`` so the event loop is never
        blocked.
        """

        def __init__(self, model_name: str, claude_cfg: ClaudeConfig):
            self.model = model_name  # REQUIRED: ensemble does vars(self)['model']; also picks role
            # Bind the chosen model into the (picklable) config so _run_claude shells out as it.
            self._claude_cfg = replace_model(claude_cfg, model_name)

        async def generate(self, prompt: str, **kwargs) -> str:
            return await self.generate_with_context(
                system_message="You are an expert in elementary number theory editing a JSON "
                                "proof-sketch ledger. Respond with the requested SEARCH/REPLACE "
                                "diff or full ledger only.",
                messages=[{"role": "user", "content": prompt}],
                **kwargs,
            )

        async def generate_with_context(self, system_message: str, messages, **kwargs) -> str:
            user_text = messages[-1]["content"] if messages else ""
            full_prompt = f"{system_message}\n\n{user_text}"
            # Per-call cost/time telemetry (Forge §4.3): record wall-time + ok per model so the
            # rank-aware breadth/depth weighting can be tuned from data. Recording never affects
            # generation; a failure is recorded then re-raised so the loop's own handling is unchanged.
            start = time.perf_counter()
            try:
                out = await asyncio.to_thread(_run_claude, full_prompt, self._claude_cfg)
            except Exception:
                call_telemetry().record(self.model, time.perf_counter() - start, ok=False)
                raise
            call_telemetry().record(self.model, time.perf_counter() - start, ok=True)
            return out

    return ClaudeEvolveLLM


def replace_model(claude_cfg: ClaudeConfig, model_name: str) -> ClaudeConfig:
    """Return a copy of ``claude_cfg`` with ``model`` set to ``model_name`` (keeps timeout/launcher)."""
    return ClaudeConfig(model=model_name, timeout_s=claude_cfg.timeout_s, launcher=claude_cfg.launcher)


# --------------------------------------------------------------------------------------------------
# PICKLABLE model factories. OpenEvolve's ProcessParallelController PICKLES the whole Config (with its
# LLMModelConfig.init_client factories) to ship to worker processes. A closure/lambda is UNPICKLABLE,
# so a closure factory is silently dropped and every iteration becomes a no-op that only scores the
# seed. These are MODULE-LEVEL classes whose state ((model_name, ClaudeConfig) or a picklable stub) is
# itself picklable; the LLMInterface instance is constructed *inside the worker* by __call__.
# --------------------------------------------------------------------------------------------------
class _ClaudeLLMFactory:
    """Picklable ``init_client`` factory: builds a :class:`ClaudeEvolveLLM` for ``model_name``.

    Holds only ``(model_name, claude_cfg)`` — both picklable — so it survives the cross-process pickle
    of OpenEvolve's Config. ``__call__`` receives the ``LLMModelConfig`` and returns the LLM instance,
    constructed in the worker (where the openevolve subclass import is available)."""

    def __init__(self, model_name: str, claude_cfg: ClaudeConfig):
        self.model_name = model_name
        self.claude_cfg = claude_cfg

    def __call__(self, model_cfg):  # OpenEvolve calls init_client(LLMModelConfig) -> LLMInterface
        cls = _make_claude_evolve_llm_cls()
        return cls(self.model_name, self.claude_cfg)


class _FixedLLMFactory:
    """Picklable ``init_client`` factory that returns a pre-built (picklable) LLM instance.

    Used to inject :class:`StubEvolveLLM` for offline tests without a closure. The wrapped ``llm`` must
    itself be picklable (``StubEvolveLLM`` is — plain lists/ints/str)."""

    def __init__(self, llm):
        self.llm = llm

    def __call__(self, model_cfg):
        return self.llm


class StubEvolveLLM:
    """Deterministic, dependency-free LLM stand-in for offline tests.

    NOT an ``LLMInterface`` subclass (it never imports openevolve). It cycles a scripted list of
    ledger JSON strings, so the evolution plumbing — and especially :func:`make_gate_evaluator` — can
    be exercised without a Codex subprocess or the optional package. ``generate`` returns successive
    scripted outputs; ``generate_sync`` is the synchronous accessor used by tests.
    """

    def __init__(self, scripted: list[str]):
        if not scripted:
            raise ValueError("StubEvolveLLM needs at least one scripted ledger string")
        self.model = "stub-evolve"
        self._scripted = list(scripted)
        self.calls = 0

    def generate_sync(self) -> str:
        out = self._scripted[self.calls % len(self._scripted)]
        self.calls += 1
        return out

    async def generate(self, prompt: str, **kwargs) -> str:  # pragma: no cover - parity shim
        return self.generate_sync()

    async def generate_with_context(self, system_message: str, messages, **kwargs) -> str:  # pragma: no cover
        return self.generate_sync()


# --------------------------------------------------------------------------------------------------
# Deterministic goal-binding + vacuity gate (the HARD, non-gameable selection pressure).
#
# These helpers are pure (goal_hash equality only — NO LLM) so the in-process scorer and the
# serialised OpenEvolve evaluator can share the exact same logic. A candidate that fails ANY of these
# checks is a HARD zero: it does not even enter the graded band.
# --------------------------------------------------------------------------------------------------
def _conclusion_step(ledger):
    """The single terminal 'conclusion' step (or None if absent / not exactly one)."""
    concls = [s for s in ledger.steps if s.justification == "conclusion"]
    return concls[0] if len(concls) == 1 else None


def binds_to_goal(ledger, goal: str) -> bool:
    """True iff the ledger's top-level claim AND its terminal conclusion both bind to ``goal``.

    Binding is DETERMINISTIC ``goal_hash`` equality (the same canonicalization the DAG memo key uses) —
    never an LLM vote. A ledger whose claim==goal but whose conclusion proves a different statement
    (claim-drift / off-goal) does NOT bind, so the search cannot hill-climb toward a weaker/off-goal
    statement that still passes the structural gate.
    """
    gh = goal_hash(goal)
    if goal_hash(ledger.claim) != gh:
        return False
    concl = _conclusion_step(ledger)
    return concl is not None and goal_hash(concl.claim) == gh


def is_vacuous(ledger) -> bool:
    """True iff the conclusion is a trivial restatement of a hypothesis (proves nothing new).

    A conclusion whose claim equals (by ``goal_hash``) the claim of one of its ``given``/``assumption``
    premises has discharged no real obligation — the "proof" restates an assumption. This is the
    drift-to-vacuity failure mode the brief calls out; it is a HARD zero regardless of structure.
    """
    concl = _conclusion_step(ledger)
    if concl is None:
        return False
    ch = goal_hash(concl.claim)
    for s in ledger.steps:
        if s.id == concl.id:
            continue
        if s.justification in ("given", "assumption") and goal_hash(s.claim) == ch:
            return True
    return False


# --------------------------------------------------------------------------------------------------
# Obligation-debt accounting + strategy descriptors (the GRADED terms and the MAP-Elites axes).
#
# Obligation DEBT: every step whose justification *requires* a typed obligation (case_cover / descent /
# bounding / split_coprimality) but does NOT carry a usable one is "debt" — an asserted nontrivial
# lemma with an unresolved obligation. We penalise debt even when the ledger is structurally valid
# (the structural gate only rejects when the obligation key is missing entirely; a present-but-empty or
# numerically-unconfirmed obligation slips through structurally yet is still unresolved content). The
# 'lemma' justification is also a deferred obligation (its claim is a child goal proven elsewhere), so
# every cited-but-not-discharged lemma adds debt too.
# --------------------------------------------------------------------------------------------------
_OBLIGATION_KEY = {
    "case_cover": "case_cover",
    "descent": "descent",
    "bounding": "bounding",
    "split_coprimality": "split_coprimality",
}

# Pure BOOKKEEPING justifications: they assert no standalone mathematical fact of their own.
#   * ``given`` / ``assumption`` are PREMISE ANCHORS — the problem's hypotheses, grounded by fiat;
#   * ``conclusion`` is the terminal restatement (its goal-binding is policed by the HARD gate);
#   * ``lemma`` is a deferred obligation, debited separately (its claim is proven in another DAG node).
# Every OTHER ``obligation:none`` justification (algebra / divisibility / congruence / parity /
# factorization / well_ordering / vierzahlensatz / pell / ...) can carry a load-bearing crux.
_PREMISE_ANCHORS = frozenset({"given", "assumption"})
_BOOKKEEPING = _PREMISE_ANCHORS | frozenset({"conclusion", "lemma"})

# STRUCTURAL un-grounded-crux detection (replaces the old _ASSERTS_HARD_FACT allowlist).
#
# The allowlist was the wrong defense: it only debited a FIXED set of named hard methods, so a crux
# RELABELED under a generic ``obligation:none`` tag (algebra / divisibility / congruence / parity /
# factorization / well_ordering / ...) accrued ZERO debt and a fake all-'algebra' chain could BEAT an
# honest obligation-discharging ledger (and DEEPER fakes scored HIGHER). We INVERT the defense to a
# STRUCTURAL detector: a non-bookkeeping ``obligation:none`` step asserting a NON-TRIVIAL STANDALONE
# claim is an UN-GROUNDED CRUX (assertion debt) UNLESS it is content-grounded — i.e. it (or one of its
# transitive dependencies) discharges a typed/numeric obligation, so its content is re-checkable. A
# step that merely RESTATES a premise / an already-grounded step's claim (goal_hash equality) is
# trivial bookkeeping, not a crux. This is tag-agnostic, so relabeling the crux under any
# ``obligation:none`` tag cannot evade it.

# Strategy classes for the dominant-justification-class MAP-Elites axis. Order is the axis index.
_STRATEGY_CLASSES = ["descent", "casework", "gcd", "induction", "bounding", "other"]
_STRATEGY_OF = {
    "descent": "descent", "vieta_jumping": "descent", "well_ordering": "descent",
    "case_split": "casework", "congruence": "casework", "parity": "casework",
    "quadratic_residue": "casework",
    "gcd_coprimality": "gcd", "euclid_splitting": "gcd", "factorization": "gcd",
    "divisibility": "gcd",
    "induction": "induction",
    "bounding": "bounding", "squeeze": "bounding", "extremal": "bounding", "pigeonhole": "bounding",
}


def _discharges_typed_obligation(step, toolkit) -> bool:
    """True iff ``step`` carries a present, non-empty TYPED obligation its justification requires.

    A discharged typed obligation (case_cover / descent / bounding / split_coprimality) is the
    re-checkable CONTENT ANCHOR that grounds downstream ``obligation:none`` reasoning. (A
    present-but-empty obligation is not a discharge — it slips through the structural gate but adds
    no re-checkable content.)
    """
    if not toolkit.is_allowed(step.justification):
        return False
    kind = toolkit.obligation_for(step.justification)
    if kind == "none" or kind not in _OBLIGATION_KEY:
        return False
    ob = step.obligations.get(_OBLIGATION_KEY[kind]) if isinstance(step.obligations, dict) else None
    return bool(ob)


# Trivial-cover modulus: a COMPLETE residue cover mod 2 is the coarsest non-trivial partition
# ("n is even or odd") and carries NO problem-specific content — it is exactly the auditor's
# canonical VACUOUS obligation (case_cover{modulus:2,residues:[0,1]}), used to LAUNDER an arbitrarily
# long downstream crux chain. A cover is only a CONTENT ANCHOR (one that can ground a directly-
# dependent crux) when it is a non-trivial partition: modulus >= 3.
_MIN_NONVACUOUS_COVER_MODULUS = 3


def _nonvacuous_typed_obligation(step, toolkit) -> bool:
    """True iff ``step`` discharges a NON-VACUOUS typed obligation — a RELEVANT content anchor (V6c).

    A discharged typed obligation grounds a directly-dependent crux ONLY when it is itself non-vacuous:
      * ``case_cover`` — a *complete* cover is only contentful if it is a non-trivial partition
        (modulus >= 3). A complete mod-2 cover ``[0,1]`` is "n is even or odd" — trivially true and
        GOAL-IRRELEVANT, so it grounds NOTHING (it cannot launder a downstream crux chain to debt 0).
      * ``descent`` / ``bounding`` / ``split_coprimality`` — present & non-empty is enough (their
        content is the asserted measure / bound / coprimality reference, re-checked by the gate).

    This is the deterministic RELEVANCE test of V6(c): a complete-but-trivial cover a fake crux merely
    descends from grounds nothing, so a single ``case_cover{mod2,[0,1]}`` can no longer zero an
    arbitrarily long chain of fake cruxes.
    """
    if not _discharges_typed_obligation(step, toolkit):
        return False
    kind = toolkit.obligation_for(step.justification)
    if kind == "case_cover":
        ob = step.obligations.get("case_cover") if isinstance(step.obligations, dict) else None
        m = ob.get("modulus") if isinstance(ob, dict) else None
        if not isinstance(m, int) or m < _MIN_NONVACUOUS_COVER_MODULUS:
            return False
    return True


# Typed obligations that CITE a prior step by id as their MANDATORY provenance (the framework REQUIRES
# and CHECKS this citation deterministically). Each entry maps the obligation key -> the obligation
# field naming the cited provenance step id. Today the only such obligation is ``split_coprimality``,
# whose ``coprimality_from`` MUST reference a prior ``gcd_coprimality`` step that is a transitive
# ANCESTOR of the splitting step (enforced in ``agent/gates/obligations.py::_check_split_coprimality``:
# a dangling / wrong-kind / non-ancestor reference is a deterministic REJECT). The CITED step is the
# sanctioned, required, and gate-verified bookkeeping that a discharged split LEANS ON — it is grounded
# content, not a crux. Adding a new provenance-citing obligation here (with its required+checked
# citation field) extends this WITHOUT broadening the laundering surface, because only the step the
# DISCHARGED obligation actually NAMES in its provenance field qualifies.
_DISCHARGED_PROVENANCE_FIELD = {
    "split_coprimality": "coprimality_from",
}


def _discharged_obligation_provenance_ids(ledger, toolkit) -> set:
    """Ids of steps CITED as the required provenance of a DISCHARGED, provenance-bearing typed obligation.

    A discharged ``split_coprimality`` obligation MUST name (in ``coprimality_from``) the prior
    ``gcd_coprimality`` step it relies on, and the gate REJECTs the ledger unless that reference is a
    real, correctly-typed, transitive ANCESTOR of the splitting step (see
    ``agent/gates/obligations.py::_check_split_coprimality``). The cited step is therefore the
    framework's OWN mandated, gate-verified bookkeeping anchor for the split — NOT an un-grounded crux.

    This returns the set of such cited provenance ids so :func:`_content_grounded_ids` can treat them as
    GROUNDED. It is MINIMAL and CONSERVATIVE: only a step actually NAMED by the provenance field of a
    DISCHARGED obligation (one whose obligation key is present and non-empty) qualifies. A
    present-but-empty obligation discharges nothing and contributes no provenance; an arbitrary
    ``gcd_coprimality`` step that no discharged split cites stays a crux. So this opens no new laundering
    channel — it only un-caps the framework's REQUIRED-and-CITED honest pattern.
    """
    by_id = {s.id: s for s in ledger.steps}
    out: set = set()
    for s in ledger.steps:
        if not _discharges_typed_obligation(s, toolkit):
            continue
        kind = toolkit.obligation_for(s.justification)
        field_name = _DISCHARGED_PROVENANCE_FIELD.get(kind)
        if field_name is None:
            continue
        ob = s.obligations.get(_OBLIGATION_KEY[kind]) if isinstance(s.obligations, dict) else None
        ref = ob.get(field_name) if isinstance(ob, dict) else None
        # Only honor a citation that names a REAL step (the gate already REJECTs dangling/wrong-kind/
        # non-ancestor citations, so any ref surviving here is a sanctioned ancestor provenance step).
        if isinstance(ref, str) and ref in by_id:
            out.add(ref)
    return out


def _content_grounded_ids(ledger, toolkit) -> set:
    """Ids of steps that are CONTENT-GROUNDED by a LOCAL, RELEVANT anchor — no transitive laundering, no
    width-pumping (R3 + R5).

    A step is content-grounded iff one of:
      * it is a PREMISE ANCHOR (``given`` / ``assumption``) — grounded by fiat;
      * it itself DISCHARGES a typed obligation — a re-checkable content anchor;
      * it is the REQUIRED + CITED PROVENANCE of a DISCHARGED typed obligation downstream — the
        framework's OWN mandated bookkeeping (e.g. the ``gcd_coprimality`` step a discharged
        ``split_coprimality`` names in ``coprimality_from``, gate-verified to be a correctly-typed
        ANCESTOR). Such a step is sanctioned grounded content, NOT a crux (see
        :func:`_discharged_obligation_provenance_ids`);
      * its standalone claim merely RESTATES (``goal_hash`` equality) a premise / already-grounded step
        it directly depends on — trivial bookkeeping, not a crux;
      * it has a DIRECT dependency that is a NON-VACUOUS discharged typed obligation (a content anchor
        RELEVANT to it because it actually depends on it) — "elaboration grounding".

    R3 anti-laundering (LOCAL grounding): grounding does NOT propagate transitively through other
    un-grounded cruxes, nor from a vacuous/distant anchor reachable only in the global closure. A chain
    ``cover -> fake0 -> fake1 -> ...`` grounds at most ``fake0`` (its only contentful dependency is the
    cover); ``fake1`` onward depend only on another un-grounded crux, so they accrue ~N debt.

    R5 anti-width-pump (SATURATING grounding — Fix 1/2): a single non-vacuous discharged obligation can
    ELABORATION-ground at most ONE downstream crux SPINE. The mod-3 discharged-PUMP stacks N INDEPENDENT
    complete covers, each 1:1 'grounding' its own FALSE parallel crux that the conclusion conjoins; under
    purely-local grounding all N cruxes were grounded (debt 0) and the additive/depth reward pumped. We
    cap it structurally: when the terminal conclusion's load-bearing support rests on MULTIPLE distinct
    elaboration-grounded cruxes (each leaning on a DIFFERENT discharged anchor — the PUMP's WIDTH), only
    ONE such spine is honored; the surplus parallel cruxes are RE-CLASSIFIED un-grounded. A genuine proof
    with a single discharged obligation and a single grounded elaboration is unaffected (one spine), while
    a wider construction can never out-ground — nor out-score — one honest discharge.

    Computed in dependency order (a step's dependencies are resolved before it). Cyclic/dangling deps
    are treated as un-grounded (the structural gate already REJECTs them, so this is belt-and-braces)."""
    by_id = {s.id: s for s in ledger.steps}
    grounded: set = set()
    # Steps CITED as the required provenance of a DISCHARGED, provenance-bearing typed obligation
    # (e.g. the gcd_coprimality step named by a discharged split_coprimality's coprimality_from). These
    # are the framework's OWN mandated + gate-verified bookkeeping anchors, grounded by sanction.
    provenance_ids = _discharged_obligation_provenance_ids(ledger, toolkit)
    # elaboration_anchor[sid] = the discharged-anchor id that ELABORATION-grounds sid (and nothing else
    # grounds it). Used by the anti-width-pump saturation post-pass below.
    elaboration_anchor: dict = {}
    claim_hash: dict = {}

    def gh(step) -> str:
        h = claim_hash.get(step.id)
        if h is None:
            h = goal_hash(step.claim)
            claim_hash[step.id] = h
        return h

    # Topologically order the steps (Kahn); fall back to listed order for any cyclic remainder.
    indeg = {s.id: 0 for s in ledger.steps}
    for s in ledger.steps:
        for d in s.depends_on:
            if d in indeg:
                indeg[s.id] += 1
    ready = [s.id for s in ledger.steps if indeg[s.id] == 0]
    order: list = []
    adj: dict = {s.id: [] for s in ledger.steps}
    for s in ledger.steps:
        for d in s.depends_on:
            if d in adj:
                adj[d].append(s.id)
    seen_ready: set = set(ready)
    while ready:
        cur = ready.pop()
        order.append(cur)
        for nxt in adj[cur]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0 and nxt not in seen_ready:
                ready.append(nxt)
                seen_ready.add(nxt)
    for s in ledger.steps:  # append any cyclic remainder so every step is visited once
        if s.id not in order:
            order.append(s.id)

    for sid in order:
        step = by_id.get(sid)
        if step is None:
            continue
        if step.justification in _PREMISE_ANCHORS:
            grounded.add(sid)            # premise anchors are grounded by fiat
            continue
        if _discharges_typed_obligation(step, toolkit):
            grounded.add(sid)            # a discharged obligation is itself a content anchor
            continue
        if sid in provenance_ids:
            # Sanctioned provenance: this step is the required + gate-verified citation of a DISCHARGED
            # typed obligation downstream (the gcd_coprimality a split_coprimality leans on). It is the
            # framework's OWN mandated bookkeeping anchor, not an un-grounded crux -> grounded.
            grounded.add(sid)
            continue
        # Trivial restatement of a premise / already-grounded step -> bookkeeping, grounded.
        h = gh(step)
        restates_grounded = any(
            d in grounded and d in by_id and gh(by_id[d]) == h for d in step.depends_on
        )
        if restates_grounded:
            grounded.add(sid)
            continue
        # LOCAL grounding: a non-trivial assertion is grounded ONLY by a NON-VACUOUS discharged typed
        # obligation it DIRECTLY depends on (the anchor it actually leans on, relevant to it). Grounding
        # does NOT propagate transitively through other un-grounded cruxes or via a vacuous / distant
        # anchor, so a single ``case_cover{mod2,[0,1]}`` cannot launder a downstream chain. Record the
        # anchor so the anti-width-pump post-pass can saturate one-spine-per-anchor.
        anchors = [d for d in step.depends_on
                   if d in by_id and _nonvacuous_typed_obligation(by_id[d], toolkit)]
        if anchors:
            grounded.add(sid)
            elaboration_anchor[sid] = anchors[0]
            continue
        # Otherwise it is an un-grounded crux -> assertion debt charged in _obligation_debt.

    # R5 ANTI-WIDTH-PUMP SATURATION. The mod-3 discharged-PUMP stacks N INDEPENDENT complete covers,
    # each 1:1 elaboration-grounding its own PARALLEL crux that the conclusion conjoins. We keep ONE
    # elaboration spine and re-classify the surplus PARALLEL (mutually-independent) elaboration cruxes as
    # UN-GROUNDED, so the structural cap fires and width cannot out-score one honest discharge.
    #
    # CRUCIAL: only TRULY PARALLEL elaboration cruxes are saturated. A genuine SERIAL proof that
    # discharges several obligations along ONE spine (cover -> elaborate -> descent -> elaborate -> ...)
    # has each later elaboration crux TRANSITIVELY DEPENDING ON the earlier one, so they lie on a single
    # chain (not parallel) and are ALL kept — a multi-discharge serial proof is unaffected. We treat two
    # elaboration cruxes as parallel iff neither is in the other's transitive depends_on cone AND their
    # grounding anchors are distinct and likewise mutually independent.
    support = _conclusion_support_ids(ledger)
    in_support = [sid for sid in elaboration_anchor if sid in support]
    if len(in_support) >= 2:
        cone = {sid: reachable_closure(by_id, sid) for sid in in_support}

        def parallel(a: str, b: str) -> bool:
            # independent iff neither crux is in the other's cone AND their anchors are also independent
            if a in cone[b] or b in cone[a]:
                return False
            ca, cb = elaboration_anchor[a], elaboration_anchor[b]
            if ca == cb:
                return False
            ac = reachable_closure(by_id, ca)
            bc = reachable_closure(by_id, cb)
            return ca not in bc and cb not in ac

        # Keep the deepest spine; un-ground every OTHER crux that is parallel to it (the PUMP's width).
        keep = max(in_support, key=lambda s: (_dep_depth(by_id, s), s))
        for sid in in_support:
            if sid != keep and parallel(sid, keep):
                grounded.discard(sid)
    return grounded


def reachable_closure(by_id: dict, sid: str) -> set:
    """Transitive ``depends_on`` closure of ``sid`` (EXCLUDING sid itself); cycle-safe, iterative."""
    seen: set = set()
    stack = list(by_id[sid].depends_on) if sid in by_id else []
    while stack:
        cur = stack.pop()
        if cur in seen or cur not in by_id:
            continue
        seen.add(cur)
        stack.extend(by_id[cur].depends_on)
    return seen


def _dep_depth(by_id: dict, sid: str, _stack: frozenset = frozenset()) -> int:
    """Longest depends_on chain below ``sid`` (cycle-safe), used to pick the deepest spine to keep."""
    step = by_id.get(sid)
    if step is None or sid in _stack:
        return 0
    deps = [d for d in step.depends_on if d in by_id]
    return 1 + max((_dep_depth(by_id, d, _stack | {sid}) for d in deps), default=0)


def _obligation_debt(ledger, toolkit) -> tuple[int, int]:
    """(debt, discharged): count UNRESOLVED typed/assertion obligations vs. RESOLVED ones.

    A step creates an obligation when:
      * its justification requires a TYPED obligation (case_cover / descent / bounding /
        split_coprimality) — resolved iff the required obligation key is present and non-empty;
        otherwise debt (a present-but-empty obligation slips through the structural gate yet is
        unresolved content);
      * it is a cited ``lemma`` — a DEFERRED (formalisation/proof) obligation, always debt here
        (the sub-lemma is proven in another DAG node, not in this ledger);
      * it is an UN-GROUNDED STANDALONE CRUX — a non-bookkeeping ``obligation:none`` step asserting a
        NON-TRIVIAL claim that is NOT content-grounded (it does not DIRECTLY depend on a non-vacuous
        discharged obligation, and it does not merely restate a premise/grounded step). This is the
        STRUCTURAL, tag-agnostic successor to the old named-method allowlist: relabeling the crux under
        ANY ``obligation:none`` tag (algebra / divisibility / congruence / ...) cannot evade it.

    PER-CRUX, ADDITIVE (V6b): debt is charged INDEPENDENTLY for EACH un-grounded crux, and grounding is
    LOCAL (:func:`_content_grounded_ids`), so a chain of N fake cruxes accrues ~N debt — no single
    upstream anchor can zero them all. The structural gate already rewards numeric truth (via the
    verdict); this term adds the *unresolved-content* pressure the gate cannot express, so a real
    obligation-discharging ledger out-ranks an equally-PASSED relabel-the-hard-theorem ledger.
    """
    grounded = _content_grounded_ids(ledger, toolkit)
    debt = 0
    discharged = 0
    for s in ledger.steps:
        if s.justification == "lemma":
            debt += 1  # deferred obligation: this sub-lemma is proven elsewhere, not in this ledger
            continue
        if not toolkit.is_allowed(s.justification):
            continue
        kind = toolkit.obligation_for(s.justification)
        if kind != "none" and kind in _OBLIGATION_KEY:
            key = _OBLIGATION_KEY[kind]
            ob = s.obligations.get(key) if isinstance(s.obligations, dict) else None
            if ob:
                discharged += 1
            else:
                debt += 1
            continue
        # No typed obligation required by the toolkit. A non-bookkeeping obligation:none step that is
        # NOT content-grounded is an un-grounded standalone crux -> assertion debt (the §3 trap, now
        # caught structurally regardless of which obligation:none tag it is relabeled under).
        if s.justification in _BOOKKEEPING:
            continue
        if s.id not in grounded:
            debt += 1
    return debt, discharged


def _ungrounded_crux_ids(ledger, toolkit) -> set:
    """Ids of non-bookkeeping ``obligation:none`` steps that are UN-GROUNDED cruxes (assertion debt).

    These are the steps :func:`_obligation_debt` charges as assertion debt; the subgoal-depth bonus
    must NOT count a chain padded with them as 'deeper' proof structure (else a deeper fake out-scores
    a shallower fake). Exposed as a helper so :func:`_grounded_subgoal_depth` can exclude them."""
    grounded = _content_grounded_ids(ledger, toolkit)
    out: set = set()
    for s in ledger.steps:
        if s.justification in _BOOKKEEPING:
            continue
        if not toolkit.is_allowed(s.justification):
            continue
        kind = toolkit.obligation_for(s.justification)
        if kind != "none" and kind in _OBLIGATION_KEY:
            continue  # typed-obligation steps are handled by the typed-debt path, not crux debt
        if s.id not in grounded:
            out.add(s.id)
    return out


def _is_content_anchor(step, toolkit) -> bool:
    """True iff ``step`` provides DETERMINISTICALLY re-checkable CONTENT the conclusion can rest on (R5).

    A content anchor is one of:
      * a NON-VACUOUS discharged typed obligation (case_cover mod>=3 / descent / bounding /
        split_coprimality) — re-checkable numeric content;
      * a cited ``lemma`` — a deferred obligation pointing to a child node PROVEN elsewhere in the DAG
        (its proof is a separate audited node, not a bare in-ledger assertion).

    R5 (Fix 3) — a bare ``method_ref`` is NOT a deterministic content anchor. A named-method step
    (``vierzahlensatz`` / ``pell`` / ``cauchy_bezout`` / ...) is an INVOCATION of a documented method
    whose correctness is NOT re-checkable offline by the deterministic gate — it is a CANDIDATE for the
    Layer-4 (Lean) audit, not a soft-PASSED winner. Treating any ``method_ref`` as a PASSED-band anchor
    let a lone bare named-method crux (the vierzahlensatz trap) reach the PASSED band with zero
    re-checkable content. So a ledger whose conclusion rests only on such a crux is routed to the
    NEEDS_REVIEW band (the cap below), which is the CORRECT vierzahlensatz-trap semantics: a relabel
    that discharges nothing is at most a NEEDS_REVIEW candidate, never a top-band winner.
    """
    if _nonvacuous_typed_obligation(step, toolkit):
        return True
    if step.justification == "lemma":
        return True
    return False


def _conclusion_support_ids(ledger) -> set:
    """The terminal conclusion's transitive load-bearing support (every step it depends on)."""
    by_id = {s.id: s for s in ledger.steps}
    concl = _conclusion_step(ledger)
    if concl is None:
        return set()
    seen: set = set()
    stack = list(concl.depends_on)
    support: set = set()
    while stack:
        sid = stack.pop()
        if sid in seen or sid not in by_id:
            continue
        seen.add(sid)
        support.add(sid)
        stack.extend(by_id[sid].depends_on)
    return support


def _conclusion_rests_on_ungrounded_crux(ledger, toolkit) -> bool:
    """THE STRUCTURAL CAP predicate (R5 Fix 4): does the conclusion's load-bearing support contain
    AT LEAST ONE un-grounded crux?

    This is the single provable invariant that bounds ALL present and future relabel/launder variants:
    a search candidate whose terminal conclusion's transitive support contains >= 1 un-grounded crux
    (un-grounded per :func:`_content_grounded_ids` / Fix 1) is HARD-CAPPED strictly BELOW the PASSED
    floor (routed to the NEEDS_REVIEW band by :func:`score_ledger`). Equivalently: a candidate that
    rests on an un-grounded crux is at most a NEEDS_REVIEW candidate for Layer-4, never a top-band
    winner.

    Why this bounds every fake: every documented relabel/launder/method_ref construction leaves >= 1
    un-grounded crux in the conclusion support (a generic ``obligation:none`` assertion with no
    re-checkable content, or a bare ``method_ref`` step which Fix 3 makes a non-anchor un-grounded
    crux). An HONEST ledger leaves NONE: its conclusion rests on premises, discharged obligations, cited
    lemmas, and cruxes that RELEVANTLY depend on a non-vacuous discharged obligation in their own cone —
    all grounded — so it is not capped and stays in the PASSED band.

    (Replaces the weaker V6 ``rests_only_on_ungrounded_cruxes`` test, which demoted only when NO content
    anchor appeared ANYWHERE in support — letting an off-relevance anchor sitting in a parallel branch
    of ``conclusion.depends_on`` block demotion for an arbitrarily long INDEPENDENT fake chain. The cap
    is now about the PRESENCE of an un-grounded crux in the load-bearing support, not the absence of a
    possibly-irrelevant anchor.)

    ===================================================================================================
    KNOWN LIMITATION (ACCEPTED + DOCUMENTED per the maintainer's decision). This cap grounds a crux
    STRUCTURALLY: a crux is declared grounded when it DIRECTLY depends on a non-vacuous DISCHARGED typed
    obligation (or is the required+cited provenance of one). This is a DETERMINISTIC, SYNTACTIC test of
    DEPENDENCY, not of ENTAILMENT. Consequently a FAKE crux that merely CITES (depends on) a
    trivially-true but complete-and-non-vacuous case-cover — e.g. ``case_cover{modulus:3, residues:[0,1,2]}``
    ("every integer is 0, 1, or 2 mod 3", a tautology that implies NOTHING about the goal) — is thereby
    declared grounded, so it is NOT in ``_ungrounded_crux_ids``, the cap does NOT fire, and the ledger
    CAN reach the PASSED band (empirically ~0.817 — identical to the honest mod-4 discharge).

    Distinguishing this spoof from a legitimate elaboration needs CONTENT-ENTAILMENT — does the cited
    obligation actually IMPLY the crux's claim? — which is exactly proof-checking, and is UNDECIDABLE
    offline (the same reason the project relies on Layer-4 Lean rather than a soft deterministic gate).
    This deterministic fitness is therefore a SEARCH HEURISTIC, NOT A CERTIFIER: the trivial-cover-
    citation spoof can MISLEAD THE SEARCH (it scores like an honest discharge) but it NEVER mints a false
    ``authoritative_elementary`` verdict. The ONLY authoritative elementary filter is Layer-4 (the Lean
    proof-term dependency closure + ``collectAxioms``), which rejects a relabeled / fake / non-entailing
    step because the corresponding Lean term simply will not compile. The spoof is bounded there, not
    here. (Pinned by ``tests/test_evolve_fitness.py::test_known_limitation_trivial_cover_citation_spoof_is_search_only``.)
    ===================================================================================================
    """
    if _conclusion_step(ledger) is None:
        return False
    support = _conclusion_support_ids(ledger)
    ungrounded = _ungrounded_crux_ids(ledger, toolkit)
    return any(sid in ungrounded for sid in support)


def _dominant_strategy_index(ledger) -> int:
    """Index (into _STRATEGY_CLASSES) of the most-used non-bookkeeping justification class."""
    from collections import Counter

    counts: Counter = Counter()
    for s in ledger.steps:
        if s.justification in ("given", "assumption", "conclusion", "lemma", "algebra"):
            continue
        counts[_STRATEGY_OF.get(s.justification, "other")] += 1
    if not counts:
        return _STRATEGY_CLASSES.index("other")
    dominant = counts.most_common(1)[0][0]
    return _STRATEGY_CLASSES.index(dominant)


def _modulus_band(ledger) -> int:
    """A coarse band of the largest case_cover modulus used (0 if none): banded for MAP-Elites.

    Bands: 0 = no modulus; 1 = mod in [2,4]; 2 = [5,12]; 3 = [13,36]; 4 = >36. Larger casework is a
    distinct STRATEGY behaviour, not just more steps.
    """
    best = 0
    for s in ledger.steps:
        ob = s.obligations if isinstance(s.obligations, dict) else {}
        cc = ob.get("case_cover")
        if isinstance(cc, dict) and isinstance(cc.get("modulus"), int):
            best = max(best, cc["modulus"])
    if best == 0:
        return 0
    if best <= 4:
        return 1
    if best <= 12:
        return 2
    if best <= 36:
        return 3
    return 4


def _subgoal_depth(ledger) -> int:
    """Longest dependency chain into the conclusion (a proxy for sub-goal/proof depth)."""
    by_id = {s.id: s for s in ledger.steps}
    memo: dict[str, int] = {}

    def depth(sid: str, stack: frozenset) -> int:
        if sid in memo:
            return memo[sid]
        step = by_id.get(sid)
        if step is None or sid in stack:
            return 0
        deps = [d for d in step.depends_on if d in by_id]
        d = 1 + (max((depth(x, stack | {sid}) for x in deps), default=0))
        memo[sid] = d
        return d

    concl = _conclusion_step(ledger)
    if concl is None:
        return 0
    return depth(concl.id, frozenset())


def _restatement_ids(ledger) -> set:
    """Ids of non-bookkeeping steps that merely RESTATE (``goal_hash`` equality) a step they depend on.

    A step whose claim equals — by the deterministic ``goal_hash`` canonicalization — the claim of one
    of its direct dependencies asserts NO new mathematical content; it is a trivial restatement (the
    grounded-restatement depth-pump: stacking restatements off a grounded step inflated grounded-depth
    at 0 debt). Such steps must add NO proof depth (see :func:`_grounded_subgoal_depth`), so padding a
    chain with restatements cannot raise the depth bonus. Premise anchors and the conclusion are exempt
    (a conclusion DOES restate the goal by design)."""
    out: set = set()
    by_id = {s.id: s for s in ledger.steps}
    claim_hash: dict = {}

    def gh(step) -> str:
        h = claim_hash.get(step.id)
        if h is None:
            h = goal_hash(step.claim)
            claim_hash[step.id] = h
        return h

    for s in ledger.steps:
        if s.justification in _BOOKKEEPING:
            continue
        h = gh(s)
        if any(d in by_id and gh(by_id[d]) == h for d in s.depends_on):
            out.add(s.id)
    return out


def _grounded_subgoal_depth(ledger, toolkit) -> int:
    """Longest dependency chain into the conclusion that counts only NEW, GROUNDED proof structure.

    The plain :func:`_subgoal_depth` rewards ANY chain length, so a fake chain could PAD itself with
    un-grounded cruxes (a fake all-``algebra`` chain) OR with trivial RESTATEMENTS off a grounded step
    (the grounded-restatement depth-pump) and earn a HIGHER depth bonus than an honest, shorter proof.
    The depth bonus therefore counts a step only when it adds NEW grounded structure:

      * an UN-GROUNDED crux (assertion debt) adds NO depth — it is transparent to the chain;
      * a trivial RESTATEMENT (``goal_hash`` equality with a dependency) adds NO depth — restating a
        grounded step is not new structure, so stacking restatements cannot inflate the bonus.

    A genuine grounded chain (premises -> discharged obligations -> contentful elaboration -> conclusion)
    is unaffected: each step that asserts new, grounded content still contributes 1."""
    by_id = {s.id: s for s in ledger.steps}
    transparent = _ungrounded_crux_ids(ledger, toolkit) | _restatement_ids(ledger)
    memo: dict[str, int] = {}

    def depth(sid: str, stack: frozenset) -> int:
        if sid in memo:
            return memo[sid]
        step = by_id.get(sid)
        if step is None or sid in stack:
            return 0
        deps = [d for d in step.depends_on if d in by_id]
        sub = max((depth(x, stack | {sid}) for x in deps), default=0)
        # An un-grounded crux OR a trivial restatement adds NO depth of its own; it is transparent to
        # the chain (a grounded tail still counts, but padding cannot lengthen it).
        d = sub if sid in transparent else 1 + sub
        memo[sid] = d
        return d

    concl = _conclusion_step(ledger)
    if concl is None:
        return 0
    return depth(concl.id, frozenset())


# --------------------------------------------------------------------------------------------------
# The hardened scalar fitness, shared by the in-process scorer and the serialised OpenEvolve evaluator.
# --------------------------------------------------------------------------------------------------
def _graded_score(verdict_value: str, debt: int, discharged: int, depth: int) -> float:
    """Map a goal-bound, non-vacuous candidate to a scalar IN ITS VERDICT BAND.

    Pre: the HARD gate (goal-binding + vacuity) already passed for any score > 0; this only ranks
    *within* the verdict band. REJECTED -> 0.0 (it never bound anyway). NEEDS_REVIEW lives strictly
    below PASSED. The graded quality in [0, 1] is built from POSITIVE/NEGATIVE terms, then clamped to the
    band so a NEEDS_REVIEW can never reach a PASSED:

      + a SATURATING discharged-content reward (R5 Fix 2): ``discharged / (discharged + debt)`` — a
        bounded RATIO, NOT an additive count. One honest discharge with no debt already MAXES this term
        (ratio 1.0), so stacking N independent discharges (the mod-3 discharged-PUMP) cannot exceed a
        single honest discharge's band — a WIDER construction never out-scores one honest discharge.
      + a bounded GROUNDED sub-goal depth (real proof structure, saturating; an un-grounded crux OR a
        trivial restatement adds NO depth, so padding a fake chain — wide or deep — cannot inflate it).
      - unresolved obligation DEBT (cited lemmas + missing typed obligations + un-grounded STANDALONE
        cruxes asserted under any obligation:none tag — the relabel-the-hard-theorem trap, caught
        structurally). Debt strictly lowers the score AND shrinks the saturating ratio.

    The depth reward is weighted BELOW the saturating discharge ratio so a deeper grounded elaboration
    can refine, but never dominate, the contentful reward; this keeps "discharge a real obligation" the
    dominant incentive an evolutionary search optimises, while removing the additive/pumpable surface.
    """
    if verdict_value == "rejected":
        return HARD_ZERO

    denom = discharged + debt
    discharged_ratio = (discharged / denom) if denom > 0 else 0.0  # SATURATING content reward (R5)
    depth_bonus = min(depth, 6) / 6.0             # saturating reward for genuine grounded proof structure
    debt_penalty = min(debt, 5) / 5.0             # saturating penalty for unresolved obligations
    # Weights chosen so the SATURATING discharge ratio is the dominant contentful reward AND a single
    # honest discharge with one grounded elaboration reproduces the prior honest-discharge band score
    # (PASSED, ~0.815): a bare honest discharge (ratio 1, grounded depth 3) -> quality 0.5375 -> 0.815.
    # The depth term is a small refinement (it cannot dominate the ratio), and debt strictly lowers the
    # score AND shrinks the ratio.
    quality = max(0.0, min(1.0,
        0.30                                       # baseline for a goal-bound, clean-gating ledger
        + 0.225 * discharged_ratio
        + 0.025 * depth_bonus
        - 0.50 * debt_penalty
    ))

    if verdict_value == "passed_deterministic":
        return PASSED_FLOOR + _PASSED_BAND * quality        # [0.60, 1.00]
    # needs_review (or any unexpected verdict that admitted deterministically)
    return NEEDS_REVIEW_FLOOR + _NEEDS_REVIEW_BAND * quality  # [0.30, 0.50]


def score_ledger(text: str, toolkit, goal: Optional[str] = None,
                 *, require_goal: bool = False) -> dict[str, float]:
    """Score one ledger string via the HARD-gated + GRADED fitness. Pure; NEVER executes `text`.

    Returns a metrics dict whose ``combined_score`` is:
      * **0.0 (HARD zero)** if the ledger is REJECTED, OR — when ``goal`` is given — it does not bind to
        the goal (deterministic ``goal_hash`` equality on claim AND conclusion) or is vacuous.
      * a value in **[0.30, 0.50)** if goal-bound but NEEDS_REVIEW (0.5 is NOT a win).
      * a value in **[0.60, 1.00]** if goal-bound AND PASSED_DETERMINISTIC, graded up by discharged
        obligations / sub-goal depth and down by unresolved obligation debt.

    GOAL-BINDING GUARD. ``require_goal`` controls what happens when ``goal is None``:
      * ``require_goal=False`` (the default, TEST-ONLY back-compat path): the HARD binding gate is
        SKIPPED — callers that only want the gate verdict band / obligation grading. This must only be
        used for intentional no-goal scoring; the obligation-debt grading still applies.
      * ``require_goal=True`` (the CERTIFICATION / evolution path): a MISSING goal is a HARD ZERO with
        ``goal_bound=0.0`` — the binding gate is SOUNDNESS-CRITICAL, so a caller that forgot to thread
        the goal must NOT silently admit an un-bound candidate. The serialised OpenEvolve evaluator
        always passes ``require_goal=True`` so a dropped ``MATHAGENT_EVOLVE_GOAL`` fails LOUD (zeroes)
        instead of disabling the gate.

    The MAP-Elites feature dimensions are the STRATEGY descriptors
    ``strategy_class`` / ``modulus_band`` / ``subgoal_depth`` (plus ``step_count`` kept for back-compat).

    ===================================================================================================
    KNOWN LIMITATION (ACCEPTED + DOCUMENTED per the maintainer's decision "ACCEPT + DOCUMENT the limit").
    This soft fitness is a SEARCH HEURISTIC, NOT A CERTIFIER. It grounds a crux STRUCTURALLY — a crux
    counts as grounded when it depends on a non-vacuous DISCHARGED typed obligation (or is the required+
    cited provenance of one) — which is a deterministic test of DEPENDENCY, not of ENTAILMENT. Therefore
    a FAKE crux that merely CITES a trivially-true complete case-cover (e.g.
    ``case_cover{modulus:3, residues:[0,1,2]}`` — a tautology that implies NOTHING about the goal) is
    declared grounded and CAN reach the PASSED band (empirically ~0.817). Telling this spoof apart from a
    legitimate elaboration requires CONTENT-ENTAILMENT (does the obligation actually IMPLY the crux's
    claim?), which is proof-checking and UNDECIDABLE offline — the same reason the project relies on
    Layer-4 Lean. The spoof can MISLEAD THE SEARCH but NEVER mints a false ``authoritative_elementary``
    verdict: Layer-4 (the Lean proof-term dependency closure + ``collectAxioms``) is the ONLY
    authoritative elementary filter, and it rejects a relabeled / fake / non-entailing step because the
    Lean term will not compile. See :func:`_conclusion_rests_on_ungrounded_crux` for the cap predicate
    this limitation lives in. (Pinned by
    ``tests/test_evolve_fitness.py::test_known_limitation_trivial_cover_citation_spoof_is_search_only``.)
    ===================================================================================================
    """
    from agent.gates.gate import evaluate  # local import: gate pulls in jsonschema/yaml

    report = evaluate(text, toolkit)  # fails closed: any exception -> REJECTED inside evaluate
    led = report.ledger

    # CERTIFICATION-path strict guard: a missing goal must NOT silently disable the binding gate.
    # Refuse to admit an un-bound candidate -> HARD ZERO (fail loud as a zero, never a soft pass).
    if require_goal and goal is None:
        return {
            "combined_score": HARD_ZERO,
            "step_count": float(len(led.steps)) if led is not None else 0.0,
            "strategy_class": float(_dominant_strategy_index(led)) if led is not None else float(
                _STRATEGY_CLASSES.index("other")),
            "modulus_band": float(_modulus_band(led)) if led is not None else 0.0,
            "subgoal_depth": float(_subgoal_depth(led)) if led is not None else 0.0,
            "goal_bound": 0.0,
            "obligation_debt": 0.0,
            "obligations_discharged": 0.0,
        }

    # Zero metrics for an unparseable / rejected ledger (no ledger object to describe).
    if led is None or report.verdict.value == "rejected":
        return {
            "combined_score": HARD_ZERO,
            "step_count": float(len(led.steps)) if led is not None else 0.0,
            "strategy_class": float(_dominant_strategy_index(led)) if led is not None else float(
                _STRATEGY_CLASSES.index("other")),
            "modulus_band": float(_modulus_band(led)) if led is not None else 0.0,
            "subgoal_depth": float(_subgoal_depth(led)) if led is not None else 0.0,
            "goal_bound": 0.0,
        }

    # HARD goal-binding + vacuity gate (only when a goal is supplied).
    goal_bound = 1.0
    if goal is not None:
        if not binds_to_goal(led, goal) or is_vacuous(led):
            goal_bound = 0.0

    debt, discharged = _obligation_debt(led, toolkit)
    depth = _subgoal_depth(led)                              # raw chain length: a MAP-Elites axis
    graded_depth = _grounded_subgoal_depth(led, toolkit)    # depth bonus counts GROUNDED structure only

    # THE STRUCTURAL CAP (R5 Fix 4): a structurally-PASSED ledger whose terminal conclusion's
    # load-bearing support contains AT LEAST ONE un-grounded crux is HARD-CAPPED strictly below the
    # PASSED floor (demoted to the NEEDS_REVIEW band). This is the single provable invariant that bounds
    # ALL relabel/launder/method_ref variants: every such fake leaves >= 1 un-grounded crux in the
    # conclusion support (a generic obligation:none assertion, or a bare method_ref step which Fix 3
    # makes a non-anchor un-grounded crux). An HONEST ledger leaves NONE (premises / discharged
    # obligations / cited lemmas / cruxes that RELEVANTLY depend on a non-vacuous discharge in their own
    # cone are all grounded), so it stays in the PASSED band. Demotion to NEEDS_REVIEW is the ONLY way
    # below 0.60 (``_graded_score`` clamps PASSED to [0.60, 1.0]).
    verdict_value = report.verdict.value
    if (verdict_value == "passed_deterministic"
            and _conclusion_rests_on_ungrounded_crux(led, toolkit)):
        verdict_value = "needs_review"

    if goal_bound == 0.0:
        combined = HARD_ZERO
    else:
        combined = _graded_score(verdict_value, debt, discharged, graded_depth)

    return {
        "combined_score": combined,
        "step_count": float(len(led.steps)),
        "strategy_class": float(_dominant_strategy_index(led)),
        "modulus_band": float(_modulus_band(led)),
        "subgoal_depth": float(depth),
        "goal_bound": goal_bound,
        "obligation_debt": float(debt),
        "obligations_discharged": float(discharged),
    }


def make_gate_evaluator(toolkit, goal: Optional[str] = None):
    """Return an in-process evaluator ``f(program_path) -> metrics`` that gates the ledger at that path.

    The file is READ as text and scored via :func:`score_ledger`; the evolved content is never
    executed. When ``goal`` is supplied the HARD goal-binding gate applies (off-goal/vacuous -> 0.0).
    This is the same contract the serialised OpenEvolve evaluator implements, so offline tests can
    validate scoring (with or without the goal gate) without the package.
    """

    def _evaluate(program_path: str) -> dict[str, float]:
        try:
            with open(program_path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            return {"combined_score": HARD_ZERO, "step_count": 0.0, "subgoal_depth": 0.0,
                    "modulus_band": 0.0, "strategy_class": float(_STRATEGY_CLASSES.index("other")),
                    "goal_bound": 0.0}
        return score_ledger(text, toolkit, goal=goal)

    return _evaluate


# --------------------------------------------------------------------------------------------------
# The OpenEvolve evaluator FILE body. This is written to a temp .py by run_evolution; it must be a
# top-level, self-contained function with all imports inlined (it is re-imported per evaluation). It
# READS the evolved program file as text and gates it — it never imports/execs the ledger. The GOAL is
# baked in as a generated ASCII-safe module constant (``_EVOLVE_GOAL_JSON``) that the self-contained
# function reads; the env-var ``MATHAGENT_EVOLVE_GOAL`` is a fallback. The toolkit is reloaded from
# YAML on each call (the module is re-imported per cascade stage).
# --------------------------------------------------------------------------------------------------
#
# IMPORTANT: ``gate_ledger_evaluator`` is reused VERBATIM by OpenEvolve (serialised via getsource).
# It must stay strictly ASCII and import everything it uses; it must NOT be named ``evaluate`` (the
# OpenEvolve wrapper emits ``def evaluate(p): return <name>(p)``, so an ``evaluate`` name self-recurses).
#
# GOAL TRANSPORT. OpenEvolve serialises ONLY the evaluator FUNCTION'S source into a temp module (via
# ``inspect.getsource``); module-level constants are NOT carried across. So the goal is threaded into
# the worker by the **environment variable** ``MATHAGENT_EVOLVE_GOAL`` that :func:`_run_openevolve`
# exports for the duration of the run (child processes inherit it at spawn). The ASCII-safe
# ``_EVOLVE_GOAL_JSON`` module constant below is the in-process (un-serialised) override read FIRST by
# the evaluator; in a serialised worker it is absent, so the lookup defaults to ``"null"`` and the code
# falls back to the env var. If NEITHER is present the binding gate is skipped (goal is None).

# In-process override constant (the serialised worker copy does not include this line). Default "null"
# so the env-var fallback wins everywhere except a caller that deliberately sets a process-wide goal.
_EVOLVE_GOAL_JSON = "null"


def gate_ledger_evaluator(program_path):
    # OpenEvolve evaluator: gate the evolved proof-sketch ledger at program_path with the HARD-gated,
    # goal-bound, obligation-debt-graded fitness.
    #
    # SAFETY: this READS the file as text and scores it with the deterministic gate + a deterministic
    # goal_hash binding check. It NEVER imports, execs, or evals the evolved content. All imports are
    # inlined so the function is self-contained when serialised to a temp module by OpenEvolve.
    import json as _json
    import os as _os

    from agent.gates.gate import evaluate as gate_evaluate
    from agent.gates.toolkit import load_toolkit
    from agent.tools.openevolve_bridge import score_ledger

    # Resolve the baked-in goal: the generated module constant first, then the env-var fallback.
    goal = None
    try:
        goal = _json.loads(globals().get("_EVOLVE_GOAL_JSON", "null"))
    except Exception:
        goal = None
    if goal is None:
        env_goal = _os.environ.get("MATHAGENT_EVOLVE_GOAL")
        if env_goal:
            goal = env_goal

    try:
        with open(program_path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return {"combined_score": 0.0, "step_count": 0.0, "subgoal_depth": 0.0,
                "modulus_band": 0.0, "strategy_class": 5.0, "goal_bound": 0.0}

    # OpenEvolve wraps the initial program in EVOLVE-BLOCK markers and may keep comment lines around
    # the evolved region; strip any leading/trailing comment scaffolding so the gate sees pure JSON.
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    candidate = "\n".join(lines).strip() or text

    toolkit = load_toolkit()
    try:
        # CERTIFICATION path: require_goal=True so a DROPPED MATHAGENT_EVOLVE_GOAL (goal resolves to
        # None) is a HARD ZERO -- the binding gate is SOUNDNESS-CRITICAL and must never be silently
        # disabled by a caller that forgot to thread the goal (it fails loud as a zero, not a soft pass).
        return score_ledger(candidate, toolkit, goal=goal, require_goal=True)
    except Exception:
        # Defense-in-depth: score_ledger already fails closed, but never let the evaluator crash open.
        try:
            gate_evaluate(candidate, toolkit)
        except Exception:
            pass
        return {"combined_score": 0.0, "step_count": 0.0, "subgoal_depth": 0.0,
                "modulus_band": 0.0, "strategy_class": 5.0, "goal_bound": 0.0}


def witness_spec_evaluator(program_path):
    # OpenEvolve evaluator for the NUMERIC witness/construction evolution mode. The evolved artifact is
    # a JSON witness SPEC (residue cover / descent / solution set); it is scored ONLY by the exact-
    # integer checker agent.tools.numeric.check_witness_spec.
    #
    # SAFETY: the spec is parsed by json.loads (DATA only) and its embedded expressions are validated
    # by numeric.py's restricted no-eval integer AST. It is NEVER eval/exec/imported.
    #
    # All names are imported INSIDE the body so the function is self-contained when OpenEvolve copies
    # ONLY this function's source (via getsource) into a temp worker module: a bare reference to the
    # module-level score_witness_spec would be undefined there. (This body is kept strictly ASCII so the
    # serialised temp .py compiles portably.)
    from agent.tools.openevolve_bridge import score_witness_spec

    try:
        with open(program_path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return {"combined_score": 0.0, "modulus_band": 0.0, "box_coverage": 0.0}

    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    candidate = "\n".join(lines).strip() or text
    return score_witness_spec(candidate)


def score_witness_spec(text: str) -> dict[str, float]:
    """Score a NUMERIC witness spec with the exact-integer checker (no eval). Shared in/out of process.

    A spec that the checker confirms scores 1.0; a malformed or unconfirmed spec scores 0.0. The
    MAP-Elites feature axes are ``modulus_band`` and ``box_coverage`` (a coarse log-size of the search
    box, a behaviour descriptor for how much ground the construction covers).
    """
    import json as _json

    from agent.tools.numeric import check_witness_spec

    result = check_witness_spec(text)
    score = 1.0 if result.ok else 0.0

    modulus_band = 0.0
    box_coverage = 0.0
    try:
        obj = _json.loads(text) if isinstance(text, str) else (text if isinstance(text, dict) else {})
    except Exception:
        obj = {}
    if isinstance(obj, dict):
        if obj.get("kind") == "residue_cover" and isinstance(obj.get("modulus"), int):
            m = obj["modulus"]
            modulus_band = float(1 if m <= 4 else 2 if m <= 12 else 3 if m <= 36 else 4)
        bounds = obj.get("bounds")
        if isinstance(bounds, dict):
            pts = 1
            for pair in bounds.values():
                if isinstance(pair, (list, tuple)) and len(pair) == 2 and all(isinstance(x, int) for x in pair):
                    pts *= max(1, pair[1] - pair[0] + 1)
            # log10 band of the box size, capped — a behaviour descriptor, not a hard count.
            import math
            box_coverage = float(min(6, int(math.log10(pts)) if pts > 1 else 0))
    return {"combined_score": score, "modulus_band": modulus_band, "box_coverage": box_coverage}


# --------------------------------------------------------------------------------------------------
# Public entry point.
# --------------------------------------------------------------------------------------------------
def _default_seed(goal: str) -> str:
    """A minimal, structurally-honest seed ledger (a single lemma + conclusion citing it).

    Used when no seed_sketches are supplied. It is intentionally incomplete — the population search
    is expected to evolve it — but it parses and exercises the gate from iteration zero. Both the
    top-level claim and the conclusion are the GOAL, so the seed is goal-bound from the start.
    """
    seed = {
        "problem": "evolve",
        "claim": goal,
        "steps": [
            {"id": "s1", "claim": goal, "justification": "lemma", "depends_on": []},
            {"id": "s2", "claim": goal, "justification": "conclusion", "depends_on": ["s1"]},
        ],
    }
    return json.dumps(seed, ensure_ascii=False, indent=2)


def _default_witness_seed(goal: str) -> str:
    """A minimal, structurally-valid (but trivially-failing) witness spec to seed witness evolution."""
    return json.dumps({"kind": "residue_cover", "modulus": 2, "residues": [0]}, indent=2)


def _exemplar_seed(goal: str, exemplars: list[str]) -> str:
    """A goal-bound seed ledger that injects RETRIEVED elementary exemplars as ``given`` priors.

    The retrieved elementary lemmas (e.g. Mathlib ``Nat.*`` declarations matching the goal) are folded
    in as ``given`` steps so the population starts from elementary priors and the mutation LLM has the
    relevant elementary facts in view from iteration zero — instead of the trivial single-lemma default
    seed that carries no domain prior. Both the top-level claim and the conclusion are the GOAL, so the
    seed is goal-bound from the start (clears the HARD binding gate). The exemplar text is opaque prose
    in a JSON string — it is NEVER executed; it only steers generation."""
    steps = []
    for i, ex in enumerate(exemplars):
        text = (ex or "").strip()
        if not text:
            continue
        steps.append({"id": f"g{i}", "claim": f"Elementary prior: {text}",
                      "justification": "given", "depends_on": []})
    # A single lemma the search is expected to flesh out, citing the priors above.
    dep_ids = [s["id"] for s in steps]
    steps.append({"id": "L", "claim": goal, "justification": "lemma", "depends_on": dep_ids})
    steps.append({"id": "C", "claim": goal, "justification": "conclusion", "depends_on": ["L"]})
    return json.dumps({"problem": "evolve", "claim": goal, "steps": steps},
                      ensure_ascii=False, indent=2)


def retrieval_seeds(goal: str, retriever) -> Optional[list[str]]:
    """Build retrieval-seeded island seeds for ``goal`` (OpenEvolve P2), or ``None`` to fall back.

    Queries ``retriever`` (any object with ``retrieve(claim, error="") -> list[str]``) for elementary
    exemplars matching the goal and folds them into an exemplar seed ledger. Returns ``None`` (a clean
    fallback to the trivial default seed) when ``retriever`` is None, has no ``retrieve``, raises, or
    returns nothing — so retrieval being unavailable is a graceful no-op, never a crash."""
    if retriever is None or not hasattr(retriever, "retrieve"):
        return None
    try:
        hits = retriever.retrieve(goal)
    except Exception:
        return None
    exemplars = [h for h in (hits or []) if isinstance(h, str) and h.strip()]
    if not exemplars:
        return None
    return [_exemplar_seed(goal, exemplars[:8])]


def build_evolve_config(
    *,
    llm=None,
    claude_cfg: Optional[ClaudeConfig] = None,
    breadth_model: str = "sonnet",
    depth_model: str = "opus",
    breadth_weight: float = _DEFAULT_BREADTH_WEIGHT,
    depth_weight: float = _DEFAULT_DEPTH_WEIGHT,
    rank_signal: Optional[RankSignal] = None,
    iterations: int = 20,
    num_islands: int = 2,
    feature_bins: int = 5,
    feature_dimensions: Optional[list[str]] = None,
):
    """Build the OpenEvolve ``Config`` for the proof-sketch search (no evolution run here).

    Factored out of :func:`evolve_sketches` so it can be unit-tested without running evolution. When
    ``llm is None`` the ensemble is the real AlphaEvolve-style **Sonnet (breadth) + Opus (depth)** pair
    backed by :class:`_ClaudeLLMFactory` (a *picklable* module-level factory — the prior closure-lambda
    factory was unpicklable and silently dropped, making evolution a no-op). When ``llm`` is supplied
    (offline tests) a single picklable :class:`_FixedLLMFactory` returns it.

    RANK-AWARE ROUTING (P3). When ``rank_signal`` is given it OVERRIDES ``breadth_weight``/``depth_weight``
    via :func:`rank_aware_weights`: Opus (depth) weight RISES monotonically with the node's depth /
    upward_rank / uncertainty (the critical path / deeper, lower-confidence goals), while Sonnet
    (breadth) carries the wide shallow frontier — clamped so the ensemble never collapses to one model.
    With no signal the explicit/default 0.8/0.2 split is used (full back-compat).

    The MAP-Elites ``feature_dimensions`` default to the STRATEGY descriptors
    ``["strategy_class", "modulus_band", "subgoal_depth"]`` (re-keyed off the incidental
    step_count/justification_diversity axes), but a caller (e.g. the witness mode) may override them.

    For the stub/offline path ``diff_based_evolution=False`` (full-rewrite) so a stub may emit a full
    replacement ledger; the real ensemble path uses diff-based SEARCH/REPLACE over the ledger text.
    """
    _require_openevolve()
    from openevolve.config import Config, LLMModelConfig

    claude_cfg = claude_cfg or ClaudeConfig()

    # Rank-aware routing: a signal derives the (breadth, depth) mix, overriding the passed weights.
    if rank_signal is not None:
        breadth_weight, depth_weight = rank_aware_weights(rank_signal)

    if llm is not None:
        # Offline/stub: one model, full-rewrite mode so the stub can emit a whole replacement ledger.
        models = [LLMModelConfig(name="stub-evolve", weight=1.0, init_client=_FixedLLMFactory(llm))]
        diff_based = False
    else:
        # Real AlphaEvolve ensemble: breadth (fast, high weight) + depth (stronger, low weight). The
        # two model NAMES are parameterized (default Sonnet/Opus) so a profile can address the ensemble;
        # each LLMModelConfig.name AND its _ClaudeLLMFactory reflect the chosen model.
        models = [
            LLMModelConfig(name=breadth_model, weight=breadth_weight,
                           init_client=_ClaudeLLMFactory(breadth_model, claude_cfg)),
            LLMModelConfig(name=depth_model, weight=depth_weight,
                           init_client=_ClaudeLLMFactory(depth_model, claude_cfg)),
        ]
        diff_based = True

    config = Config()
    config.max_iterations = iterations
    config.diff_based_evolution = diff_based
    # ARTIFACT LANGUAGE (avoid a crash). Our evolved artifact is a JSON ledger, NOT a programming
    # language. OpenEvolve leaves ``config.language`` as ``None`` by default and only lazily fills it
    # (via ``extract_code_language(initial_program)``) inside ``Controller.__init__``. Any worker code
    # path that reaches ``parse_full_rewrite(resp, config.language)`` with ``language is None`` crashes
    # with ``TypeError: can only concatenate str (not "NoneType")`` (it does ``"```" + language``). On
    # a JSON ledger ``extract_code_language`` returns ``"unknown"`` anyway, which carries no useful
    # fenced-block semantics — so we pin it to ``"text"`` deterministically: the full-rewrite parser
    # then treats the LLM's whole reply as the new ledger text, and the diff parser's fenced blocks are
    # matched as plain text. This makes the breadth/depth mutations ACTUALLY land instead of being
    # silently dropped on a parse crash. (Set BEFORE the controller's lazy init so ours wins.)
    config.language = "text"
    config.llm.models = models
    config.llm.evaluator_models = list(config.llm.models)  # post_init gotcha: set explicitly
    config.database.num_islands = max(1, num_islands)
    config.database.feature_dimensions = list(
        feature_dimensions or ["strategy_class", "modulus_band", "subgoal_depth"]
    )
    config.database.feature_bins = feature_bins
    # BREADTH EXPLORATION (AlphaEvolve's core power). The breadth (Sonnet) model proposes MANY diverse
    # candidate ledgers per generation; MAP-Elites + the island database keep them diverse and the
    # deterministic fitness oracle SELECTS across generations. Tilt the database toward EXPLORATION so
    # the search actually fans out over the strategy grid rather than hill-climbing one elite: a higher
    # exploration ratio + a non-trivial migration cadence make breadth do the heavy lifting (GOAL 1/3).
    config.database.exploration_ratio = 0.6   # favor sampling diverse cells over re-exploiting one elite
    config.database.exploitation_ratio = 0.3
    config.database.migration_interval = max(1, iterations // 4)  # cross-pollinate islands within a run
    config.evaluator.cascade_evaluation = False  # no evaluate_stage1/2/3 defined
    config.evaluator.parallel_evaluations = 1    # in-process evaluator, but never executes the ledger
    return config


def _run_openevolve(*, initial_program, evaluator, config, iterations, output_dir, goal=None):
    """Shared OpenEvolve driver: optionally bake ``goal`` into the env so the evaluator sees it.

    The goal is exported to ``MATHAGENT_EVOLVE_GOAL`` for the duration of the run (the serialised
    evaluator reads it as a fallback to the baked-in constant). We restore the prior env afterwards so
    repeated calls with different goals never leak across runs.
    """
    import os

    from openevolve.api import run_evolution

    prev = os.environ.get("MATHAGENT_EVOLVE_GOAL")
    if goal is not None:
        os.environ["MATHAGENT_EVOLVE_GOAL"] = goal
    try:
        return run_evolution(
            initial_program=initial_program,
            evaluator=evaluator,
            config=config,
            iterations=iterations,
            output_dir=output_dir,
            cleanup=output_dir is None,
        )
    finally:
        if goal is not None:
            if prev is None:
                os.environ.pop("MATHAGENT_EVOLVE_GOAL", None)
            else:
                os.environ["MATHAGENT_EVOLVE_GOAL"] = prev


def evolve_sketches(
    goal: str,
    toolkit,
    *,
    llm=None,
    claude_cfg: Optional[ClaudeConfig] = None,
    breadth_model: str = "sonnet",
    depth_model: str = "opus",
    breadth_weight: float = _DEFAULT_BREADTH_WEIGHT,
    depth_weight: float = _DEFAULT_DEPTH_WEIGHT,
    rank_signal: Optional[RankSignal] = None,
    iterations: int = 20,
    seed_sketches: Optional[list[str]] = None,
    retriever=None,
    output_dir: Optional[str] = None,
    num_islands: int = 2,
    feature_bins: int = 5,
) -> tuple[str, float, dict]:
    """Evolve a proof-sketch ledger population for ``goal`` and return the best candidate.

    Runs OpenEvolve's real ``run_evolution`` controller with the AlphaEvolve-style **Sonnet (breadth)
    + Opus (depth)** Claude ensemble (or the supplied ``llm`` for offline tests) and the HARD-gated,
    goal-bound, obligation-debt-graded evaluator. The GOAL is threaded into the (serialised) evaluator
    so a candidate that does not bind to it — by deterministic ``goal_hash`` equality — scores a HARD
    zero at archive insertion (it never enters the elite archive as a "win"). NEEDS_REVIEW (0.5) is not
    a win: it scores strictly below any goal-bound PASSED. The MAP-Elites feature dimensions are the
    STRATEGY descriptors (dominant-justification-class / modulus-band / subgoal-depth).

    ``breadth_weight``/``depth_weight`` set the ensemble sampling mix (breadth >> depth per AlphaEvolve).
    Returns ``(best_ledger_text, best_fitness, metrics)``. ``best_fitness`` is in [0.0, 1.0].

    Raises ``RuntimeError("openevolve not installed; ...")`` if the optional package is missing.
    """
    _require_openevolve()

    config = build_evolve_config(
        llm=llm, claude_cfg=claude_cfg,
        breadth_model=breadth_model, depth_model=depth_model,
        breadth_weight=breadth_weight, depth_weight=depth_weight, rank_signal=rank_signal,
        iterations=iterations, num_islands=num_islands, feature_bins=feature_bins,
    )

    # Retrieval-seeded islands (OpenEvolve P2): seed from a RETRIEVED elementary exemplar ledger
    # matching the goal (injects elementary priors), with graceful fallback to the structural default.
    seeds = seed_sketches or retrieval_seeds(goal, retriever) or [_default_seed(goal)]
    # EXPLORE/EXPLOIT barrier: an AutoReason-refined (exploit) artifact must NEVER seed the explore
    # archive. Fail loud if a caller tries (keeps the two stages strictly one-directional).
    for s in seeds:
        _BARRIER.assert_not_refined(s)
    initial_program = seeds[0]  # OpenEvolve seeds the population from a single initial program

    result = _run_openevolve(
        initial_program=initial_program,
        evaluator=gate_ledger_evaluator,  # top-level, self-contained -> serialised via getsource
        config=config,
        iterations=iterations,
        output_dir=output_dir,
        goal=goal,
    )

    best_text = result.best_code or initial_program
    # Strip EVOLVE-BLOCK comment scaffolding OpenEvolve adds around the seed.
    lines = [ln for ln in best_text.splitlines() if not ln.lstrip().startswith("#")]
    best_text = ("\n".join(lines).strip()) or best_text
    return best_text, float(result.best_score), dict(result.metrics or {})


@dataclass
class EvolveChampion:
    """The outcome of a FIRST-CLASS evolutionary proving run (:func:`evolve_prove`).

    ``ledger`` is the best evolved proof-sketch ledger TEXT; ``fitness`` its HARD-gated, goal-bound,
    obligation-debt-graded score in [0, 1]; ``metrics`` the full per-candidate metrics dict.
    ``goal_bound`` is True iff BOTH the top-level claim AND terminal conclusion bind to the goal by
    deterministic ``goal_hash`` equality, and ``passed`` iff the champion cleared the deterministic gate
    into the PASSED band (``fitness >= PASSED_FLOOR``). ``accepted`` is the single first-class decision:
    a champion is accepted (ready to hand to the DAG + Lean) iff it is a goal-bound PASSED ledger — NOT
    the unreachable ``fitness == 1.0`` (the HARD-gated band caps a genuine PASSED ledger well below 1.0,
    so a == 1.0 gate would discard every real champion and degrade evolve to a no-op)."""
    ledger: str
    fitness: float
    metrics: dict
    goal_bound: bool
    passed: bool

    @property
    def accepted(self) -> bool:
        """True iff this is a goal-bound champion that cleared the PASSED band (hand it to DAG + Lean)."""
        return self.goal_bound and self.passed


def evolve_prove(
    goal: str,
    toolkit,
    *,
    iterations: int = 20,
    llm=None,
    claude_cfg: Optional[ClaudeConfig] = None,
    breadth_weight: float = _DEFAULT_BREADTH_WEIGHT,
    depth_weight: float = _DEFAULT_DEPTH_WEIGHT,
    rank_signal: Optional[RankSignal] = None,
    seed_sketches: Optional[list[str]] = None,
    retriever=None,
    num_islands: int = 2,
) -> EvolveChampion:
    """FIRST-CLASS evolutionary proving: explore for a goal-bound, obligation-discharged champion ledger.

    This is the clear entrypoint a user invokes (``scripts/prove.py --evolve K`` wires straight to it):
    it runs the breadth-led OpenEvolve exploration loop (Sonnet samples MANY diverse candidate ledgers
    per generation; MAP-Elites + the island database evolve them; the HARD-gated, goal-bound,
    obligation-debt-graded fitness SELECTS across generations) and returns the best champion together
    with the SINGLE acceptance decision.

    The acceptance bar is a goal-bound PASSED ledger (``fitness >= PASSED_FLOOR`` AND it binds to the
    goal), NOT the unreachable ``fitness == 1.0``: the HARD-gated band caps a genuine PASSED ledger at
    ~0.72-0.99, so an ``== 1.0`` gate (the old ``--evolve`` behaviour) discards EVERY real champion and
    silently degrades evolve to a fallback-only no-op. An accepted champion is goal-bound and PASSED, so
    it is ready to hand to the DAG + Lean for authoritative verification.

    Returns an :class:`EvolveChampion`. Raises ``RuntimeError`` (the install hint) iff openevolve is
    missing — callers should gate on :func:`available` first.
    """
    best_text, fitness, metrics = evolve_sketches(
        goal, toolkit, llm=llm, claude_cfg=claude_cfg,
        breadth_weight=breadth_weight, depth_weight=depth_weight, rank_signal=rank_signal,
        iterations=iterations, seed_sketches=seed_sketches, retriever=retriever,
        num_islands=num_islands,
    )
    # Re-derive goal-binding DETERMINISTICALLY from the champion text (do NOT trust the float alone):
    # the HARD fitness already zeroes an off-goal candidate, but we want an explicit, audit-friendly
    # binding bit independent of the band arithmetic.
    goal_bound = False
    try:
        led = parse_ledger(best_text)
        goal_bound = binds_to_goal(led, goal) and not is_vacuous(led)
    except (LedgerError, Exception):
        goal_bound = False
    passed = fitness >= PASSED_FLOOR
    return EvolveChampion(ledger=best_text, fitness=float(fitness), metrics=dict(metrics),
                          goal_bound=bool(goal_bound), passed=bool(passed))


def evolve_witnesses(
    goal: str,
    *,
    llm=None,
    claude_cfg: Optional[ClaudeConfig] = None,
    breadth_weight: float = _DEFAULT_BREADTH_WEIGHT,
    depth_weight: float = _DEFAULT_DEPTH_WEIGHT,
    iterations: int = 20,
    seed_specs: Optional[list[str]] = None,
    output_dir: Optional[str] = None,
    num_islands: int = 2,
    feature_bins: int = 5,
) -> tuple[str, float, dict]:
    """NUMERIC-GROUNDING evolution mode: evolve a witness/construction SPEC, scored ONLY by numeric.py.

    The evolved artifact is a JSON witness spec (a residue cover, a descent measure/next pair, or a
    claimed solution set — see :func:`agent.tools.numeric.check_witness_spec`). Its fitness is the
    exact-integer checker's verdict (1.0 confirmed / 0.0 not) — a NON-GAMEABLE, contentful signal:
    non-elementary objects are literally unrepresentable in the integer-only AST. The spec is parsed by
    json.loads (DATA only) and its expressions by the restricted no-eval AST; it is NEVER eval/exec'd.

    MAP-Elites feature axes are ``modulus_band`` and ``box_coverage``. Returns
    ``(best_spec_text, best_fitness, metrics)``.

    Raises ``RuntimeError("openevolve not installed; ...")`` if the optional package is missing.
    """
    _require_openevolve()

    config = build_evolve_config(
        llm=llm, claude_cfg=claude_cfg,
        breadth_weight=breadth_weight, depth_weight=depth_weight,
        iterations=iterations, num_islands=num_islands, feature_bins=feature_bins,
        feature_dimensions=["modulus_band", "box_coverage"],
    )

    seeds = seed_specs or [_default_witness_seed(goal)]
    initial_program = seeds[0]

    result = _run_openevolve(
        initial_program=initial_program,
        evaluator=witness_spec_evaluator,
        config=config,
        iterations=iterations,
        output_dir=output_dir,
        goal=None,  # witness scoring is goal-agnostic (it grounds the construction, not the claim)
    )

    best_text = result.best_code or initial_program
    lines = [ln for ln in best_text.splitlines() if not ln.lstrip().startswith("#")]
    best_text = ("\n".join(lines).strip()) or best_text
    return best_text, float(result.best_score), dict(result.metrics or {})


# --------------------------------------------------------------------------------------------------
# Decomposer adapter: lets the backend slot into DagDriver(decomposer=...) (the Decomposer Protocol
# in agent/orchestrator/dag_driver.py).
# --------------------------------------------------------------------------------------------------
class OpenEvolveBackend:
    """Decomposer that generates a child blueprint by EVOLVING the proof-sketch ledger population.

    Implements the ``Decomposer`` Protocol (``decompose(goal, feedback) -> (sketch, children)``) so it
    can be passed directly as ``decomposer=`` to ``DagDriver``. ``decompose`` runs
    :func:`evolve_sketches` (which threads the GOAL into the HARD-gated fitness so only goal-bound,
    obligation-discharging blueprints can win) and extracts child goals from the best ledger's
    ``lemma`` steps via the existing :func:`children_from_sketch` helper.
    """

    def __init__(self, toolkit, claude_cfg: Optional[ClaudeConfig] = None, *,
                 generations: int = 20, llm=None,
                 breadth_model: str = "sonnet",
                 depth_model: str = "opus",
                 breadth_weight: float = _DEFAULT_BREADTH_WEIGHT,
                 depth_weight: float = _DEFAULT_DEPTH_WEIGHT,
                 rank_signal: Optional[RankSignal] = None,
                 retriever=None):
        self.toolkit = toolkit
        self.claude_cfg = claude_cfg or ClaudeConfig()
        self.generations = generations
        self.llm = llm
        # Profile-addressable ensemble: which models search (names) and their sampling mix (weights).
        self.breadth_model = breadth_model
        self.depth_model = depth_model
        self.breadth_weight = breadth_weight
        self.depth_weight = depth_weight
        # P3 rank-aware routing + P2 retrieval-seeded islands (both optional / back-compat None).
        self.rank_signal = rank_signal
        self.retriever = retriever

    @staticmethod
    def available() -> bool:
        return available()

    def decompose(self, goal: str, feedback: Optional[list[str]] = None) -> tuple[str, list[str]]:
        sketch, _fitness, _metrics = evolve_sketches(
            goal, self.toolkit, llm=self.llm, claude_cfg=self.claude_cfg,
            breadth_model=self.breadth_model, depth_model=self.depth_model,
            breadth_weight=self.breadth_weight, depth_weight=self.depth_weight,
            rank_signal=self.rank_signal, retriever=self.retriever,
            iterations=self.generations,
        )
        return sketch, children_from_sketch(sketch)


__all__ = [
    "available",
    "evolve_sketches",
    "evolve_prove",
    "EvolveChampion",
    "evolve_witnesses",
    "build_evolve_config",
    "make_gate_evaluator",
    "score_ledger",
    "score_witness_spec",
    "binds_to_goal",
    "is_vacuous",
    "gate_ledger_evaluator",
    "witness_spec_evaluator",
    "StubEvolveLLM",
    "OpenEvolveBackend",
    "RankSignal",
    "rank_aware_weights",
    "call_telemetry",
    "CallTelemetry",
    "retrieval_seeds",
    "ExploreExploitBarrier",
    "explore_exploit_barrier",
    "_ClaudeLLMFactory",
    "_FixedLLMFactory",
]
