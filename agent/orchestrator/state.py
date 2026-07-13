"""Per-node state machine and budget caps (the liveness model, PLAN.md Section 4.3).

The single most likely real failure is a silent stall / unbounded repair-or-replan loop. The Budget
makes every loop terminate deterministically: when a cap is hit the driver transitions a node to
EXHAUSTED rather than retrying forever.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional


class NodeState(enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    PROVEN = "proven"                        # soft success: the informal gate accepted it (no Lean / fail-open)
    LEAN_VERIFIED = "lean_verified"          # HARD success: Lean compiled + audited it elementary. DOMINATES PROVEN.
    FAILED_ELEMENTARY = "failed_elementary"  # a real proof, but not by elementary means
    FAILED_GAP = "failed_gap"                # a logical gap / not actually proven
    EXHAUSTED = "exhausted"                  # ran out of budget before deciding

    @property
    def is_terminal(self) -> bool:
        return self in (
            NodeState.PROVEN,
            NodeState.LEAN_VERIFIED,
            NodeState.FAILED_ELEMENTARY,
            NodeState.FAILED_GAP,
            NodeState.EXHAUSTED,
        )

    @property
    def is_success(self) -> bool:
        # Both soft PROVEN and hard LEAN_VERIFIED are terminal-SUCCESS. LEAN_VERIFIED DOMINATES PROVEN
        # (a Lean-confirmed proof is strictly stronger) but both count as "this node proved".
        return self in (NodeState.PROVEN, NodeState.LEAN_VERIFIED)


class BudgetExceeded(Exception):
    pass


@dataclass
class Budget:
    """Hard caps for orchestrator-scheduled search/review work on one problem.

    External verification and evolutionary controllers expose their own bounded controls; they do
    not masquerade as calls charged by this scheduler.
    """

    max_llm_calls: int = 150
    max_repair_iters: int = 6
    max_replan_depth: int = 2

    # OPTIONAL per-node Lean VERIFY sub-cap (P3). None (the default) = UNLIMITED: the per-node Lean
    # path is uncounted and the prover/decomposer search behaves BYTE-IDENTICALLY to before this
    # feature existed. When set to an int, per-node Lean compiles (leaf node_verifier + AND-node
    # sketch_verifier) draw from this SEPARATE pool so they cannot starve the main max_llm_calls budget
    # that funds prover/decomposer/reviewer search. This sub-cap is independent of calls_spent.
    max_node_verify_calls: Optional[int] = None

    calls_spent: int = 0
    repairs_spent: int = 0
    replans_spent: int = 0
    node_verify_spent: int = 0

    # --- LLM calls ---
    def can_call(self) -> bool:
        return self.calls_spent < self.max_llm_calls

    def spend_call(self, n: int = 1) -> None:
        if self.calls_spent + n > self.max_llm_calls:
            raise BudgetExceeded(
                f"llm call budget exhausted ({self.calls_spent}/{self.max_llm_calls})"
            )
        self.calls_spent += n

    # --- repair iterations ---
    def can_repair(self) -> bool:
        return self.repairs_spent < self.max_repair_iters

    def spend_repair(self) -> None:
        if self.repairs_spent >= self.max_repair_iters:
            raise BudgetExceeded(
                f"repair budget exhausted ({self.repairs_spent}/{self.max_repair_iters})"
            )
        self.repairs_spent += 1

    # --- per-node Lean VERIFY calls (P3 sub-cap, SEPARATE from max_llm_calls) ---
    def can_verify_node(self) -> bool:
        """True iff another per-node Lean verify may run. None (the default) => UNLIMITED (always True),
        so the default path is byte-identical: nothing is counted or blocked."""
        if self.max_node_verify_calls is None:
            return True
        return self.node_verify_spent < self.max_node_verify_calls

    def spend_verify_node(self, n: int = 1) -> None:
        """Account one (or n) per-node Lean verify call. A no-op accounting bump when the sub-cap is
        unlimited (None) — we still track the count for observability without ever blocking. When a
        cap is set, overshooting it raises BudgetExceeded (mirrors spend_call) so the loop stays bounded;
        callers gate on can_verify_node() first and fall back to soft PROVEN rather than spending past
        the cap, so this raise is a defensive guard, not the normal control flow."""
        if (self.max_node_verify_calls is not None
                and self.node_verify_spent + n > self.max_node_verify_calls):
            raise BudgetExceeded(
                f"node-verify budget exhausted ({self.node_verify_spent}/{self.max_node_verify_calls})"
            )
        self.node_verify_spent += n

    # --- re-plans (Phase 2; tracked now for completeness) ---
    def can_replan(self) -> bool:
        return self.replans_spent < self.max_replan_depth

    def spend_replan(self) -> None:
        self.replans_spent += 1

    def snapshot(self) -> dict:
        snap = {
            "calls_spent": self.calls_spent,
            "repairs_spent": self.repairs_spent,
            "replans_spent": self.replans_spent,
            "max_llm_calls": self.max_llm_calls,
            "max_repair_iters": self.max_repair_iters,
        }
        # Only surface the per-node verify sub-cap when it is CONFIGURED (non-None). When unset (the
        # default) the snapshot is byte-identical to before this feature, so every trace event that
        # splats snapshot() is unchanged on the default path.
        if self.max_node_verify_calls is not None:
            snap["node_verify_spent"] = self.node_verify_spent
            snap["max_node_verify_calls"] = self.max_node_verify_calls
        return snap
