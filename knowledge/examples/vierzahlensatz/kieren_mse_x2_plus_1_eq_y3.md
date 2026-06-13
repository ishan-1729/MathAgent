# Kieren MSE Example: `X^2 + 1 = Y^3`

This file is a method example, not a benchmark problem record.

## Theorem demonstrated

`X^2 + 1 = Y^3` has only integer solution `(X, Y) = (0, 1)`.

## Intermediate lemma

If `3a^4 + 3a^2 + 1 = b^2`, then `a = 0` and `b = +/-1`.

## Vierzahlensatz entry point

The proof reaches:

```text
3d^2(4d^2 + 1) = c(c + 1).
```

## Parameterization

Apply Vierzahlensatz:

```text
3d^2 = pq
4d^2 + 1 = rs
c = pr
c + 1 = qs
```

## Key follow-up moves

- `gcd(c, c + 1) = 1` gives `gcd(p, q) = 1`.
- Write `d = uv`.
- Split square factors into `p = u^2, q = 3v^2` or `p = 3u^2, q = v^2`.
- Continue with eliminations and congruence contradictions.

This outline records the method pipeline only; it does not paste the full proof.
