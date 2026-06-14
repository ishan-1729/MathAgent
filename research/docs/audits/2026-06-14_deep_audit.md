# MathAgent — Deep Audit & Doc/Code Consistency Check

**Date:** 2026-06-14  
**Method:** multi-agent orchestration — 5 Opus 4.8 code-audit workers (by subsystem) + 3 Opus 4.8 doc-consistency workers (system_design.md, build_status.md, PLAN.md) + **2 adversarial Codex GPT-5.5-xHigh agents** (one attacking soundness, one hunting doc overclaims), then Opus 4.8 skeptic-verifiers confirming/refuting every critical/high/medium finding (default-refute on doubt).  
**Scale:** 39 agents, 45 raw findings → 45 deduped → 28 verified → **21 confirmed**, 1 uncertain, 6 refuted (false positives filtered).  
**Test baseline at audit time:** offline pytest PASSES (exit 0, ~8–9 skipped live-Lean); 0 TODO/FIXME/bare-except; skeleton check passes.

> ⚠️ The green offline suite is partly *why* the soundness holes persist: the fail-open faithfulness and the `True := trivial` certification are asserted as **expected** outcomes by the tests themselves, and the RCE/timeout/desync paths use scripted stand-ins that cannot raise or inject. A clean offline run does **not** imply a sound production path here.

---

## Executive summary

MathAgent is a number-theory proving system whose defining guarantee is soundness: a multi-layer gate (typed-ledger deterministic checks -> adversarial review -> Lean Layer-4 dependency/axiom audit -> statement faithfulness) is supposed to ensure that anything certified "authoritative_elementary" is a genuine, elementary, faithful proof of the requested goal. This audit confirms 21 distinct defects (after de-duplication; the evidence set lists several IDs twice). Of these, 5 are CRITICAL and 7 are HIGH, and the majority of the critical/high findings are real soundness holes that can cause a wrong, non-elementary, or unfaithful proof to be accepted as authoritative.

The single most important theme, flagged independently by both Codex adversarial agents and by the primary auditor, is that the faithfulness panel -- the only binding between the informal claim and the Lean statement -- FAILS OPEN. FormalizeAuditResult.faithful returns True when no checker is configured (formalize_bridge.py:58-60, verified directly), and faithfulness is opt-in and OFF by default in the production CLI (scripts/prove.py). So the default `prove.py --terminal-gate` invocation prints authoritative_elementary=True for a proof of `True := trivial`, exactly contradicting the documented formula authoritative_elementary = compiled AND audit.passed AND faithful (system_design.md:153,509). This is codified as a PASSING test, so it is intended behavior, not an accident.

Beyond that, the elementary/correctness gate has multiple independent soundness gaps: (1) a sentinel-injection RCE-of-the-audit where an LLM proof can self-report a clean empty audit (lean_bridge.py), (2) an actual RCE via sympy.sympify on model-controlled descent expressions reachable on every prove attempt (numeric.py, reproduced end-to-end), (3) RalphLoop -- the engine the DAG uses at every node -- crashes open on NEEDS_REVIEW with no judges, admitting elastic-justification steps (descent/vieta_jumping) with zero review while the sibling FlatDriver correctly guards the same case, and (4) the gate never checks that a ledger's conclusion step or the decomposition sketch's conclusion actually matches the stated goal. The Layer-4 allowlist also over-exempts denylisted dependencies via unscoped bare-component matching, though no live stock-Mathlib trigger was demonstrated.

The remaining findings are medium/low correctness, error-handling, and resource-leak issues (greedy verdict regex degrading the judge/tournament layers, locale-comma answer mis-grading on the headline accuracy metric, LeanServer stream desync / process leaks / unhandled BrokenPipeError, and stale-decomposition state hygiene). Consistency review surfaced several real doc-vs-code overclaims and partials, most notably the faithfulness fail-open contradiction, the ArXivMath "elementary gate" that is only a prose substring filter, the Layer-4 "internal dependency closure exempt" wording (code is actually name-scoped and STRICTER, failing closed), the unwired boundary_rulings accessor, and a stale build_status.md (test counts and snapshot stamp out of date).

TEST BASELINE: The offline pytest suite PASSES (exit 0) with ~9 skipped live-Lean tests; 0 TODO/FIXME/HACK markers and 0 bare-excepts in agent/; check_repo_skeleton.py passes. Note the green suite is partly WHY these holes persist: the fail-open faithfulness and the True:=trivial certification are asserted as expected outcomes by the tests themselves, and the live/timeout/RCE/desync paths are not exercised offline (tests use scripted formalizers/judges that cannot raise or inject). A clean offline run does not imply a sound production path here.

---

## Confirmed defects (21)

### 🔴 CRITICAL (5)

#### Faithfulness check fails open: authoritative_elementary=True with no panel (default CLI path)
- **Location:** `agent/orchestrator/formalize_bridge.py:58-65, 142-147`  
- **Category:** soundness  
- **Why:** FormalizeAuditResult.faithful returns `self.faithfulness is None or self.faithfulness.faithful`, so absent faithfulness counts as faithful; authoritative = elementary_verified AND faithful collapses to audit-only. The Lean Layer-4 audit only certifies the term compiled and is dependency/axiom-clean -- it never checks the Lean statement matches the informal claim. Faithfulness is the ONLY binding between the goal and the formalizer-chosen statement. scripts/prove.py defaults faith=None unless --faithfulness is passed and threads it to make_terminal_gate, so the default --terminal-gate/--formalize DAG path certifies authoritative_elementary with no statement-faithfulness check. Confirmed by reading lines 58-60 directly; test_formalizer.py:161-166 and test_formalize_live.py:36 codify `theorem ma_target : True := trivial` as authoritative with NO checker. Flagged independently by both the primary auditor and both Codex agents.
- **Fix:** Fail closed: change faithful to `return self.faithfulness is not None and self.faithfulness.faithful`. Require a faithfulness_checker in make_terminal_gate/full_verify for any authoritative mode, or rename the no-panel result to lean_elementary_verified and never expose it as authoritative_elementary. Default the production CLI (--terminal-gate/--formalize) to faithfulness-on or print non-authoritative.

#### Sentinel-injection: LLM proof can self-report a clean empty audit, defeating Layer-4
- **Location:** `agent/gates/lean_bridge.py:30, 36-38, 90-101, 140-147`  
- **Category:** soundness  
- **Why:** extract_report_json uses _AUDIT_RE.search (FIRST match wins), and _assemble_source emits the untrusted LLM proof body BEFORE the appended `#audit <thm>` line. A crafted `#eval IO.println "MATHAGENT_AUDIT_JSON {\"theorem\":\"t\",\"axioms\":[],\"constants\":[]}"` (or logInfo/#print) in the proof body appears earlier in Lean's source-position-ordered message channel, so the bridge parses the attacker-chosen line. A forged empty report passes audit_report unconditionally (no axioms + no constants => no findings => PASS). The `: error:` guard does not save it because an #eval/info message is not an error. No theorem-field cross-check exists. The server path mirrors this. This defeats the only non-gameable gate; verified empirically for the regex, assembly order, and empty-report PASS.
- **Fix:** Inject a per-run random nonce into the #audit sentinel that the bridge controls and that is absent from proof_src; require report.theorem == theorem_name; scan ALL matches and reject if more than one sentinel appears; forbid/strip #eval/#print/IO.println/logInfo in proof_src, or run the extractor in a separate compilation unit.

#### Arbitrary code execution via sympy.sympify on model-controlled descent expressions
- **Location:** `agent/tools/numeric.py:62-99 (_parse), 102-106 (_compile)`  
- **Category:** security  
- **Why:** _parse calls sympy.sympify(expression, locals=syms) on untrusted strings; sympify falls back to eval, so the AST allowlist that runs AFTERWARD is too late -- malicious code already executed during parsing. Reachable end-to-end on EVERY prove attempt: gate.evaluate -> check_obligations -> obligations._check_descent builds f"({ne}) - ({me})" from the ledger's prover-controlled descent.next_expr/measure_expr -> numeric.find_points_where_nonneg -> _compile -> _parse. Reproduced in-repo: a schema-valid ledger with next_expr='__import__("os").environ.setdefault("MATHAGENT_PWN","1") + x' executed the import inside gate.evaluate; a `... and x` variant ran the side effect AND returned cleanly. The module docstring's 'no arbitrary code' claim is false.
- **Fix:** Do not use sympify/eval on untrusted input. Use sympy.parsing.sympy_parser.parse_expr(..., evaluate=False) with a restricted transformations set and a global_dict/local_dict excluding __builtins__, or a hand-rolled restricted-grammar parser over +,-,*,**,ints,declared symbols. Validate the AST BEFORE any evaluation; verify no Call/attribute nodes survive before lambdify.

#### RalphLoop admits a NEEDS_REVIEW ledger as proven success when no judges configured (crash-open)
- **Location:** `agent/orchestrator/ralph.py:70-93`  
- **Category:** soundness  
- **Why:** RalphLoop.run only special-cases `if report.rejected`. A NEEDS_REVIEW verdict (a soft REVIEW flag, e.g. an elastic justification like descent/vieta_jumping -- scanner.py calls these the central NT method and prime-smuggling site -- or a denylisted prose term) falls through to the judge block; with self.judges==[] the for-loop never runs, judge_notes stays empty, and line 93 returns RalphResult(success=True). DagDriver._prove then marks the node proven-direct. This is the DEFAULT production path: DagDriver sets judges = judges or [] and scripts/prove.py wires NO judges into the per-node Ralph loop; terminal_gate is also None by default. The sibling FlatDriver guards exactly this case at driver.py:134-137 (EXHAUSTED + 'review_unhandled'); RalphLoop omits the guard, so the two drivers disagree on the safety-critical case. (Note: this is the same root defect tracked as both RALPH-001/critical and F1/high in the evidence; rated critical here.)
- **Fix:** Mirror the FlatDriver guard: in RalphLoop.run, before declaring success, if report.verdict is Verdict.NEEDS_REVIEW and not self.judges, return a non-success (exhausted/failed) result and emit a 'review_unhandled' event. Import Verdict from agent.gates.gate.

#### Documentation overclaim turned soundness hole: authoritative_elementary advertised as faithfulness-checked but isn't
- **Location:** `agent/orchestrator/formalize_bridge.py:58 (vs system_design.md:153,509)`  
- **Category:** overclaim  
- **Why:** Docs state authoritative_elementary = compiled AND audit.passed AND faithful and 'authoritative_elementary also means faithful', i.e. the strongest certification guarantees a faithfulness check ran. The code reaches that status with faithfulness never run (fail-open faithful property). This is the documentation/contract face of the same defect as the top finding; surfaced separately because it is the user-facing guarantee that is violated, and it is reachable in production via independent argparse flags (--terminal-gate without --faithfulness). Codified by test_formalizer.py:161-166 certifying True:=trivial as authoritative for an n+0=n ledger.
- **Fix:** Same fix as the fail-open faithfulness defect: enforce faithful is not None for authoritative, OR split statuses into elementary_audited vs authoritative_elementary (latter requires faithfulness present and passing), and make CLI certification modes imply --faithfulness or print non-authoritative. Then correct the docs to match.

### 🟠 HIGH (5)

#### Decomposition sketch's conclusion-step claim is never checked against the parent goal
- **Location:** `agent/orchestrator/dag_driver.py:243-288`  
- **Category:** soundness  
- **Why:** _try_decomposition verifies the sketch's lemma steps match declared children and that the sketch is structurally valid, but nothing checks the terminal conclusion step (or the sketch top-level claim) actually concludes the parent goal. evaluate()/validate_structure never compare a conclusion to a goal. Reproduced live: a sketch citing a real child via a lemma step but whose conclusion proves 'SOMETHING TOTALLY DIFFERENT' was committed; after the child proved, the parent flipped to PROVEN with proof_kind='decomposition' and the wrong sketch flowed verbatim into proof_bundle. Faithfulness only re-binds the ROOT (and is off by default), so intermediate decomposition nodes are unbound from their goals.
- **Fix:** In _try_decomposition (and/or validate_structure) require goal_hash(conclusion.claim) == goal_hash(goal) and that the sketch top-level claim mirrors the goal; reject otherwise.

#### Gate never verifies the conclusion step's claim equals the ledger's stated claim/goal
- **Location:** `agent/gates/ledger.py:196-209`  
- **Category:** soundness  
- **Why:** validate_structure enforces exactly-one connected conclusion but never checks conclusions[0].claim == ledger.claim (the schema documents claim as 'mirrors the terminal step's claim'). evaluate() does not even receive the requested goal. So a ledger can advertise claim='THE REAL GOAL' while its conclusion proves 'A DIFFERENT THING' and still pass PASSED_DETERMINISTIC. This is the root enabler of the decomposition-conclusion hole and also affects FlatDriver/RalphLoop direct proofs. Existing fixtures (test_gate.py:32-36) deliberately use claim='c' with conclusion claim='done' and expect PASS, proving the mirroring is unenforced by design.
- **Fix:** Add a REJECT finding when goal_hash(conclusions[0].claim) != goal_hash(ledger.claim) (same canonicalization as the DAG, or at least NFC/whitespace-normalized equality), and bind ledger.claim to the requested goal in the deterministic path.

#### RalphLoop crash-open on NEEDS_REVIEW with no judges (node-level soundness path)
- **Location:** `agent/orchestrator/ralph.py:70-93`  
- **Category:** soundness  
- **Why:** Same defect mechanism as the critical crash-open finding, scoped to the per-node DAG view: a node whose prose mentions a REVIEW (not REJECT) term, or uses an elastic justification, is silently accepted as a direct elementary proof at every DAG node because RalphLoop returns success on NEEDS_REVIEW with empty judges. Only the root is later re-checked by the optional, default-off terminal Lean gate; intermediate nodes are never independently re-checked. Listed here as the high-severity node-level facet (de-duplicated with the critical RALPH-001).
- **Fix:** Same as the critical crash-open fix: require report.verdict is PASSED_DETERMINISTIC (or a fully-passing judge panel) before returning RalphResult(True, ...).

#### authoritative=True does not require faithfulness to have actually run (wiring + default omission)
- **Location:** `agent/orchestrator/formalize_bridge.py:58-65, 142-147`  
- **Category:** soundness  
- **Why:** make_terminal_gate and full_verify accept faithfulness_checker=None and default to it; faith is only computed when a checker is supplied; DagResult.authoritative_elementary inherits the loophole. The default production DAG path certifies authoritative_elementary without ANY statement-faithfulness check -- the only thing tying the formalizer-chosen Lean statement to the goal. This is the wiring/propagation facet of the critical fail-open faithfulness defect (de-duplicated; same root cause, listed to capture the make_terminal_gate/full_verify and DagResult propagation surface).
- **Fix:** Treat absent faithfulness as NOT faithful for authoritative; have make_terminal_gate/full_verify refuse to report authoritative without a checker; at minimum default the production CLI to faithfulness-on.

#### Layer-4 audit: bare-component allowlist over-exempts denylisted dependencies
- **Location:** `agent/gates/lean_audit.py:91-107, 123-136`  
- **Category:** soundness  
- **Why:** audit_report exempts a denylisted constant if it matches ANY allowlist pattern (allow = infrastructure_allowlist + elementary_by_fiat), and _name_matches treats a dot-less pattern as an unscoped dotted-COMPONENT match. The denylist contains bare components (IsDedekindDomain, EllipticCurve), so a constant whose name contains BOTH a denylisted component AND an unrelated bare infra component (Decidable, SizeOf, instHAdd) as separate segments is silently downgraded to INFO 'denylist_exempted' and accepted as elementary. Reproduced by direct execution: EllipticCurve.Decidable, IsDedekindDomain.SizeOf, EllipticCurve.Affine.instHAdd all returned PASS. Skeptic downgraded the corrected severity to medium because no stock-Mathlib trigger was demonstrated (real auto-generated names fuse the infra word into one camelCase segment, e.g. instDecidableEq, which does not match the bare pattern), and the finding's Nat.rec/Nat.gcd evidence is inaccurate (those are dotted, prefix-anchored patterns). Listed under the auditor's original high severity but flagged as a latent/defense-in-depth hole, not a demonstrated live bypass.
- **Fix:** Make the denylist authoritative regardless of allowlist for content-bearing namespace/decl matches: require an exemption to match the SAME prefix span (or only the infra/by-fiat decl itself, not any constant that merely contains an infra component elsewhere). Prefer prefix-anchored matching for both lists and require the allow match to DOMINATE the deny match (longer/more-specific prefix) rather than 'any match wins'.

### 🟡 MEDIUM (7)

#### A raising faithfulness judge crashes the whole DAG run (no exception isolation)
- **Location:** `agent/orchestrator/faithfulness.py:58`  
- **Category:** error-handling  
- **Why:** adversarial_check runs `votes = [judge(...) for lens in lenses]` with no try/except. A live Codex-backed judge can raise CodexError on network error/timeout/malformed response (codex_prover.py _run_codex). The exception propagates unguarded out of PanelFaithfulnessChecker.check, formalize_and_audit (line 144), the terminal-gate closure, and DagDriver.run line 138, aborting the entire run AFTER a proof was already found. This is crash-FAIL not crash-open (authoritative reads getattr(terminal,'authoritative',False) and terminal is never assigned), but one flaky call discards an expensive complete run. Not exercised offline (tests use ScriptedFaithfulnessChecker).
- **Fix:** Wrap each per-lens judge call in try/except; on error record a SingleVerdict(faithful=False, ...) (default-closed) so a crashing lens conservatively counts as unfaithful rather than aborting. Alternatively guard the terminal_gate call in DagDriver.run and treat an exception as a non-authoritative result.

#### LeanServer response reads are not correlated to commands; a timeout desyncs the stream and a stale report can certify the wrong theorem
- **Location:** `agent/gates/lean_server.py:111-154`  
- **Category:** soundness  
- **Why:** _read_response returns the next JSON object off the shared stdout queue with no request/response id matching, and on a command timeout (LeanBridgeError) the queue is never drained/reset and the proc is never restarted. The REPL's eventual response then becomes the response to the NEXT audit() call, permanently desyncing by one. audit_report never checks report.theorem == theorem_name (the extractor stamps it but it is never compared), so a stale/desynced report for a DIFFERENT proof can PASS, certifying a proof never compiled in this turn. The terminal gate uses this server and the repair loop reuses the same desynced instance, so this is on the authoritative path. Kept medium: requires a real timeout, a subsequent reuse, and the stale report happening to be a PASS while the current proof would fail.
- **Fix:** Verify report.theorem matches the requested theorem_name in audit/audit_report (REJECT on mismatch). On a command timeout, drain/reset the queue or restart the proc before the next command, and/or tag commands and responses with a monotonic id.

#### Server audit() can raise BrokenPipeError/OSError/ValueError (not LeanBridgeError) when the REPL has died, crashing the run
- **Location:** `agent/gates/lean_server.py:106-109, 141-154`  
- **Category:** error-handling  
- **Why:** _send does proc.stdin.write/flush with no error handling. If the persistent REPL crashed between audits (e.g. OOM on a large Mathlib proof), the next write raises BrokenPipeError (POSIX) / OSError [Errno 22] or ValueError on a closed pipe (Windows, verified on this host). audit() does not wrap _send and formalize_and_audit catches only LeanUnavailable/LeanBridgeError, so the exception propagates uncaught through the terminal gate (dag_driver.py:138, no try/except) and aborts the whole run. Realistic: prove.py creates one LeanServer reused across every node audit. Crash-open operationally (lost expensive job), NOT a soundness hole.
- **Fix:** In _send/_command/audit catch BrokenPipeError/OSError/ValueError, call self.close() to mark the server dead so the next call restarts, and re-raise as LeanBridgeError; optionally check proc.poll() before sending.

#### Depth-limit failures are memoized as permanent FAILED_GAP, poisoning the goal cache
- **Location:** `agent/orchestrator/dag_driver.py:157, 161-164, 181-183`  
- **Category:** correctness  
- **Why:** A node failing only because depth > max_depth is written to the shared deep-hash node as FAILED_GAP; the memo check at line 157 then short-circuits the SAME goal forever, even when later reached on a shallower branch where it would NOT hit the limit. Nodes are globally keyed by goal_hash and shared across branches, and DFS can explore deep-before-shallow, so one branch hitting the cap permanently marks the lemma unprovable for all branches -- losing provable proofs and making the outcome traversal-order-dependent (breaks claimed determinism). Not unsound (never accepts a false proof). Skeptic correction: the budget-exhaustion sub-claim is inert because Budget is monotonic with no in-run replenishment, so only the depth-limit path genuinely loses proofs; severity confirmed medium. The EXHAUSTED state exists (state.py:19) and FlatDriver uses it; DagDriver never does, confirming the conflation is unintended.
- **Fix:** Do not permanently memoize limit-induced failures: leave the node OPEN (reset state) when failure was due to depth so a shallower/later visit can retry, or introduce a distinct non-cacheable EXHAUSTED state that the memo check at line 157 does NOT treat as terminal.

#### Greedy verdict regex mis-parses judge/comparator output containing braces/brackets in prose
- **Location:** `agent/tools/codex_prover.py:190 (_VERDICT_RE), 315 (_JSON_ARRAY_RE), 213-221, 245-253, 283-292, 405-412`  
- **Category:** correctness  
- **Why:** _VERDICT_RE=r'\{.*\}' and _JSON_ARRAY_RE=r'\[.*\]' (both DOTALL) greedily span from the first delimiter to the last. Codex output frequently contains braces (sets {1,2,3}, Lean terms) or bracketed citations before the final JSON, so the captured span is invalid JSON, json.loads raises, and the failure branch is taken. Reproduced in a shell. The faithfulness judge and CodexReviewer fail CLOSED (sound but lossy); CodexComparator/CodexSolutionComparator return 0 (tie) and CodexCritic returns [] ('flawless', skip revision), silently degrading the search/judge/tournament layers in normal operation. The ledger parser already does the right thing (fenced ```json extraction); the verdict prompts do not.
- **Fix:** Extract the LAST balanced JSON object/array (scan from the end or use a balance-aware extractor), or instruct/extract a fenced ```json block as the ledger parser does. Prefer rightmost-balanced-object extraction.

#### Bare comma-separated answers always treated as sets, mis-grading locale decimals and ordered lists
- **Location:** `agent/tools/answer_check.py:91-99 (_split_collection), 110-124`  
- **Category:** correctness  
- **Why:** _split_collection treats ANY cleaned string with a comma and no '=' as a set. Verified: '3,5' -> {3,5} (so '3,5' vs '3.5' returns False), '1,000' -> {1,000} (vs '1000' False), and '(1,2)' vs '{1,2}' returns False even when order matches (tuple-vs-set kind mismatch). This is the live headline-metric grader (arxivmath.run_benchmark calls answers_equivalent), so mis-grades corrupt reported ArXivMath accuracy. NOT a soundness hole -- it is the accuracy-reporting path, not the proof gate. Tests never cover cross-delimiter or locale-comma cases.
- **Fix:** Only classify as a collection with explicit delimiters ({...} set, (...) tuple); try numeric/expression parse before comma-splitting; normalize thousands separators.

#### LeanServer.start() leaks the subprocess if Mathlib-load init times out or the REPL exits
- **Location:** `agent/gates/lean_server.py:73-85, 106-109, 141-154`  
- **Category:** resource-leak  
- **Why:** start() assigns self.proc = Popen(...) then calls _command(_init_cmd(), init_timeout_s) with no try/except. If _read_response raises LeanBridgeError (init timeout, or 'process exited' on EOF), self.proc stays set and the heavyweight Mathlib REPL (plus pump threads/pipes) is never terminated; callers catch the error without closing the server, so retries multiply the leak. In prove.py the eager `LeanServer().start()` is worse: a raising start() leaves server=None and the orphaned Popen unreachable. The genuinely durable leak is the still-alive Mathlib process on the timeout path. Resource leak, not a soundness hole; trigger requires init failure.
- **Fix:** Wrap the init handshake in try/except that calls self.close() (terminate proc, close pipes) before re-raising, so a half-started server is always cleaned up.

### ⚪ LOW (4)

#### _parse's exception list does not cover all sympify failure modes
- **Location:** `agent/tools/numeric.py:72`  
- **Category:** error-handling  
- **Why:** _parse catches only (sympy.SympifyError, SyntaxError, TypeError, AttributeError). Hostile input can raise others, e.g. ModuleNotFoundError from '__import__("nonexistent")' (reproduced), which escapes as a non-NumericError; the obligations guards (descent: NumericError/TypeError/ValueError; case_cover: NumericError/KeyError/TypeError) also miss it. Violates the module's documented contract. Skeptic corrected severity high->low: NO soundness impact (gate.evaluate wraps everything in except Exception and fail-closes to REJECTED, verified), and the only present-day callers are gate-protected; the real effect is a degraded diagnostic (generic internal_error REJECT instead of a targeted descent_expr_error code). Should be fixed alongside the SEC-1 RCE remediation.
- **Fix:** After replacing sympify (RCE fix), wrap parsing in `except Exception as e: raise NumericError(...)` so the contract holds, and broaden the obligations._check_descent/_check_case_cover guards accordingly.

#### LeanServer.close() never reaps the subprocess or closes stdout/stderr pipes
- **Location:** `agent/gates/lean_server.py:156-163`  
- **Category:** resource-leak  
- **Why:** close() closes stdin and calls terminate() but never calls proc.wait() (so the process is never reaped -> zombie on POSIX / lingering handle on Windows), never closes proc.stdout/stderr (the two daemon pump threads keep those FDs open), and has no kill() fallback (a REPL ignoring SIGTERM is never killed). start()'s failure path also leaves a half-started process uncleaned. Skeptic corrected severity medium->low: pure resource management, no soundness/memoization impact; the only production caller does one start/one close per process invocation, after which OS teardown reclaims FDs -- unbounded accumulation only materializes under repeated start/close cycles in one long-lived process, which the current entrypoint does not do.
- **Fix:** After terminate(), call proc.wait(timeout=...) with a kill() fallback on TimeoutExpired; close proc.stdout and proc.stderr; wrap start() so a failed init terminates the half-started process.

#### Failed decomposition leaves stale sketch/children committed on the node (no rollback)
- **Location:** `agent/orchestrator/dag_driver.py:276-288`  
- **Category:** correctness  
- **Why:** _try_decomposition calls commit_decomposition (sets node.proof=sketch, proof_kind='decomposition', children=child_keys) BEFORE recursing on children; on child failure it returns (False, []) with no rollback, and mark_failed only flips state to FAILED_GAP without clearing the metadata. assemble() expands node.children unconditionally, so a debug inspection renders a 'decomposition' subtree that was never proven. Skeptic corrected severity medium->low: NOT a soundness hole -- mark_proven_via_children refuses to mark a parent proven with unproven children, and the authoritative serializer proof_bundle gates on `proven` at every descendant (dag.py:262), so stale metadata never reaches the formalizer/Lean audit. Latent state-hygiene defect affecting only the debug-only assemble() consumer.
- **Fix:** Roll back the commit on child failure (clear node.proof/proof_kind/children, restore state) before returning False, or defer commit until all children are proven. Alternatively make assemble()/proof_bundle() refuse to expand a node that is not proven.

#### ArXivMath 'elementary-admissibility gate' is only a prose denylist substring filter
- **Location:** `scripts/run_benchmark.py:70, 73`  
- **Category:** overclaim  
- **Why:** _elementary_ok returns `not any(term in low for term in self._deny)` over the soft prose_terms list -- a pure lowercase substring check with no typed ledger, no gate.evaluate, no Lean Layer-4 audit (cruder than even the real Layer-1b scanner, which honors allow_context_terms). The code docstring and the run record (2026-06-13_nt_vanilla_vs_harness.md:26) both call it an 'elementary-admissibility gate', borrowing a name that elsewhere denotes the authoritative Lean audit. Skeptic corrected severity high->low: this is the final-answer ACCURACY path, no proof is certified here, nothing flows into the authoritative gate, so there is NO soundness hole -- a documentation/naming overclaim only. (Note: system_design.md itself correctly states ArXivMath is final-answer and routes certification to prove.py, so the docs angle is weaker than the original finding implied.)
- **Fix:** Rename it a 'prose denylist filter' rather than an elementary gate, or require harness candidates to emit ledgers and pass gate.evaluate; route proof-certification claims through the terminal Lean audit path.

---

## Doc-vs-code consistency (12 issues)

- **[CONTRADICTED]** authoritative_elementary = compiled AND audit.passed AND faithful; 'authoritative_elementary also means faithful'  
  *(system_design.md:153, :509)* — formalize_bridge.py:58-60 returns faithful=True when faithfulness is None, and faithfulness is opt-in/OFF by default in the CLI, so authoritative_elementary is reachable with faithfulness never run. The 'AND faithful' conjunct is vacuously satisfied. This is the documentation face of the top critical soundness hole and was flagged independently by both Codex agents. The UI defaults faithfulness ON when certifying (ui/server.py:75), but the CLI/library default does not.

- **[PARTIAL]** Elastic justifications (bounding, factorization, squeeze, descent, vieta_jumping) are forced to mandatory adversarial review.  
  *(system_design.md §3 'Layer 3, grounded safely')* — scanner.py:22 emits the REVIEW finding correctly, and the coprimality-provenance check is fully deterministic/REJECT as claimed. But 'mandatory review' is only enforced in FlatDriver (driver.py:134-137). In the Ralph/DAG production path RalphLoop.run returns success on a NEEDS_REVIEW ledger when judges is empty, so an elastic-justification ledger is admitted by the search with NO review unless --judges is configured. This is the consistency-review face of the RalphLoop crash-open critical bug.

- **[PARTIAL]** PROVEN is distinct from authoritative_elementary=True (formalized+compiled+audited+faithful); the harness can reach the first without the second.  
  *(system_design.md §4 pipeline + 'two distinct verdicts')* — The PROVEN-vs-authoritative distinction is real and correctly separated, but the 'AND faithful' conjunct is vacuously satisfied when no faithfulness checker is supplied (default OFF in prove.py), so authoritative can be True with faithfulness never run.

- **[OVERCLAIMED]** The elementary-by-fiat allowlist exempts an allowed-but-heavy API's INTERNAL dependency closure / provenance from the content denylist (resolving the 'allowed method, non-elementary Mathlib proof' trap).  
  *(system_design.md §3; PLAN.md §5 Layer 4 item B (278-281), §10 (418); build_status.md:99)* — lean_audit.py:123-136 is name-scoped, not closure/provenance-scoped: a transitive dep pulled in by legendreSym appears under its own (e.g. Cyclotomic) name and is still REJECTED (confirmed by test_lean_audit.py:85-103). The code is STRICTER than documented and fails CLOSED -- no soundness hole, but it would over-reject a real legendreSym-using proof, and the bare by-fiat token `legendreSym` does not even exempt Mathlib's own LegendreSymbol internals. (Separately, the bare-component MATCHING precedence is the lean_audit high/medium soundness finding above -- the matching is unsound in the other direction.)

- **[PARTIAL]** A four-valued boundary_rulings vocabulary (allowed / allowed_with_citation / allowed_per_problem_whitelist / disallowed) read via Toolkit.ruling() gives the harness's explicit, auditable boundary answer.  
  *(system_design.md §3 'Boundary rulings')* — The vocabulary and accessor exist exactly as described (allowed_toolkit.yaml:51-61, toolkit.py:62-65), but Toolkit.ruling() is never consulted by any gate or orchestrator -- it is data plus an unused accessor, not wired into enforcement.

- **[PARTIAL]** untrusted-input-gated invariant: memoized/retrieved artifacts pass the gate before reuse ('every artifact passes the same Layer-1/2 checks before reuse').  
  *(system_design.md §14 invariant 9; PLAN.md Layer 0 'No trust-by-cache' (227))* — The end-to-end invariant holds (nothing un-audited reaches authoritative_elementary: first-proof gating + final aggregate Layer-4 audit of proof_bundle + conservative goal_hash), but reuse is 'gated-once + audited-in-aggregate' rather than re-gated at each cache hit -- on a memo HIT the cached ledger is reused without an additional re-evaluate (dag_driver.py:153-156). The literal per-reuse wording overstates what happens on a cache hit; not a soundness hole given conservative hashing.

- **[OVERCLAIMED]** ArXivMath harness answerer runs 'under an elementary gate' / 'elementary-admissibility gate'.  
  *(system_design.md §525; run record 2026-06-13_nt_vanilla_vs_harness.md:25-26)* — run_benchmark.py:70-75 is a pure prose substring denylist filter -- no typed ledger, no gate.evaluate, no Layer-4. Naming overclaim on the final-answer accuracy path; no proof is certified, so no soundness consequence. (system_design.md elsewhere correctly states ArXivMath is final-answer and routes certification to prove.py, which softens the docs angle.)

- **[CONTRADICTED]** Snapshot header: regenerated on every edit so a stale doc is detectable.  
  *(build_status.md header (lines 3-5))* — build_status.md cites commit 29ad0da but HEAD is 3 doc-edit commits later (none regenerated the stamp), violating the doc's own integrity contract. The stamp also can't be reproduced from the shown short SHA. Minor but it is the doc's self-described freshness guarantee.

- **[OVERCLAIMED]** Aggregate offline result: 280 tests passed, 8 skipped; tests/ has 29 files.  
  *(build_status.md §5 (268), §3.8 (229))* — Actual run is 296 passed / 8 skipped; tests/ has 30 test_*.py + conftest = 31 files. Stale undercount -- PLAN.md §11 (442) correctly says 296. The 8-skipped figure is correct. (The baseline provided to this report cites ~9 skipped live-Lean tests; the documented and re-run figure is 8 skipped.)

- **[PARTIAL]** Decomposition is honest: sketch lemma steps must exactly match declared child goals; acyclicity prevents circular proofs; no trust-by-cache (memoized sub-lemmas re-checked).  
  *(build_status.md (78-81); PLAN.md Layer 0 (227), §4.1)* — Lemma-claims==child-goal-hashes, cycle guards, and sketch re-evaluation are all implemented. But the conclusion-step/top-level claim is NOT bound to the parent goal (the dag_driver high soundness finding above), and memoized PROVEN sub-lemmas are reused by goal_hash without re-running the gate on a cache hit -- sound only because caching happens after first-proof gating and goal_hash is conservative. The 'every artifact passes the same checks before reuse' wording overstates the cache-hit path.

- **[PARTIAL]** max_replan_depth is consumed by the decomposition loop.  
  *(build_status.md §3.5 (187), §7 (321); PLAN.md §4.1 (161))* — dag_driver.py:195-202 consumes the replan budget via can_replan/spend_replan, so it is functionally correct in the current single caller. But Budget.spend_replan (state.py:77-78) does NOT enforce the cap (increments unconditionally, unlike spend_call/spend_repair which raise on overflow); safety relies entirely on the caller checking can_replan first. Latent inconsistency, not currently triggerable.

- **[PARTIAL]** Connected-ledger invariant: 'one connected conclusion' / 'one connected terminal'.  
  *(system_design.md:78; build_status.md:67)* — ledger.py:211 marks orphan (disconnected, non-conclusion) steps as advisory INFO, not REJECT (test_ledger.py:124 asserts orphan_step is not a reject). A ledger with an unused extra step returns passed_deterministic with only an INFO. Flagged by Codex; the 'one connected conclusion' holds for the conclusion specifically, but unused steps are advisory, not rejected, so the stricter reading of the invariant is overstated.

---

## Codex (GPT-5.5-xHigh) adversarial — independent findings

- SOUNDNESS mission: Codex independently identified the strongest hole as authoritative_elementary=True being producible with NO faithfulness panel (fail-open default). It pinpointed the exact lines (formalize_bridge.py:58 faithful-when-None, :63 authoritative=elementary AND faithful, :143 checker only runs if not None) and the CLI default path (prove.py:86 faith=None, :125 terminal gate built with possibly-None checker, :149 prints authoritative_elementary). Concrete reproduced trigger: ledger claim '2+2=5', formalizer returns `theorem ma_target : True := by trivial`, full_verify(..., faithfulness_checker=None) reports authoritative_elementary=True. Noted the real Lean temp-file path could not run in its read-only sandbox (no writable tmp, REPL init timeout), so it confirmed via the boundary path with a passing LeanAuditResult.

- OVERCLAIM mission: Codex flagged the SAME root issue as the worst doc-vs-code contradiction -- system_design.md:153/:509 promise authoritative_elementary means faithfulness-checked, but the code treats missing faithfulness as success and test_formalizer.py:161 certifies True:=trivial as authoritative for an n+0=n ledger. The two missions converging on the faithfulness fail-open from independent angles (runtime exploit vs. documentation contract) is the report's highest-confidence signal.

- Codex surfaced two additional material overclaims the primary auditor also reached: (1) the ArXivMath 'elementary gate' is only a prose denylist substring filter (run_benchmark.py:70,73 -- no typed ledger, no evaluate, no Layer-4); (2) the 'one connected conclusion' ledger invariant is not enforced as a reject -- orphan steps are advisory INFO (ledger.py:211, test_ledger.py:124), so a ledger with unused steps still passes deterministically.

- Both Codex agents proposed the same fix direction the auditor endorsed: fail closed -- faithful() should return `self.faithfulness is not None and self.faithfulness.faithful`, require a checker in full_verify/make_terminal_gate for any authoritative mode, or split statuses (elementary_audited vs authoritative_elementary) so the strongest label can never be reached without a faithfulness panel; CLI certification modes should imply --faithfulness or print non-authoritative.

---

## Top risks

- Faithfulness fails open by default: the production CLI certifies authoritative_elementary=True for a proof of the WRONG (even vacuous True) statement whenever --faithfulness is omitted. This defeats the system's defining soundness guarantee on the default invocation and is the convergent finding of both Codex agents and the primary auditor. Codified as a passing test, so it is intended behavior, not a regression.
- The 'only non-gameable gate' (Lean Layer-4) is gameable via sentinel injection: a model-generated proof can emit `#eval IO.println "MATHAGENT_AUDIT_JSON {...empty...}"` before the real #audit and self-report a clean audit (first-match-wins regex + untrusted body emitted before #audit + no theorem-name cross-check). A sorry/non-elementary proof can be admitted as authoritative.
- Arbitrary code execution on EVERY prove attempt: sympy.sympify runs model-controlled descent expressions as Python before the AST allowlist, reachable through gate.evaluate -> obligations._check_descent. Reproduced end-to-end inside the gate. This is the trusted, sound deterministic gate executing untrusted code.
- RalphLoop -- the engine the DAG uses at every node -- crashes open on NEEDS_REVIEW with no judges (the default config), silently admitting elastic-justification steps (descent/vieta_jumping, the documented 'prime smuggling site') with zero adversarial review, while the sibling FlatDriver correctly guards the identical case.
- Goals are never bound to proofs at the informal level: neither a ledger's conclusion claim nor a decomposition sketch's conclusion is checked against the stated goal/parent goal, so a structurally valid proof of a DIFFERENT statement passes the deterministic gate and (for intermediate nodes) flows into the assembled bundle. Root faithfulness re-binds only the root and only when enabled.
- Operational crash-open on the most expensive live path: a raising faithfulness judge, a LeanServer command timeout (also a stale-report soundness hazard), or a dead-REPL BrokenPipeError each propagate uncaught and abort an otherwise-complete proving run. Untested offline because tests use scripted, non-raising stand-ins.
- Documentation/operator trust risk: build_status.md is stale (test counts, snapshot stamp), the boundary_rulings spec is unwired, and the ArXivMath 'elementary gate' label overstates a prose substring filter -- operators reading the docs will overestimate what is actually enforced.

---

## Recommended fixes (prioritized)

- **[P0] Make faithfulness fail closed and require it for authoritative certification**  
  `agent/orchestrator/formalize_bridge.py:58-65 (+ scripts/prove.py:86-89,125-127)`  
  → Change the faithful property to `return self.faithfulness is not None and self.faithfulness.faithful`. Have make_terminal_gate/full_verify refuse to report authoritative without a checker (or split into elementary_audited vs authoritative_elementary). Default the production CLI certification modes (--terminal-gate/--formalize) to faithfulness-on, or print non-authoritative when no checker ran. Update the test fixtures (test_formalizer.py:161-166, test_formalize_live.py:36) and system_design.md:153,509 to match the closed-loop semantics.

- **[P0] Close the Lean audit sentinel-injection hole**  
  `agent/gates/lean_bridge.py:30,36-38,90-101,140-147 (+ lean_server.py:31-37,149)`  
  → Inject a per-run random nonce into the #audit sentinel that the bridge controls and that is absent from proof_src; require the parsed report.theorem == theorem_name; scan ALL sentinel matches and reject if more than one appears; strip/forbid #eval/#print/IO.println/logInfo in proof_src, or run the extractor in a separate compilation unit. Apply the same theorem-name check on the server path.

- **[P0] Eliminate the sympify RCE in the numeric gate**  
  `agent/tools/numeric.py:62-99,102-106`  
  → Replace sympy.sympify with sympy.parsing.sympy_parser.parse_expr(..., evaluate=False) using a restricted transformations set and a local/global dict excluding __builtins__, or a hand-rolled restricted-grammar parser over +,-,*,**,ints,declared symbols. Validate the AST BEFORE any evaluation; verify no Call/attribute nodes survive before lambdify. Then broaden _parse to `except Exception -> raise NumericError` (fixes the related error-handling defect) and widen the obligations._check_descent/_check_case_cover guards. Remove the false 'no arbitrary code' docstring claim.

- **[P0] Mirror the FlatDriver NEEDS_REVIEW guard in RalphLoop**  
  `agent/orchestrator/ralph.py:70-93`  
  → Before declaring success, if report.verdict is Verdict.NEEDS_REVIEW and not self.judges, return a non-success (exhausted/failed) result and emit a 'review_unhandled' event (import Verdict from agent.gates.gate). Equivalently require report.verdict is PASSED_DETERMINISTIC (or a fully-passing judge panel) before returning RalphResult(True, ...). Add a regression test for the descent/empty-judges path.

- **[P1] Bind ledger and decomposition conclusions to their goals**  
  `agent/gates/ledger.py:196-209 and agent/orchestrator/dag_driver.py:243-288`  
  → In validate_structure, emit a REJECT when goal_hash(conclusions[0].claim) != goal_hash(ledger.claim) (using the DAG's canonicalization, or NFC/whitespace-normalized equality). In _try_decomposition (and/or validate_structure with a passed-in goal) require goal_hash(conclusion.claim) == goal_hash(goal) and that the sketch top-level claim mirrors the goal; reject otherwise. Update test_gate.py:32-36 which currently expects claim/conclusion mismatch to PASS.

- **[P1] Harden the Layer-4 allowlist/denylist matching precedence**  
  `agent/gates/lean_audit.py:91-107,123-136`  
  → Use prefix-anchored matching for both lists and require the allow match to DOMINATE the deny match (longer/more-specific prefix), so a denylisted namespace is not exempted merely because an unrelated bare infra component (Decidable/SizeOf/instHAdd) appears as a separate dotted segment. Only exempt the infra/by-fiat decl itself or constants under its prefix span. Add regression tests for EllipticCurve.Decidable / IsDedekindDomain.SizeOf returning REJECT.

- **[P1] Make Lean server I/O robust and correlate audit responses**  
  `agent/gates/lean_server.py:73-85,106-163 (+ agent/orchestrator/dag_driver.py:138, formalize_bridge.py:111-128)`  
  → Verify report.theorem == theorem_name in audit/audit_report (REJECT on mismatch). On a command timeout, drain/reset the queue or restart the proc (or tag commands/responses with a monotonic id) to stop stream desync. Catch BrokenPipeError/OSError/ValueError in _send/_command/audit, call close(), and re-raise as LeanBridgeError. Wrap start()'s init handshake in try/except that calls close() before re-raising; in close() add proc.wait(timeout) with a kill() fallback and close stdout/stderr. Optionally guard the terminal_gate call in DagDriver.run.

- **[P1] Isolate faithfulness-judge exceptions (default-closed)**  
  `agent/orchestrator/faithfulness.py:58`  
  → Wrap each per-lens judge call in try/except; on error record a SingleVerdict(faithful=False, ...) so a crashing lens conservatively counts as unfaithful rather than aborting the whole DAG run. Add a test with a raising judge.

- **[P2] Stop poisoning the goal cache with depth-limit failures**  
  `agent/orchestrator/dag_driver.py:157,161-164,181-183`  
  → Do not permanently memoize limit-induced failures: leave the node OPEN (reset state) when failure was due to depth, or use a distinct non-cacheable EXHAUSTED state (already defined in state.py:19, used by FlatDriver) that the memo check at line 157 does NOT treat as terminal. Add a cross-branch test (deep-then-shallow visit of the same goal).

- **[P2] Extract the rightmost balanced JSON in Codex verdict parsing**  
  `agent/tools/codex_prover.py:190,315 (+ 213-221,245-253,283-292,405-412)`  
  → Replace the greedy {.*}/[.*] regexes with a rightmost-balanced-object/array extractor (or require/extract a fenced ```json block as agent/gates/ledger.py:69-78 already does for the ledger). Add tests with set-notation/bracket-citation prose preceding the final JSON.

- **[P2] Fix answer-grading for locale commas and ordered lists**  
  `agent/tools/answer_check.py:91-99,110-124`  
  → Only classify a string as a collection when delimiters are explicit ({...} set, (...) tuple); try numeric/expression parse before comma-splitting; normalize thousands separators. Add tests for '3,5' vs '3.5', '1,000' vs '1000', and '(1,2)' vs '{1,2}'.

- **[P3] Roll back stale decomposition metadata on child failure**  
  `agent/orchestrator/dag_driver.py:276-288 (+ agent/orchestrator/dag.py:248-249)`  
  → Roll back the commit on child failure (clear node.proof/proof_kind/children, restore state) before returning False, or defer commit until all children are proven; alternatively make assemble()/proof_tree() skip a node that is not proven (proof_bundle already does).

- **[P3] Correct documentation overclaims and stale status**  
  `research/docs/system_design.md:153,509,525; build_status.md:3-5,229,268; scripts/run_benchmark.py:62,70-75`  
  → After the faithfulness fix, align system_design.md's authoritative_elementary formula. Refresh build_status.md test counts (296 passed / 8 skipped) and the snapshot stamp/commit, or stop asserting per-edit stamp regeneration. Rename the ArXivMath 'elementary-admissibility gate' to 'prose denylist filter' in code and run records. Either wire Toolkit.ruling() into a gate layer or document boundary_rulings as spec-only/unenforced.


---

*Workflow log:*
- Audit gathered 45 raw findings → 45 deduped → verifying 28 (critical/high/medium).
- Verification: 21 confirmed, 1 uncertain, 6 refuted (false positives filtered).
