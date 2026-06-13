# OpenGauss

Type: repo dossier

## What it is

OpenGauss is Math, Inc.'s open-source project-scoped orchestration layer for Lean workflows. It should not be confused with the unrelated `openGauss` database project. In this context, OpenGauss is a `gauss`-based shell for managing theorem-proving and formalization workflows inside a chosen Lean repository. It does not replace the underlying proving logic; it wraps and coordinates it. ([Math, Inc. OpenGauss page](https://www.math.inc/opengauss), [OpenGauss repository](https://github.com/math-inc/OpenGauss))

## Status

OpenGauss is public as an open-source repository with local installation flows for macOS/Linux and Windows via WSL2, plus a hosted Morph template. Math, Inc. positions it as an open autoformalization harness connected to the broader Gauss/FormalQualBench ecosystem. The public surface is therefore real and operational, but the strongest benchmark claims are still vendor-authored rather than neutral third-party evaluations. ([Math, Inc. OpenGauss page](https://www.math.inc/opengauss), [OpenGauss repository](https://github.com/math-inc/OpenGauss), [Introducing Gauss](https://www.math.inc/gauss))

## Core design

The core design is explicit in the README: OpenGauss manages the project model, backend sessions, workflow spawning, swarm tracking, and recovery, while the actual theorem-proving and formalization behaviors still come from `lean4-skills`. In other words, OpenGauss is orchestration and workflow infrastructure, not a new proving algorithm. ([OpenGauss repository](https://github.com/math-inc/OpenGauss), [lean4-theorem-proving-skill](https://github.com/cameronfreer/lean4-theorem-proving-skill))

Its headline command surface is:

- `/prove`
- `/draft`
- `/review`
- `/checkpoint`
- `/refactor`
- `/golf`
- `/autoprove`
- `/formalize`
- `/autoformalize`
- `/swarm`

Each of these forwards into the corresponding Lean workflow while preserving project context. The repo also documents `/swarm attach` and `/swarm cancel`, which makes the system explicitly task-oriented rather than just a thin wrapper around one-shot prompts. ([OpenGauss repository](https://github.com/math-inc/OpenGauss))

## How it works in practice

OpenGauss treats Lean work as project-scoped by default. Before running managed workflows, the user selects or registers the active project using commands such as `/project init`, `/project create`, `/project use`, or `/project clear`. From then on, OpenGauss launches backend child agents from the active project root so that each forwarded workflow command runs in the correct environment. The project file `.gauss/project.yaml` and the active root are therefore part of the system's state model, not merely convenience metadata. ([OpenGauss repository](https://github.com/math-inc/OpenGauss))

This matters because long-running Lean work is often limited less by raw proof search than by operational friction: choosing the right repo, preserving state, recovering from interruptions, coordinating multiple tasks, and keeping proving, review, and refactoring flows distinct. OpenGauss is trying to solve exactly that layer of the problem. ([OpenGauss repository](https://github.com/math-inc/OpenGauss))

## Why the design helps

The design helps because project context is first-class. A lot of theorem-proving infrastructure works well on isolated benchmark statements but becomes brittle on real codebases with many files, imports, declarations, and in-flight tasks. By introducing an explicit project model plus managed backend sessions, OpenGauss makes it easier to run repeated proving/formalization cycles without constantly rebuilding the execution context. That is especially valuable in multi-agent or long-horizon formalization settings, where the cost of losing context can dominate the cost of individual proof attempts. ([OpenGauss repository](https://github.com/math-inc/OpenGauss), [Introducing Gauss](https://www.math.inc/gauss))

## Interfaces and usage surface

The documented local install path is through the repo installer scripts. On macOS/Linux, the canonical path is `./scripts/install.sh`; on Windows, the README explicitly routes setup through WSL2 using `./scripts/install.ps1 -WithWorkspace`. The quick-start loop is `gauss`, optional `/chat`, then `/project create` or `/project init`, followed by one of the Lean workflow commands. The repo also documents a local-model path through an OpenAI-compatible `vLLM` server and names built-in backends such as `claude-code` and `codex`. ([OpenGauss repository](https://github.com/math-inc/OpenGauss))

## Strengths

- Makes project context explicit and persistent.
- Unifies proving, review, refactor, and formalization flows under one command surface.
- Better suited to long-running and multi-agent work than ad hoc prompt-driven sessions.
- Clear operational focus: session management, workflow routing, and project-scoped execution.

## Limitations / risks

- The mathematical intelligence still comes from the underlying model and `lean4-skills` workflows.
- Installation and auth complexity can still be significant.
- Product claims about benchmark performance should be read as official but vendor-authored claims.
- The system is valuable only if the surrounding project workflow is substantial enough for orchestration to matter.

## Sources

- Math, Inc. "OpenGauss: an open source, state of the art autoformalization harness." [https://www.math.inc/opengauss](https://www.math.inc/opengauss)
- Math, Inc. `OpenGauss` repository. [https://github.com/math-inc/OpenGauss](https://github.com/math-inc/OpenGauss)
- Math, Inc. "Introducing Gauss, an agent for autoformalization." [https://www.math.inc/gauss](https://www.math.inc/gauss)
- Cameron Freer et al. `lean4-theorem-proving-skill`. [https://github.com/cameronfreer/lean4-theorem-proving-skill](https://github.com/cameronfreer/lean4-theorem-proving-skill)
