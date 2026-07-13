# Formal

This is the Lean formalization area. It is **initialized and load-bearing**:
`lean/mathagent_formal/` is a fully set-up Lean 4.30.0 + Mathlib + `repl` lake project, and the
authoritative **Layer-4** elementarity audit (proof-term dependency closure + `collectAxioms`) runs
against it. See `../agent/gates/lean/Audit.lean`, `../agent/gates/lean_audit.py`, and `lean/README.md`.

Terminal certification can use either one-shot `lake env lean` compilation or the persistent REPL when
`lean.server=true`; per-node Lean verification requires the persistent server. A server request is a
required capability, not a best-effort optimization: supervision/startup fails closed rather than silently
downgrading it. The supervisor also rejects an inert server request: `lean.server=true` is legal only when
an authoritative terminal gate or per-node Lean gate will consume it. In `elementarity=none`, no Lean gate
is attached and every Lean flag, including `lean.server`, must be false.

Compilation and even a passing dependency audit are necessary but insufficient for a certificate. The
terminal result also requires a unanimous trusted statement-faithfulness checker and production components
that explicitly declare `certification_trusted=True`; generic/scripted test doubles default to untrusted.
The audit itself must also carry bridge-verified provenance: a runtime-derived Lean toolchain matching the
project pin plus a SHA-256 receipt for the Lake manifest (`core-only` for a bare-Lean audit). Missing,
caller-supplied, mismatched, or mid-run-changing provenance cannot mint authority.
