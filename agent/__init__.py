"""MathAgent — the agentic system for elementary number-theory proving.

This package holds the runnable harness described in agent/PLAN.md:
  - agent.gates        : the elementary-constraint gate (the defining mechanism)
  - agent.tools        : deterministic tools (numeric/witness search, ...)
  - agent.orchestrator : the control loop, state machine, and run trace

v1 is training-free: Layers 1–3 pressure and filter elementarity; authoritative certification is
reserved for the compiled Lean proof-term dependency/axiom audit (Layer 4) plus faithfulness.
"""

__version__ = "0.1.0"
