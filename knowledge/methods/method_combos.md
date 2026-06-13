---
name: Method combinations
type: method
status: draft
aliases: [combo patterns, staged transformations]
allowed_in_final_proofs: true
primary_problem_classes: [multi-stage Diophantine proofs]
related_methods: [vierzahlensatz, squeeze_sequence, pell_equation, cauchy_bezout_substitution, sum_to_product, arithmetic_progressions]
---

# Method Combinations

## Statement / Core idea

Some proofs emerge only after two or more elementary methods are staged. These are combo patterns, not proven recipes.

## When to try this

- One method exposes structure but does not finish the problem.
- A parameterization leaves a discriminant, recurrence, or congruence obstruction.
- Multiple weak signals point to the same small set of variables.

## Pattern signatures

- Product equality followed by square splitting.
- Quadratic reduction followed by sequence bounds.
- Centered progression followed by product factorization.

## Preconditions and normalizations

- Record each method loaded.
- Verify every transition independently.
- Keep the final proof elementary.

## Canonical transformation

Apply a first method to expose hidden structure, then choose a second method based on the new obstruction.

## Downstream moves

- Vierzahlensatz -> gcd/square splitting -> squeeze/discriminant.
- Vierzahlensatz -> Cauchy/Bezout substitution.
- Pell reduction -> squeeze on recurrence sequence.
- Sum-to-product -> arithmetic progression normalization.

## Worked examples

- Placeholder: use this file as a map to method-specific examples, not as a proof source.

## Common failure modes

- Treating a combo as automatically valid.
- Losing track of variables across transformations.
- Mixing methods without recording which facts are established.

## Lean-relevant lemmas

- Interface lemmas between parameterizations and gcd facts.
- Discriminant-square lemmas after substitution.
- Recurrence bound lemmas after Pell reduction.

## Search prompts for agents

- What structure did the first method expose?
- What is now the bottleneck?
- Which second method attacks that bottleneck?

## Evaluation hooks

- Record whether the combo produced a reusable staged pattern.
