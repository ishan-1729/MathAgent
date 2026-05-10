# Axplorer

Type: repo dossier

## What it is

`AxiomMath/axplorer` is a public repository for mathematical search and optimization over structured objects. It is not Lean-native and does not operate over proof states. The repository is better understood as a hybrid learned-search system for generating and improving mathematical constructions or examples than as a theorem prover. ([Axplorer repo](https://github.com/AxiomMath/axplorer), [Axplorer README](https://github.com/AxiomMath/axplorer/blob/main/README.md))

## Status

Axplorer is public on GitHub under Apache-2.0. The official setup surface is the repository itself, with environment creation via `micromamba` and experiments run through `python train.py ...`. I did not find a formal paper linked from the official materials checked here, so the README is the best current description of the system. ([Axplorer repo](https://github.com/AxiomMath/axplorer), [Axplorer README](https://github.com/AxiomMath/axplorer/blob/main/README.md))

## Core design

Axplorer's key idea is to combine learned generation with classical search instead of relying on either one alone. The documented loop is:

1. generate an initial pool of valid examples
2. train a decoder-only transformer on the best examples
3. sample new candidates from the model
4. run local search to repair or improve those candidates
5. keep the best objects for the next epoch

Model generation is therefore never the whole algorithm. The model proposes candidates, search repairs or improves them, and selection determines what becomes training signal for the next round. ([Axplorer README](https://github.com/AxiomMath/axplorer/blob/main/README.md))

## Built-in environments and extensibility

The built-in environments are `square`, `isosceles`, and `sphere`, corresponding to concrete combinatorial/geometric search problems. The repository also documents creating custom environments via `new_envs.ipynb`. That custom-environment hook is the main reason Axplorer is interesting as a general tool rather than only a set of example experiments: if a mathematical search space can be encoded as an environment with a scoring function and local improvement logic, Axplorer provides a framework for exploring it. ([Axplorer README](https://github.com/AxiomMath/axplorer/blob/main/README.md))

## Why the design helps

The design helps because pure neural generation often struggles to stay inside a constrained mathematical search space, while pure hand-written search can be too slow or too myopic. Axplorer uses the model to bias the search toward promising objects and uses local search plus population selection to keep the process grounded in the actual objective. The result is a system aimed at iterative improvement over candidate structures rather than direct symbolic proof production. ([Axplorer README](https://github.com/AxiomMath/axplorer/blob/main/README.md))

## Setup / usage notes

The README's setup is `micromamba env create -f environment.yml` followed by `conda activate env_axplorer`. Training then runs through `python train.py` with environment-specific flags such as `--env_name`, `--exp_name`, `--gensize`, `--pop_size`, and temperature/search parameters. The README also documents resume-by-`exp_id`, data-generation-only runs, and custom environment creation, which makes the repo feel more like an experimentation framework than a fixed benchmark script. ([Axplorer README](https://github.com/AxiomMath/axplorer/blob/main/README.md))

## Strengths

- Clear hybrid design combining learned generation and search.
- Supports repeated improvement over a population rather than one-shot generation.
- Custom environments give it wider potential scope than the built-in examples alone.
- Useful as a framework for exploratory mathematical search over structured objects.

## Limitations / risks

- Not Lean-native and not a proof system.
- Official description is README-level rather than paper-level.
- Built-in tasks are search/optimization problems, not formal reasoning tasks.
- Adapting it to a new mathematical domain requires environment engineering.
- Good candidate generation does not by itself provide explanation or proof.

## Sources

- Axiom Math. `axplorer`. [https://github.com/AxiomMath/axplorer](https://github.com/AxiomMath/axplorer)
- Axiom Math. `README.md` for `axplorer`. [https://github.com/AxiomMath/axplorer/blob/main/README.md](https://github.com/AxiomMath/axplorer/blob/main/README.md)
