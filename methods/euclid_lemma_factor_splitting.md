---
name: Euclid lemma factor splitting
type: method
status: draft
aliases: [coprime factor splitting, square splitting]
allowed_in_final_proofs: true
primary_problem_classes: [divisibility, square factors, product equations]
related_methods: [vierzahlensatz, cauchy_bezout_substitution]
---

# Euclid Lemma Factor Splitting

## Statement / Core idea

Use coprimality and Euclid's lemma to force factors of a product into controlled components, especially when a product is a square times a small coefficient.

## When to try this

- `gcd(A, B) = 1` and `AB` is a square or nearly a square.
- A small coefficient such as `2` or `3` can land in one factor.
- Product parameterization has exposed coprime pieces.

## Pattern signatures

- `AB = n z^2` with `gcd(A, B) = 1`.
- Adjacent factors produce coprimality.
- Need to split prime powers without invoking unique factorization in other rings.

## Preconditions and normalizations

- Prove gcd first.
- Factor the small coefficient explicitly.
- Handle signs and zero before square conclusions.

## Canonical transformation

From `gcd(A, B) = 1` and `AB = n z^2`, assign square parts to `A` and `B`, with finite cases for prime factors of `n`.

## Downstream moves

- Case split by the small coefficient.
- Substitute square parameters.
- Use congruences to eliminate cases.

## Worked examples

- In the Vierzahlensatz seed, `3d^2 = pq` and `gcd(p, q) = 1` gives two cases for where the factor `3` lands.

## Common failure modes

- Square splitting before proving coprimality.
- Forgetting negative factors.
- Hiding a large prime-factor argument without stating it.

## Lean-relevant lemmas

- Euclid's lemma for integers.
- Coprime product square splitting.
- Finite case split for a squarefree coefficient.

## Search prompts for agents

- Which product is a square up to a small coefficient?
- Are the factors coprime?
- Where can each prime in the small coefficient land?

## Evaluation hooks

- Record the gcd proof and each coefficient case.
