# Allowed Inputs

Allowed:

- elementary integer arithmetic, divisibility, parity, elementary factorization;
- congruences / modular arithmetic and the Chinese Remainder Theorem (residues mod 8 drive
  the odd-squares reduction);
- gcd and coprimality;
- quadratic residues and elementary residue bookkeeping;
- size / bounding and counting arguments;
- finite / infinite descent, the extremal / well-ordering principle;
- listed methods in `knowledge/methods/`.

Per-problem citable input (pinned by the orchestrator) — **trust boundary, stated
explicitly**:

- **Gauss–Legendre three-squares theorem**: an integer `m > 0` is a sum of three integer
  squares iff `m` is not of the form `4^a(8b + 7)`. This is **NOT elementary by this repo's
  core toolkit** (its standard proofs use ternary quadratic forms / class-number or
  Dirichlet-density machinery). It is admitted here as a **citable fact**, analogous to the
  Zsygmondy and Pell rulings in PLAN §2.1 — cited, not re-derived. The benchmark task is the
  **elementary reduction** `8n + 3 = x^2 + y^2 + z^2  ⇒  n = T_a + T_b + T_c`, using only the
  elementary tools above. Do not silently expand this citable input into a fresh non-elementary
  proof of a different step.

Disallowed as final proof tools (global denylist categories), except the explicit citable
input above:

- unique factorization in rings of algebraic integers (UFDs);
- ideals / class groups / class field theory; algebraic number fields;
- elliptic curves; modular forms / modularity; algebraic geometry;
- `p`-adic theory beyond `v_p` and elementary congruences;
- Catalan / Mihailescu; Baker's theory of linear forms in logarithms;
- analytic number-theory machinery (beyond invoking the citable three-squares theorem);
- computational brute force without an explicit elementary finite bound.
