# Instructions

This directory contains human-maintained reference rules for MathAgent proof attempts, contamination
control, Lean output, and disallowed final methods. The live adapters do not read these Markdown files at
runtime; their actual prompts and validation rules live in `agent/tools/*.py` and `agent/gates/`.

Problem-specific experiment instructions may add constraints, but should not silently weaken the global
elementary-proof rule. Changing a reference document alone does not change enforcement.
