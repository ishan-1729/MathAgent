# Codex-backed DAG harness — adapting AlphaProof_Nexus + LEAP

This documents how MathAgent realizes **LEAP's AND-OR DAG + memoization** and the
**AlphaProof_Nexus harness**, substituting **Codex / GPT-5.5-xHigh** for the proprietary AlphaProof
prover. The deterministic gate (`agent/gates/`) stays authoritative; Codex is only the
generator / soft reviewer.

## Concept → module map

| Paper concept | Source | MathAgent module | Status |
| --- | --- | --- | --- |
| AND-OR proof DAG (OR = goal, AND = decomposition into sub-lemmas) | LEAP | [`orchestrator/dag.py`](../../agent/orchestrator/dag.py) | built |
| Hierarchical memoization / **deep-hash goal cache** (reuse a sub-lemma across branches) | LEAP + AlphaProof_Nexus | `dag.py` (`goal_hash`, `get_or_create`, `assemble` marks shared nodes) | built |
| Acyclicity guard (a decomposition can't depend on an ancestor) | LEAP state-writer | `dag.py` (`would_create_cycle`, `commit_decomposition`) | built |
| **Ralph loop** (per-goal episodes: prove → "recompile" → carry *lessons learned* → repeat) | AlphaProof_Nexus | [`orchestrator/ralph.py`](../../agent/orchestrator/ralph.py) | built |
| `sorry`-style placeholders for proposed lemmas | LEAP / AlphaProof_Nexus EVOLVE-BLOCK | the `lemma` justification: a sketch step whose `claim` is a child goal | built |
| Direct→decompose→review→recurse (DFS + backtracking) | LEAP | [`orchestrator/dag_driver.py`](../../agent/orchestrator/dag_driver.py) | built |
| **Decomposition reviewer** ("does it simplify?" + "is it elementary?") | LEAP reviewer | `CodexReviewer` / `Reviewer` protocol | built |
| **AlphaProof as a per-subgoal prover tool** | AlphaProof_Nexus | **`CodexProver` (GPT-5.5-xHigh)** in [`tools/codex_prover.py`](../../agent/tools/codex_prover.py) | built (substitute) |
| SafeVerify (statement-unchanged + axiom/dependency gate) | AlphaProof_Nexus | the deterministic gate `agent/gates/` (authoritative); Lean dependency audit is the later Layer-4 | partial |
| OEIS "test-lemma" / numeric sanity | AlphaProof_Nexus | `agent/tools/numeric.py` (Layer 3) | built |
| Evolutionary population + Plackett-Luce Elo over incomplete sketches | AlphaProof_Nexus / AlphaEvolve | — | not built (the paper's optional "v2 scaling"; DFS is used) |

## Why Codex substitutes for AlphaProof

AlphaProof is a proprietary RL-trained Lean prover, unreproducible here. AlphaProof_Nexus's own finding
is that **the *basic* agent (a strong general LLM in a compile-feedback loop) already solves the
elementary-NT problems** — AlphaProof mainly buys efficiency. So a strong general model in the same loop
is a faithful substitute. We use the locally-installed Codex CLI at the configured `gpt-5.5` /
`model_reasoning_effort=xhigh`.

### Invocation (the substitution point)
`_run_codex` in `tools/codex_prover.py` shells out to:
```
codex exec --skip-git-repo-check --ephemeral -s read-only --color never \
  -c model=gpt-5.5 -c model_reasoning_effort=xhigh -o <final-message-file>
```
with the prompt on **stdin** (UTF-8) and a throwaway cwd, so the call is non-interactive, read-only,
and side-effect-free. The model's final message is captured from the `--output-last-message` file and
parsed into a step-ledger by the gate. Three roles share this mechanism:
- `CodexProver.prove(goal)` → a step-ledger (focused prover).
- `CodexDecomposer.decompose(goal)` → a sketch ledger; child goals are the claims of its `lemma` steps.
- `CodexReviewer.review(goal, sketch, children)` → `{useful, elementary, notes}`.

> Note (`codex-plugin-cc`): the OpenAI Codex *plugin for Claude Code* is interactive-only (no headless
> API), so it is **not** used. We drive the Codex CLI directly, as above.

## Control flow (per goal, in `DagDriver._prove`)
1. **Direct attempt** via the Ralph loop (Codex prover + deterministic gate + lessons-learned).
2. On failure, **decompose** (up to `max_decomp_attempts`): Codex proposes a sketch citing `lemma`
   sub-goals; the sketch's lemma claims must match the declared children (honest decomposition).
3. **Review** the decomposition (reviewer must say useful ∧ elementary); **acyclicity** check; validate
   the sketch through the gate.
4. **Commit** and **recurse** (DFS) on each child, **reusing** any already-proven sub-lemma via the
   deep-hash cache; backtrack to another decomposition if a branch fails.
5. Everything draws from one `Budget` and recursion is depth-bounded → the search always terminates.

## Running it
```
# Full DAG harness (Codex at xHigh):
python scripts/prove.py "For every integer n, n^2 is congruent to 0 or 1 modulo 4."
# Quick single-shot direct proof:
python scripts/prove.py --direct --effort low "For all integers n, n + 0 = n."
```
Live integration is validated by `tests/test_codex_integration.py` (the live test is opt-in:
`MATHAGENT_CODEX_TESTS=1` + codex on PATH). The DAG/Ralph logic is fully unit-tested offline with
scripted provers (no Codex needed), so `make test` stays fast and deterministic.

## Honest caveats
- **The reviewer is the same model family as the prover** (Codex). The literature warns same-model judges
  share blind spots; the *deterministic* gate is the real guard, and the `Reviewer`/`Judge` protocols let
  a different model be slotted in later.
- **The live decomposition path is lightly exercised** end-to-end (Codex tends to prove the small
  validation goals directly); the decompose/review/recurse *logic* is thoroughly unit-tested offline.
- **No Lean Layer-4 yet**, so "gate-passed" still means *pressured*-elementary, not *enforced* — the
  authoritative dependency audit remains the outstanding piece (PLAN §5/§7).
- **Population/Elo search is not built** (the paper's optional scaling); the harness uses DFS + memoization.
