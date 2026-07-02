---
name: IMO 1988 Problem 6 (Vieta jumping / finite descent)
type: problem
status: ready
problem_class: Diophantine divisibility (extremal / finite descent)
tier: T1
statement: "Let a and b be positive integers such that ab + 1 divides a^2 + b^2. Prove that (a^2 + b^2) / (ab + 1) is a perfect square."
known_solution_available: true
include_standard_proof: false
---

# IMO 1988 Problem 6

## Statement

Let `a` and `b` be positive integers such that `ab + 1` divides `a^2 + b^2`.
Prove that

```text
(a^2 + b^2) / (ab + 1)
```

is a perfect square.

## Variables and domain

`a, b` are positive integers with the divisibility hypothesis `ab + 1 | a^2 + b^2`.

## Target conclusion

For every such pair `(a, b)`, the integer quotient `k = (a^2 + b^2)/(ab + 1)` is a
perfect square. (Concretely, whenever the hypothesis holds one finds `k = gcd(a, b)^2`;
the benchmark target is only the "perfect square" claim.)

## Allowed methods

See `allowed_inputs.md`. This is the canonical Vieta-jumping / finite-descent problem:
fix the quotient `k`, treat `a^2 - k·b·a + (b^2 - k) = 0` as a quadratic in `a`, and use
the second Vieta root to descend to a smaller solution, contradicting a minimal choice.

## Disallowed shortcuts

Do not invoke non-elementary machinery (see the global denylist in `allowed_inputs.md`).
No special external theorem is needed or permitted beyond the standard integer toolkit.

## Known related examples

The extremal/descent method is curated in `knowledge/methods/`. Do not paste a full proof
into this folder (anti-contamination policy).

## Benchmark notes

- Source: International Mathematical Olympiad 1988, Problem 6 (a canonical Vieta-jumping
  problem).
- Numeric validation (this session): searched all pairs `1 <= a, b <= 200`; every pair
  satisfying `ab + 1 | a^2 + b^2` yields a perfect-square quotient (0 non-square quotients).
  Sample `(a, b, k)`: `(1,1,1)`, `(2,8,4)`, `(3,27,9)`, `(4,64,16)`, `(5,125,25)`.
- Tier: T1 (olympiad NT). The statement is true and elementarily provable by finite descent.
