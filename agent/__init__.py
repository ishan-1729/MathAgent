"""MathAgent — the agentic system for elementary number-theory proving.

This package holds the runnable harness described in agent/PLAN.md:
  - agent.gates        : the elementary-constraint gate (the defining mechanism)
  - agent.tools        : deterministic tools (numeric/witness search, ...)
  - agent.orchestrator : the control loop, state machine, and run trace

v1 is training-free: the deterministic verifiers in this package *pressure and filter*
elementarity; the authoritative hard gate (the Lean dependency audit, Layer 4) is a later phase.
"""

__version__ = "0.1.0"
