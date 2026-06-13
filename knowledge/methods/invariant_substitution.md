---
name: Invariant substitution
type: method
status: draft
aliases: [descent invariant, conserved substitution]
allowed_in_final_proofs: true
primary_problem_classes: [finite descent, parametric Diophantine systems]
related_methods: [vieta_jumping, cauchy_bezout_substitution]
---

# Invariant Substitution

## Statement / Core idea

Substitute variables in a way that preserves a key expression, congruence class, or product while changing the size or shape of the solution.

## When to try this

- A transformation seems to preserve the equation.
- A descent needs a smaller solution with the same invariant.
- A symmetry is hidden by the current variables.

## Pattern signatures

- Expressions unchanged under swapping, shifting, or replacing a variable by a companion.
- Repeated occurrence of the same quadratic or product form.
- A candidate map lowers one variable while preserving integrality.

## Preconditions and normalizations

- State the invariant explicitly.
- Prove the substitution is reversible or preserves the needed direction.
- Track domain restrictions.

## Canonical transformation

Define a new tuple from the old tuple and verify the invariant equation term by term.

## Downstream moves

- Finite descent.
- Reduction to a normalized residue class.
- Combination with Vieta jumping.

## Worked examples

- Placeholder: use with companion roots or shifted coprime coordinates.

## Common failure modes

- The substitution only works over rationals.
- The invariant is preserved but positivity is lost.
- The descent measure is not strictly reduced.

## Lean-relevant lemmas

- Substitution preserves equation.
- Substitution preserves integrality and domain.
- Descent measure decreases.

## Search prompts for agents

- What expression should remain invariant?
- Does a natural symmetry suggest a new solution?
- Can the transformed solution be smaller?

## Evaluation hooks

- Record the invariant and the descent measure.
