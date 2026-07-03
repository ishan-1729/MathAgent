---
name: AIME 1984 Problem 7 (nested-recursion functional equation)
type: problem
status: ready
problem_class: Integer functional equation / nested recursion
tier: calibration
statement: "Let f be a function on the integers with f(n) = n - 3 for n >= 1000 and f(n) = f(f(n+5)) for n < 1000. Prove that f(84) = 997."
known_solution_available: true
include_standard_proof: false
---

# AIME 1984, Problem 7

## Statement

Let `f` be the function on the integers defined by

```text
f(n) = n - 3                for n >= 1000,
f(n) = f(f(n + 5))          for n < 1000.
```

Prove that `f(84) = 997`.

## Variables and domain

`n` is an integer; `f` is the (well-defined) function above. The recursion for `n < 1000`
increases the argument by `5` inside a double application, and the base case `n >= 1000`
subtracts `3`; the target evaluates `f` at `n = 84`.

## Target conclusion

`f(84) = 997`.

## Allowed methods

See `allowed_inputs.md`. Standard-toolkit calibration problem. The elementary route is to prove,
by (finite / well-ordered) induction downward from the base region, the closed form

```text
f(n) = 997   for even  n with 84 <= n < 1000,
f(n) = 998   for odd   n with 84 <= n < 1000,
```

(more precisely, on `n < 1000` the value depends only on the parity of `n`, stabilizing to
`{997, 998}`), from which `f(84) = 997` since `84` is even. The induction is grounded by unfolding
`f(f(n+5))` until the argument reaches the base region `n >= 1000`, which happens after finitely
many steps because each unfolding raises the argument. No per-problem external input is required.

## Disallowed shortcuts

Do not invoke non-elementary machinery (see the global denylist in `allowed_inputs.md`). The
argument is a finite descent / induction over integers; no external theorem is needed or permitted.

## Known related examples

A classic "nested recursion stabilizes on a parity class" functional equation. Do not paste a full
proof into this folder (anti-contamination policy).

## Benchmark notes

- Source: 1984 AIME, Problem 7.
- Numeric validation (this session): computed `f` with memoization from the two defining rules;
  `f(84) = 997`. Spot values confirming the parity stabilization: `f(999) = 998`, `f(998) = 997`,
  `f(85) = 998`, and `f(1000) = 997`.
- Tier: **calibration** — a short, fully elementary induction; a capability check, not research.
