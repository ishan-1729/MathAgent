"""Fail-CLOSED supervisor: the safety controller that vets a :class:`RunProfile`.

This is the one gate between a parsed profile and the builder/driver. Following the
Ramadge-Wonham discipline it is **minimally restrictive**: it forbids exactly the
profiles whose required capabilities are absent or whose fields contradict, and it
admits every safe configuration untouched.

Two hard properties:

* **Side-effect free until called, and cheap when called.** Importing this module
  performs NO availability probes and imports NO heavy tool modules; all probes are
  done lazily inside :func:`validate_profile` and are designed to finish in well
  under a second so the supervisor can fail *before* any model/Lean call.
* **Probes never raise.** Every capability probe (claude/codex CLI on PATH, Lean
  REPL reachable, ``openevolve`` importable) is wrapped so that *any* exception is
  treated as "capability unavailable" -> a precise :class:`SupervisorError`, never a
  surprise traceback. A broken environment fails closed with an actionable message.

Guards implemented (each maps to a contract item):

* **S1** ``elementarity=authoritative`` => formalizer role resolvable AND the exact requested Lean
  transport reachable (one-shot compiler or persistent REPL).
* **S2** ``lean.per_node`` => formalizer role resolvable.
* **S3** any enabled evolutionary stage => ``openevolve`` installed.
* **S4** ``elementarity=none`` => ``lean.per_node``/``terminal``/``strict`` all False.
* **S5** ``mode=direct`` => DAG-only search stages are disabled; direct proofs may still use the
  terminal authoritative gate.
* **S6** every active role resolves through its primary or declared fallback provider.
* **S7** inactive roles are not capability-probed and cannot reject an otherwise runnable profile.
* **S8** any role ``provider=scripted`` only for an explicitly test/offline profile.
* **S9** ``lean.terminal`` must equal ``elementarity == authoritative`` (terminal is DERIVED from
  elementarity by the builder's policy; a contradicting ``lean.terminal`` is rejected, not silently
  ignored).
* **S10** ``lean.per_node`` => ``lean.server`` (a per-node warm REPL is required; per-node without a
  warm server is impractical, mirroring prove.py's ``normalize_lean_flags`` implication).
* **S11** ``ensemble`` weights must be >= 0 with a positive sum, and both model names non-empty (a
  degenerate all-zero / negative weighting or an empty model name is unusable).
* **S12** scripted formalizer/faithfulness roles can never back an authoritative certificate,
  including as a declared fallback that is not currently selected.
* **S13** OpenEvolve validates its explicit ensemble provider (currently Claude) rather than borrowing
  an undeclared backend from another role.
* **S14** DAG population/evolution stages require decomposition; inert stage declarations are rejected.
* **S15** ``lean.server`` cannot be enabled without a terminal or per-node gate that consumes it.
* **S16** a non-default refinement judge count requires the refinement stage.
"""
from __future__ import annotations

import math
import re

from agent.orchestrator.run_profile import (
    ElementarityLevel,
    Mode,
    ProviderKey,
    Role,
    RoleSpec,
    RunProfile,
)


class SupervisorError(RuntimeError):
    """Raised when a :class:`RunProfile` is rejected by :func:`validate_profile`.

    The message is always actionable: it names the offending field(s) and the fix.
    """


# --------------------------------------------------------------------------- #
# Lazy capability probes. Each NEVER raises: any failure => "unavailable".     #
# They import the heavy tool modules lazily so importing the supervisor stays  #
# free of side effects and heavy deps.                                         #
# --------------------------------------------------------------------------- #
def _claude_available() -> bool:
    """True iff the Claude CLI launcher is discoverable. Never raises."""
    try:
        from agent.tools.claude_cli import find_claude

        return find_claude() is not None
    except Exception:
        return False


def _codex_available() -> bool:
    """True iff the Codex CLI launcher is discoverable. Never raises."""
    try:
        from agent.tools.codex_prover import find_codex

        return find_codex() is not None
    except Exception:
        return False


def _lean_compiler_available() -> bool:
    """True iff the one-shot compiler path used by the terminal gate is reachable.

    ``formalize_and_audit`` prefers the bundled Mathlib Lake project when present, so that path needs
    ``lake``.  Only an installation without that project uses bare ``lean``.  A built persistent REPL
    is deliberately *not* required for ``lean.server=False``.
    """
    try:
        from agent.gates import lean_bridge

        if lean_bridge.find_mathlib_project() is not None:
            return lean_bridge.find_lake() is not None
        return bool(lean_bridge.available())
    except Exception:
        return False


def _lean_repl_available() -> bool:
    """True iff the persistent Mathlib REPL requested by ``lean.server`` is built and reachable."""
    try:
        from agent.gates.lean_server import LeanServer

        return bool(LeanServer.available())
    except Exception:
        return False


def _openevolve_available() -> bool:
    """True iff the optional ``openevolve`` package is importable. Never raises."""
    try:
        from agent.tools.openevolve_bridge import available as _oe_available

        return bool(_oe_available())
    except Exception:
        return False


# Probe table keyed by provider so the per-role guards (S6/S7) stay table-driven and
# every provider has exactly one source of truth for "is this backend installed?".
_PROVIDER_PROBE = {
    ProviderKey.claude: _claude_available,
    ProviderKey.codex: _codex_available,
}


# --------------------------------------------------------------------------- #
# Helpers over the profile's role table.                                       #
# --------------------------------------------------------------------------- #
def _roles(profile: RunProfile) -> list[tuple[Role, RoleSpec]]:
    """All declared ``(Role, RoleSpec)`` pairs, in declaration order."""
    rp = profile.roles
    return [(role, getattr(rp, role.value)) for role in Role]


def _active_roles(profile: RunProfile) -> list[tuple[Role, RoleSpec]]:
    """Only roles the effective wiring will actually construct or call.

    Keeping this list aligned with ``builder.build_driver`` prevents an unused provider from making a
    run fail before it starts. The direct-proof judge shares ``stages.review`` with the decomposition
    reviewer; in direct mode only the judge is active.
    """
    st = profile.stages
    active = {Role.prover}
    if profile.mode is Mode.dag and st.decompose:
        active.add(Role.decomposer)
        if st.review:
            active.add(Role.reviewer)
    if st.review:
        active.add(Role.judge)
    if profile.mode is Mode.dag and st.population > 0:
        active.add(Role.comparator)
    if profile.mode is Mode.dag and st.refine:
        active.add(Role.refiner)
    if profile.elementarity is ElementarityLevel.authoritative or profile.lean.per_node:
        active.add(Role.formalizer)
    if profile.elementarity is ElementarityLevel.authoritative:
        active.add(Role.faithfulness)
    return [(role, spec) for role, spec in _roles(profile) if role in active]


def _is_test_profile(profile: RunProfile) -> bool:
    """Heuristic for an *explicitly* test/offline profile (gates the scripted provider).

    We require an explicit opt-in signal rather than inferring it, so that a stray
    ``provider=scripted`` in a real profile is caught. The signal is a ``test`` /
    ``offline`` WHOLE-WORD token in the profile ``name`` or ``notes`` (case-insensitive) —
    matched as a token, not a substring, so ordinary words like ``fastest`` / ``latest`` /
    ``greatest`` / ``contest`` (or notes such as "latest tuning") do NOT accidentally qualify a
    live profile as a test double and admit scripted roles.
    """
    tokens = set(re.split(r"[^a-z0-9]+", f"{profile.name} {profile.notes}".lower()))
    return "test" in tokens or "offline" in tokens


def _provider_available(provider: ProviderKey) -> bool:
    """Availability of a provider's backend CLI (scripted has no external dep)."""
    if provider is ProviderKey.scripted:
        return True
    probe = _PROVIDER_PROBE.get(provider)
    if probe is None:
        return False
    try:
        return bool(probe())
    except Exception:
        return False


def _selected_provider(spec: RoleSpec, available=None) -> ProviderKey | None:
    """First reachable provider in the declared primary->fallback chain."""
    available = available or _provider_available
    if available(spec.provider):
        return spec.provider
    if spec.fallback is not None and available(spec.fallback):
        return spec.fallback
    return None


def _validate_declared_provider(profile: RunProfile, role: Role, provider: ProviderKey, *,
                                field: str) -> None:
    """Validate declaration-level trust independently of current provider reachability.

    Registry resolution probes again after supervision.  A primary backend can disappear between
    those checks, so an untrusted fallback must be rejected even when the primary probe currently
    succeeds.  This check intentionally performs no capability probe.
    """
    if provider is not ProviderKey.scripted:
        return
    if not _is_test_profile(profile):
        raise SupervisorError(
            f"role '{role.value}' declares {field}='scripted', which is only permitted in an "
            "explicitly test/offline profile. Mark the profile as test/offline (put 'test' or "
            "'offline' in its name/notes), or choose a live provider."
        )
    if (profile.elementarity is ElementarityLevel.authoritative
            and role in {Role.formalizer, Role.faithfulness}):
        raise SupervisorError(
            f"authoritative certification cannot declare scripted {field} for role "
            f"'{role.value}': certificate components and every fallback they may select must be "
            "production-trusted. Use a live provider, or lower elementarity for the offline wiring "
            "test."
        )


def _revalidated_profile(profile: RunProfile) -> RunProfile:
    """Round-trip a possibly ``model_copy``-mutated profile through Pydantic.

    Pydantic v2 deliberately does not validate ``model_copy(update=...)`` values.  The supervisor is
    the runtime trust boundary, so it must reject an object that bypassed singleton H0, budget bounds,
    enum, or nested-model validation before reading any of those fields.  A coercible but structurally
    non-canonical copy is also rejected rather than returning a repaired profile that the builder would
    then ignore.
    """
    try:
        dumped = profile.model_dump(mode="python", warnings=False)
        validated = RunProfile.model_validate(dumped, strict=True)
    except Exception as exc:
        raise SupervisorError(
            "RunProfile failed schema revalidation. Do not inject unvalidated values with "
            "model_copy(update=...); rebuild the profile through RunProfile.model_validate instead."
        ) from exc
    if validated != profile:
        raise SupervisorError(
            "RunProfile is not a canonical validated model (likely an unchecked model_copy update). "
            "Rebuild it through RunProfile.model_validate before execution."
        )
    return validated


# --------------------------------------------------------------------------- #
# The controller.                                                              #
# --------------------------------------------------------------------------- #
def validate_profile(profile: RunProfile) -> None:
    """Vet ``profile``; raise :class:`SupervisorError` on the first violated guard.

    Minimally restrictive (Ramadge-Wonham): rejects ONLY profiles that are either
    self-contradictory or require an absent capability; admits every safe config.
    Runs cheap lazy probes and must fail in well under a second, BEFORE any model or
    Lean call. Returns ``None`` for an admissible profile.
    """
    # Untrusted model copies are rejected before a single field is read or capability is probed.
    profile = _revalidated_profile(profile)

    # ----- cross-field contradictions first (no probes needed: cheapest) ----- #
    # S4: elementarity=none means NO Lean wiring; any Lean flag set is a contradiction.
    if profile.elementarity is ElementarityLevel.none:
        lean = profile.lean
        offenders = [
            n for n, v in (
                ("per_node", lean.per_node),
                ("terminal", lean.terminal),
                ("strict", lean.strict),
            ) if v
        ]
        if offenders:
            raise SupervisorError(
                "elementarity='none' disables all Lean verification, but "
                f"lean.{{{', '.join(offenders)}}} is set True. "
                "Set those Lean flags False, or raise elementarity to 'soft'/'authoritative'."
            )

    # S5: direct mode uses the DagDriver's direct-only execution path. Review is still meaningful there
    # (it wires the direct-proof judge). Decomposition/population/refinement and the stuck-node evolve
    # fallback are DAG-only; goal-dependent evolve/evolve_witness pre-search may seed either mode.
    if profile.mode is Mode.direct:
        st = profile.stages
        dag_only = {
            "decompose": st.decompose,
            "population": st.population > 0, "refine": st.refine,
            "evolve_fallback": st.evolve_fallback > 0,
        }
        on = [f"{k}={getattr(st, k)}" for k, active in dag_only.items() if active]
        if on:
            raise SupervisorError(
                "mode='direct' is a single-shot direct proof, but the DAG-only stage(s) "
                f"{{{', '.join(on)}}} are enabled. Turn those stages off for a direct profile, or use "
                "mode='dag'."
            )

    # S14: population ranks decomposition candidates and the evolve fallback supplies a replacement
    # decomposition.  With decomposition disabled both knobs are inert despite being reported as on.
    if profile.mode is Mode.dag and not profile.stages.decompose:
        inert = []
        if profile.stages.population > 0:
            inert.append(f"population={profile.stages.population}")
        if profile.stages.evolve_fallback > 0:
            inert.append(f"evolve_fallback={profile.stages.evolve_fallback}")
        if inert:
            raise SupervisorError(
                "mode='dag' with stages.decompose=False cannot enable decomposition-dependent "
                f"stage(s) {{{', '.join(inert)}}}. Enable decomposition, or set those stages to 0."
            )
    if not profile.stages.refine and profile.stages.judges != 1:
        raise SupervisorError(
            f"stages.judges={profile.stages.judges} is inert while stages.refine=False. Enable the "
            "refinement tournament, or leave judges at its neutral default of 1."
        )
    # S15: a preference tournament cannot establish mathematical correctness.  Every challenger must
    # pass the independent Layer-2 proof judge's ``no_gaps`` decision before it can replace an already
    # reviewed incumbent, so refinement without review is an invalid (not merely weaker) profile.
    if profile.stages.refine and not profile.stages.review:
        raise SupervisorError(
            "stages.refine=True requires stages.review=True: the refinement comparator ranks "
            "candidates but cannot certify logical correctness. Enable review, or disable refinement."
        )

    # S9: lean.terminal is DERIVED from elementarity (the builder attaches the terminal Layer-4 gate
    # iff elementarity=authoritative, via elementarity_policy.policy_for). A lean.terminal that
    # CONTRADICTS that derivation is a decorative field silently disagreeing with real wiring, so we
    # reject it fail-closed: lean.terminal MUST equal (elementarity == authoritative).
    want_terminal = profile.elementarity is ElementarityLevel.authoritative
    if profile.lean.terminal != want_terminal:
        raise SupervisorError(
            f"lean.terminal={profile.lean.terminal} contradicts elementarity="
            f"'{profile.elementarity.value}': the terminal Layer-4 gate is DERIVED from elementarity "
            f"and attaches iff elementarity='authoritative', so lean.terminal must be {want_terminal}. "
            "Set lean.terminal accordingly, or change elementarity."
        )

    # S10: per-node Lean requires the persistent warm server (a per-node compile without a warm REPL
    # reloads Mathlib per node — impractical). Mirrors prove.py's normalize_lean_flags implication
    # (--lean-per-node implies --server) and its hard-error on a missing REPL under --lean-per-node.
    if profile.lean.per_node and not profile.lean.server:
        raise SupervisorError(
            "lean.per_node=True requires lean.server=True (a warm persistent Lean REPL): per-node "
            "verification compiles every leaf/AND-composition and reloading Mathlib per node is "
            "impractical. Set lean.server=True, or disable lean.per_node."
        )
    if profile.lean.strict and not profile.lean.per_node:
        raise SupervisorError(
            "lean.strict=True is meaningful only with lean.per_node=True. Enable per-node Lean "
            "verification (and lean.server), or disable strict mode."
        )
    if (profile.lean.server and not profile.lean.per_node
            and profile.elementarity is not ElementarityLevel.authoritative):
        raise SupervisorError(
            "lean.server=True is inert unless a terminal authoritative gate or per-node Lean gate "
            "uses it. Set elementarity='authoritative' with lean.terminal=True, enable lean.per_node, "
            "or disable lean.server."
        )

    # S11: the breadth/depth ensemble weighting must be usable — non-negative weights with a positive
    # sum (so the normalized sampling mix is well-defined) and non-empty model names.
    ens = profile.ensemble
    if not math.isfinite(ens.breadth_weight) or not math.isfinite(ens.depth_weight):
        raise SupervisorError(
            "ensemble weights must be finite real numbers (NaN and infinity are invalid controls)."
        )
    if ens.breadth_weight < 0 or ens.depth_weight < 0:
        raise SupervisorError(
            f"ensemble weights must be non-negative (got breadth_weight={ens.breadth_weight}, "
            f"depth_weight={ens.depth_weight}). Use non-negative weights."
        )
    weight_sum = ens.breadth_weight + ens.depth_weight
    if not math.isfinite(weight_sum) or weight_sum <= 0:
        raise SupervisorError(
            "ensemble weights must have a positive finite sum: at least one of breadth_weight/"
            "depth_weight must be positive and their total must not overflow."
        )
    if not ens.breadth_model.strip() or not ens.depth_model.strip():
        raise SupervisorError(
            "ensemble breadth_model and depth_model must be non-empty model names."
        )

    # Validate every declared primary/fallback before probing.  Legality and certificate trust do not
    # depend on which backend happens to be reachable during this particular preflight.
    for role, spec in _roles(profile):
        _validate_declared_provider(profile, role, spec.provider, field="provider")
        if spec.fallback is not None:
            _validate_declared_provider(profile, role, spec.fallback, field="fallback")
        if spec.model is not None and not spec.model.strip():
            raise SupervisorError(
                f"role '{role.value}' model must be a non-blank name when provided."
            )
        if (spec.timeout_s is not None
                and (spec.timeout_s <= 0 or spec.timeout_s > 86_400)):
            raise SupervisorError(
                f"role '{role.value}' timeout_s must be in [1, 86400] seconds when provided."
            )
        if spec.fallback is not None and spec.fallback is not spec.provider:
            provider_specific = []
            if spec.model is not None:
                provider_specific.append("model")
            if spec.effort is not None:
                provider_specific.append("effort")
            if provider_specific:
                raise SupervisorError(
                    f"role '{role.value}' declares cross-provider fallback "
                    f"{spec.provider.value}->{spec.fallback.value} while provider-specific field(s) "
                    f"{{{', '.join(provider_specific)}}} are set. A fallback may be selected after a "
                    "later availability probe, so omit those fields (use each provider's safe "
                    "defaults) or use a same-provider configuration."
                )
        selectable = {spec.provider}
        if spec.fallback is not None:
            selectable.add(spec.fallback)
        if spec.effort is not None and selectable != {ProviderKey.codex}:
            raise SupervisorError(
                f"role '{role.value}' sets effort='{spec.effort}', but effort is a Codex-only "
                "control and the declared provider chain includes "
                f"{', '.join(sorted(p.value for p in selectable))}. Remove effort or use only Codex."
            )

    # Snapshot each provider capability once.  Besides avoiding redundant launcher discovery, this
    # makes a single validation decision coherent when a flaky/alternating probe changes between calls.
    availability: dict[ProviderKey, bool] = {}

    def provider_available(provider: ProviderKey) -> bool:
        if provider not in availability:
            availability[provider] = _provider_available(provider)
        return availability[provider]

    # ----- active-role provider availability + explicit fallback selection ----- #
    selected: dict[Role, ProviderKey] = {}
    for role, spec in _active_roles(profile):
        provider = _selected_provider(spec, provider_available)
        if provider is None:
            chain = spec.provider.value + (f" -> {spec.fallback.value}" if spec.fallback else "")
            raise SupervisorError(
                f"active role '{role.value}' has no reachable provider in its declared chain "
                f"({chain}). Install one backend or change the role provider/fallback."
            )
        selected[role] = provider

    # ----- capability guards that depend on the elementarity/lean/stage knobs - #
    # The active-role pass above already established formalizer reachability for authoritative and
    # per-node profiles.  Probe the exact Lean transport the requested wiring will actually use.
    lean_requested = (
        profile.elementarity is ElementarityLevel.authoritative or profile.lean.per_node
    )
    if lean_requested and Role.formalizer not in selected:
        raise SupervisorError(
            "Lean verification requires a reachable formalizer provider. Install its backend, change "
            "the formalizer provider/fallback, or disable the requested Lean verification."
        )

    # Per-node Lean always requests the warm REPL (S10), as does an authoritative terminal gate whose
    # server transport is enabled.  Probe once even when both gates share the same server.
    needs_repl = profile.lean.per_node or (
        profile.elementarity is ElementarityLevel.authoritative and profile.lean.server
    )
    if needs_repl and not _lean_repl_available():
        request = "lean.per_node=True" if profile.lean.per_node else (
            "elementarity='authoritative' with lean.server=True"
        )
        remedy = ("disable lean.per_node" if profile.lean.per_node
                  else "set lean.server=False for one-shot compilation")
        raise SupervisorError(
            f"{request} requires the persistent Lean REPL, but it is unavailable (run "
            f"`lake build repl` in the Mathlib project). Build the REPL, or {remedy}."
        )
    if (profile.elementarity is ElementarityLevel.authoritative
            and not profile.lean.server and not _lean_compiler_available()):
        raise SupervisorError(
            "elementarity='authoritative' with lean.server=False requires the one-shot Lean "
            "compiler path (Lake for the bundled Mathlib project, otherwise bare Lean), but it "
            "is unavailable. Install/locate Lean and Lake, or lower elementarity."
        )

    # S3/S13: every evolutionary mode uses the same optional OpenEvolve package and explicitly
    # declared ensemble transport.  Validate all three modes, including the first-class CLI/profile
    # pre-searches, before any ordinary prover or evolutionary model call can start.
    evolution_enabled = any((
        profile.stages.evolve > 0,
        profile.stages.evolve_witness > 0,
        profile.stages.evolve_fallback > 0,
    ))
    if evolution_enabled and not _openevolve_available():
        raise SupervisorError(
            "an enabled evolutionary stage requires the optional 'openevolve' package, "
            "which is not installed (pip install mathagent[evolve]). Install it, or set "
            "stages.evolve/evolve_witness/evolve_fallback to 0."
        )

    if evolution_enabled:
        if profile.ensemble.provider is not ProviderKey.claude:
            raise SupervisorError(
                "OpenEvolve currently implements only ensemble.provider='claude'; choose Claude or "
                "disable every evolutionary stage."
            )
        if not provider_available(profile.ensemble.provider):
            raise SupervisorError(
                "the enabled evolutionary stage requires the declared Claude ensemble backend, but the "
                "Claude CLI is unavailable. Install/locate it or disable the evolutionary stage."
            )

    return None
