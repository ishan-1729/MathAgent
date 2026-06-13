# AXLE

Type: product + docs dossier

## What it is

AXLE is Axiom's public Lean tooling layer for proof verification, proof/code transformation, and structured proof manipulation. The official docs present it less as a single prover and more as a toolbox of Lean operations that can validate, inspect, normalize, extract, simplify, rename, merge, or repair candidate proof artifacts. ([AXLE product page](https://axle.axiommath.ai/), [AXLE docs](https://axle.axiommath.ai/v1/docs/))

## Status

AXLE has a public web surface plus documented Python, CLI, and HTTP API interfaces. The docs describe a weekly public release cadence and public maintenance window, which makes AXLE feel like live infrastructure rather than a static repo snapshot. I did not find a formal paper for AXLE itself among the official materials checked here, so the primary source of truth is the product/docs surface. ([AXLE docs](https://axle.axiommath.ai/v1/docs/), [AXLE product page](https://axle.axiommath.ai/))

## Core design

The docs split AXLE into proof verification, code analysis, and code transformation primitives. Publicly documented tools include `verify_proof`, `check`, `extract_theorems`, `extract_decls`, `rename`, `theorem2lemma`, `theorem2sorry`, `merge`, `simplify_theorems`, `repair_proofs`, `have2lemma`, `have2sorry`, `sorry2lemma`, `disprove`, and `normalize`. AXLE describes these utilities as Lean metaprograms, which is important: the system is not just a text post-processor but an environment-aware transformation layer written against Lean itself. ([AXLE docs](https://axle.axiommath.ai/v1/docs/))

## How it works in practice

AXLE operates on Lean artifacts that already exist. Its typical role is downstream of proof generation and upstream of human audit or repository integration. Once candidate Lean code has been produced, AXLE can:

- check whether it compiles in a specified Lean environment
- verify a proof against a formal statement
- split larger files into theorem-level or declaration-level units
- simplify theorem bodies by removing unused tactics or `have` statements
- transform declarations into different forms for inspection or further work
- attempt limited repair of broken proofs

This is a different role from a model that proposes a new proof from scratch. AXLE's value comes from making proof artifacts easier to validate, inspect, reuse, and clean up. ([AXLE docs](https://axle.axiommath.ai/v1/docs/), [`simplify_theorems`](https://axle.axiommath.ai/v1/docs/tools/simplify_theorems/))

## Why the design helps

The design helps because generated Lean code is often not useful in its first raw form. Even when a candidate proof is close to correct, it may be overly verbose, mixed with unrelated declarations, difficult to audit, or broken in a way that is localized enough for tooling to fix. By providing environment-aware proof utilities instead of only a yes/no checker, AXLE turns Lean proof manipulation into infrastructure. That is especially valuable in agentic workflows, where the problem is often not only "can the model propose something" but also "can we efficiently turn the proposal into a clean, inspectable artifact". ([AXLE docs](https://axle.axiommath.ai/v1/docs/), [AXLE product page](https://axle.axiommath.ai/))

## Interfaces and usage surface

AXLE is documented through four interfaces: web UI, Python API, CLI, and HTTP API. The docs show CLI usage such as `axle verify-proof ...`, `axle check ...`, and `axle simplify-theorems ...`, along with Python and `curl` examples. A notable design detail is explicit Lean environment selection, e.g. `lean-4.28.0`, which makes AXLE useful for environment-specific validation rather than only generic proof checking. The docs also list `axiom-axle-mcp` as a related resource, which suggests an intended role in tool-augmented agent workflows. ([AXLE docs](https://axle.axiommath.ai/v1/docs/), [`simplify_theorems`](https://axle.axiommath.ai/v1/docs/tools/simplify_theorems/))

## Trust model and limitations

AXLE's own documentation warns that `verify_proof` assumes the Lean environment itself is trusted and recommends stronger isolated checkers such as `lean4checker`, Comparator, or SafeVerify when adversarial metaprogramming is a concern. That warning is important: AXLE is a powerful proof utility layer, but it is not pretending to solve every trust-boundary problem on its own. More broadly, AXLE is tooling rather than a complete theorem-proving stack, and its value depends on already having Lean artifacts worth checking or transforming. ([`verify_proof`](https://axle.axiommath.ai/verify_proof), [AXLE docs](https://axle.axiommath.ai/v1/docs/))

## Strengths

- Broad proof-utility surface instead of a single narrow checker.
- Environment-aware operations tied to actual Lean versions.
- Strong fit for cleanup, extraction, normalization, and auditability.
- Multiple interfaces make it easy to integrate into scripts, agents, or direct interactive use.

## Sources

- Axiom Math. "AXLE - Axiom Lean Engine." [https://axle.axiommath.ai/](https://axle.axiommath.ai/)
- Axiom Math. "AXLE documentation." [https://axle.axiommath.ai/v1/docs/](https://axle.axiommath.ai/v1/docs/)
- Axiom Math. "Verify Proof." [https://axle.axiommath.ai/verify_proof](https://axle.axiommath.ai/verify_proof)
- Axiom Math. "simplify_theorems." [https://axle.axiommath.ai/v1/docs/tools/simplify_theorems/](https://axle.axiommath.ai/v1/docs/tools/simplify_theorems/)
