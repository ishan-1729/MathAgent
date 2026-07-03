---
name: AMC 12 2000 Problem 1 (maximize a sum under a fixed product)
type: problem
status: ready
problem_class: Elementary factorization / extremal sum
tier: calibration
statement: "Let i, m, o be distinct positive integers with i*m*o = 2001. Prove that the maximum possible value of i + m + o is 671."
known_solution_available: true
include_standard_proof: false
---

# AMC 12 2000, Problem 1

## Statement

Let `i`, `m`, `o` be **distinct** positive integers such that

```text
i * m * o = 2001.
```

Prove that the maximum possible value of `i + m + o` is `671`.

## Variables and domain

`i, m, o` are distinct positive integers whose product is `2001`. The order of the three
values is irrelevant to the sum, so "distinct positive integers with product 2001" ranges over
the unordered factor triples of `2001` into three different factors.

## Target conclusion

Over all such triples, `max (i + m + o) = 671`, attained by the triple `{1, 3, 667}`.

## Allowed methods

See `allowed_inputs.md`. This is a standard-toolkit calibration problem: factor `2001`, enumerate
the (finitely many) unordered triples of distinct positive divisors whose product is `2001`, and
compare their sums. No per-problem external input is required.

The elementary idea: `2001 = 3 * 23 * 29`, so a divisor triple is a partition of the prime set
`{3, 23, 29}` (with the empty part giving the factor `1`) into the three slots. To maximize the
sum with a fixed product, push the factorization toward the extreme `{1, small, large}`: the
largest possible single factor is `3 * 23 * 29 / (1 * 3) = 667`, giving `{1, 3, 667}` and sum
`1 + 3 + 667 = 671`. Any other distinct-triple assignment yields a strictly smaller sum.

## Disallowed shortcuts

Do not invoke non-elementary machinery (see the global denylist in `allowed_inputs.md`). The
enumeration is over an explicit finite set of divisors, so the bounding argument is fully
elementary — no external theorem is needed or permitted.

## Known related examples

Standard "fix the product, maximize the sum" extremal reasoning. Do not paste a full proof into
this folder (anti-contamination policy).

## Benchmark notes

- Source: 2000 AMC 12, Problem 1.
- Numeric validation (this session): `2001 = 3 * 23 * 29` (sympy `factorint`). Enumerated every
  unordered triple of distinct positive divisors with product `2001`; the maximum sum is `671`,
  attained uniquely by `{1, 3, 667}`.
- Tier: **calibration** — a short, fully elementary enumeration; a capability check, not research.
