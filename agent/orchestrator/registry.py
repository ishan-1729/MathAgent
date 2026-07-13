"""Provider registry: resolve a (Role, RoleSpec) -> a component behind that role's existing Protocol.

This is the one place that knows HOW to construct each role's component from each provider. The builder
asks :func:`resolve` for one component per :class:`~agent.orchestrator.run_profile.Role`; the supervisor
has already validated availability, so resolution itself is a pure construction step (no model/Lean
calls — only object creation).

Design:

* ``PROVIDERS`` is a dict keyed ``(Role, ProviderKey) -> factory``. A ``@register(role, provider)``
  decorator populates it. The DEFAULT provider is :attr:`ProviderKey.claude`.
* ``resolve(role, spec, deps)`` selects the first reachable provider in the declared
  ``primary -> fallback`` chain, then calls its factory. ``deps`` is a small :class:`Deps` bundle
  (the shared toolkit + optional budget/trace) the live factories need; scripted factories ignore it.
* claude factories construct the classes in :mod:`agent.tools.claude_roles` (the DEFAULT backend).
* codex factories WRAP the existing :mod:`agent.tools.codex_prover` / :mod:`agent.tools.formalizer`
  classes verbatim (an OPTIONAL provider — the maintainer's default is Claude).
* scripted factories return deterministic stubs (this module, below) for offline tests.

Each resolved component conforms to the EXISTING Protocol the DAG/tournament expect for that role:
Prover (prover), Decomposer (decomposer), Reviewer (reviewer), Comparator (comparator), Judge (judge),
Formalizer (formalizer), a faithfulness checker (faithfulness), RevisionController (refiner).

SAFETY: nothing here ``exec``/``eval``/``import``s model output; factories only build objects. The
live components themselves only generate text (see their modules) and the deterministic gate stays
authoritative downstream.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from agent.gates.toolkit import Toolkit, load_toolkit
from agent.orchestrator.dag_driver import ReviewVerdict
from agent.orchestrator.driver import JudgeVerdict
from agent.orchestrator.faithfulness import (
    FaithfulnessVerdict,
    ScriptedFaithfulnessChecker,
    SingleVerdict,
    DEFAULT_LENSES,
)
from agent.orchestrator.population import Candidate
from agent.orchestrator.run_profile import ProviderKey, Role, RoleSpec
from agent.orchestrator.tournament import RevisionController
from agent.tools.formalizer import (
    DEFAULT_THEOREM_NAME,
    FormalizationResult,
    ScriptedFormalizer,
)


class RegistryError(RuntimeError):
    """No factory registered for a (Role, ProviderKey) pair (an unconfigured combination)."""


# --------------------------------------------------------------------------------------------------
# Dependency bundle handed to factories (so live components get the shared toolkit / budget / trace).
# --------------------------------------------------------------------------------------------------
@dataclass
class Deps:
    """Shared dependencies a factory may need. Scripted factories ignore all of these.

    ``toolkit`` is the elementary toolkit (provers/decomposers/reviewers/formalizers need it). It is
    lazily defaulted via :func:`load_toolkit` so a bare ``Deps()`` works in tests, but the builder
    should pass the SAME instance to every role for a run. ``budget``/``trace`` feed the refiner's
    tournament controller.
    """

    toolkit: Optional[Toolkit] = None
    budget: object = None
    trace: object = None
    # Refiner knobs (mirror make_*_refiner defaults); the builder may override per profile.
    n_judges: int = 1
    max_passes: int = 2
    k_stop: int = 2
    margin: int = 1
    seed: int = 0

    def require_toolkit(self) -> Toolkit:
        if self.toolkit is None:
            self.toolkit = load_toolkit()
        return self.toolkit


# A factory: (spec, deps) -> component conforming to the role's Protocol.
Factory = Callable[[RoleSpec, Deps], object]

PROVIDERS: dict[tuple[Role, ProviderKey], Factory] = {}
_RESOLUTION_ATTR = "_mathagent_resolution"


def resolution_of(component: object | None) -> Optional[dict[str, object]]:
    """Return a defensive copy of provider provenance attached by :func:`resolve`, if available."""
    if component is None:
        return None
    try:
        value = getattr(component, _RESOLUTION_ATTR, None)
    except Exception:
        return None
    return dict(value) if isinstance(value, dict) else None


def register(role: Role, provider: ProviderKey) -> Callable[[Factory], Factory]:
    """Decorator: register ``factory`` as the builder for ``(role, provider)``."""

    def deco(factory: Factory) -> Factory:
        key = (role, provider)
        if key in PROVIDERS:
            raise RegistryError(f"duplicate factory for {role.value}/{provider.value}")
        PROVIDERS[key] = factory
        return factory

    return deco


def resolve(role: Role, spec: RoleSpec, deps: Optional[Deps] = None) -> object:
    """Resolve ``role`` through its primary/fallback provider chain.

    Pure construction: builds and returns an object, performing no model/Lean call. Raises
    :class:`RegistryError` if no reachable/registered provider can supply the role. Availability is
    probed only when a fallback is declared; without one, historical construction semantics remain
    unchanged and the supervisor is responsible for the preflight capability check.
    """
    deps = deps or Deps()
    provider = spec.provider
    if spec.fallback is not None:
        # Local import avoids a module cycle and keeps import-time behavior side-effect free. These are
        # cheap launcher-discovery probes, never model calls.
        from agent.orchestrator.supervisor import _provider_available

        if not _provider_available(provider):
            if _provider_available(spec.fallback):
                provider = spec.fallback
            else:
                raise RegistryError(
                    f"no reachable provider for role={role.value}: "
                    f"{spec.provider.value} -> {spec.fallback.value}"
                )
    effective = (spec if provider is spec.provider else
                 spec.model_copy(update={"provider": provider, "fallback": None}))
    key = (role, provider)
    factory = PROVIDERS.get(key)
    if factory is None:
        raise RegistryError(
            f"no provider registered for role={role.value} provider={provider.value}"
        )
    component = factory(effective, deps)
    cfg = getattr(component, "cfg", None)
    if cfg is None:
        # Composite roles (notably RevisionController/refiner) retain their live provider config on
        # the critic/author rather than the controller itself. Record the config that will actually
        # make calls instead of emitting null model/effort fields for an active role.
        cfg = getattr(getattr(component, "critic", None), "cfg", None)
    model = getattr(cfg, "model", effective.model)
    effort = getattr(cfg, "reasoning_effort", effective.effort)
    timeout_s = getattr(cfg, "timeout_s", effective.timeout_s)
    provenance: dict[str, object] = {
        "role": role.value,
        "provider": provider.value,
        "model": model if isinstance(model, str) else None,
        "effort": effort if isinstance(effort, str) else None,
        "timeout_s": (timeout_s if isinstance(timeout_s, int)
                      and not isinstance(timeout_s, bool) else None),
        "fallback_selected": provider is not spec.provider,
    }
    try:
        setattr(component, _RESOLUTION_ATTR, provenance)
    except Exception as exc:
        raise RegistryError(
            f"resolved role={role.value} provider={provider.value} cannot retain provenance"
        ) from exc
    return component


# ==================================================================================================
# Claude factories (the DEFAULT backend — agent/tools/claude_roles.py + ClaudeFormalizer).
# Imports are LOCAL to each factory so importing this module never pulls the live CLI wrappers unless
# a Claude role is actually resolved (keeps the module import cheap + side-effect free).
# ==================================================================================================
def _claude_cfg(spec: RoleSpec, default_model: str):
    """A ClaudeConfig pinned to spec.model (registry honors the per-role model), with optional timeout."""
    from agent.tools.claude_cli import ClaudeConfig

    kwargs = {"model": spec.model or default_model}
    if spec.timeout_s is not None:
        kwargs["timeout_s"] = spec.timeout_s
    return ClaudeConfig(**kwargs)


@register(Role.prover, ProviderKey.claude)
def _claude_prover(spec: RoleSpec, deps: Deps) -> object:
    from agent.tools.claude_roles import ClaudeProver

    return ClaudeProver(deps.require_toolkit(), _claude_cfg(spec, "opus"))


@register(Role.decomposer, ProviderKey.claude)
def _claude_decomposer(spec: RoleSpec, deps: Deps) -> object:
    from agent.tools.claude_roles import ClaudeDecomposer

    return ClaudeDecomposer(deps.require_toolkit(), _claude_cfg(spec, "sonnet"))


@register(Role.reviewer, ProviderKey.claude)
def _claude_reviewer(spec: RoleSpec, deps: Deps) -> object:
    from agent.tools.claude_roles import ClaudeReviewer

    return ClaudeReviewer(deps.require_toolkit(), _claude_cfg(spec, "sonnet"))


@register(Role.comparator, ProviderKey.claude)
def _claude_comparator(spec: RoleSpec, deps: Deps) -> object:
    from agent.tools.claude_roles import ClaudeComparator

    return ClaudeComparator(_claude_cfg(spec, "sonnet"))


@register(Role.judge, ProviderKey.claude)
def _claude_judge(spec: RoleSpec, deps: Deps) -> object:
    from agent.tools.claude_roles import ClaudeLedgerJudge

    return ClaudeLedgerJudge(_claude_cfg(spec, "sonnet"))


@register(Role.formalizer, ProviderKey.claude)
def _claude_formalizer(spec: RoleSpec, deps: Deps) -> object:
    from agent.tools.formalizer import ClaudeFormalizer

    # default_model="sonnet" aligns with RolesProfile.formalizer (which always pins a model, so this
    # fallback is a belt-and-braces default the RoleSpec normally overrides — e.g. authoritative.yaml
    # pins opus). It was formerly "opus" (dead/unreachable and inconsistent with RolesProfile).
    return ClaudeFormalizer(deps.require_toolkit(), _claude_cfg(spec, "sonnet"))


@register(Role.faithfulness, ProviderKey.claude)
def _claude_faithfulness(spec: RoleSpec, deps: Deps) -> object:
    from agent.tools.claude_roles import ClaudeFaithfulnessChecker

    return ClaudeFaithfulnessChecker(_claude_cfg(spec, "sonnet"))


@register(Role.refiner, ProviderKey.claude)
def _claude_refiner(spec: RoleSpec, deps: Deps) -> object:
    from agent.tools.claude_roles import make_claude_refiner

    return make_claude_refiner(
        _claude_cfg(spec, "opus"),
        n_judges=deps.n_judges,
        budget=deps.budget,
        trace=deps.trace,
        max_passes=deps.max_passes,
        k_stop=deps.k_stop,
        margin=deps.margin,
        seed=deps.seed,
    )


# ==================================================================================================
# Codex factories (OPTIONAL provider — wrap agent/tools/codex_prover.py + CodexFormalizer verbatim).
# ==================================================================================================
def _codex_cfg(spec: RoleSpec):
    """A CodexConfig honoring spec.model / spec.effort / spec.timeout_s when given."""
    from agent.tools.codex_prover import CodexConfig

    kwargs: dict = {}
    if spec.model is not None:
        kwargs["model"] = spec.model
    if spec.effort is not None:
        kwargs["reasoning_effort"] = spec.effort
    if spec.timeout_s is not None:
        kwargs["timeout_s"] = spec.timeout_s
    return CodexConfig(**kwargs)


@register(Role.prover, ProviderKey.codex)
def _codex_prover(spec: RoleSpec, deps: Deps) -> object:
    from agent.tools.codex_prover import CodexProver

    return CodexProver(deps.require_toolkit(), _codex_cfg(spec))


@register(Role.decomposer, ProviderKey.codex)
def _codex_decomposer(spec: RoleSpec, deps: Deps) -> object:
    from agent.tools.codex_prover import CodexDecomposer

    return CodexDecomposer(deps.require_toolkit(), _codex_cfg(spec))


@register(Role.reviewer, ProviderKey.codex)
def _codex_reviewer(spec: RoleSpec, deps: Deps) -> object:
    from agent.tools.codex_prover import CodexReviewer

    return CodexReviewer(deps.require_toolkit(), _codex_cfg(spec))


@register(Role.comparator, ProviderKey.codex)
def _codex_comparator(spec: RoleSpec, deps: Deps) -> object:
    from agent.tools.codex_prover import CodexComparator

    return CodexComparator(_codex_cfg(spec))


@register(Role.judge, ProviderKey.codex)
def _codex_judge(spec: RoleSpec, deps: Deps) -> object:
    from agent.tools.codex_prover import CodexLedgerJudge

    return CodexLedgerJudge(_codex_cfg(spec))


@register(Role.formalizer, ProviderKey.codex)
def _codex_formalizer(spec: RoleSpec, deps: Deps) -> object:
    from agent.tools.formalizer import CodexFormalizer

    return CodexFormalizer(deps.require_toolkit(), _codex_cfg(spec))


@register(Role.faithfulness, ProviderKey.codex)
def _codex_faithfulness(spec: RoleSpec, deps: Deps) -> object:
    from agent.tools.codex_prover import CodexFaithfulnessChecker

    return CodexFaithfulnessChecker(_codex_cfg(spec))


@register(Role.refiner, ProviderKey.codex)
def _codex_refiner(spec: RoleSpec, deps: Deps) -> object:
    from agent.tools.codex_prover import make_codex_refiner

    return make_codex_refiner(
        _codex_cfg(spec),
        n_judges=deps.n_judges,
        budget=deps.budget,
        trace=deps.trace,
        max_passes=deps.max_passes,
        k_stop=deps.k_stop,
        margin=deps.margin,
        seed=deps.seed,
    )


# ==================================================================================================
# Scripted deterministic stubs (offline tests). Each conforms to the SAME Protocol as the live role,
# with fixed, side-effect-free behavior. No CLI is ever invoked.
# ==================================================================================================
class ScriptedProver:
    """Returns a fixed ledger text for any goal (the gate parses it downstream)."""

    def __init__(self, ledger: str = '{"claim": "", "steps": []}'):
        self.ledger = ledger

    def prove(self, problem: str, feedback: Optional[list[str]] = None) -> str:
        return self.ledger


class ScriptedDecomposer:
    """Returns a fixed sketch + child-goal list (default: an empty, no-child decomposition)."""

    def __init__(self, sketch: str = '{"claim": "", "steps": []}',
                 children: Optional[list[str]] = None):
        self.sketch = sketch
        self.children = list(children or [])

    def decompose(self, goal: str, feedback: Optional[list[str]] = None) -> tuple[str, list[str]]:
        return self.sketch, list(self.children)


class ScriptedReviewer:
    """Returns a fixed ReviewVerdict (default: accept — useful & elementary)."""

    def __init__(self, useful: bool = True, elementary: bool = True,
                 notes: Optional[list[str]] = None):
        self._verdict = ReviewVerdict(useful=useful, elementary=elementary, notes=list(notes or []))

    def review(self, goal: str, sketch: str, child_goals: list[str]) -> ReviewVerdict:
        return self._verdict


class ScriptedComparator:
    """Returns a fixed pairwise result for any pair (default: tie)."""

    def __init__(self, result: int = 0):
        self.result = result

    def compare(self, a: Candidate, b: Candidate) -> int:
        return self.result


class ScriptedLedgerJudge:
    """Deterministic proof-ledger judge for offline profiles (accepts by default)."""

    name = "scripted-ledger-judge"

    def __init__(self, *, elementary: bool = True, no_gaps: bool = True,
                 notes: Optional[list[str]] = None):
        self._verdict = JudgeVerdict(
            judge=self.name,
            elementary=elementary,
            no_gaps=no_gaps,
            notes=list(notes or []),
        )

    def review(self, ledger: object) -> JudgeVerdict:
        return self._verdict


class _ScriptedCritic:
    def critique(self, goal: str, incumbent: str) -> list[str]:
        return []


class _ScriptedAuthor:
    def revise(self, goal: str, incumbent: str, critique: list[str]) -> str:
        return incumbent


class _ScriptedSynthesizer:
    def synthesize(self, goal: str, a: str, b: str) -> str:
        return a


def _scripted_refiner(deps: Deps) -> RevisionController:
    """A real RevisionController wired from scripted parts — converges immediately (no displacement)."""
    judges = [ScriptedComparator(0) for _ in range(max(1, deps.n_judges))]
    return RevisionController(
        _ScriptedCritic(), _ScriptedAuthor(), _ScriptedSynthesizer(), judges,
        max_passes=deps.max_passes, k_stop=deps.k_stop, margin=deps.margin,
        budget=deps.budget, trace=deps.trace, seed=deps.seed,
    )


@register(Role.prover, ProviderKey.scripted)
def _scripted_prover(spec: RoleSpec, deps: Deps) -> object:
    return ScriptedProver()


@register(Role.decomposer, ProviderKey.scripted)
def _scripted_decomposer(spec: RoleSpec, deps: Deps) -> object:
    return ScriptedDecomposer()


@register(Role.reviewer, ProviderKey.scripted)
def _scripted_reviewer(spec: RoleSpec, deps: Deps) -> object:
    return ScriptedReviewer()


@register(Role.comparator, ProviderKey.scripted)
def _scripted_comparator(spec: RoleSpec, deps: Deps) -> object:
    return ScriptedComparator()


@register(Role.judge, ProviderKey.scripted)
def _scripted_judge(spec: RoleSpec, deps: Deps) -> object:
    return ScriptedLedgerJudge()


@register(Role.formalizer, ProviderKey.scripted)
def _scripted_formalizer(spec: RoleSpec, deps: Deps) -> object:
    # A trivially-true theorem keeps offline terminal-gate wiring exercisable without a live model.
    return ScriptedFormalizer(f"theorem {DEFAULT_THEOREM_NAME} : True := trivial",
                              theorem_name=DEFAULT_THEOREM_NAME)


@register(Role.faithfulness, ProviderKey.scripted)
def _scripted_faithfulness(spec: RoleSpec, deps: Deps) -> object:
    return ScriptedFaithfulnessChecker(faithful=True)


@register(Role.refiner, ProviderKey.scripted)
def _scripted_refiner_factory(spec: RoleSpec, deps: Deps) -> object:
    return _scripted_refiner(deps)


__all__ = [
    "RegistryError",
    "Deps",
    "Factory",
    "PROVIDERS",
    "register",
    "resolve",
    # scripted stubs (exported so tests / offline profiles can reference them directly)
    "ScriptedProver",
    "ScriptedDecomposer",
    "ScriptedReviewer",
    "ScriptedComparator",
    "ScriptedLedgerJudge",
]
