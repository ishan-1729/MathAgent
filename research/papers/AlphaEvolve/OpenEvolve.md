# OpenEvolve

Type: repo dossier

## What it is

`algorithmicsuperintelligence/openevolve` is a public open-source evolutionary coding agent positioned explicitly as an open-source implementation of the AlphaEvolve idea. It is not the AlphaEvolve white paper itself and it is not a Lean-specific system. Its target use case is broader: given an initial program, an evaluator, and a metric-bearing feedback loop, it repeatedly proposes code changes, runs evaluation, and keeps strong or diverse candidates in circulation. In practice it is a framework for LLM-guided program search rather than a single benchmark-specific demo. ([OpenEvolve repo](https://github.com/algorithmicsuperintelligence/openevolve), [README](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/README.md))

## Relationship to AlphaEvolve

The useful way to read OpenEvolve is as a public engineering surface around the same general recipe that the AlphaEvolve paper describes: optimize code by combining LLM proposal generation with automatic evaluation and evolutionary selection. The repo does not present itself as an official DeepMind release; instead it presents itself as an open-source implementation, with its own CLI, Python API, examples, configuration system, and additional engineering features such as Docker packaging, a visualizer, and provider-agnostic LLM backends. That makes it important as a practical artifact even when the paper remains the better source for the high-level scientific framing. ([OpenEvolve repo](https://github.com/algorithmicsuperintelligence/openevolve), [README](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/README.md), [pyproject.toml](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/pyproject.toml))

## Status

OpenEvolve is a live Python package and GitHub repository with an installable CLI entry point, example configurations, tests, and a substantial examples gallery. The package metadata targets Python 3.10+, exposes the `openevolve-run` console script, and declares a normal library surface in addition to the command-line workflow. The repository also ships Docker support and example directories for optimization, symbolic regression, prompt optimization, circle packing, GPU kernels, and related tasks. ([pyproject.toml](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/pyproject.toml), [README](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/README.md))

## Core workflow

The runtime contract is simple but strict. A run starts from an initial program and an evaluation file containing an `evaluate` function. OpenEvolve then iterates through propose, apply, evaluate, archive, and resample steps. The evaluator is the grounding mechanism: it returns metrics, and the system uses those metrics to rank or filter candidates. For file-based use the documented entry point is `openevolve-run <initial_program> <evaluation_file> --config ... --iterations ...`; for library use the main calls are `run_evolution`, `evolve_function`, `evolve_algorithm`, and `evolve_code`. One practical detail is worth noting: if the library API is given code without explicit `EVOLVE-BLOCK-START` markers, it can wrap the whole program as an evolvable block automatically, which lowers the barrier for quick experiments. ([README](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/README.md), [cli.py](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/openevolve/cli.py), [api.py](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/openevolve/api.py))

## Architecture and search design

The architectural center of OpenEvolve is not just "LLM + loop" but a quality-diversity search scheme. The README and code both describe a MAP-Elites-style archive combined with island-based evolution. The database keeps per-island feature maps, an archive of elite programs, migration intervals, diversity bookkeeping, and a separately tracked absolute best program so that a strong candidate is not lost while the broader search stays diverse. This matters because the system is designed to search for good programs across a space of alternatives, not merely hill-climb a single incumbent. ([README](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/README.md), [database.py](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/openevolve/database.py))

## Prompting and LLM layer

The prompt-construction layer is richer than a naive "here is the current program, improve it" setup. The prompt sampler can assemble prompts from the current program, parent program, previous attempts, top-performing programs, separate inspiration programs, current metrics, feature coordinates, and optional evaluation artifacts. The configuration surface also supports template stochasticity, system-message customization, prompt-template overrides, ensemble model weighting, evaluator-specific model ensembles, and provider-agnostic API routing through OpenAI-compatible endpoints. In other words, OpenEvolve treats prompting as part of the search algorithm rather than as a static wrapper around code generation. ([README](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/README.md), [prompt/sampler.py](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/openevolve/prompt/sampler.py), [config.py](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/openevolve/config.py))

## Evaluation, artifacts, and reproducibility

The repo puts unusual emphasis on evaluation engineering. The evaluator can support cascade evaluation, and the README explicitly highlights artifacts as a side-channel: error output, profiling data, and other execution feedback can be carried forward so later generations are informed by concrete failure modes rather than score alone. Reproducibility is also treated as a first-class concern. Configuration and controller code expose random seeding, shared seed propagation into model configs, component isolation, checkpoints, logs, and optional evolution traces. This combination of artifact feedback and reproducibility machinery is a large part of why OpenEvolve is more than a thin wrapper around an API call. ([README](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/README.md), [config.py](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/openevolve/config.py), [controller.py](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/openevolve/controller.py), [api.py](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/openevolve/api.py))

## Interfaces and outputs

There are three main user surfaces. First, the CLI supports iterative runs with config files, output directories, iteration limits, target scores, log levels, API-base overrides, model overrides, and checkpoint resume. Second, the Python API supports embedding the system directly in other code through `run_evolution`, `evolve_function`, `evolve_algorithm`, and `evolve_code`. Third, the repository includes examples, a visualizer script, and Docker packaging, which makes it easier to inspect run trajectories rather than treating the system as a black box. This is one of the main differences between a paper concept and a usable experimentation tool. ([cli.py](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/openevolve/cli.py), [api.py](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/openevolve/api.py), [README](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/README.md))

## Why the design works

The design works when the task admits an automatic evaluator and the space of plausible improvements is large enough that diversity matters. LLMs contribute proposal power and code-level prior knowledge; MAP-Elites and island structure keep the search from collapsing too quickly onto one local pattern; evaluators provide hard grounding; artifact feedback gives the system more than scalar reward; and configurable prompts let the search exploit domain knowledge when the user has it. The important point is that OpenEvolve is not trying to prove correctness symbolically. It is trying to discover better programs under an executable scoring loop. ([README](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/README.md), [database.py](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/openevolve/database.py), [prompt/sampler.py](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/openevolve/prompt/sampler.py))

## Strengths

- Real engineering surface rather than only a conceptual paper.
- Clear evaluator contract, which makes adaptation to new optimization tasks straightforward.
- Supports both CLI-first and library-first workflows.
- Diversity-preserving search is built into the architecture rather than added as an afterthought.
- Prompting, evaluator feedback, checkpoints, and examples are all treated as core parts of the system.

## Limitations / caveats

- It is only as good as the evaluator; if the score is poorly chosen, evolution will optimize the wrong thing.
- It is not a formal verifier and does not guarantee correctness beyond what the evaluator checks.
- The project presents benchmark-style achievements in the README, but the right level of trust should still come from the concrete example code and evaluation setup rather than headline claims alone.
- It is not Lean-native and does not directly address theorem proving without substantial task-specific adaptation.
- As with AlphaEvolve more broadly, tasks that require human judgment rather than executable feedback are a poor fit. ([README](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/README.md), [OpenEvolve repo](https://github.com/algorithmicsuperintelligence/openevolve))

## Sources

- OpenEvolve repository: [https://github.com/algorithmicsuperintelligence/openevolve](https://github.com/algorithmicsuperintelligence/openevolve)
- OpenEvolve README: [https://github.com/algorithmicsuperintelligence/openevolve/blob/main/README.md](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/README.md)
- OpenEvolve package metadata: [https://github.com/algorithmicsuperintelligence/openevolve/blob/main/pyproject.toml](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/pyproject.toml)
- OpenEvolve CLI: [https://github.com/algorithmicsuperintelligence/openevolve/blob/main/openevolve/cli.py](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/openevolve/cli.py)
- OpenEvolve high-level API: [https://github.com/algorithmicsuperintelligence/openevolve/blob/main/openevolve/api.py](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/openevolve/api.py)
- OpenEvolve configuration: [https://github.com/algorithmicsuperintelligence/openevolve/blob/main/openevolve/config.py](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/openevolve/config.py)
- OpenEvolve controller: [https://github.com/algorithmicsuperintelligence/openevolve/blob/main/openevolve/controller.py](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/openevolve/controller.py)
- OpenEvolve program database: [https://github.com/algorithmicsuperintelligence/openevolve/blob/main/openevolve/database.py](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/openevolve/database.py)
- OpenEvolve prompt sampler: [https://github.com/algorithmicsuperintelligence/openevolve/blob/main/openevolve/prompt/sampler.py](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/openevolve/prompt/sampler.py)
