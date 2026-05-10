---
name: Arithmetic progressions
type: method
status: draft
aliases: [AP normalization, centered progression]
allowed_in_final_proofs: true
primary_problem_classes: [integer sequences, power sums, polygonal numbers]
related_methods: [sum_to_product, euclid_style_set_extension]
---

# Arithmetic Progressions

## Statement / Core idea

Normalize variables that lie in arithmetic progression, often by centering them around a midpoint and exploiting parity or symmetric factorization.

## When to try this

- Several terms have equal gaps.
- A sum or product over a progression appears.
- The problem mentions consecutive or evenly spaced integers.

## Pattern signatures

- `x, x + d, x + 2d`.
- Consecutive products.
- Symmetric offsets `m - kd, ..., m + kd`.

## Preconditions and normalizations

- Decide whether the center is integral or half-integral.
- Track gcd of first term and common difference.
- Preserve positivity if required.

## Canonical transformation

Replace progression terms by a center and common difference, then use parity and sum-to-product identities.

## Downstream moves

- Sum-to-product factorization.
- Modular contradiction.
- Reduction to a Pell-type quadratic.

## Worked examples

- Placeholder: centered three-term and four-term progressions.

## Common failure modes

- Losing integer parity after centering.
- Ignoring common factor normalization.
- Treating endpoints asymmetrically after a symmetric substitution.

## Lean-relevant lemmas

- Equivalence between progression and centered form.
- Parity condition for integral centers.
- Basic gcd normalization for progression terms.

## Search prompts for agents

- Can the variables be centered?
- What parity condition does the center impose?
- Which product or sum factors after centering?

## Evaluation hooks

- Record the progression normalization and parity constraints.
