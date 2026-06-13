---
name: Finite/infinite descent
type: method
status: draft
aliases: [infinite descent, Fermat descent, minimal counterexample, well-ordering descent]
allowed_in_final_proofs: true
primary_problem_classes: [Diophantine equations, nonexistence proofs, finite descent]
related_methods: [vieta_jumping, invariant_substitution, well_ordering, squeeze_sequence]
justifies: [descent, well_ordering, extremal]
---

# Finite / Infinite Descent

## Statement / Core idea

From a hypothetical solution (or counterexample) construct a strictly smaller one of the same kind,
in the same domain. Since a set of positive integers has no infinite strictly decreasing chain
(well-ordering), no solution can exist — or every solution reduces to an explicit base case.

Equivalently: assume a **minimal** counterexample by well-ordering, then derive a smaller one for the
contradiction.

## When to try this

- A Diophantine equation is conjectured to have no nontrivial / no positive solutions.
- A solution's components can be shown to share a factor, letting you divide down.
- A transformation (often a Vieta companion root, or a parity/`gcd` reduction) produces another
  solution with a smaller positive measure.

## Pattern signatures

- "Show the only solution is the trivial one."
- A symmetry or quadratic structure yields a second, smaller solution (→ `vieta_jumping`).
- Coprimality + a square/`gcd` argument forces all variables even, so `(x,y,z) -> (x/2, y/2, z/2)`.

## Preconditions and normalizations

Descent has exactly three obligations — the gate (`agent/gates/`) enforces their *presence*, and
spot-checks them numerically when concrete expressions are supplied:

1. **A well-founded measure** `mu : solutions -> N` (e.g. `mu = |x|`, or `x + y + z`, or `max`).
2. **Strict decrease**: the constructed solution has `mu' < mu`.
3. **Stays in domain**: the constructed object is again a *valid* solution (integral, positive,
   satisfies the equation).

Record these in the step's `descent` obligation:
```json
{"justification": "descent",
 "obligations": {"descent": {
    "measure": "x", "strictly_decreases": true, "stays_in_domain": true,
    "measure_expr": "x", "next_expr": "x - 2", "variables": ["x"], "sample_bounds": {"x": [1, 50]}}}}
```
The optional `measure_expr`/`next_expr`/`sample_bounds` let the numeric tool confirm `next < measure`
across a sample box (a cheap guard against a decrease that is asserted but false).

## Canonical transformation

Given a minimal solution `S` with measure `mu(S)`, build `S'` with `mu(S') < mu(S)` and `S'` valid;
contradiction with minimality. (Or: iterate `S -> S'` to descend to a base case and enumerate it.)

## Downstream moves

- Combine with `vieta_jumping` (the companion root is the smaller solution).
- Combine with `gcd_coprimality` / `parity` (all-even forces division by 2).
- Reduce to a finite base case checked by `case_split` / numeric search.

## Worked examples

- Classic: `x^4 + y^4 = z^2` has no positive integer solution (Fermat). Parameterize the primitive
  Pythagorean triple, extract a smaller solution of the same form.
- `a^2 + b^2 = 3 c^2` implies `a=b=c=0` via mod 3: `3 | a`, `3 | b`, then `3 | c`, descend.

## Common failure modes

- The companion / reduced object is **not** shown to stay positive or integral (domain obligation).
- The measure does **not** strictly decrease (e.g. `<=` instead of `<`, or only decreases sometimes).
- Minimality is invoked but the smaller solution is accidentally the *same* solution.
- Hidden non-elementary input smuggled into the "reduction" step.

## Lean-relevant lemmas

Descent's wall in Lean is supplying a kernel-accepted well-founded measure. Build a **reusable
combinator** parameterized by `measure : a -> N` and a per-step decrease lemma, so the model only
provides the measure and the decrease:

- `Nat.strongRecOn` / `Nat.strong_induction_on` — strong induction on the measure.
- `WellFounded.fix`, `Acc.rec`, `Nat.lt_wfRel` — well-founded recursion plumbing (on the gate's Lean
  **infrastructure allowlist**, so the dependency audit does not over-reject).
- `Nat.find` / `Nat.findGreatest` — extract the minimal counterexample.
- `termination_by` / `decreasing_by` — discharge termination for a recursively-defined descent.

## Search prompts for agents

- What positive integer quantity must decrease?
- What transformation yields another valid, smaller solution?
- Is the reduced object provably integral, positive, and a genuine solution?
- What is the base case, and is it handled?

## Evaluation hooks

- Record the measure, the decrease proof, and the domain-preservation proof.
- If a concrete reduction is available, attach `measure_expr`/`next_expr` for a numeric decrease check.
