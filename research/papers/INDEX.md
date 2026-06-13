# INDEX

This index summarizes the current contents of `D:\Projects\Math\MathAgent\research\papers`. It covers both finished Markdown dossiers and folders that currently contain only source artifacts.

## Comparison table

| Tool | Type | Lean-native? | Primary role | Main mechanism / design | Public surface |
| --- | --- | --- | --- | --- | --- |
| AlphaEvolve (+ `OpenEvolve.md`) | paper / white-paper style note + companion repo dossier | No | Autonomous scientific and algorithmic discovery | AlphaEvolve gives the research framing; OpenEvolve gives a public implementation surface around evaluator-driven evolutionary code search | white paper + companion repo note |
| AlphaProof Nexus (`AlphaProof_Nexus`) | paper + system note | Yes | AI-driven formal proof search for research-level mathematics | LLM prover agents refine Lean sketches under compiler feedback, optionally call AlphaProof on generated subgoals, and use an evolutionary population/rating layer for search coordination | paper source + Markdown dossier |
| Aristotle | product + paper hybrid | Partly Lean-centered | Informal-to-formal theorem proving system | Generates informal lemmas or subgoals, formalizes them, and uses Lean-based proving in an iterative loop | paper + product / company material |
| AutoReason (`AutoReason`) | paper / method note | No | Structured iterative self-refinement | Three-way tournament between incumbent, adversarial revision, and synthesis, judged by fresh agents with Borda aggregation and stop conditions | paper-style note + repo link |
| AutoResearch | repo / method dossier | No | Autonomous research workflow scaffold | Program-driven experiment loop for iterative prompting, coding, evaluation, and logging | GitHub repo / README |
| AXLE | repo / docs dossier | No, but proof-oriented | Proof checking and transformation toolkit | Parses proof artifacts and exposes utilities for verification, normalization, extraction, and refactoring | docs + repo APIs / CLI |
| Axplorer | repo / product dossier | No | Search and exploration over mathematical objects or constructions | Retrieval / exploration interface over structured mathematical spaces rather than Lean proof construction | repo / product surface |
| BFS-Prover-V2 (`BFSProverV2`) | paper + open-source system note | Yes | Lean step-prover with scalable training and search | Multi-turn off-policy RL for tactic learning plus planner-enhanced multi-agent best-first search at inference time | paper + code / project page |
| BRIDGE | paper | Yes | Verified code synthesis in Lean 4 | Structured prompting decomposes code generation into specifications, theorem statements, and proofs | paper |
| GoedelProverV2 | paper + open-source system note | Yes | Lean theorem prover with scaffolded synthesis and verifier-guided self-correction | Whole-proof generation is strengthened with scaffolded data synthesis, self-correction from Lean compiler feedback, RL, and model averaging | paper source + Markdown dossier |
| LeanAgent | paper | Yes | Lifelong learning for formal theorem proving | Curriculum learning, theorem retrieval, replay buffers, and iterative proof search over Lean corpora | paper |
| LeanDojo-v2 (`LeanDojov2`) | paper + software stack | Yes | End-to-end Lean data, training, and proof-search infrastructure | Unified extraction, benchmarking, model training, inference APIs, and Lean IDE-facing tooling | paper + library |
| LeanNavigator | paper | Yes | Large-scale theorem/proof data generation | Explores Lean state-transition graphs to synthesize many new theorem/proof pairs | paper |
| LeanProgress | paper | Yes | Proof-search guidance / progress estimation | Learns signals about remaining proof distance or proof progress to guide search | paper |
| Leanstral | docs / model-release dossier | Yes | Lean 4 proof assistant model and repo-work assistant | Instruction-tuned model aimed at proof construction, repair, and iterative Lean repository work | release docs / repo / launch material |
| LEAP | paper | Yes | Agentic formal theorem proving with general-purpose LLMs | Blueprint-driven decomposition into an AND-OR DAG with hierarchical memoization, interleaved informal–formal planning, and verification-guided search over Lean compiler feedback plus an LLM decomposition reviewer; uses only general LLMs (no specialized prover) | paper source + Markdown dossier |
| LongCat-Flash-Prover (`LongCatFlashProver`) | paper + model / project release note | Yes | Lean-native formal reasoning model with tool-integrated RL | Hybrid-experts iteration for auto-formalization, sketching, and proving, trained with tool-integrated RL and HisPO | paper + model card + project page |
| MathCode | repo / product-style dossier | Yes | AI coding assistant with Lean formalization support | Combines a coding-agent loop with Lean theorem translation, REPL interaction, and proof attempts | repo / docs |
| OpenGauss | docs / repo dossier | Yes | Project-scoped Lean orchestration system | Multi-command workflow over Lean projects with actions such as proving, drafting, reviewing, checkpointing, and refactoring | docs + repo |
| Pantograph | paper + software note | Yes | Machine-to-machine interface for Lean 4 | Rich programmable proof-state interface designed for search, data extraction, and tactic orchestration | paper + library |

## Tool families

### Lean proof agents and repo-aware assistants

- **Leanstral** is a model and workflow surface aimed at day-to-day Lean 4 proof engineering: constructing proofs, repairing broken scripts, and operating over repositories rather than isolated benchmark prompts.
- **OpenGauss** sits one level higher as a project-scoped orchestration layer. Its value is not a new theorem prover by itself, but a workflow shell around common Lean tasks such as `prove`, `draft`, `review`, `checkpoint`, `refactor`, `autoprove`, `formalize`, and `autoformalize`.
- **MathCode** is broader than Lean-specific assistants, but it matters here because it tries to fuse a general coding-agent loop with persistent Lean interaction and theorem-formalization capabilities.

### Lean theorem-proving systems and training stacks

- **AlphaProof Nexus (`AlphaProof_Nexus`)** focuses on research-level Lean proof discovery rather than benchmark-only proving. It combines repo-style proof sketches, LLM editing loops, Lean validation, optional AlphaProof calls for subgoals, and an evolutionary sketch/rating database.
- **BFS-Prover-V2 (`BFSProverV2`)** is a current-generation step prover focused on scaling both training-time RL and inference-time search. Its distinctive move is to pair off-policy expert iteration with a planner-enhanced multi-agent prover architecture.
- **GoedelProverV2** focuses on strong whole-proof generation with verifier-guided self-correction, scaffolded statement synthesis, reinforcement learning, and model averaging. Its emphasis is efficiency: strong Lean proving at smaller model scales and lower test-time budgets.
- **LEAP** takes the opposite bet from the specialized-prover stacks: it uses *only* general-purpose LLMs (no fine-tuned prover) inside an agentic harness. Its distinctive move is to codify the human blueprint workflow as an AND-OR DAG with hierarchical memoization, interleave informal and formal planning, and gate decompositions with both the Lean compiler and an LLM reviewer. It also contributes the Lean-IMO-Bench evaluation set. Relevant when designing training-free agent loops and DAG-based proof memory.
- **LongCat-Flash-Prover (`LongCatFlashProver`)** emphasizes native formal reasoning as a model capability, splitting the task into auto-formalization, sketching, and proving, then training these capacities with tool-integrated reinforcement learning.
- **LeanAgent** focuses on lifelong learning for theorem proving, especially how a prover can improve as it accumulates solved problems and retrieval structure.
- **LeanNavigator** is primarily about data generation at scale: it expands the available theorem/proof distribution by exploring Lean state-transition graphs and synthesizing additional formal problems.
- **LeanProgress** addresses search guidance rather than proof generation alone, supplying a learned notion of progress that can steer proof search more efficiently.
- **LeanDojo-v2 (`LeanDojov2`)** also belongs here in part, because it is not only a benchmark paper but a reusable infrastructure stack for repository extraction, model training, inference, and evaluation.

### Lean interfaces and proof-manipulation infrastructure

- **Pantograph** is an interface layer: it exposes Lean 4 to external systems in a way that supports search, proof-state inspection, and tactic orchestration.
- **AXLE** is closer to a proof-utility layer. It is useful when the goal is not generation alone, but checking, restructuring, extracting, or transforming proof artifacts downstream.
- **BRIDGE** is a strong example of verified synthesis in Lean 4: instead of asking for code alone, it frames synthesis around specifications, intermediate theorems, and proofs.
- **Aristotle** sits at the interface between informal mathematics and formal proof. Its core interest is the loop from informal lemma discovery and decomposition into formally checkable Lean steps.

### Refinement, search, and outer-loop research systems

- **AlphaEvolve** is not Lean-native. It belongs in the folder because it represents a strong outer-loop pattern: propose programs or hypotheses, evaluate them, mutate them, and preserve successful trajectories. The companion note `AlphaEvolve/OpenEvolve.md` covers the public open-source implementation surface built around that same evaluator-driven evolutionary coding pattern.
- **AutoReason** is not a theorem prover; it is a structured refinement method for cases where generation and evaluation should be separated. Its contribution is the disciplined stop-or-revise loop rather than domain-specific proof machinery.
- **AutoResearch** is even more general-purpose. It is best understood as a reusable autonomous research scaffold for experiments, prompts, and evaluation loops rather than a formal prover.
- **Axplorer** appears more aligned with mathematical search and exploration than with proof checking. It is relevant as a discovery or conjecture-support layer, not as a Lean proof backend.

## Reading guide

- Read **LeanDojo-v2 (`LeanDojov2`)**, **Pantograph**, **LeanAgent**, **LeanNavigator**, and **LeanProgress** for the strongest coverage of Lean-native training, search, and interface infrastructure.
- Read **AlphaProof Nexus**, **BFS-Prover-V2**, **GoedelProverV2**, **LEAP**, and **LongCat-Flash-Prover** for recent theorem-prover systems that push different combinations of RL, search, self-correction, tool-integrated reasoning, and Lean-verified agent loops. **LEAP** is the reference for a training-free, general-LLM-only agent built around blueprint decomposition and DAG-based proof memory.
- Read **Leanstral**, **OpenGauss**, and **MathCode** for newer agent-style or repo-oriented workflows built around interactive Lean use.
- Read **BRIDGE** and **Aristotle** when the focus is the bridge from natural-language or program-synthesis structure into formally verified Lean artifacts.
- Read **AlphaEvolve** together with **OpenEvolve** when you want both layers: the paper-level framing and the practical open-source implementation surface.
- Read **AutoReason**, **AutoResearch**, and **Axplorer** as adjacent outer-loop systems for refinement, experimentation, and exploration rather than direct Lean proof checking.

## What is still missing from the folder

A few adjacent categories are still underrepresented even after these additions:

- Lean-native retrieval systems focused specifically on premise selection, theorem search, and context construction.
- Lean automation layers centered on tactic synthesis, proof repair, or verified rewriting beyond the papers already present here.
- Computational mathematics systems that pair well with theorem proving but are not themselves Lean provers, such as number-theory search / CAS / SAT / SMT support notes.
- Formal-math dataset and benchmark notes outside the current Lean-centric cluster, especially where they influence training and evaluation design.
