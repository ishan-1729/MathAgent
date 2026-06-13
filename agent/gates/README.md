# agent/gates/

**The defining mechanism of MathAgent.** These gates enforce the elementary-only constraint.

> **Load-bearing insight (from the literature review): "Lean-verified ≠ elementary."**
> A proof can compile against Mathlib while freely invoking class groups, elliptic curves, p-adics, or
> Mihailescu's theorem. Compilation success certifies *correctness*, never *method admissibility*. So the
> hard gate is a **proof-term dependency audit**, not "it compiled." Also: elementarity is a property of the
> *proof*, with no projection operator — you cannot "round" a class-group argument into a descent argument
> (unlike numeric constraints such as integrality). Hence soft pruning + a hard verification gate, never a
> single soft judge.

## The dual gate

### Soft gates (rank / prune during search — never authoritative)
- **Elementary Judge node** — pre-commit reviewer that scores admissibility and prunes branches before they
  enter the proof DAG (strongest soft lever).
- **Constrained-scope framing** — the allowed toolkit is pinned as an explicit, agent-immutable fact set.
- **Paradigm scaffold** — force an intermediate elementary-arithmetic reasoning trace before any formalization.
- **Retrieval bias** — premise retrieval restricted to a curated elementary corpus in `knowledge/`.

### Hard gates (deterministic accept/reject — authoritative), ranked by robustness
1. **Two-tier proof-term dependency audit** — walk the kernel proof term's constant-dependency closure; reject
   if it touches a **content-bearing denylist** declaration, while always permitting an **infrastructure
   allowlist** (`WellFounded.fix`, `Acc.rec`, `Nat.rec`, `Decidable`/`DecidableEq` instances, `SizeOf`,
   hierarchy instance projections) and **elementary-by-fiat** APIs (e.g. Legendre/QR). Classify each constant
   by `ConstantInfo` kind so plumbing is never mistaken for content. *Naive namespace-prefix matching
   over-rejects nearly every real elementary proof — see `../PLAN.md` §5 Layer 4.* The one gate that cannot be
   routed around.
2. **Axiom integrity** — kernel `collectAxioms`: accepted axiom set ⊆ `{propext, Classical.choice, Quot.sound}`
   (this, not a source scan, catches `sorry`/injected-axiom smuggling).
3. **AST legality check** (LongCat `V_leg`-style) — statement unchanged, no `unsafe`/`macro`/redefinition,
   plus forbidden `import`/`open` rejection (coarse first filter).
4. **Tactic-palette whitelist** — restrict to elementary tactics; *necessary but not sufficient* (`simp`,
   `nlinarith`, `decide`, `polyrith`, `exact?` can silently pull heavy lemmas — always re-audit the term).

The **boundary rulings** for contested tools (Pell fundamental solution, roots-of-unity filter, QR/Jacobi,
Zsygmondy, LTE `p=2`, `v_p` vs `p`-adic) are pinned in `../PLAN.md` §2.1 and belong in `allowed_toolkit.md`.

For the v1 (informal-first) plan, the hard gate operates over the informal proof via a structured
**method-ledger** check, with the Lean dependency audit prototyped as the Lean track matures. See
`../PLAN.md` §5 for the full design, the denylist seed, and the v1-vs-later split.

> **Layer 4 is now built + live-validated (Lean 4.30.0).** `lean/Audit.lean` (extractor),
> `lean_audit.py` (the decision logic), and `lean_bridge.py` (the runner) implement the proof-term
> dependency + axiom audit. Confirmed: an elementary core proof passes; a `sorry` proof is rejected
> via the axiom whitelist. See `../../research/docs/lean_layer4_and_population.md`.

## Artifacts that live here (as they are built)
- `denylist.md` / `denylist.yaml` — banned methods, Mathlib namespaces, and lemma families.
- `allowed_toolkit.md` — the positive elementary toolkit (mirrors `agent/instructions/elementary_proof_rules.md`).
- `auditor/` — the dependency-audit + AST-legality tool (Lean track).
