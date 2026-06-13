---
name: Quadratic to Pell collapse
type: library_identity
status: draft
source: TODO
related_methods: [pell_equation, squeeze_sequence]
problem_classes: [quadratic Diophantine equations]
---

# Quadratic to Pell Collapse

## Identity / lemma

Certain quadratic Diophantine constraints with square discriminant can be normalized into `X^2 - D Y^2 = N`.

## Why it is high-impact

It converts a loose quadratic condition into a structured recurrence or congruence problem.

## Trigger pattern

- A quadratic in one variable has discriminant forced to be a square.
- Completing a square leaves one fixed nonsquare coefficient.

## Collapse effect

Moves the search from arbitrary quadratic algebra to a Pell-type equation with explicit elementary recurrence obligations.

## Proof sketch or verification status

Draft. Each use must show the exact square completion and domain equivalence.

## Example applications

- Discriminant-square checks after substitutions.

## Lean lemma target

Equivalence between the original quadratic statement and the normalized Pell-type equation.

## Notes / provenance

Add concrete identities only after verification.
