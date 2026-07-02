---
name: Hardy & Wright Theorem 120 (Bernoulli numbers mod 1 for primes 3n+1)
type: problem
status: ready
problem_class: Bernoulli-number congruence (von Staudt corollary)
tier: calibration
statement: "In Hardy & Wright's Bernoulli indexing (B_1 = 1/6, B_2 = 1/30, ..., so B_k = |B_{2k}| in modern even-index notation), prove that for every prime k of the form 3n + 1 the rational B_k - 1/6 is an integer, i.e. B_k = 1/6 (mod 1)."
known_solution_available: true
include_standard_proof: false
---

# Hardy & Wright, Theorem 120

## Statement (verbatim, with edition pinned)

> **THEOREM 120.** *If `k` is a prime of the form `3n + 1`, then*
> `B_k ≡ 1/6 (mod 1)`.

Source: G. H. Hardy and E. M. Wright, *An Introduction to the Theory of Numbers*,
Chapter VII ("General Properties of Congruences"), §7.10, Theorem 120, p. 93. The theorem
number and statement are valid in the **4th, 5th, and 6th editions** (see Benchmark notes for
the numbering-stability evidence). The result is due to R. Rado (J. London Math. Soc., 1934).

**Notation caveat (load-bearing).** This is **Hardy & Wright's Bernoulli indexing**, in which
`B_1 = 1/6, B_2 = 1/30, B_3 = 1/42, ...` — their `B_k` is the absolute value of the modern
`B_{2k}`. In modern even-index notation the claim reads: for a prime `k ≡ 1 (mod 3)`, the
fractional part of `|B_{2k}|` is `1/6`. Do not transplant `B_k ≡ 1/6` into a modern-notation
context without translating the index.

## Variables and domain

`k` is a prime with `k ≡ 1 (mod 3)`; `B_k` is the `k`-th Bernoulli number in Hardy & Wright's
indexing (a rational number); `≡ (mod 1)` means the two rationals differ by an integer.

## Target conclusion

For every prime `k` of the form `3n + 1`, the rational `B_k - 1/6` is an integer.

## Allowed methods

See `allowed_inputs.md`. In Hardy & Wright the result is a short corollary of **von Staudt's
theorem** (their Theorem 118, §7.9–7.10) on the fractional part of Bernoulli numbers; von
Staudt's theorem is admitted as a citable per-problem input (it also has a fully elementary
proof, given in H&W §7.9–7.10 via sums of powers and congruences).

## Disallowed shortcuts

No analytic machinery (zeta values, Euler products) as final steps — see the global denylist
in `allowed_inputs.md`.

## Known related examples

Neighbouring results in H&W: Theorem 118 = von Staudt's theorem; Theorem 119 = the
sums-of-powers lemma used in its proof; Theorem 121 (§8.1, p. 95) = the Chinese Remainder
Theorem. Do not paste a full proof into this folder (anti-contamination policy).

## Benchmark notes

- **Statement provenance:** read verbatim from a scan of the 4th edition (1960), p. 93
  (https://blngcc.wordpress.com/wp-content/uploads/2008/11/hardy-wright-theory_of_numbers.pdf).
  Cross-checked against the independent theorem index at https://t5k.org/notes/hw_index.html
  ("valid for both the 4th and 5th editions"), which places Theorem 121 = Chinese Remainder
  Theorem at p. 95, immediately after. The 6th edition (rev. Heath-Brown & Silverman, 2008)
  kept the fifth-edition text and pagination unchanged (adding only end-notes and one new
  chapter), so the numbering carries over; this last step rests on the documented revision
  policy, not a direct 6th-edition page scan.
- **Numeric sanity check (this session, sympy):** for every prime `k ≡ 1 (mod 3)` with
  `k < 80` — i.e. `k ∈ {7, 13, 19, 31, 37, 43, 61, 67, 73, 79}` — the fractional part of
  `|B_{2k}|` (modern indexing = H&W `B_k`) equals exactly `1/6`. Spot values:
  H&W `B_7 = 7/6`, `B_13 = 8553103/6`.
- Tier: **calibration** (PLAN §8.1) — a known short textbook elementary proof exists (H&W
  §7.10); this is a capability check, not research.
