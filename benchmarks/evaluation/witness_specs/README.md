# Deterministic witness-check battery (Mode B)

A small battery of JSON **witness specs** scored by the exact-integer, no-LLM checker
`agent.tools.numeric.check_witness_spec` (each embedded expression is parsed by the restricted
no-eval integer AST in `_safe_parse` — never `eval`/`exec`/`sympify`). Run them with:

```
python scripts/run_witness_checks.py \
    --specs-dir benchmarks/evaluation/witness_specs \
    --out benchmarks/evaluation/witness_specs/RESULTS.jsonl
```

Each spec produces one JSONL row: `{spec, valid_schema, confirmed, score, error}`.

## What the checker supports (schema facts)

The `kind` field selects one of exactly three checkers. **Everything is integer-only**: expressions
allow `+ - * **` over integer literals and declared variables; bounds must be integer pairs. Caps:
`MAX_BOX_POINTS = 100_000` (product of `(hi-lo+1)` over variables), `MAX_ABS_BOUND = 1e9`,
`MAX_POW_EXPONENT = 64`.

- **`solution_set`** — `{expression, variables, bounds:{v:[lo,hi]}, claimed:[{v:val,...}]}`. The
  checker enumerates the closed box and requires `claimed` to be **exactly** the box's solution set
  of `expression == 0` (no missing, no spurious). `expression` may contain a single `=` (it becomes
  `lhs - rhs`).
- **`residue_cover`** — `{modulus:M, residues:[...]}`. CONFIRMS iff the residues (taken `mod M`) form
  a **complete** residue system `{0,1,…,M-1}`. `check_witness_spec` additionally requires `M >= 2`.
  Note the **direction**: the checker verifies a *complete cover of the modulus classes*, not that a
  named subset is closed under some operation (see `squares_mod8_cover` below).
- **`descent`** — `{measure_expr, next_expr, variables, bounds}` — strict decrease everywhere in the
  box. (Not used in this battery.)

A malformed / unrepresentable spec sets `ok=False` **and** records an `error` string (a HARD
failure). That is the design: the runner records the error row and never crashes.

## The specs

### `amc12_2000_p1_solution_set.json` — CONFIRMS (score 1.0)
AMC 12 2000 P1: ordered triples with `i*m*o = 2001` (`2001 = 3·23·29`).

**Bounded slice (documented exactly).** The full ordered box `[1,2001]^3` has
`2001^3 ≈ 8.0e9` points, far over the resource cap. This witness deliberately checks the closed slice
`i∈[1,3], m∈[1,15], o∈[1,2001]` (90,045 points). The **complete** solution set of the equation inside
that slice is the three triples in `claimed`:
`(1,1,2001), (1,3,667), (3,1,667)`. In particular it includes the problem's maximizing distinct
factor triple `(1,3,667)`, but it does not claim to enumerate the full AMC search space.

The checker cannot express the problem's extra constraints (**distinct** entries, or an `i≤m≤o`
ordering). Those constraints and the reduction from all factor triples remain proof obligations outside
this witness. The spec grounds only the exact *complete-solution-set-in-the-declared-box* claim.

### `mordell_k7_no_solutions.json` — CONFIRMS empty-in-box (score 1.0)
`y^2 = x^3 + 7` (Mordell, `k = 7`), `claimed: []` over the box `|x|≤20, |y|≤100` (8,241 points).
The box is aligned so the range of `x^3+7` (max `8007` at `x=20`) fits under `|y|≤100`
(`100^2 = 10000`). The checker confirms there is **no** integer point in this box.

**Scope.** This grounds only the *small-box* claim (no solutions with `|x|≤20`). It is **not** the
theorem: `y^2 = x^3 + 7` has no integer solutions *at all*, which needs the classical factorization
argument. Parity forces `x` odd, and `y²+1=x³+8=(x+2)(x²-2x+4)`; the positive quadratic factor is
`3 (mod 4)`, so it has a prime divisor `p≡3 (mod 4)`, contradicting `y²≡-1 (mod p)`. The witness check
corroborates the small cases; the proof
lives elsewhere.

### `squares_mod8_cover.json` — CONFIRMS (score 1.0)
Grounds the classical fact "a square is `≡ 0, 1, or 4 (mod 8)`" in the **only direction the
`residue_cover` checker supports**. The checker verifies a *complete cover of the modulus classes*,
so it cannot directly accept the subset `{0,1,4}` (that subset is incomplete and would report
`missing=[2,3,5,6,7]`). Instead this spec encodes the **completeness of the mod-8 case system**:
`residues=[0,1,2,3,4,5,6,7]` confirms "every integer is congruent to exactly one of `0..7 mod 8`",
which is the case-exhaustiveness obligation a squares-mod-8 argument discharges. Over those 8
classes the reader checks `n^2 mod 8 ∈ {0,1,4}` directly (`0,1,4,1,0,1,4,1`). The completeness is
what the deterministic checker can and does confirm; the `{0,1,4}` conclusion is the per-class
evaluation on top of it.

### `kissing_number_dim11_REJECTED.json` — REJECTED (deterministic error; the rejection IS the measurement)
An honest attempt to express "a kissing configuration of 593 unit spheres in `R^11`" with the only
vocabulary the schema has: unit-sphere coordinates as a `solution_set` witness. Unit-sphere
coordinates are **real**, so the bounds are `[-1.0, 1.0]` and a claimed point has non-integer
coordinates. The integer-only checker rejects this deterministically:
`error = "spec bound for 'x' must be integers"`, `confirmed=False`.

This rejection **is** the measurement: an AlphaEvolve-class geometric object living over `R` is
**outside Mode B by construction**. Mode B certifies exact-integer obligations only; a real-coordinate
sphere packing is unrepresentable in its AST, and the check honestly reports that.

### `tensor_rank_444_REJECTED.json` — REJECTED (deterministic error; the rejection IS the measurement)
An honest attempt to express "a rank-48 decomposition of the `4×4×4` matrix-multiplication tensor
over `C`". Such a decomposition has **complex** coefficients; JSON/the integer AST has no complex
type, so a coefficient is encoded as the string `"2+3i"`. The checker rejects it deterministically
when it tries to coerce the claimed entry to an integer:
`error = "ValueError: invalid literal for int() with base 10: '2+3i'"`, `confirmed=False`.

Again the rejection is the point: a complex-entried tensor decomposition (the AlphaEvolve matmul
result) is **outside Mode B by construction** — it cannot be an integer witness, and the check says so.

## Reading the results

`valid_schema` is True when the checker recorded **no** validation `error` (the object was
representable). The three math specs are `valid_schema=true, confirmed=true`. The two controls are
`valid_schema=false, confirmed=false` with a recorded `error` — the intended, reproducible outcome.
