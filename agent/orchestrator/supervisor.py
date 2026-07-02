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

* **S1** ``elementarity=authoritative`` => formalizer role resolvable AND Lean reachable.
* **S2** ``lean.per_node`` => formalizer role resolvable.
* **S3** ``stages.evolve_fallback > 0`` => ``openevolve`` installed.
* **S4** ``elementarity=none`` => ``lean.per_node``/``terminal``/``strict`` all False.
* **S5** ``mode=direct`` => ``elementarity != authoritative``.
* **S6** any role ``provider=codex`` => the Codex CLI is present.
* **S7** any role ``provider=claude`` => the Claude CLI is present.
* **S8** any role ``provider=scripted`` only for an explicitly test/offline profile.
* **S9** ``lean.terminal`` must equal ``elementarity == authoritative`` (terminal is DERIVED from
  elementarity by the builder's policy; a contradicting ``lean.terminal`` is rejected, not silently
  ignored).
* **S10** ``lean.per_node`` => ``lean.server`` (a per-node warm REPL is required; per-node without a
  warm server is impractical, mirroring prove.py's ``normalize_lean_flags`` implication).
* **S11** ``ensemble`` weights must be >= 0 with a positive sum, and both model names non-empty (a
  degenerate all-zero / negative weighting or an empty model name is unusable).
"""
from __future__ import annotations

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


def _lean_available() -> bool:
    """True iff a built Lean REPL is reachable. Never raises (a slow/broken probe => False)."""
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
    """The (Role, RoleSpec) pairs of a profile, in declaration order."""
    rp = profile.roles
    return [(role, getattr(rp, role.value)) for role in Role]


def _is_test_profile(profile: RunProfile) -> bool:
    """Heuristic for an *explicitly* test/offline profile (gates the scripted provider).

    We require an explicit opt-in signal rather than inferring it, so that a stray
    ``provider=scripted`` in a real profile is caught. The signal is a ``test`` /
    ``offline`` token in the profile ``name`` or ``notes`` (case-insensitive).
    """
    haystack = f"{profile.name} {profile.notes}".lower()
    return "test" in haystack or "offline" in haystack


def _provider_available(provider: ProviderKey) -> bool:
    """Availability of a provider's backend CLI (scripted has no external dep)."""
    if provider is ProviderKey.scripted:
        return True
    probe = _PROVIDER_PROBE.get(provider)
    return probe() if probe is not None else False


def _formalizer_resolvable(profile: RunProfile) -> bool:
    """Whether the formalizer role's backend is present.

    The registry (a later wave) maps role+provider -> component; until it lands the
    faithful proxy for "resolvable" is "the formalizer provider's CLI is installed"
    (or scripted, which is always resolvable). This is the capability the formalizer
    needs to do real autoformalization.
    """
    spec = profile.roles.formalizer
    return _provider_available(spec.provider)


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

    # S5: direct mode cannot carry the authoritative (terminal-Lean) elementarity contract.
    if profile.mode is Mode.direct and profile.elementarity is ElementarityLevel.authoritative:
        raise SupervisorError(
            "mode='direct' is incompatible with elementarity='authoritative' "
            "(the authoritative terminal/per-node Lean gates are DAG-wired). "
            "Use mode='dag', or lower elementarity to 'soft'/'none'."
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

    # S11: the breadth/depth ensemble weighting must be usable — non-negative weights with a positive
    # sum (so the normalized sampling mix is well-defined) and non-empty model names.
    ens = profile.ensemble
    if ens.breadth_weight < 0 or ens.depth_weight < 0:
        raise SupervisorError(
            f"ensemble weights must be non-negative (got breadth_weight={ens.breadth_weight}, "
            f"depth_weight={ens.depth_weight}). Use non-negative weights."
        )
    if (ens.breadth_weight + ens.depth_weight) <= 0:
        raise SupervisorError(
            "ensemble weights sum to 0: at least one of breadth_weight/depth_weight must be positive "
            "(an all-zero weighting has no well-defined sampling mix)."
        )
    if not ens.breadth_model.strip() or not ens.depth_model.strip():
        raise SupervisorError(
            "ensemble breadth_model and depth_model must be non-empty model names."
        )

    # ----- per-role provider availability (S6 codex, S7 claude, S8 scripted) - #
    for role, spec in _roles(profile):
        provider = spec.provider
        if provider is ProviderKey.scripted:
            # S8: scripted stubs are deterministic test doubles, never a live backend.
            if not _is_test_profile(profile):
                raise SupervisorError(
                    f"role '{role.value}' uses provider='scripted', which is only "
                    "permitted in an explicitly test/offline profile. Mark the profile "
                    "as test/offline (put 'test' or 'offline' in its name/notes), or "
                    "choose provider='claude'/'codex'."
                )
            continue
        if not _provider_available(provider):
            # S6 / S7: the role's live backend CLI must be on PATH.
            cli = "Codex" if provider is ProviderKey.codex else "Claude"
            raise SupervisorError(
                f"role '{role.value}' uses provider='{provider.value}', but the "
                f"{cli} CLI was not found on PATH. Install/locate it, or switch that "
                "role to an available provider."
            )

    # ----- capability guards that depend on the elementarity/lean/stage knobs - #
    # S1: authoritative needs BOTH a resolvable formalizer AND a reachable Lean.
    if profile.elementarity is ElementarityLevel.authoritative:
        if not _formalizer_resolvable(profile):
            raise SupervisorError(
                "elementarity='authoritative' requires the formalizer role to be "
                f"resolvable, but its provider='{profile.roles.formalizer.provider.value}' "
                "backend is unavailable. Install that CLI or change the formalizer provider."
            )
        if not _lean_available():
            raise SupervisorError(
                "elementarity='authoritative' requires a reachable Lean REPL for the "
                "terminal gate, but none was found (run `lake build repl` in the Mathlib "
                "project). Lower elementarity to 'soft', or build/locate Lean."
            )

    # S2: opt-in per-node Lean needs a resolvable formalizer (it formalizes each node).
    if profile.lean.per_node and not _formalizer_resolvable(profile):
        raise SupervisorError(
            "lean.per_node=True requires the formalizer role to be resolvable, but its "
            f"provider='{profile.roles.formalizer.provider.value}' backend is unavailable. "
            "Install that CLI, change the formalizer provider, or disable lean.per_node."
        )

    # S3: an evolve fallback population needs the optional openevolve package.
    if profile.stages.evolve_fallback > 0 and not _openevolve_available():
        raise SupervisorError(
            "stages.evolve_fallback > 0 requires the optional 'openevolve' package, "
            "which is not installed (pip install mathagent[evolve]). Install it, or set "
            "stages.evolve_fallback=0."
        )

    return None
