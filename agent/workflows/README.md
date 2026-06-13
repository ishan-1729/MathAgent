# Workflows

Workflows are not just scripts. They define agent prompt exposure, method/library access, and search bias.

The `research/papers/` folder can be used to brainstorm workflow designs and coherent software configurations. For example, a workflow may combine a planner/prover split, Lean feedback, retrieval, self-correction, or evolutionary search if the configuration is described explicitly.

Actual runs should happen on branches.

For a fixed problem, compare all workflows with the same evaluation metrics.

Each paper-inspired workflow should record:

- source papers or software systems;
- components used;
- mathematical context exposed;
- evaluator and stopping rule;
- expected failure modes.
