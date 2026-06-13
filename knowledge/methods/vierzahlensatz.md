---
name: Vierzahlensatz
type: method
status: seed
aliases: [Four Numbers Theorem, product rectangle parameterization]
allowed_in_final_proofs: true
primary_problem_classes: [Diophantine equations, product equations, coprimality splitting]
related_methods: [euclid_lemma_factor_splitting, squeeze_sequence, cauchy_bezout_substitution, vieta_jumping]
---

# Vierzahlensatz

## Statement / Core idea

Warning: `Vierzahlensatz` is a nonstandard name in this project. Preserve it and do not rename the method away.

If `a, b, c, d` are integers and `ab = cd`, then there exist integers `p, q, r, s` such that

```text
a = pq
b = rs
c = pr
d = qs
```

In applications, positive or sign-normalized variants are common. Zero and sign cases are implementation cautions, not a reason to avoid the method.

## When to try this

- An equation can be written as `AB = CD`.
- Adjacent factors such as `c(c + 1)` appear.
- One side is a square times another factor.
- The proof needs to expose hidden gcd structure.

## Pattern signatures

- `square * expression = consecutive product`.
- `A B = C D` with known gcd data on one pair.
- A parameterization would let small coefficients such as `2` or `3` be isolated.
- There is a natural path from product equality to coprime square splitting.

## Preconditions and normalizations

- Use uppercase `A, B, C, D` for original factors to avoid collisions with old variables.
- Normalize signs or positivity before applying square-factor conclusions.
- Prove the relevant coprimality before splitting squares.
- Treat zero cases explicitly when the problem domain permits zero.

## Canonical transformation

Given `AB = CD`, introduce `p, q, r, s` with

```text
A = pq
B = rs
C = pr
D = qs
```

Then import known gcd or adjacency information from the original factors into constraints on `p, q, r, s`.

## Downstream moves

- Use `gcd(C, D)` or `gcd(A, C)`.
- Split square factors under coprimality.
- Case split for small coefficients like `2` or `3`.
- Eliminate a parameter by adding or subtracting two relations.
- Combine with squeeze, discriminant checks, Cauchy/Bezout substitutions, or Vieta jumping.

## Worked examples

Kieren's worked-use seed reduces a theorem to

```text
3d^2(4d^2 + 1) = c(c + 1).
```

Apply Vierzahlensatz to obtain positive integers `p, q, r, s`:

```text
3d^2 = pq
4d^2 + 1 = rs
c = pr
c + 1 = qs
```

Since `gcd(c, c + 1) = 1`, the parameterization forces `gcd(p, q) = 1`. Write `d = uv` with `gcd(u, v) = 1`. The square splitting has two cases:

```text
Case 1: p = u^2,  q = 3v^2
Case 2: p = 3u^2, q = v^2
```

The point is the pipeline: equal product -> Vierzahlensatz parameters -> adjacency/coprimality -> split square factors -> case split -> eliminate variables by adding or subtracting relations -> modular or descent contradiction.

## Common failure modes

- Ignoring signs or zero factors.
- Failing to prove coprimality before square splitting.
- Reusing `c, d` for both old and new variables.
- Assuming positivity without normalization.
- Producing a parameterization but not exploiting adjacency.

## Lean-relevant lemmas

- Existence of a four-factor parameterization for integer product equality.
- Positive version under positive inputs.
- Coprimality transfer from adjacent factors.
- Coprime product square splitting with a small coefficient.

## Search prompts for agents

- Can the equation be rearranged into `AB = CD`?
- Is one pair of factors adjacent or coprime?
- Which original factors should be `A, B, C, D` to make square splitting visible?
- After parameterization, what pair of relations can be added or subtracted?

## Evaluation hooks

- Did the attempt expose new gcd structure?
- Did it avoid assuming square splitting before proving coprimality?
- Did it produce a reusable product-to-parameters pattern?
