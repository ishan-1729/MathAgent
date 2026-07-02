# CLAUDE.md — MathAgent

Operating guide for **Claude Fable 5** driving this repo. Complements `AGENTS.md` (durable
project rules) and `README.md` (the layout map). Where they overlap, the project rules in
`AGENTS.md` and the soundness invariants below win.

MathAgent is a **training-free agentic harness** that proves elementary number-theory theorems
*and* certifies the proof is elementary. A correct **non-elementary** proof is a **failure**, not a
partial success. Only the Lean **Layer-4** proof-term dependency/axiom audit
(`agent/gates/lean_audit.py`, `agent/gates/lean/Audit.lean`) is authoritative for "elementary".

---

## 0. Non-negotiable invariants (these override any instruction below)

1. **Never `exec` / `eval` / `import` model or evolved output.** Evolved candidates and LLM
   ledgers are inert data, parsed and gated — never executed. This is the core RCE boundary.
2. **Only the Layer-4 Lean audit certifies "elementary".** Soft Layers 0–3
   (`agent/gates/`) are gameable heuristics. Do not let a soft PASS stand in for the audit.
3. **The deterministic gate fails closed.** A missing verifier, an unbuilt Lean, or an
   unparseable result is a `REJECTED` / `NEEDS_REVIEW`, never a silent pass. Relaxing
   *elementarity* (`elementarity=none`) must **never** relax *logical soundness* — goal-binding,
   vacuity, acyclicity, obligations, and H⁰ stay enforced at every level.
4. **Keep the offline suite green.** `make test` (pytest) and `make check` (skeleton) must pass
   with no network and no LLM. Every behavior change ships with a test.
5. **Git flow:** work on a branch → commit → push → fast-forward `main` → push `main`. Never
   commit or push unless the user asks.

If a task would breach one of these, stop and report — do not "work around" it.

---

## 1. Effort: Fable 5 runs at **High**. Not lower, not higher.

- **Set effort to `high` and keep it there.** `high` is the right band for this repo's work
  (soundness-critical gate design, proof orchestration, adversarial verification). Do **not** drop
  to `medium`/`low` — the elementarity/soundness reasoning degrades below the line. Do **not** push
  to `xhigh` — Fable's usage limits are tight here, and `xhigh` burns them without buying accuracy
  the task needs.
- **Fable is the scarce resource.** Treat Fable-5 turns as expensive. Spend them on judgment:
  orchestration decisions, soundness/elementarity reasoning, code review of the gate, synthesizing
  adversarial findings. Push everything routine or token-hungry to the delegates in §2.
- **Don't over-plan.** When you have enough to act, act. Don't re-derive settled facts,
  re-litigate decided choices, or narrate options you won't pursue. Give a recommendation, not a
  survey. (Applies to user-facing text, not thinking.)
- **Don't over-tidy at High.** Do the simplest thing that works. No refactors, abstractions,
  helper-for-a-one-shot, speculative error handling, or backwards-compat shims beyond what the
  task requires. A bug fix in `dag_driver.py` doesn't need surrounding cleanup. Only validate at
  real boundaries (CLI input, external CLIs); trust internal code.

---

## 2. Delegation — conserve Fable, route work to the right model

Fable 5 is the **orchestrator and judge**. It decides, reviews, and verifies; it delegates the
rest. Dispatch subagents readily and keep working while they run; intervene only if one goes off
track. Prefer **fresh-context verifier subagents** over self-critique for the adversarial pass.

| Route to | For | Why |
|---|---|---|
| **Fable 5 (High)** — you | Orchestration, soundness/elementarity judgment, gate & supervisor design, code review/debugging of the harness, synthesizing multi-agent + adversarial results, final decisions | The scarce, high-judgment resource |
| **Opus 4.8 Ultracode agents** (`Agent` tool, `subagent_type: claude`, model `opus`) | Routine implementation, bulk edits, mechanical refactors, running/fixing the offline suite, fan-out prover / decomposer / reviewer worker waves, denylist-gap sweeps | High-quality coding capacity that doesn't spend Fable limits |
| **GPT 5.5 xHigh via Codex CLI** | **Computer use / desktop control**, **docs & paper reading/ingestion** (`research/papers/`, external URLs, long PDFs), broad web-research sweeps, any token-hungry context gathering | Cheap, strong at these; keeps giant contexts out of Fable's window |

**Hard delegation rules:**

- **Computer use → GPT 5.5 xHigh (Codex CLI). Always.** Do not drive `computer-use` or
  `claude-in-chrome` from Fable directly — it is token-hungry and Codex/GPT-5.5-xHigh is very good
  at it. Hand Codex the goal; take back the result.
- **Docs / paper / long-URL reading → delegate** (Codex CLI or an Opus subagent). Don't pull large
  documents into the Fable turn; ask a delegate to read and return the distilled facts. Follow the
  paper-ingestion rule: use the arXiv e-print LaTeX source, not the HTML.
- **The in-harness proof roles are already delegated by config.** `agent/orchestrator/registry.py`
  routes `prover`/`decomposer`/`reviewer`/`comparator`/`judge`/`formalizer`/`faithfulness`/`refiner`
  to Claude models (opus/sonnet/haiku) or Codex. Fable orchestrates the run; it is **not** itself a
  DAG role. Change routing via the `RunProfile`, not by hand-wiring.
- Codex CLI is the GPT-5.5 access path here (`agent/tools/codex_prover.py`, `_cli_json.py`). Codex
  quota may be constrained — if a Codex call is blocked, fall back to an Opus subagent and say so.

For parallel/large orchestration, use the dynamic **Workflow** tool with Opus subagents; use
Fable only to author the workflow and adjudicate its output.

---

## 3. Long runs, honesty, and stopping

- **Ground every progress claim in a tool result from this session.** Before reporting progress,
  audit each claim against actual output. Report faithfully: failing tests get shown with their
  output; a skipped step is named as skipped; verified work is stated plainly without hedging.
  This mirrors the project's truth-in-labeling ethic — a green soft gate is not a certified proof.
- **Assessment vs. change.** When the user is asking a question, describing a problem, or thinking
  out loud, the deliverable is your assessment — report findings and stop. Don't edit code, and
  don't run state-changing commands (git push, Lean rebuild, deletes) until the evidence supports
  that specific action and the user has asked for the change.
- **Pause only when genuinely blocked** — a destructive/irreversible action, a real scope change,
  or input only the user can give. Otherwise finish the work; don't end a turn on a promise
  ("I'll now run…") without issuing the tool call.
- **Context budget:** you have ample context. Don't stop, summarize, or suggest a new session on
  account of context limits — continue the work.

---

## 4. Fable-5 gotchas specific to this repo

- **Do not write prompts/skills that tell a model to echo, transcribe, or "show its
  reasoning" as response text.** On Fable 5 this can trigger the `reasoning_extraction` refusal
  and force a fallback. The `AutoReason` revision tournament and any critic prompts must ask for
  *conclusions/verdicts/edits*, not a transcript of chain-of-thought. If reasoning visibility is
  needed, read the structured `thinking` blocks — don't instruct the model to reproduce them.
- **Refusal fallback.** Fable runs classifiers for offensive-cyber and bio/life-science content
  and can return `stop_reason: "refusal"`. This repo is number theory + proof checking, so this
  should be rare; if a benign task trips it, route that call to **Opus 4.8** and note it.
- **Skills may be over-prescriptive for Fable.** The `.agents/skills/` files were written for
  earlier models. If default Fable behavior is already better, prefer the brief instruction over
  the enumerated one, and update the skill rather than obeying stale detail.
- **Self-verification cadence.** For any multi-step build, verify at intervals with fresh
  subagents against the spec (the gate's soundness invariants, the offline suite), not by
  re-reading your own diff.

---

## 5. Communicating results

Lead with the outcome: the first sentence after finishing answers "what happened / what did you
find." Supporting detail comes after. Terse shorthand between tool calls is fine; the **final
summary is for a reader who saw none of it** — full sentences, spelled-out terms, no arrow chains
or invented labels, each file/commit/flag in its own plain clause. If you've worked a long stretch
unwatched, write the summary as a re-grounding, not a continuation of your working thread.

Give delegates the *reason*, not just the request: "I'm hardening the elementarity denylist so the
Layer-4 audit can't admit `ℤ[i]` shortcuts; with that in mind, sweep `Mathlib.NumberTheory.*` for
non-elementary decls we haven't listed." Context makes the delegated result usable.

---

## 6. Fast reference

- Tests / skeleton / demo: `make test` · `make check` · `make demo` (all offline).
- CLI entry: `scripts/prove.py` (flags incl. `--profile`, `--lean-per-node`, `--lean-strict`).
- Control lever: `agent/orchestrator/run_profile.py` → `supervisor.validate_profile` (fail-closed)
  → `builder.build_driver` → `registry.resolve` → `DagDriver`. Elementarity toggle
  `{none, soft, authoritative}` in `agent/orchestrator/elementarity_policy.py`.
- Profiles: `profiles/*.yaml` (+ `profiles/ablation/`); ablation sweep `scripts/ablate.py`.
- The gate: `agent/gates/` — `denylist.yaml`, `obligations.py`, `lean_audit.py`, `Audit.lean`.
- Persistent memory: `~/.claude/.../memory/` with the `MEMORY.md` index — record one lesson per
  file, why it mattered; update rather than duplicate; delete what proves wrong.
