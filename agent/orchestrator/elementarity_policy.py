"""The elementarity-level -> gate-wiring policy (one tiny, pure lookup table).

A :class:`~agent.orchestrator.run_profile.RunProfile` carries a single
:class:`~agent.orchestrator.run_profile.ElementarityLevel`. The builder must translate that one knob
into THREE concrete wiring decisions for the existing ``DagDriver``:

* ``enforce_elementarity`` — does the in-engine ``refute_elementary`` chokepoint REJECT on the
  elementarity dimension (vs. ADMIT it and downgrade a ``FAILED_ELEMENTARY`` outcome to soft PROVEN)?
* ``attach_terminal_gate`` — wire the terminal Layer-4 (formalize -> Lean audit -> faithfulness) gate?
* ``attach_per_node_lean`` — wire per-node / sketch Lean verifiers at leaf / AND nodes?

This module is the single source of truth for that mapping. It is PURE: no model/Lean/tool calls and
no heavy imports, so it can be consulted by the supervisor and the builder alike with zero side
effects.

TABLE (authoritative — keep in lockstep with the architecture contract)::

    level          enforce  terminal  per_node
    none            False    False     False
    soft            True     False     False
    authoritative   True     True      <lean.per_node>

Note ``authoritative``'s ``attach_per_node_lean`` is data-dependent: it is the run's
``lean.per_node`` flag. ``policy_for`` therefore takes that flag (defaulting to ``False`` so a bare
``policy_for(level)`` is still total); the builder passes ``profile.lean.per_node``.
"""
from __future__ import annotations

from dataclasses import dataclass

from agent.orchestrator.run_profile import ElementarityLevel


@dataclass(frozen=True)
class ElementarityPolicy:
    """The three wiring decisions the builder reads off an :class:`ElementarityLevel`."""

    enforce_elementarity: bool
    attach_terminal_gate: bool
    attach_per_node_lean: bool


def policy_for(level: ElementarityLevel, *, lean_per_node: bool = False) -> ElementarityPolicy:
    """Map an :class:`ElementarityLevel` to its :class:`ElementarityPolicy` per the TABLE.

    ``lean_per_node`` is the run's ``lean.per_node`` flag; it ONLY affects ``authoritative`` (the one
    row whose ``attach_per_node_lean`` is data-dependent). For ``none``/``soft`` per-node Lean is
    always off regardless of the flag (those levels wire no Lean gates at all).
    """
    if level is ElementarityLevel.none:
        return ElementarityPolicy(
            enforce_elementarity=False,
            attach_terminal_gate=False,
            attach_per_node_lean=False,
        )
    if level is ElementarityLevel.soft:
        return ElementarityPolicy(
            enforce_elementarity=True,
            attach_terminal_gate=False,
            attach_per_node_lean=False,
        )
    if level is ElementarityLevel.authoritative:
        return ElementarityPolicy(
            enforce_elementarity=True,
            attach_terminal_gate=True,
            attach_per_node_lean=bool(lean_per_node),
        )
    # Unreachable: ElementarityLevel is a closed enum and every member is handled above. Fail closed
    # (the strictest policy) rather than silently returning an under-enforcing one.
    raise ValueError(f"unknown elementarity level: {level!r}")


__all__ = ["ElementarityPolicy", "policy_for"]
