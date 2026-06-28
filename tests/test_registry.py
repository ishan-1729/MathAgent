"""Offline tests for agent/orchestrator/registry.py (W3).

No CLI is ever invoked: factories only construct objects. We assert (a) every role resolves under the
scripted provider to a stub conforming to that role's EXISTING Protocol, (b) the claude provider yields
the claude_roles classes (the DEFAULT backend) honoring spec.model, (c) the codex provider yields the
codex_prover/formalizer classes, and (d) an unregistered pair raises RegistryError.
"""
import pytest

from agent.orchestrator.driver import Prover
from agent.orchestrator.dag_driver import Decomposer, Reviewer
from agent.orchestrator.population import Comparator
from agent.orchestrator.tournament import (
    RevisionController, RevisionCritic, RevisionAuthor, Synthesizer,
)
from agent.orchestrator.run_profile import ProviderKey, Role, RoleSpec
from agent.orchestrator.registry import (
    Deps, PROVIDERS, RegistryError, register, resolve,
)


# A toolkit-shaped stub so scripted/live factories that call allowed_keys never touch disk in tests
# that don't need the real toolkit. (Scripted factories ignore deps entirely; claude/codex provers
# only read .allowed_keys() lazily at call time, never at construction.)
class _FakeToolkit:
    def allowed_keys(self):
        return {"algebra", "congruence"}


def _deps():
    return Deps(toolkit=_FakeToolkit())


# --- scripted: every role resolves to a Protocol-conforming stub --------------------------------

def test_scripted_prover_conforms():
    c = resolve(Role.prover, RoleSpec(provider=ProviderKey.scripted), _deps())
    assert isinstance(c, Prover)
    assert c.prove("goal") == c.prove("other")  # deterministic


def test_scripted_decomposer_conforms():
    c = resolve(Role.decomposer, RoleSpec(provider=ProviderKey.scripted), _deps())
    assert isinstance(c, Decomposer)
    sketch, kids = c.decompose("goal")
    assert isinstance(sketch, str) and isinstance(kids, list)


def test_scripted_reviewer_conforms_and_accepts():
    c = resolve(Role.reviewer, RoleSpec(provider=ProviderKey.scripted), _deps())
    assert isinstance(c, Reviewer)
    v = c.review("g", "sketch", [])
    assert v.ok is True


def test_scripted_comparator_and_judge_conform():
    comp = resolve(Role.comparator, RoleSpec(provider=ProviderKey.scripted), _deps())
    judge = resolve(Role.judge, RoleSpec(provider=ProviderKey.scripted), _deps())
    assert isinstance(comp, Comparator) and isinstance(judge, Comparator)


def test_scripted_formalizer_conforms():
    c = resolve(Role.formalizer, RoleSpec(provider=ProviderKey.scripted), _deps())
    res = c.formalize("ledger")
    assert res.ok is True and "theorem" in res.lean_source


def test_scripted_faithfulness_conforms():
    c = resolve(Role.faithfulness, RoleSpec(provider=ProviderKey.scripted), _deps())
    v = c.check("claim", "theorem t : True := trivial", "t")
    assert v.faithful is True


def test_scripted_refiner_is_a_real_controller():
    c = resolve(Role.refiner, RoleSpec(provider=ProviderKey.scripted),
                Deps(toolkit=_FakeToolkit(), n_judges=3))
    assert isinstance(c, RevisionController)
    assert len(c.judges) == 3
    assert all(isinstance(j, Comparator) for j in c.judges)
    assert isinstance(c.critic, RevisionCritic)
    assert isinstance(c.author, RevisionAuthor)
    assert isinstance(c.synthesizer, Synthesizer)


def test_scripted_refiner_converges_without_displacement():
    c = resolve(Role.refiner, RoleSpec(provider=ProviderKey.scripted), _deps())
    res = c.refine("goal", "incumbent")
    assert res.content == "incumbent"
    assert res.changed is False and res.displacements == 0


def test_scripted_is_the_default_for_an_explicit_test_profile():
    # scripted must be a registered provider for EVERY role (so an offline profile is fully resolvable).
    for role in Role:
        assert (role, ProviderKey.scripted) in PROVIDERS


# --- claude (DEFAULT backend): correct classes + spec.model honored -----------------------------

def test_claude_provider_returns_claude_classes():
    from agent.tools.claude_roles import (
        ClaudeProver, ClaudeDecomposer, ClaudeReviewer, ClaudeComparator, ClaudeJudge,
        ClaudeFaithfulnessChecker,
    )
    from agent.tools.formalizer import ClaudeFormalizer

    d = _deps()
    assert isinstance(resolve(Role.prover, RoleSpec(provider=ProviderKey.claude), d), ClaudeProver)
    assert isinstance(resolve(Role.decomposer, RoleSpec(provider=ProviderKey.claude), d), ClaudeDecomposer)
    assert isinstance(resolve(Role.reviewer, RoleSpec(provider=ProviderKey.claude), d), ClaudeReviewer)
    assert isinstance(resolve(Role.comparator, RoleSpec(provider=ProviderKey.claude), d), ClaudeComparator)
    assert isinstance(resolve(Role.judge, RoleSpec(provider=ProviderKey.claude), d), ClaudeJudge)
    assert isinstance(resolve(Role.formalizer, RoleSpec(provider=ProviderKey.claude), d), ClaudeFormalizer)
    assert isinstance(resolve(Role.faithfulness, RoleSpec(provider=ProviderKey.claude), d),
                      ClaudeFaithfulnessChecker)


def test_claude_default_is_used_when_provider_unset():
    # RoleSpec() defaults to provider=claude.
    from agent.tools.claude_roles import ClaudeProver

    assert isinstance(resolve(Role.prover, RoleSpec(), _deps()), ClaudeProver)


def test_claude_honors_spec_model_default_when_none():
    c = resolve(Role.prover, RoleSpec(provider=ProviderKey.claude), _deps())
    assert c.cfg.model == "opus"  # per-role default
    c2 = resolve(Role.comparator, RoleSpec(provider=ProviderKey.claude), _deps())
    assert c2.cfg.model == "haiku"


def test_claude_honors_explicit_spec_model():
    c = resolve(Role.prover, RoleSpec(provider=ProviderKey.claude, model="sonnet"), _deps())
    assert c.cfg.model == "sonnet"


def test_claude_refiner_is_a_real_controller():
    c = resolve(Role.refiner, RoleSpec(provider=ProviderKey.claude),
                Deps(toolkit=_FakeToolkit(), n_judges=2))
    from agent.tools.claude_roles import ClaudeCritic
    assert isinstance(c, RevisionController)
    assert len(c.judges) == 2
    assert isinstance(c.critic, ClaudeCritic)


# --- codex (OPTIONAL provider): correct classes + spec passthrough ------------------------------

def test_codex_provider_returns_codex_classes():
    from agent.tools.codex_prover import (
        CodexProver, CodexDecomposer, CodexReviewer, CodexComparator, CodexSolutionComparator,
        CodexFaithfulnessChecker,
    )
    from agent.tools.formalizer import CodexFormalizer

    d = _deps()
    assert isinstance(resolve(Role.prover, RoleSpec(provider=ProviderKey.codex), d), CodexProver)
    assert isinstance(resolve(Role.decomposer, RoleSpec(provider=ProviderKey.codex), d), CodexDecomposer)
    assert isinstance(resolve(Role.reviewer, RoleSpec(provider=ProviderKey.codex), d), CodexReviewer)
    assert isinstance(resolve(Role.comparator, RoleSpec(provider=ProviderKey.codex), d), CodexComparator)
    assert isinstance(resolve(Role.judge, RoleSpec(provider=ProviderKey.codex), d),
                      CodexSolutionComparator)
    assert isinstance(resolve(Role.formalizer, RoleSpec(provider=ProviderKey.codex), d), CodexFormalizer)
    assert isinstance(resolve(Role.faithfulness, RoleSpec(provider=ProviderKey.codex), d),
                      CodexFaithfulnessChecker)


def test_codex_passes_model_and_effort_through():
    c = resolve(Role.prover, RoleSpec(provider=ProviderKey.codex, model="gpt-5.5", effort="high"), _deps())
    assert c.cfg.model == "gpt-5.5" and c.cfg.reasoning_effort == "high"


def test_codex_refiner_is_a_real_controller():
    c = resolve(Role.refiner, RoleSpec(provider=ProviderKey.codex),
                Deps(toolkit=_FakeToolkit(), n_judges=2))
    from agent.tools.codex_prover import CodexCritic
    assert isinstance(c, RevisionController)
    assert isinstance(c.critic, CodexCritic)


# --- registry mechanics --------------------------------------------------------------------------

def test_resolve_defaults_deps_when_none():
    # Scripted ignores deps, so resolve(..., None) must still work.
    c = resolve(Role.prover, RoleSpec(provider=ProviderKey.scripted))
    assert isinstance(c, Prover)


def test_unregistered_pair_raises():
    # Remove a pair, confirm RegistryError, then restore it (no cross-test leakage).
    key = (Role.prover, ProviderKey.scripted)
    saved = PROVIDERS.pop(key)
    try:
        with pytest.raises(RegistryError):
            resolve(Role.prover, RoleSpec(provider=ProviderKey.scripted))
    finally:
        PROVIDERS[key] = saved


def test_duplicate_registration_raises():
    with pytest.raises(RegistryError):
        @register(Role.prover, ProviderKey.scripted)
        def _dup(spec, deps):
            return None
