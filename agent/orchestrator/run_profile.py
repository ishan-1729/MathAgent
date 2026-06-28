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
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------------------------- #
# Enums (make the legal space explicit; anything else fails closed at parse).  #
# --------------------------------------------------------------------------- #
class ElementarityLevel(str, Enum):
    """How hard the elementarity dimension is enforced.

    none          -> elementarity is NOT enforced (no Lean gates wired)
    soft          -> elementarity enforced in-engine; no terminal Lean gate
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

    Defaults to the Claude provider; ``model``/``effort``/``timeout_s`` are optional
    overrides, and ``fallback`` names an alternate provider the registry may use.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: ProviderKey = ProviderKey.claude
    model: Optional[str] = None
    effort: Optional[str] = None
    timeout_s: Optional[int] = None
    fallback: Optional[ProviderKey] = None


def _claude(model: str) -> RoleSpec:
    """A Claude RoleSpec pinned to a per-role default model."""
    return RoleSpec(provider=ProviderKey.claude, model=model)


class RolesProfile(BaseModel):
    """One RoleSpec per role.

    Each role defaults to ``provider=claude`` with a sensible per-role model:
    prover/refiner = opus; decomposer/reviewer/formalizer/faithfulness = sonnet;
    comparator/judge = haiku.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    prover: RoleSpec = Field(default_factory=lambda: _claude("opus"))
    decomposer: RoleSpec = Field(default_factory=lambda: _claude("sonnet"))
    reviewer: RoleSpec = Field(default_factory=lambda: _claude("sonnet"))
    comparator: RoleSpec = Field(default_factory=lambda: _claude("haiku"))
    judge: RoleSpec = Field(default_factory=lambda: _claude("haiku"))
    formalizer: RoleSpec = Field(default_factory=lambda: _claude("sonnet"))
    faithfulness: RoleSpec = Field(default_factory=lambda: _claude("sonnet"))
    refiner: RoleSpec = Field(default_factory=lambda: _claude("opus"))


class StageProfile(BaseModel):
    """Which pipeline stages are active and their breadth knobs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decompose: bool = True
    review: bool = True
    population: int = 0
    evolve_fallback: int = 0
    refine: bool = False
    h0_consistency: bool = True


class LeanProfile(BaseModel):
    """Lean verification wiring."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    per_node: bool = False
    terminal: bool = False
    strict: bool = False
    server: bool = False


class BudgetProfile(BaseModel):
    """Hard caps on the run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_llm_calls: int = 60
    max_node_verify_calls: Optional[int] = None
    max_depth: int = 3
    max_decomp_attempts: int = 2
    episodes: int = 3


# --------------------------------------------------------------------------- #
# Top-level profile.                                                           #
# --------------------------------------------------------------------------- #
class RunProfile(BaseModel):
    """The single frozen control lever for a run.

    Construct directly, or load from YAML via :meth:`from_yaml`. The
    :attr:`profile_hash` is a stable content hash over the full (canonical) dump.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = "default"
    seed: int = 0
    notes: str = ""
    mode: Mode = Mode.dag
    elementarity: ElementarityLevel = ElementarityLevel.soft
    stages: StageProfile = Field(default_factory=StageProfile)
    roles: RolesProfile = Field(default_factory=RolesProfile)
    budgets: BudgetProfile = Field(default_factory=BudgetProfile)
    lean: LeanProfile = Field(default_factory=LeanProfile)

    # -- loading -------------------------------------------------------------- #
    @classmethod
    def from_yaml(cls, path: str | Path) -> "RunProfile":
        """Parse a YAML file into a validated RunProfile (no side effects)."""
        import yaml  # lazy: keep import-time cost off the module

        text = Path(path).read_text(encoding="utf-8")
        data = yaml.safe_load(text)
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
