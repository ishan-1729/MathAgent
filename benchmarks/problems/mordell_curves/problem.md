---
name: Mordell curves with an elementary obstruction (vetted specific k)
type: problem
status: ready
problem_class: Diophantine equation (Mordell equation, specific k)
tier: T2
statement: "Prove that the Mordell equation y^2 = x^3 + 7 has no solutions in integers x, y. (Primary pinned case; the folder also lists further vetted k with elementary non-existence proofs.)"
known_solution_available: true
include_standard_proof: false
---

# Mordell Curves `y^2 = x^3 + k` — Vetted Specific `k` With an Elementary Obstruction

## Scope (read first)

This folder is a **vetted list of specific `k`** for which `y^2 = x^3 + k` has **no integer
solutions** and the non-existence has a **purely elementary** (congruence / elementary
factorization) proof. It is **NOT** the generic Mordell equation.

> **General `y^2 = x^3 + k` is out of scope by PLAN §2.** For general `k` the equation is
> not elementarily solvable — determining the integer points needs Baker's theory of linear
> forms in logarithms or elliptic-curve descent, both on the global denylist. Only the
> specific `k` listed below (each with a recorded elementary mechanism) are in scope.

## Vetted list

### k = 7  (primary, pinned)

**Statement.** The equation

```text
y^2 = x^3 + 7
```

has no solutions in integers `x, y`.

**Elementary mechanism (recorded, not proved here).** If `x` were even then
`y^2 ≡ 7 ≡ 3 (mod 8)`, impossible for a square; so `x` is odd. Add 1 to both sides:
`y^2 + 1 = x^3 + 8 = (x + 2)(x^2 - 2x + 4)`. For odd `x`, `x^2 - 2x + 4 ≡ 3 (mod 4)`, so it has
a prime factor `p ≡ 3 (mod 4)`; that `p` divides `y^2 + 1`, forcing `-1` to be a quadratic
residue mod `p`, which is impossible for `p ≡ 3 (mod 4)`. Contradiction. (Congruence +
elementary factorization only — no ANT, no elliptic descent.)

### Additional vetted `k` (source-verified + numerically confirmed)

Each entry below has (i) an elementary mechanism documented in a reliable source and (ii)
numeric confirmation of no integer solutions with `|x|, |y| <= 10^4` (this session). The
uniform engine: reduce mod 8 / mod 4 to pin the parity and residue of `x`; rewrite
`y^2 ± c = x^3 ± a^3` and factor the cubic sum/difference over ℤ; the quadratic factor lands
in a residue class forcing a prime `p` (with `p ≡ 3 mod 4`, or `p ≡ ±3 mod 8`) to divide it;
then `-1` (resp. `2`, `-2`) would have to be a quadratic residue mod `p` — impossible. The
only non-basic ingredient is the Legendre-symbol / Euler-criterion test for when `-1, 2, -2`
are squares mod `p`, which is standard elementary number theory (quadratic reciprocity and its
supplements are ALLOWED, PLAN §2.1).

| k | Elementary mechanism (sketch) | QR criterion |
|---|---|---|
| 11 | `x ≡ 1 (mod 4)`; `y^2 + 16 = (x+3)(x^2-3x+9)`, quadratic factor `≡ 3 (mod 4)` ⟹ prime `p ≡ 3 (mod 4)` with `-1` a QR. | `-1` |
| 45 | `x` odd, `x ≡ 3 (mod 4)`, `3 ∤ y`; split `x ≡ 3/7 (mod 8)`; factor `≡ 3` or `5 (mod 8)` ⟹ `p ≡ ±3 (mod 8)` needs `2` a QR. | `2` |
| 6 | `x` odd, `x ≡ 3 (mod 8)`; `y^2 + 2 = (x+2)(x^2-2x+4)`, factor `≡ 7 (mod 8)` ⟹ needs `-2` a QR (`p ≡ 1,3 mod 8`), contradiction. | `-2` |
| 46 | `x` odd, `x ≡ 3 (mod 8)`; `y^2 + 18 = (x+4)(x^2-4x+16)`, factors `≡ 7, 5 (mod 8)` ⟹ `-2` a QR fails. | `-2` |
| -5 | `x ≡ 1 (mod 4)`; `y^2 + 4 = (x-1)(x^2+x+1)`, factor `≡ 3 (mod 4)` ⟹ `p ≡ 3 (mod 4)`, `-1` a QR. (Use the mod-4 proof; the `ℤ[√-5]` argument in the literature is invalid.) | `-1` |
| -6 | `x` odd, `x ≡ 7 (mod 8)`; `y^2 - 2 = (x-2)(x^2+2x+4)`, factor `≡ 3 (mod 8)` ⟹ `p ≡ ±3 (mod 8)` needs `2` a QR. | `2` |
| -24 | Two-stage: mod-4 (`-1` criterion) forces `x, y` even; descend to `2 y'^2 = x'^3 - 3`, then `y'^2 ≡ -2 (mod p)` with a mod-8 count. | `-1`, `-2` |
| -3 | `x` odd, `x ≡ 3 (mod 4)`; `y^2 + 4 = (x+1)(x^2-x+1)`, factor `≡ 3 (mod 4)` ⟹ `p ≡ 3 (mod 4)`, `-1` a QR. | `-1` |
| -9 | `x` odd, `x ≡ 1 (mod 4)`; `y^2 + 1 = (x-2)(x^2+2x+4)`, factor `≡ 3 (mod 4)` ⟹ `p ≡ 3 (mod 4)` (handle `p = 3` via `3 ∤` descent). | `-1` |
| -12 | `x` odd, `x ≡ 1 (mod 4)`; `y^2 + 4 = (x-2)(x^2+2x+4)`, factor `≡ 3 (mod 4)` ⟹ `p ≡ 3 (mod 4)`. | `-1` |

**Sources.** Keith Conrad, *Examples of Mordell's Equation*
(https://kconrad.math.uconn.edu/blurbs/gradnumthy/mordelleqn1.pdf), Section 2 "Examples
without Solutions" — covers `k = 7, -5, 11, -6, 45, 6, 46, -24` with elementary proofs and
poses `-3, -9, -12` as exercises; Section 3 (the non-elementary contrast cases, which use
unique factorization in `ℤ[i]`, `ℤ[√-2]`, etc.) is explicitly excluded here. Cleo Alexa,
*Solutions of Mordell's Equation* (UC Berkeley DRP, 2025,
https://wp.math.berkeley.edu/drp/wp-content/uploads/sites/18/2025/05/2025_Spring_Alexa.pdf) —
full elementary proofs for `k = -3, -9, -12`.

**Deliberately excluded as NON-elementary** (same Conrad document, Section 3): `k = 1, -1, -2,
-4, -8, 16, -26, -64` and similar — these use unique factorization in quadratic rings
(`ℤ[i]`, `ℤ[√-2]`) or Delaunay–Nagell descent, which are on the global denylist.

## Variables and domain

`x, y` are integers; `k` ranges over the vetted list above.

## Target conclusion

For each vetted `k`, the equation `y^2 = x^3 + k` has **no** integer solutions.

## Allowed methods

See `allowed_inputs.md` (elementary congruences and factorization only).

## Disallowed shortcuts

Baker's theory, elliptic-curve descent, algebraic number theory, class groups — see the
global denylist in `allowed_inputs.md`. These are exactly what the generic problem would
require, and are banned.

## Known related examples

Curated elementary NT methods in `knowledge/methods/`. Do not paste a full proof into this
folder (anti-contamination policy).

## Benchmark notes

- Sources: Keith Conrad, *Examples of Mordell's Equation* (canonical, Section 2); Cleo Alexa,
  UC Berkeley DRP 2025 (independent elementary proofs for `k = -3, -9, -12`). URLs in the
  "Additional vetted `k`" section above.
- Numeric validation (this session): searched all `|x| <= 10^4` (with `|y| <= 10^4`); every
  vetted `k ∈ {7, 11, 45, 6, 46, -5, -6, -24, -3, -9, -12}` has **no** integer solutions in
  that range (empty solution set), consistent with non-existence.
- Tier: T2.
- **Entry format for future extensions:** append a `k` here **only** with (i) a verified
  elementary mechanism (congruence + elementary factorization, no Baker / elliptic-descent /
  ANT) from a reliable source and (ii) numeric confirmation of no solutions with
  `|x|, |y| <= 10^4`. Do not add a `k` whose only known proof uses unique factorization in a
  quadratic ring (Conrad Section 3) — those are non-elementary and out of scope.
