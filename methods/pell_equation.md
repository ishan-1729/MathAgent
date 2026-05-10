---
name: Pell equation reduction
type: method
status: draft
aliases: [Pell reduction, Pell recurrence]
allowed_in_final_proofs: true
primary_problem_classes: [quadratic Diophantine equations, recurrences]
related_methods: [squeeze_sequence, quadratic_to_pell_collapse]
---

# Pell Equation Reduction

## Statement / Core idea

Reduce a quadratic Diophantine condition to a Pell-type equation and use elementary recurrence structure rather than algebraic-number-theory machinery.

## When to try this

- A quadratic in one variable has square discriminant.
- Completing a square gives `X^2 - D Y^2 = N`.
- A recurrence family can be compared modulo small bases.

## Pattern signatures

- `ax^2 + bx + c = y^2`.
- Norm-looking forms that can be handled by recurrence.
- Fixed nonsquare `D` with variable `X, Y`.

## Preconditions and normalizations

- State the allowed elementary Pell facts.
- Avoid final reliance on units in algebraic integer rings.
- Check signs and small exceptional solutions.

## Canonical transformation

Clear denominators and complete squares to reach `X^2 - D Y^2 = N`, then derive or cite an elementary recurrence.

## Downstream moves

- Squeeze on recurrence terms.
- Congruence filters on Pell sequences.
- Descent using a smaller solution from recurrence identities.

## Worked examples

- Placeholder: connect quadratic discriminant square conditions to recurrence filters.

## Common failure modes

- Smuggling in UFD or unit-group arguments.
- Missing exceptional small solutions.
- Treating every generalized Pell equation as solved without a finite elementary reduction.

## Lean-relevant lemmas

- Square completion equivalence.
- Recurrence definitions and basic invariants.
- Modular periodicity for recurrence terms.

## Search prompts for agents

- Does a square discriminant appear?
- Can a quadratic be normalized to `X^2 - D Y^2 = N`?
- Which elementary recurrence facts are enough?

## Evaluation hooks

- Record whether the Pell step is elementary and finitely bounded.
