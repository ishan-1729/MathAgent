# Allowed Inputs

Allowed:

- elementary integer arithmetic, divisibility, parity, elementary factorization;
- congruences / modular arithmetic and the Chinese Remainder Theorem;
- gcd and coprimality (Bézout, Euclid's lemma);
- quadratic residues, the Jacobi symbol, Euler's criterion, quadratic reciprocity and its
  supplementary laws;
- orders of elements and Fermat–Euler;
- `p`-adic valuation `v_p` and lifting-the-exponent (LTE);
- finite / infinite descent, the extremal / well-ordering principle;
- size / bounding arguments;
- **Pell fundamental-solution theorem** — citable elementary fact (PLAN §2.1); the equation
  rewrites as the negative Pell relation `X^2 - 2 (Y^2)^2 = -1`;
- **Zsygmondy / primitive-prime-divisor** — citable with citation (PLAN §2.1);
- listed methods in `knowledge/methods/`.

Per-problem whitelist (PLAN §2.1, §8.1) — **allowed for this problem only**:

- **quartic-residue / biquadratic-character (biquadratic reciprocity) machinery.** The known
  elementary proofs of `x^2 + 1 = 2 y^4` genuinely require quartic residues; they are admitted
  here as a citable elementary-adjacent tool. This whitelist does **not** extend to any other
  problem.

Disallowed as final proof tools (even for this problem):

- Baker's theory of linear forms in logarithms;
- algebraic number fields / rings of integers used as a black box; ideals / class groups /
  class field theory; UFDs in rings of algebraic integers;
- elliptic curves; modular forms / modularity; algebraic geometry;
- `p`-adic theory beyond `v_p` and elementary congruences;
- Catalan / Mihailescu;
- analytic number-theory machinery;
- computational brute force without an explicit elementary finite bound.
