---
name: Gauss's Eureka theorem (three triangular numbers)
type: problem
status: ready
problem_class: additive number theory (sums of polygonal numbers)
tier: T2
known_solution_available: true
include_standard_proof: false
---

# Gauss's Eureka Theorem — Every Number is a Sum of Three Triangular Numbers

## Statement

Every nonnegative integer `n` can be written as a sum of at most three triangular numbers,

```text
n = T_a + T_b + T_c,   where  T_k = k(k+1)/2  and  a, b, c >= 0.
```

## Variables and domain

`n` is a nonnegative integer; `a, b, c` are nonnegative integers; `T_k = k(k+1)/2` is the
`k`-th triangular number. "At most three" is captured by allowing any of `a, b, c` to be `0`
(since `T_0 = 0`).

## Target conclusion

For every `n >= 0` there exist nonnegative integers `a, b, c` with `n = T_a + T_b + T_c`.

## Standard equivalence (key reduction)

`n` is a sum of three triangular numbers **iff** `8n + 3` is a sum of three odd squares.
Multiplying `n = sum k_i(k_i+1)/2` by 8 and adding 3 gives
`8n + 3 = sum (2k_i + 1)^2`, a sum of three odd squares; conversely a representation
`8n + 3 = x^2 + y^2 + z^2` forces `x, y, z` all odd (residues mod 8), and each `2k+1 = x`
recovers a triangular number. So Gauss's Eureka theorem is equivalent to: **`8n + 3` is a
sum of three squares (necessarily three odd squares).**

## Allowed methods

See `allowed_inputs.md`. The classical proof route goes through the **Gauss–Legendre
three-squares theorem** (a number `m` is a sum of three squares iff `m` is not of the form
`4^a(8b + 7)`). Since `8n + 3 ≡ 3 (mod 8)` is never of that excluded form, three-squares
applies. **The three-squares theorem is NOT elementary by this repo's core toolkit**, so it is
admitted here as a **citable per-problem input** (see the trust boundary in
`allowed_inputs.md`). The benchmark task is then the **elementary reduction**: from
`8n + 3 = x^2 + y^2 + z^2` (granted by the citable theorem) derive the triangular
decomposition of `n`.

## Disallowed shortcuts

Do not re-derive or replace the three-squares input with non-elementary machinery beyond the
explicit citation (see the global denylist in `allowed_inputs.md`).

## Known related examples

Curated additive-NT methods in `knowledge/methods/`. Do not paste a full proof into this
folder (anti-contamination policy).

## Benchmark notes

- Source: C. F. Gauss, *Disquisitiones Arithmeticae* (1801) — the "EYPHKA! num = Δ + Δ + Δ"
  diary result; the three-squares theorem is Gauss–Legendre.
- Numeric validation (this session): every `n` with `0 <= n <= 2000` is a sum of three
  triangular numbers (no exceptions). The equivalence "`n` = sum of three triangulars ⇔
  `8n + 3` = sum of three odd squares" was checked for all `0 <= n <= 500` with zero
  mismatches.
- Tier: T2. Statement is true; elementarily provable **given** the citable three-squares
  input.
