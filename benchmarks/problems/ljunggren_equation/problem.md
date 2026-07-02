---
name: Ljunggren's equation x^2 + 1 = 2 y^4
type: problem
status: ready
problem_class: Diophantine equation (quartic)
tier: T3-hard
known_solution_available: true
include_standard_proof: false
---

# Ljunggren's Equation `X^2 + 1 = 2 Y^4`

## Statement

Determine all solutions in positive integers of

```text
X^2 + 1 = 2 Y^4.
```

## Variables and domain

`X, Y` are positive integers.

## Target conclusion

The only solutions in positive integers are

```text
(X, Y) = (1, 1)  and  (X, Y) = (239, 13).
```

## Allowed methods

See `allowed_inputs.md`. Note the **per-problem whitelist**: quartic-residue /
biquadratic-character machinery is admitted for this problem (the known elementary proofs
require it). Baker's theory and algebraic-number-field machinery remain banned.

## Disallowed shortcuts

Do not close the problem by Baker's theory of linear forms in logarithms, by working in a
number field / with units of `Z[sqrt(2)]` as a black box, or by any other global-denylist
method except the explicitly whitelisted quartic-residue tools (see `allowed_inputs.md`).

## Known related examples

Related to Pell / negative-Pell structure (`X^2 - 2 Y'^2 = -1` with `Y' = Y^2`). The Pell
fundamental-solution theorem is a citable elementary fact (PLAN §2.1). Do not paste a full
proof into this folder (anti-contamination policy).

## Benchmark notes

- Source: W. Ljunggren, "Zur Theorie der Gleichung `x^2 + 1 = D y^4`" (1942); the case
  `D = 2` has exactly the two positive solutions above. This is the standard pinned form
  (PLAN §8.1).
- Numeric validation (this session): searched `1 <= X <= 10^6`; the only positive solutions
  are `(1, 1)` and `(239, 13)`. Verified `1^2 + 1 = 2·1^4 = 2` and
  `239^2 + 1 = 57122 = 2·13^4`.
- Tier: T3-hard. The known elementary proofs are **long** (a book-length chapter — e.g. the
  quartic-residue treatment in the literature) and use higher-reciprocity machinery, which is
  why quartic residues are whitelisted here and this sits in its own hard tier rather than
  "a bit harder than T2".
