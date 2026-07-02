# Allowed Inputs

Allowed:

- elementary integer arithmetic, divisibility, parity, elementary factorization (including
  factoring `a^3 ± b^3` over ℤ);
- congruences / modular arithmetic (residues mod 4 and mod 8 drive every proof here) and the
  Chinese Remainder Theorem;
- gcd and coprimality (Bézout, Euclid's lemma);
- quadratic residues, the Legendre / Jacobi symbol, Euler's criterion, quadratic reciprocity
  and its supplementary laws (the tests for when `-1`, `2`, `-2` are squares mod `p` — PLAN
  §2.1 rules these ALLOWED);
- finite / infinite descent, the extremal / well-ordering principle;
- size / bounding arguments;
- listed methods in `knowledge/methods/`.

No per-problem external whitelist beyond the standard elementary toolkit is needed: every
vetted `k` in `problem.md` closes by congruences + factorization + the QR criterion.

Disallowed as final proof tools (global denylist categories) — these are exactly what the
**generic** Mordell equation `y^2 = x^3 + k` requires, and are what keeps generic Mordell out
of scope:

- Baker's theory of linear forms in logarithms;
- elliptic-curve descent / heights / the Mordell–Weil theorem;
- unique factorization in rings of algebraic integers (e.g. `ℤ[i]`, `ℤ[√-2]`, `ℤ[√-d]`) —
  this is precisely the machinery of Conrad's Section 3 (the excluded non-elementary cases);
- ideals / class groups / class field theory; algebraic number fields; algebraic geometry;
- modular forms / modularity;
- `p`-adic theory beyond `v_p` and elementary congruences;
- Catalan / Mihailescu; analytic number-theory machinery;
- computational brute force without an explicit elementary finite bound.
