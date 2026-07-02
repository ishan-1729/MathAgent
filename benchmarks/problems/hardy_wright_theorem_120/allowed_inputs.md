# Allowed Inputs

Allowed:

- elementary integer and rational arithmetic, divisibility, parity;
- congruences / modular arithmetic (including congruences between rationals with denominators
  prime to the modulus, and `≡ (mod 1)` bookkeeping);
- gcd and coprimality (Bézout, Euclid's lemma);
- Fermat–Euler and orders of elements;
- sums of powers `1^k + 2^k + ... + (p-1)^k (mod p)` (elementary — this is H&W's own Theorem
  119 route);
- generating functions / formal power series **as formal manipulation** (PLAN §2.1 ruling) —
  needed only to define Bernoulli numbers via `x/(e^x - 1)`; no convergence or complex-analytic
  facts;
- divisor analysis of `2k` for prime `k` (which primes `p` satisfy `p - 1 | 2k`);
- listed methods in `knowledge/methods/`.

Per-problem citable input:

- **von Staudt's theorem** (H&W Theorem 118, §7.9): the fractional part of `(-1)^k B_k` equals
  `Σ 1/p (mod 1)`, summed over primes `p` with `(p - 1) | 2k` (H&W indexing). Citable like the
  Pell / Zsygmondy rulings in PLAN §2.1. Note it is not a trust compromise: H&W's own proof
  (§7.9–7.10, via Theorem 119) is fully elementary, so a solution may alternatively re-derive
  it with the tools above.

Disallowed as final proof tools (global denylist categories):

- analytic number-theory machinery (zeta values `ζ(2k)`, Euler–Maclaurin, `L`-functions,
  Gauss-sum evaluations);
- `p`-adic theory beyond `v_p` and elementary congruences (no Kummer/Clausen–von-Staudt via
  `p`-adic `L`-functions or measures);
- unique factorization in rings of algebraic integers (UFDs); ideals / class groups / class
  field theory; algebraic number fields;
- elliptic curves; modular forms / modularity; algebraic geometry;
- Catalan / Mihailescu; Baker's theory of linear forms in logarithms;
- computational brute force without an explicit elementary finite bound.
