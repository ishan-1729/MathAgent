> **SUPERSEDED — external review (GPT Pro), archived for provenance.**
> This is GPT Pro's original review of the OpenEvolve / AlphaEvolve stacking brief. Its substantive, agreed-with points were
> incorporated into [`openevolve_stacking_brief.md`](../openevolve_stacking_brief.md) on 2026-06-14 (the hardened edits there are marked
> *"revised after independent review"* / *"verified in code"*). Kept as the audit trail behind
> those edits — **not live project guidance; do not treat as current.**

---

I read the uploaded brief and treated it as self-contained evidence. Overall: **the §9 recommendations are directionally sound and mostly well-prioritised**, because they correctly frame OpenEvolve as a **diversity proposer**, not as a certifier. The central design principle is right: MathAgent’s product is not “find a plausible proof,” but “find and certify an elementary proof,” and only the Layer-4 Lean dependency audit is authoritative for elementarity. The brief is explicit that `PROVEN` is only an informal/soft-gate result, while `authoritative_elementary = True` requires formalisation, compilation, dependency audit, and faithful Lean statement matching. 

My main correction is prioritisation: **do not treat “OpenEvolve + DAG decomposition” as the top practical move unless the fitness hardening is implemented first or simultaneously**. The current OpenEvolve integration is safe plumbing, but its fitness is still the deterministic soft gate mapped to `0/0.5/1.0`, with `step_count` and `justification_diversity` as MAP-Elites axes; the brief itself says this optimises against a gameable signal.  So the top recommendation should be reframed as:

> **Minimum viable safe stack:** OpenEvolve as fallback decomposer **only after** hard pre-filters for goal-binding/faithfulness and numeric-grounded obligations are in place; Layer-4 audit remains terminal and authoritative, not routine inner-loop fitness.

## Judgment of the top recommendation

The top recommendation is **sound**: “verification-guided, cascade-gated diversity proposer” is the right role for OpenEvolve. It respects the key invariant that evolution should explore candidate proof sketches while non-gameable checks do selection. This aligns with the brief’s Layer table: soft layers and LLM judges are gameable, while Layer 3 exact integer checks and Layer 4 kernel dependency audits are non-gameable. 

It is also **safe in spirit**, because it preserves the major safety decision: evolve ledger text, not executable programs. That matters because the brief says OpenEvolve’s native code-evolution mode is arbitrary-code-execution by design, whereas the current integration only reads evolved JSON ledgers and never `exec`/`eval`/`import`s evolved content. 

The weak spot is the phrase **“feeding `audit.passed` back as a rare fitness bonus.”** A bonus is useful for search telemetry, but for certification it must be a **hard final predicate**, not merely a large score component. A candidate with excellent soft/numeric score but no Layer-4 audit must remain “uncertified.” The brief says Layer 4 is the only authoritative elementary gate and that treating high-fitness ledgers as elementary without it is the top category error. 

## Pairing-by-pairing assessment

| #                                                               |                               My judgment |                  Priority | Notes                                                                                                                                                                                                                                                                                                                                                                                                          |
| --------------------------------------------------------------- | ----------------------------------------: | ------------------------: | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1. OpenEvolve + LEAP DAG decomposition                          |              **Good, but not safe alone** | High only after hardening | This is useful because decomposition diversity is plausibly a real bottleneck. But if “best” means best under the current soft-gate fitness, it amplifies the known failure mode: plausible ledgers that pass structure while hiding gaps. It should be gated by goal-binding and numeric obligations before DAG commitment.                                                                                   |
| 2. OpenEvolve + numeric grounding                               |  **Strongest substantive recommendation** |                 Very high | This is the best use of evolution because it gives OpenEvolve a non-gameable, contentful reward: exact integer verification of witnesses, finite case covers, and descent measures. It is also well-matched to elementary number theory. The implementation effort may be higher than “medium” if the system must infer reliable obligation schemas from prose, but the direction is excellent.                |
| 3. OpenEvolve + faithfulness panel                              |            **Essential, but partly soft** |                 Very high | Goal-binding/vacuity checks should be mandatory before archive insertion, not just before final selection. However, the LLM-based faithfulness panel is still gameable. The deterministic goal↔claim binding should carry more authority than panel votes, and panel results should be used as fail-closed routing/pre-filtering rather than as proof-quality reward.                                          |
| 4. OpenEvolve + Lean Layer-4 audit cascade                      |                 **Correct and necessary** |        High, but terminal | This is the right cascade shape: cheap filters first, Layer 4 only on survivors. The effort may be closer to high than medium depending on formalisation reliability. A failed formalisation should be treated carefully: it may mean “unknown/tooling failed,” not necessarily “mathematically bad.” But a passed audit is the only certification signal.                                                     |
| 5. OpenEvolve + Elo/BT/PUCT ranking                             |             **Useful secondary selector** |                    Medium | This should rank among candidates already passing hard pre-filters. It is risky as a primary selector because LLM judges are gameable and may share blind spots with generators. It should never override numeric grounding, goal-binding, or Layer-4 outcomes.                                                                                                                                                |
| 6. OpenEvolve + AutoReason tournament                           |       **Good explore → exploit pipeline** |               Medium-high | The one-directional design is right: evolve rough diverse candidates, then polish one champion. The anti-pattern warning not to feed AutoReason output back into the archive is important and should be enforced. The only caveat is that “stays elementary” must not mean “judge says elementary”; it must mean it preserves or improves hard-check status.                                                   |
| 7. Re-key MAP-Elites to proof strategy + retrieval-seed islands | **Important archive-quality improvement** |            High after 2/3 | This fixes a real flaw: `step_count` and `justification_diversity` are surface-level diversity axes. Strategy descriptors such as descent/casework/gcd/induction, modulus band, and depth are much better aligned with proof search. Retrieval-seeded islands are promising, but need anti-contamination: retrieved exemplars should be tagged with allowed-toolkit status and not import heavy-method priors. |

## What I would change in the prioritisation

I would split the roadmap into **safety-critical prerequisites** and **performance improvements**.

**Phase 0: Do before relying on OpenEvolve outputs.**
Implement hard goal-binding, vacuity rejection, and “`NEEDS_REVIEW` is not a win.” The brief explicitly warns that bare soft-gate fitness can hill-climb toward vacuous, weakened, or off-goal ledgers and that `NEEDS_REVIEW → 0.5` should not be treated as success. 

**Phase 1: Add non-gameable content rewards.**
Prioritise numeric grounding over LLM ranking. Exact integer checks are the closest thing here to a cheap, domain-relevant truth signal; they reward real proof content and make many non-elementary objects unrepresentable in the evaluator’s AST. 

**Phase 2: Use OpenEvolve as fallback decomposer in the DAG.**
Only fire it on stuck nodes, and only commit evolved decompositions after acyclicity, strict-simpler-child, goal-binding, and numeric-obligation checks. This fits the AND-OR DAG architecture, where one bad decomposition can dead-end a subtree and where memoization can reuse good subgoals. 

**Phase 3: Improve diversity and ranking.**
Re-key MAP-Elites to strategy descriptors, use retrieval-seeded islands, and then use Elo/BT/PUCT to pick among hard-filtered candidates. LLM judges should be discriminators of promise, not arbiters of correctness.

**Phase 4: Layer-4 audit only for survivors and final certificates.**
Use Lean compile + dependency audit sparingly, as the final cascade stage. The brief’s cost asymmetry explicitly supports this: Lean audit is much more expensive than LLM mutation, and the AlphaEvolve cascade exists to keep expensive checks rare. 

## My proposed composition

My recommended stack is:

1. **Direct/Ralph first.** Try the cheap direct prover and ordinary decomposer before evolution.
2. **Trigger OpenEvolve only on stuck nodes.** “Stuck” should mean repeated Ralph/decomposer failure, contradictory reviewer feedback, or repeated low numeric-grounding score.
3. **Archive by proof strategy, not surface form.** MAP-Elites cells should be keyed by proof family: descent, modular casework, gcd/divisibility, induction, contradiction, bounding, construction; with secondary descriptors such as modulus band, descent-measure type, subgoal depth, and number of unresolved obligations.
4. **Fitness should be a vector, not a scalar.** Track at least:

   * structural validity,
   * goal-binding/faithfulness,
   * unresolved obligation count,
   * numeric-grounded obligations passed,
   * subgoal simplicity/decrease,
   * formalisation attempt status,
   * Layer-4 audit status where available.

   Do not collapse these too early into a single score, because “passed structure but failed goal-binding” should not be comparable to “on-goal but incomplete.”
5. **Use numeric grounding as the main evolutionary reward.** Let OpenEvolve mutate descent measures, modular splits, residue covers, witness tuples, and bounded construction schemas. This is where evolutionary search is most likely to help: it can explore many candidate constructions against an exact checker.
6. **Use LLM judges only inside constrained bands.** Elo/BT/PUCT can rank two candidates that both pass hard filters, but should not rescue candidates that fail goal-binding or numeric checks.
7. **AutoReason polishes the selected champion, one-way.** The refined output can proceed to formalisation, but should not be reintroduced into the diversity archive.
8. **Layer-4 audit is the certificate boundary.** Anything without Layer-4 success is reported as “candidate proof” or `PROVEN`, not elementary-certified.

## Additional recommendations not explicit enough in §9

**Add adversarial regression tests for evolutionary reward hacking.**
Use known traps like Ljunggren-style “relabel the hard theorem as an allowed method” to ensure evolved ledgers do not improve fitness by changing wording, weakening claims, or hiding obligations. The brief gives exactly this failure mode: the deterministic gate passed a ledger whose crux step hid a genuinely hard fact under an allowed method label. 

**Introduce an “obligation debt” metric.**
Every evolved step that asserts a nontrivial lemma should create typed obligations: finite case cover, descent decrease, divisibility implication, witness verification, or formalisation-required. Fitness should penalise unresolved obligation debt even if the ledger is structurally valid.

**Separate “search fitness” from “reporting status.”**
Search can use graded scores, but user-facing status should be categorical:

* `rejected`,
* `candidate/incomplete`,
* `soft-proven`,
* `formalized but not elementary`,
* `authoritative_elementary`.

This prevents score leakage into certification language.

**Treat formalisation failure as a diagnostic class, not just zero.**
Layer-4 success is decisive, but failure modes differ: unfaithful statement, Lean compile failure, heavy dependency, non-whitelisted axiom, timeout, missing Mathlib lemma, or formalizer error. Evolution should learn from safe diagnostic summaries, but certification should not blur these categories.

**Use cross-model judging deliberately.**
The brief warns about same-model judge monoculture. Since mutation already uses Sonnet/Opus, use a different family or deterministic checks for high-stakes review where possible; otherwise, judges may reward the same linguistic patterns the generators produce. 

## Risks and assumptions I cannot verify from the brief

I cannot verify that the Decomposer adapter already has the exact hooks needed for stuck-node triggering, goal-hash binding, or safe archive insertion; the brief says the adapter exists, but not whether the current implementation enforces all those conditions.

I also cannot verify the maturity of the formalizer. The Layer-4 cascade is conceptually right, but its practical value depends on how often good informal ledgers can be translated into faithful Lean statements. If formalisation is brittle, use Layer-4 as certification and occasional elite feedback, but do not over-penalise candidates merely because the formalizer failed.

Finally, the numeric-grounding mode is excellent but domain-specific. It will help most on modular arithmetic, finite search, descent, and explicit construction problems; it may be less useful for proof strategies whose key step is conceptual rather than expressible as a bounded integer obligation.

## Bottom line

The §9 recommendations are **mostly sound**, and the anti-patterns are exactly the right ones. The biggest adjustment is that **fitness hardening is not an enhancement; it is a prerequisite**. OpenEvolve should not be judged by whether it can produce ledgers that pass the current deterministic gate. It should be judged by whether it increases the number and diversity of candidates that survive goal-binding, numeric grounding, and eventually Layer-4 audit.

My preferred ordering is:

1. goal-binding/vacuity hard filter,
2. numeric-grounding evolution mode,
3. OpenEvolve fallback decomposer in the DAG,
4. strategy-keyed MAP-Elites + retrieval-seeded islands,
5. Elo/BT/PUCT ranking over hard-filtered elites,
6. AutoReason one-way polishing,
7. Layer-4 audit as rare elite feedback and final certificate.