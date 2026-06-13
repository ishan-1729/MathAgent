---
name: Vieta jumping
type: method
status: draft
aliases: [root jumping, quadratic descent]
allowed_in_final_proofs: true
primary_problem_classes: [quadratic Diophantine equations, finite descent]
related_methods: [finite elementary descent, invariant_substitution]
---

# Vieta Jumping

## Statement / Core idea

View a Diophantine equation as a quadratic in one variable. If one integer root exists, Vieta relations may produce another integer root, often smaller, giving descent.

## When to try this

- The equation is quadratic in one variable after fixing the others.
- Root sum and product are integral.
- A minimal counterexample can be chosen.

## Pattern signatures

- Symmetric or near-symmetric quadratic equations.
- Positive variables with a natural size ordering.
- A discriminant condition is already forced.

## Preconditions and normalizations

- Choose a minimal positive solution.
- Prove the jumped root is integer and in the valid domain.
- Prove the jumped root is strictly smaller and nonnegative.

## Canonical transformation

Treat the equation as `Az^2 + Bz + C = 0`; if `z` is one root, use Vieta to define the companion root.

## Downstream moves

- Contradict minimality.
- Combine with invariant substitutions.
- Use modular filters to handle boundary cases.

## Worked examples

- Placeholder: standard olympiad-style quadratic descent patterns.

## Common failure modes

- Companion root is not shown positive.
- Minimality parameter is poorly chosen.
- Boundary root is accidentally the same root.

## Lean-relevant lemmas

- Vieta relation for integer quadratic roots.
- Positivity and strict descent proof.
- Minimal counterexample contradiction.

## Search prompts for agents

- Which variable makes the equation quadratic?
- What is the companion root?
- Does the companion root preserve the equation and reduce size?

## Evaluation hooks

- Record the descent measure and domain preservation proof.
