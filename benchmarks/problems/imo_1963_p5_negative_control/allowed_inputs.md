# Allowed Inputs

This problem is a **DELIBERATE out-of-scope negative control**, so the usual "allowed methods"
framing does not apply in the normal way.

What it is:

- a **real / trigonometric** identity — `cos(pi/7) - cos(2*pi/7) + cos(3*pi/7) = 1/2` — about
  values of the cosine function over the reals;
- NOT a statement of elementary **integer** number theory: there is no integer variable, no
  divisibility hypothesis, and no Diophantine content.

Why it is here (the control's intent):

- to test the **scope boundary** of the elementary-integer-NT harness. Under an
  elementarity-enforcing profile (`elementarity=soft` or `authoritative`) the **expected outcome is
  a failure to certify**: `FAILED_ELEMENTARY`, or the goal is unformalizable within the integer-NT
  scope that the terminal Layer-4 Lean audit checks.
- **That non-certification is the pass condition.** A run that certifies this as an
  `authoritative_elementary` integer-NT result would signal a scope leak, not a success.

No external citable input is admitted, because no in-scope proof is expected: the control
deliberately has no path to an elementary integer-NT certificate.

Note: the identity is numerically TRUE (the alternating sum evaluates to `0.500000000000` to 12
decimal places). Its truth is not in question — only its membership in the harness's target domain,
which is the whole point of the control.
