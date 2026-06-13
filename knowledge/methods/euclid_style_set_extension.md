---
name: Euclid-style set extension
type: method
status: draft
aliases: [Euclid construction, finite set contradiction]
allowed_in_final_proofs: true
primary_problem_classes: [divisibility, prime construction, finite obstruction]
related_methods: [arithmetic_progressions, sum_to_product]
---

# Euclid-Style Set Extension

## Statement / Core idea

Assume a finite set captures all objects of a type, then build a new integer from that set whose divisibility properties force an object outside it.

## When to try this

- A theorem asserts infinitude or nonexistence of a finite obstruction set.
- Products over a finite set appear.
- A constructed number is congruent to `1` or `-1` modulo each listed factor.

## Pattern signatures

- `N = k * product(...) +/- 1`.
- Need to extend a finite list.
- Pairwise coprimality is central.

## Preconditions and normalizations

- Specify the finite set.
- Prove the constructed number is in the target class.
- Exclude trivial divisors.

## Canonical transformation

Form a Euclid number from the finite set and analyze its divisors or residues.

## Downstream moves

- Coprimality contradiction.
- Arithmetic progression residue control.
- Descent on minimal missing element.

## Worked examples

- Placeholder: prime-style finite set extension arguments.

## Common failure modes

- Constructed number is not in the required class.
- A divisor exists but does not preserve the target property.
- The argument proves only nondivisibility, not the needed theorem.

## Lean-relevant lemmas

- Product congruence modulo listed factors.
- Divisor not in finite set.
- Positivity of constructed number.

## Search prompts for agents

- What finite set is being contradicted?
- Can a product plus or minus one preserve the target class?
- Which divisor must be new?

## Evaluation hooks

- Record the finite set and extension construction.
