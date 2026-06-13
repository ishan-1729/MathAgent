---
name: Sum-to-product identities
type: method
status: draft
aliases: [factorized sums, product normalization]
allowed_in_final_proofs: true
primary_problem_classes: [polynomial identities, arithmetic progressions]
related_methods: [arithmetic_progressions, vierzahlensatz]
---

# Sum-to-Product Identities

## Statement / Core idea

Convert sums or differences into products so that divisibility, coprimality, or Vierzahlensatz-style parameterization becomes available.

## When to try this

- A sum of powers hides a factorization.
- Difference of two structured values appears.
- Arithmetic progression terms have symmetric offsets.

## Pattern signatures

- `x^n - y^n`.
- Symmetric pairs like `(a - d)` and `(a + d)`.
- Difference of squares or cubes after normalization.

## Preconditions and normalizations

- Confirm the identity is nontrivial enough to load from library or method context.
- Track parity when halving symmetric expressions.
- Preserve integer variables after centering.

## Canonical transformation

Rewrite the sum or difference as a product, then analyze gcds between the product factors.

## Downstream moves

- Apply Euclid factor splitting.
- Expose `AB = CD` for Vierzahlensatz.
- Normalize arithmetic progressions around a center.

## Worked examples

- Placeholder: center an arithmetic progression, then factor paired sums.

## Common failure modes

- Introducing half-integers unintentionally.
- Using only obvious algebra without gaining structure.
- Missing gcd interactions between new factors.

## Lean-relevant lemmas

- Named factorization identities.
- Parity-preserving centered substitutions.
- Gcd of sum/difference factors.

## Search prompts for agents

- Can a sum be recentered into a difference?
- Does a known identity turn this into a product?
- What gcd data appears after factorization?

## Evaluation hooks

- Record the identity used and the new product structure.
