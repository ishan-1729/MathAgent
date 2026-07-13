# Lean

Problem-specific Lean attempts can live under the matching `benchmarks/problems/*/lean/` folder, with reusable helpers promoted later.

## `mathagent_formal/` — the Mathlib lake project

A Mathlib-dependent lake project (pinned to **Lean 4.30.0** + Mathlib `v4.30.0` rev) used as the
compile/audit environment for the **Layer-4 dependency audit** (`agent/gates/lean_bridge.py` runs
`lake env lean` here so formalized `import Mathlib` proofs resolve). The Lean **extractor** lives at
`agent/gates/lean/Audit.lean`.

Every production report identifies the toolchain from the running Lean binary and carries the full SHA-256
of this project's exact `lake-manifest.json`. The bridge compares the runtime toolchain with
`lean-toolchain` and checks the pin/manifest before and after compilation (or persistent-server startup),
so an environment label or concurrent manifest rewrite cannot spoof an authoritative receipt.

Only the small project files are committed (`lakefile.toml`, `lean-toolchain`, `lake-manifest.json`,
the lib sources). The heavy `.lake/` (toolchain, Mathlib clone, ~5GB of oleans) is git-ignored. To
reproduce the build environment after cloning:

```sh
cd formal/lean/mathagent_formal
lake exe cache get      # download prebuilt Mathlib oleans (matches the pinned rev)
lake build              # fast on a cache hit
```

Without this, the core-only Lean audit still works (`agent/gates/lean_bridge.py` falls back to bare
`lean`); only `import Mathlib` proofs need the project. See
`research/docs/lean_layer4_and_population.md`.
