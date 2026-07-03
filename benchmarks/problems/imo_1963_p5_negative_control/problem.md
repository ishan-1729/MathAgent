---
name: IMO 1963 Problem 5 (trigonometric identity — DELIBERATE out-of-scope negative control)
type: problem
status: ready
problem_class: Real/trigonometric identity (NOT integer number theory) — negative control
tier: negative-control
statement: "Prove that cos(pi/7) - cos(2*pi/7) + cos(3*pi/7) = 1/2."
known_solution_available: true
include_standard_proof: false
---

# IMO 1963, Problem 5 (negative control)

## Statement

Prove that

```text
cos(pi/7) - cos(2*pi/7) + cos(3*pi/7) = 1/2.
```

## This is a DELIBERATE out-of-scope negative control

This folder is intentionally **outside** MathAgent's target domain. The claim is a **real /
trigonometric** identity about values of the cosine function, not a statement of elementary
**integer** number theory. It is included as a **negative control**: under an elementarity-enforcing
profile the expected outcome is a **failure to certify** — `FAILED_ELEMENTARY`, or the goal is
simply unformalizable within the integer-NT scope the terminal Layer-4 gate audits. **That outcome
is the point of this problem.** A run that "certifies" this as an elementary integer-NT result would
indicate the scope boundary is leaking; a clean non-certification is the pass condition.

See `allowed_inputs.md` for the full statement of the control's intent and expected verdict.

## Variables and domain

The quantities are **real numbers** (`cos` of rational multiples of `pi`), not integers. There is
no integer variable, no divisibility hypothesis, and no Diophantine structure — by construction.

## Target conclusion (as a control)

The desired *mathematical* fact is that the alternating cosine sum equals `1/2`. The desired
*system* behavior is that an elementarity-enforcing run does NOT mint an
`authoritative_elementary` certificate for it, because it is out of scope.

## Benchmark notes

- Source: International Mathematical Olympiad 1963, Problem 5.
- Numeric validation (this session): evaluating the sum to 12 decimal places gives
  `0.500000000000`, so the identity itself is true; only its *domain* (real trigonometry) is
  out of scope for the elementary-integer-NT harness.
- Tier: **negative-control** — measures the scope boundary, not proof capability. Do not add a
  proof to this folder.
