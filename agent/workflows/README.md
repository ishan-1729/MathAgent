# Workflows

These workflow files are human-readable experiment designs for prompt exposure, method/library access, and
search bias. They are not scripts, executable `RunProfile` YAML, or content automatically loaded by the live
harness. An experiment must translate the design into explicit profile/CLI configuration and prompt code,
then record that effective configuration.

The `research/papers/` folder can be used to brainstorm workflow designs and coherent software configurations. For example, a workflow may combine a planner/prover split, Lean feedback, retrieval, self-correction, or evolutionary search if the configuration is described explicitly.

Actual runs should happen on branches.

For a fixed problem, compare all workflows with the same evaluation metrics.

Each paper-inspired workflow should record:

- source papers or software systems;
- components used;
- mathematical context exposed;
- evaluator and stopping rule;
- expected failure modes.
