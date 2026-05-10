---
name: Cauchy/Bezout substitution
type: method
status: draft
aliases: [Bezout parameter substitution, coprime linear substitution]
allowed_in_final_proofs: true
primary_problem_classes: [coprime Diophantine systems, divisibility constraints]
related_methods: [vierzahlensatz, euclid_lemma_factor_splitting]
---

# Cauchy/Bezout Substitution

## Statement / Core idea

Use coprimality to introduce Bezout relations or substitute variables through linear combinations that preserve divisibility data.

## When to try this

- Two parameters are coprime and appear in several linear or bilinear relations.
- A proof needs to eliminate one variable while preserving integer constraints.
- A product parameterization leaves a stubborn gcd condition.

## Pattern signatures

- `gcd(m, n) = 1` plus divisibility by a linear expression.
- Need to solve `mx + ny = k`.
- A parameter can be shifted by multiples of a coprime partner.

## Preconditions and normalizations

- Prove the gcd statement first.
- Track sign conventions for Bezout coefficients.
- Avoid introducing rational variables.

## Canonical transformation

Choose integers `u, v` with `mu + nv = 1`, then rewrite target expressions in the induced coprime coordinate system.

## Downstream moves

- Eliminate a variable.
- Convert divisibility into congruence.
- Pair with Vierzahlensatz after product splitting.

## Worked examples

- Placeholder: apply after `gcd(p, q) = 1` is obtained from adjacent factors.

## Common failure modes

- Treating Bezout coefficients as positive without proof.
- Losing equivalence after substitution.
- Forgetting to map solutions back to original variables.

## Lean-relevant lemmas

- Bezout identity for coprime integers.
- Divisibility equivalences after substitution.
- Round-trip substitution lemma.

## Search prompts for agents

- Which coprime pair controls the equation?
- Can one variable be replaced by a Bezout combination?
- Does the substitution make a congruence immediate?

## Evaluation hooks

- Record the exact coprime pair and the equivalence preserved.
