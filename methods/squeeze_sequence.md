---
name: Squeeze sequence
type: method
status: draft
aliases: [sandwich sequence, adjacent bounds]
allowed_in_final_proofs: true
primary_problem_classes: [Diophantine inequalities, recurrence constraints]
related_methods: [pell_equation, vierzahlensatz]
---

# Squeeze Sequence

## Statement / Core idea

Construct adjacent bounds or a monotone sequence that traps an integer-valued expression until equality or contradiction is forced.

## When to try this

- A variable is known to lie between two consecutive squares, cubes, or recurrence terms.
- A Pell or recurrence reduction gives ordered candidate values.
- A discriminant must be an integer square but falls between squares.

## Pattern signatures

- `n^2 < expression < (n + 1)^2`.
- Consecutive recurrence terms bound a parameter.
- A descent step produces a smaller member of the same ordered family.

## Preconditions and normalizations

- Establish positivity and monotonicity on the relevant range.
- Make endpoints explicit.
- Confirm that the squeezed quantity is integral.

## Canonical transformation

Rewrite the target expression into a form with natural adjacent bounds, then compare against neighboring terms.

## Downstream moves

- Convert failed squarehood into contradiction.
- Combine with Pell recurrences.
- Use a squeezed discriminant to block a quadratic substitution.

## Worked examples

- Placeholder: use after Vierzahlensatz when a parameter must make a discriminant square.

## Common failure modes

- Bounds are not strict enough.
- Monotonicity is assumed outside its valid range.
- Endpoint equality cases are skipped.

## Lean-relevant lemmas

- Integer between consecutive squares is not a square.
- Monotone recurrence comparison lemmas.
- Explicit endpoint equality checks.

## Search prompts for agents

- What quantity must be a square or cube?
- Can it be bounded between adjacent candidates?
- Does a recurrence give a natural neighboring pair?

## Evaluation hooks

- Record the squeezed quantity and the exact adjacent bounds.
