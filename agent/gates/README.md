# agent/gates/

**The defining mechanism of MathAgent.** These gates enforce the elementary-only constraint.

> **Load-bearing insight (from the literature review): "Lean-verified ≠ elementary."**
> A proof can compile against Mathlib while freely invoking class groups, elliptic curves, p-adics, or
> Mihailescu's theorem. Compilation success certifies *correctness*, never *method admissibility*. So the
> hard gate is a **proof-term dependency audit**, not "it compiled." Also: elementarity is a property of the
> *proof*, with no projection operator — you cannot "round" a class-group argument into a descent argument
> (unlike numeric constraints such as integrality). Hence soft pruning + a hard verification gate, never a
> single soft judge.

## Target theory T (what "elementary" certifies)

The Layer-4 certificate does **not** assert the canonical mathematical property "this proof is elementary" —
there is no such canonical property. As the constraint-induction synthesis documents
([`research/docs/constraint_induction_2026-06-28.md`](../../research/docs/constraint_induction_2026-06-28.md),
§1a and open question #1), "elementary" has no single accepted definition (the informal "no complex analysis"
sense, first-order PA, IΔ₀+exp, the reverse-math Big Five, and Buss's feasible S¹₂ are mutually inequivalent),
so any operational definition is a **stipulation, not a discovered fact**.

What the audit actually decides is the *decidable* predicate **"this proof term's transitive
dependency/axiom footprint is contained in a stipulated fragment T"**. At Layer 4, T is defined by
[`denylist.yaml`](denylist.yaml): `lean_denylist_decls`, the dominating
`lean_infrastructure_allowlist` / `lean_elementary_by_fiat` carve-outs, and the axiom policy (including the
accepted base `{propext, Classical.choice, Quot.sound}`). The positive justification vocabulary in
[`allowed_toolkit.yaml`](allowed_toolkit.yaml) constrains the informal ledger at Layers 0–3; it is not a
positive whitelist over every constant in the compiled Lean proof. Informally T is a PA / IΔ₀+exp-flavored
fragment of elementary number theory, but the authoritative meaning here is exactly the versioned Layer-4
policy, not a canonical mathematical definition. (By Gödel/Church–Turing the complementary claim — "φ has
*no* elementary proof" — is undecidable, so this gate is sound-but-incomplete *by theorem*, not by effort: it
can over-reject a genuinely-elementary proof whose only found derivation routes through a denylisted lemma.)

Because T is a stipulated, human-authored, versioned approximation, **the policy revision and Lean/Mathlib
toolchain must accompany any durable verdict**: the same proof term can pass Layer 4 under one revision of
`denylist.yaml` and be rejected under the next (the `ℤ[i]`/`Zsqrtd` additions of 2026-06-28
are exactly such a revision). The production bridge records the runtime-derived Lean toolchain and a
SHA-256 receipt for the exact Lake manifest (or an explicit `core-only` marker), rejects a runtime/project-pin
mismatch or a manifest that changes during compilation/server startup, and never accepts the former
caller-controlled `MATHAGENT_TOOLCHAIN` label. Durable experiment/certificate storage retains that receipt
and must also retain the policy revision. A Layer-4 verdict is therefore only meaningful relative to those inputs
— treat "certified elementary" as shorthand for "footprint ⊆ T@\<policy-revision, toolchain\>", never as a
canonical mathematical property.

## The layered gate

### Soft gates (rank / prune during search — never authoritative)
- **Elementary Judge node** — pre-commit reviewer that scores admissibility and prunes branches before they
  enter the proof DAG (strongest soft lever).
- **Constrained-scope framing** — generation roles receive the closed justification vocabulary and
  elementary-method instructions. The prose files under `agent/instructions/` and the contents of
  `knowledge/` are not implicitly injected into prompts.
- **Paradigm scaffold** — force an intermediate elementary-arithmetic reasoning trace before any formalization.
- **Retrieval bias** — premise retrieval is restricted to a curated elementary subset of Mathlib.

### Implemented deterministic defenses

1. **Two-tier proof-term dependency audit** — walk the kernel proof term's constant-dependency closure; reject
   if it touches a **content-bearing denylist** declaration, while always permitting an **infrastructure
   allowlist** (`WellFounded.fix`, `Acc.rec`, `Nat.rec`, `Decidable`/`DecidableEq` instances, `SizeOf`,
   hierarchy instance projections) and **elementary-by-fiat** APIs (e.g. Legendre/QR). Classify each constant
   by `ConstantInfo` kind so plumbing is never mistaken for content. *Naive namespace-prefix matching
   over-rejects nearly every real elementary proof — see `../PLAN.md` §5 Layer 4.* The one gate that cannot be
   routed around.
2. **Axiom integrity** — kernel `collectAxioms`: accepted axiom set ⊆ `{propext, Classical.choice, Quot.sound}`
   (this, not a source scan, catches `sorry`/injected-axiom smuggling).
3. **Conservative source boundary** — before compilation, `lean_bridge.py` lexes model-authored Lean,
   permits only fixed umbrella imports, requires the requested theorem to be declared locally, and rejects
   quoted literals/identifiers, attributes, `#` commands, `unsafe`, `macro`, elaborator/evaluation bridges,
   `set_option`, foreign/native hooks, and related code-bearing tokens. The bridge also binds the emitted
   audit to a fresh nonce, validates the returned theorem/report shape, and derives the toolchain/manifest
   receipt. A content-passing legacy or synthetic report without that verified receipt is non-authoritative.

Only items 1–2 decide elementary certification. Item 3 narrows the untrusted-source attack surface and
prevents theorem/import substitution; it is not a substitute for the kernel dependency audit.

### Deferred defense-in-depth

- A full **elaborated-AST legality pass** in the LongCat `V_leg` style remains unimplemented. The current
  lexer-level source validator is deliberately conservative but is not described as a complete AST pass.
- A **restricted-import Lean environment** and a deterministic **tactic-palette whitelist** remain optional
  future filters. Prompt rules discourage problematic tactics, but tactics such as `simp`, `nlinarith`,
  `decide`, `polyrith`, or `exact?` can pull dependencies indirectly, so the proof term must always be
  re-audited.

The **boundary rulings** for contested tools (Pell fundamental solution, roots-of-unity filter, QR/Jacobi,
Zsygmondy, LTE `p=2`, `v_p` vs `p`-adic) are pinned in `../PLAN.md` §2.1 and declared in
`allowed_toolkit.yaml` (`boundary_rulings`) — but note they are **declared, not enforced**: no gate consults
`Toolkit.ruling()` yet.

The structured **method-ledger** gate is the deterministic admission filter for informal search. It can
reject malformed, logically incomplete, numerically false, or explicitly non-elementary candidates, but a
pass is only `soft_proven`. Certification additionally requires a compiling Lean proof, a passing Layer-4
audit, statement faithfulness, and trusted production formalizer/faithfulness components. Scripted and
generic duck-typed components default to `certification_trusted=False` and cannot mint authority.

> **Layer 4 is now built + live-validated (Lean 4.30.0).** `lean/Audit.lean` (extractor),
> `lean_audit.py` (the decision logic), and `lean_bridge.py` (the runner) implement the proof-term
> dependency + axiom audit. Confirmed: an elementary core proof passes; a `sorry` proof is rejected
> via the axiom whitelist. See
> [`../../research/docs/lean_layer4_and_population.md`](../../research/docs/lean_layer4_and_population.md).

## Artifacts that live here
- `denylist.yaml` — banned methods, Mathlib namespaces, and lemma families.
- `allowed_toolkit.yaml` — the positive elementary toolkit + `boundary_rulings` (mirrors `agent/instructions/elementary_proof_rules.md`).
- `ledger.schema.json` — the Draft-07 step-ledger schema.
- Layer-1–3 gate: `ledger.py`, `obligations.py`, `scanner.py`, `gate.py`, `toolkit.py`, `report.py`.
- Layer-4 (authoritative Lean audit): `lean/` (`Audit.lean` extractor) + `lean_audit.py` (decision logic) + `lean_bridge.py` (runner) + `lean_server.py` (persistent Mathlib REPL). *(There is no `auditor/` directory.)*
