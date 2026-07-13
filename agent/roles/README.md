# agent/roles/

Human-readable prompt specifications for the swarm. **Only two roles have standalone files today** —
`prover.md` and `critic_judge.md` — and those files are documentation, not files loaded dynamically by the
live adapters. Production Claude/Codex prompts are constructed in `agent/tools/claude_roles.py`,
`agent/tools/codex_prover.py`, and `agent/tools/formalizer.py`. `agent/instructions/` is likewise reference
material and is not implicitly loaded into a model prompt.

Registry roles (see `../orchestrator/run_profile.py` and `../orchestrator/registry.py`):

| Role | Job | Key inputs |
| --- | --- | --- |
| **Prover** | Produce a goal-bound step-ledger for one node; retry with gate/judge feedback. | goal, closed justification vocabulary, feedback/context |
| **Decomposer / Blueprinter** | Produce a ledger sketch plus the exact child-goal set for an AND node. | goal, feedback/context |
| **Decomposition reviewer** | Decide whether a proposed decomposition is useful and elementary before commit. | goal, sketch, child goals |
| **Full-ledger judge** | Review a parsed direct proof for both elementarity and logical gaps inside the Ralph loop. | parsed ledger |
| **Comparator** | Compare two decomposition candidates for population ranking. | candidate pair |
| **Refiner** | Run the optional critic→author→synthesizer→judge incumbent tournament. | goal, incumbent ledger |
| **Formalizer** | Translate a ledger or composition sketch to Lean and repair it from compiler/audit feedback. | ledger/sketch, errors, retrieved lemmas |
| **Faithfulness checker** | Adversarially compare the Lean statement with the informal claim through four lenses. | informal claim, Lean source, theorem name |

`stages.review=false` disables both the decomposition reviewer and the full-ledger judge; it does not
change deterministic ledger validation. The refiner's judge panel is separately controlled by
`stages.judges` and only exists when refinement is enabled.

All reviewers and judges are **soft** search controls, not certification authorities. The terminal Lean
dependency/axiom audit plus trusted statement-faithfulness path is authoritative. Scripted roles are test
doubles and are untrusted for certification by default.
