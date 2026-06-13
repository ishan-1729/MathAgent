"""Per-node state machine and budget caps (the liveness model, PLAN.md Section 4.3).

The single most likely real failure is a silent stall / unbounded repair-or-replan loop. The Budget
makes every loop terminate deterministically: when a cap is hit the driver transitions a node to
EXHAUSTED rather than retrying forever.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass


class NodeState(enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    PROVEN = "proven"
    FAILED_ELEMENTARY = "failed_elementary"  # a real proof, but not by elementary means
    FAILED_GAP = "failed_gap"                # a logical gap / not actually proven
    EXHAUSTED = "exhausted"                  # ran out of budget before deciding

    @property
    def is_terminal(self) -> bool:
        return self in (
            NodeState.PROVEN,
            NodeState.FAILED_ELEMENTARY,
            NodeState.FAILED_GAP,
            NodeState.EXHAUSTED,
        )

    @property
    def is_success(self) -> bool:
        return self is NodeState.PROVEN


class BudgetExceeded(Exception):
    pass


@dataclass
class Budget:
    """Hard caps for a single problem run. Defaults mirror PLAN.md Section 4.3."""

    max_llm_calls: int = 150
    max_repair_iters: int = 6
    max_replan_depth: int = 2

    calls_spent: int = 0
    repairs_spent: int = 0
    replans_spent: int = 0

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
        self.repairs_spent += 1

    # --- re-plans (Phase 2; tracked now for completeness) ---
    def can_replan(self) -> bool:
        return self.replans_spent < self.max_replan_depth

    def spend_replan(self) -> None:
        self.replans_spent += 1

    def snapshot(self) -> dict:
        return {
            "calls_spent": self.calls_spent,
            "repairs_spent": self.repairs_spent,
            "replans_spent": self.replans_spent,
            "max_llm_calls": self.max_llm_calls,
            "max_repair_iters": self.max_repair_iters,
        }
