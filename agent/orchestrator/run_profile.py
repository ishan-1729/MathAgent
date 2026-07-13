"""The single frozen control lever for a MathAgent run.

A ``RunProfile`` is a pure, side-effect-free Pydantic v2 model: loading or hashing
one performs NO model/Lean/tool calls and imports NO heavy tool modules. It is the
one object that flows::

    RunProfile -> supervisor.validate_profile -> builder.build_driver -> DagDriver

Illegal states are made unrepresentable via enums + ``extra='forbid'`` + ``frozen``;
the supervisor (a separate, lazy module) catches the cross-field/availability rest.

This module deliberately depends ONLY on the stdlib + pydantic + (lazily) PyYAML so
that a profile can be parsed and hashed cheaply, with no import-time side effects.
"""
from __future__ import annotations

import hashlib
import json
from enum import Enum
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


_MAX_PROFILE_BYTES = 1 * 1024 * 1024
_MAX_LLM_CALLS = 1_000
_MAX_NODE_VERIFY_CALLS = 1_000
_MAX_SEARCH_DEPTH = 64
_MAX_EPISODES = 64


# --------------------------------------------------------------------------- #
# Enums (make the legal space explicit; anything else fails closed at parse).  #
# --------------------------------------------------------------------------- #
class ElementarityLevel(str, Enum):
    """How hard the elementarity dimension is enforced.

    none          -> elementarity is NOT enforced (no Lean gates wired)
    soft          -> elementarity enforced in-engine; no terminal Lean gate (+ optional per-node)
    authoritative -> elementarity enforced + terminal Lean gate (+ optional per-node)
    """

    none = "none"
    soft = "soft"
    authoritative = "authoritative"


class ProviderKey(str, Enum):
    """Which backend provides a role's component."""

    claude = "claude"
    codex = "codex"
    scripted = "scripted"


class Role(str, Enum):
    """The pluggable roles the registry resolves provider -> component for."""

    prover = "prover"
    decomposer = "decomposer"
    reviewer = "reviewer"
    comparator = "comparator"
    judge = "judge"
    formalizer = "formalizer"
    faithfulness = "faithfulness"
    refiner = "refiner"


class Mode(str, Enum):
    """Top-level run mode."""

    dag = "dag"
    direct = "direct"


# --------------------------------------------------------------------------- #
# Leaf models.                                                                 #
# --------------------------------------------------------------------------- #
class RoleSpec(BaseModel):
    """How a single role is provided.

    Defaults to the Claude provider; ``model``/``timeout_s`` are optional overrides. ``effort`` is a
    Codex-only override enforced by the supervisor, and ``fallback`` names an alternate provider the
    registry may use.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: ProviderKey = ProviderKey.claude
    model: Optional[str] = None
    effort: Optional[Literal["low", "medium", "high", "xhigh"]] = None
    timeout_s: Optional[int] = None
    fallback: Optional[ProviderKey] = None


def _claude(model: str) -> RoleSpec:
    """A Claude RoleSpec pinned to a per-role default model."""
    return RoleSpec(provider=ProviderKey.claude, model=model)


class RolesProfile(BaseModel):
    """One RoleSpec per role.

    Each role defaults to ``provider=claude`` with a sensible per-role model:
    prover/refiner = opus; decomposer/reviewer/comparator/judge/formalizer/faithfulness = sonnet.
    (comparator/judge default to sonnet — not haiku — for roster compliance: the pairwise
    Elo/judge panels are soundness-adjacent enough to warrant sonnet over the smaller model.)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    prover: RoleSpec = Field(default_factory=lambda: _claude("opus"))
    decomposer: RoleSpec = Field(default_factory=lambda: _claude("sonnet"))
    reviewer: RoleSpec = Field(default_factory=lambda: _claude("sonnet"))
    comparator: RoleSpec = Field(default_factory=lambda: _claude("sonnet"))
    judge: RoleSpec = Field(default_factory=lambda: _claude("sonnet"))
    formalizer: RoleSpec = Field(default_factory=lambda: _claude("sonnet"))
    faithfulness: RoleSpec = Field(default_factory=lambda: _claude("sonnet"))
    refiner: RoleSpec = Field(default_factory=lambda: _claude("opus"))


class StageProfile(BaseModel):
    """Which pipeline stages are active and their breadth knobs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decompose: bool = True
    # Layer-2 review switch. In DAG mode this wires both the decomposition reviewer and the direct-proof
    # judge; in direct mode only the direct-proof judge is relevant.
    review: bool = True
    # Breadth fan-out knobs: 0 disables; a negative/absurd value is a misconfiguration, not a silent
    # off-switch, so bound them at parse (ge=0 rejects negatives; a generous le caps a runaway typo
    # well above every shipped profile's value).
    population: int = Field(default=0, ge=0, le=1000)
    # First-class evolutionary pre-search modes.  These live in the profile (rather than only in
    # argparse) so supervision can validate the optional package and ensemble provider before any
    # model call.  ``evolve`` seeds the ordinary prover pipeline; ``evolve_witness`` is an auxiliary
    # exact-integer construction search and never proves the theorem by itself.
    evolve: int = Field(default=0, ge=0, le=1000)
    evolve_witness: int = Field(default=0, ge=0, le=1000)
    evolve_fallback: int = Field(default=0, ge=0, le=1000)
    refine: bool = False
    # Number of independent judges in the optional refinement tournament.  The direct-proof ledger
    # judge remains a separate single role; this knob controls only the multi-judge refiner panel.
    judges: int = Field(default=1, ge=1, le=25)
    # H0 is a logical-soundness invariant, not an optimization/elementarity axis. It is intentionally
    # typed as the singleton True so a profile cannot manufacture a PROVEN composition while skipping
    # sibling-context consistency. Historical no-H0 experiments must use a non-proof analysis harness,
    # never the production RunProfile -> DagDriver path.
    h0_consistency: Literal[True] = True
    # MEMOIZATION toggle (ablation axis). True (the default) keeps the split-keyed goal cache: a proven
    # subgoal is reused across branches. False makes every node prove FRESH — the driver skips the goal
    # cache reads (no cross-branch short-circuit / cache_hit) so an identical repeated subgoal is
    # re-attempted. Correctness is unchanged (the split-keyed memo module itself is untouched); this only
    # lets an ablation MEASURE the memo's contribution.
    memo: bool = True


class EnsembleProfile(BaseModel):
    """The breadth/depth OpenEvolve ensemble knobs (which models search, and their sampling mix).

    The AlphaEvolve-style ensemble pairs a FAST breadth model (sampled often, high weight) with a
    STRONGER depth model (sampled rarely, low weight). These fields make that mix profile-addressable:
    the builder threads them into the OpenEvolve backend. Defaults match the historical hardcoded
    Sonnet-breadth / Opus-depth 0.8/0.2 split so existing callers are unchanged.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # OpenEvolve currently has one implemented live adapter: the Claude CLI ensemble. Making that
    # provider explicit lets the supervisor validate the real dependency instead of silently borrowing
    # Claude from an unrelated decomposer role.
    provider: ProviderKey = ProviderKey.claude
    breadth_model: str = Field(default="sonnet", max_length=128)
    depth_model: str = Field(default="opus", max_length=128)
    breadth_weight: float = 0.8
    depth_weight: float = 0.2
    timeout_s: int = Field(default=600, gt=0, le=86_400)


class LeanProfile(BaseModel):
    """Lean verification wiring."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    per_node: bool = False
    terminal: bool = False
    strict: bool = False
    server: bool = False


class BudgetProfile(BaseModel):
    """Hard caps on orchestrator search/review work.

    ``max_llm_calls`` covers calls explicitly scheduled by Ralph/DAG/tournament control. Lean
    formalization/faithfulness and OpenEvolve are separately bounded by ``repair_iters``/fixed lenses
    and the stage iteration caps, and are reported separately by their result objects.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Range-bounded at parse so a negative/zero cap fails CLOSED with an actionable pydantic error
    # instead of silently making the run FAIL as if the math failed (e.g. episodes=0 => every node
    # runs zero Ralph episodes and the goal terminally fails with no diagnostic).
    max_llm_calls: int = Field(default=60, ge=1, le=_MAX_LLM_CALLS)
    max_node_verify_calls: Optional[int] = Field(
        default=None, ge=0, le=_MAX_NODE_VERIFY_CALLS)
    max_depth: int = Field(default=3, ge=0, le=_MAX_SEARCH_DEPTH)
    max_decomp_attempts: int = Field(default=2, ge=0, le=_MAX_SEARCH_DEPTH)
    # Global decomposition re-plan cap.  This is distinct from attempts allowed at each DAG node;
    # historically the builder silently reused max_decomp_attempts and ignored --max-replan.
    max_replan_depth: int = Field(default=2, ge=0, le=_MAX_SEARCH_DEPTH)
    episodes: int = Field(default=3, ge=1, le=_MAX_EPISODES)


# --------------------------------------------------------------------------- #
# Top-level profile.                                                           #
# --------------------------------------------------------------------------- #
class RunProfile(BaseModel):
    """The single frozen control lever for a run.

    Construct directly, or load from YAML via :meth:`from_yaml`. The
    :attr:`profile_hash` is a stable content hash over the full (canonical) dump.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(default="default", min_length=1, max_length=128)
    seed: int = 0
    notes: str = Field(default="", max_length=4096)
    mode: Mode = Mode.dag
    elementarity: ElementarityLevel = ElementarityLevel.soft
    stages: StageProfile = Field(default_factory=StageProfile)
    roles: RolesProfile = Field(default_factory=RolesProfile)
    budgets: BudgetProfile = Field(default_factory=BudgetProfile)
    lean: LeanProfile = Field(default_factory=LeanProfile)
    ensemble: EnsembleProfile = Field(default_factory=EnsembleProfile)

    # -- loading -------------------------------------------------------------- #
    @classmethod
    def from_yaml(cls, path: str | Path) -> "RunProfile":
        """Parse a bounded, duplicate-key-free YAML file into a validated RunProfile."""
        import yaml  # lazy: keep import-time cost off the module

        profile_path = Path(path)
        with profile_path.open("rb") as fh:
            raw = fh.read(_MAX_PROFILE_BYTES + 1)
        if len(raw) > _MAX_PROFILE_BYTES:
            raise ValueError(f"RunProfile YAML exceeds {_MAX_PROFILE_BYTES} bytes")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("RunProfile YAML must be UTF-8") from exc

        class _UniqueKeyLoader(yaml.SafeLoader):
            pass

        def _construct_unique_mapping(loader, node, deep=False):
            mapping = {}
            for key_node, value_node in node.value:
                key = loader.construct_object(key_node, deep=deep)
                try:
                    duplicate = key in mapping
                except TypeError as exc:
                    raise ValueError("RunProfile YAML mapping keys must be scalar") from exc
                if duplicate:
                    raise ValueError(f"duplicate RunProfile YAML key: {key!r}")
                mapping[key] = loader.construct_object(value_node, deep=deep)
            return mapping

        _UniqueKeyLoader.add_constructor(
            yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
            _construct_unique_mapping,
        )
        data = yaml.load(text, Loader=_UniqueKeyLoader)
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ValueError(
                f"RunProfile YAML must be a mapping, got {type(data).__name__}"
            )
        return cls.model_validate(data)

    # -- identity ------------------------------------------------------------- #
    @property
    def profile_hash(self) -> str:
        """A stable content hash over the canonical model dump.

        Enum members serialize by ``value`` (mode='dag'), so the hash is stable
        across processes and changes iff a field value changes.
        """
        payload = self.model_dump(mode="json")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
