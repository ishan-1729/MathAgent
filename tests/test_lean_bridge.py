"""Tests for the Lean Layer-4 bridge (compile -> extract -> audit).

Two flavors:
  - LIVE tests (`@live`): opt-in, skipped unless `lean` is installed AND env var
    MATHAGENT_LEAN_TESTS=1 is set, so the default suite needs no Lean toolchain. Validated against
    Lean 4.30.0 during development (an elementary proof passes; a `sorry` is rejected via sorryAx).
  - OFFLINE tests: the sentinel-injection guards (report parsing, proof sanitization, nonce) run
    with no Lean toolchain at all.
"""
import os

import pytest

from agent.gates import lean_bridge

_LIVE = os.environ.get("MATHAGENT_LEAN_TESTS") == "1" and lean_bridge.available()
# Apply this only to the live tests so the offline sentinel-injection tests still run by default.
live = pytest.mark.skipif(
    not _LIVE, reason="set MATHAGENT_LEAN_TESTS=1 and install lean for live Lean-audit tests")


@live
def test_live_elementary_proof_passes():
    res = lean_bridge.audit_lean_source(
        "theorem ma_add_zero (n : Nat) : n + 0 = n := Nat.add_zero n",
        "ma_add_zero", timeout_s=300)
    assert res.passed, [str(f) for f in res.rejects()]


@live
def test_live_sorry_is_rejected():
    res = lean_bridge.audit_lean_source(
        "theorem ma_sorry : (2 : Nat) = 2 := by sorry", "ma_sorry", timeout_s=300)
    assert not res.passed
    assert "sorry_axiom" in {f.code for f in res.rejects()}


# ---- offline sentinel-injection guards (L2 fix a): no live Lean needed ----

class TestSentinelInjectionOffline:
    """The extractor's report parsing must reject a proof body that forges its own sentinel."""

    JSON = '{"theorem":"t","axioms":[],"constants":[]}'

    def test_nonce_required_when_provided(self):
        nonce = lean_bridge.make_nonce()
        good = f"MATHAGENT_AUDIT_JSON {nonce} {self.JSON}"
        assert lean_bridge.extract_report_json(good, nonce) == self.JSON
        # A line without the run nonce is NOT accepted when a nonce is in force.
        bare = f"MATHAGENT_AUDIT_JSON {self.JSON}"
        assert lean_bridge.extract_report_json(bare, nonce) is None
        # Wrong nonce rejected.
        assert lean_bridge.extract_report_json(good, "deadbeef") is None

    def test_forged_second_sentinel_rejected(self):
        nonce = lean_bridge.make_nonce()
        forged_empty = '{"theorem":"t","axioms":[],"constants":[]}'
        # An attacker emits a clean empty report via the bare sentinel, then the real one follows.
        text = (f'MATHAGENT_AUDIT_JSON {forged_empty}\n'
                f'MATHAGENT_AUDIT_JSON {nonce} {{"theorem":"t","axioms":["sorryAx"],"constants":[]}}')
        assert lean_bridge.extract_report_json(text, nonce) is None
        # Even without a nonce in force, >1 bare sentinel is rejected (fail closed).
        two = f"MATHAGENT_AUDIT_JSON {forged_empty}\nMATHAGENT_AUDIT_JSON {forged_empty}"
        assert lean_bridge.extract_report_json(two) is None

    def test_make_nonce_is_fresh_and_unguessable(self):
        a, b = lean_bridge.make_nonce(), lean_bridge.make_nonce()
        assert a != b and len(a) >= 16 and a.isalnum()

    def test_reject_eval_and_print_in_proof(self):
        for bad in (
            'theorem t : True := by trivial\n#eval IO.println "MATHAGENT_AUDIT_JSON {}"',
            "theorem t : True := trivial\n#print axioms t",
            "theorem t : True := trivial\n#reduce (1 + 1)",
            'theorem t : True := by have := dbg_trace "x"; trivial',
            'def f := logInfo "hi"',
        ):
            with pytest.raises(lean_bridge.LeanBridgeError):
                lean_bridge._reject_if_forbidden(bad)
        # A clean proof passes the sanitizer (no raise).
        lean_bridge._reject_if_forbidden("theorem t (n : Nat) : n + 0 = n := Nat.add_zero n")

    def test_reject_elaborator_shadowing_of_audit(self):
        # Codex residual P0: an untrusted body redefines the `#audit` elaborator so the appended
        # trusted `#audit <thm> "<nonce>"` runs attacker code that emits ONE clean nonce-bearing
        # sentinel (passing both the >1-count and the nonce check). The sanitizer must ban the
        # elaborator/macro/syntax (re)definition keywords that make this shadow possible.
        shadows = (
            'elab "#audit " id:ident nonce:(str)? : command => do\n'
            '  logWarning ("MATHAGENT_AUDIT_JSON " ++ "x")\n'
            'theorem ma_bad : False := by sorry',
            'macro "#audit " id:ident : command => `(theorem dummy : True := trivial)',
            'elab_rules : command | `(#audit $x) => pure ()',
            'macro_rules | `(p) => `(q)',
            'syntax "#audit " ident : command',
            'notation "AUDIT" => 1',
            'infixl:65 " ⊕ " => HAdd.hAdd',
            'theorem t : True := by trivial\nlogWarning "x"',
            'def f := logError "boom"',
        )
        for bad in shadows:
            with pytest.raises(lean_bridge.LeanBridgeError):
                lean_bridge._reject_if_forbidden(bad)

    def test_reject_run_cmd_and_qualified_emitters(self):
        # 2026-07-04 audit: `run_cmd`/`run_elab` run arbitrary CommandElabM/TermElabM code (incl.
        # logInfo) and were not banned; and the bare-name-only match was bypassable via namespace
        # qualification (`Lean.logInfo`, `_root_.IO.println`). All must raise now.
        for bad in (
            'run_cmd Lean.logInfo "hi"',
            'run_cmd Lean.logInfoAt stx "hi"',
            'run_elab do pure ()',
            'def f := Lean.logInfo "hi"',
            'def f := Lean.Elab.logWarningAt stx "x"',
            'def f := _root_.IO.println "x"',
            'def f := Std.dbg_trace "x"',
        ):
            with pytest.raises(lean_bridge.LeanBridgeError):
                lean_bridge._reject_if_forbidden(bad)

    def test_qualified_emitter_ban_has_no_new_false_positives(self):
        # The dotted-qualification fix is scoped to the EMIT family; the keyword tokens
        # (prefix/infix/syntax/...) stay bare-name-only, so legit dotted decl components and
        # identifiers merely containing a banned token must still pass.
        lean_bridge._reject_if_forbidden(
            "theorem t : True := trivial\n"
            "example := List.prefix_append\n"      # `.prefix` component must NOT trip `prefix`
            "example := Nat.log 2 8\n"             # `.log` is not an emit token
            "def my_run_cmd := 1")                 # substring inside an identifier

    def test_reject_literal_audit_token_and_sentinel_in_body(self):
        # Defense in depth: the untrusted body may not name the trusted `#audit` command nor embed
        # the sentinel string, even absent a redefinition keyword.
        with pytest.raises(lean_bridge.LeanBridgeError):
            lean_bridge._reject_if_forbidden("-- see #audit docs\ntheorem t : True := trivial")
        with pytest.raises(lean_bridge.LeanBridgeError):
            lean_bridge._reject_if_forbidden(
                'theorem t : True := trivial\n-- MATHAGENT_AUDIT_JSON sneaky')

    def test_shadowing_attack_cannot_be_assembled(self):
        # End-to-end of the residual P0 trigger: assembling the shadow proof must RAISE, so no
        # forged-but-clean report can ever reach the auditor.
        nonce = lean_bridge.make_nonce()
        attack = (
            'elab "#audit " id:ident nonce:(str)? : command => do\n'
            '  logWarning ("MATHAGENT_AUDIT_JSON " ++ "{\\"theorem\\":\\"ma_bad\\"}")\n'
            'theorem ma_bad : False := by sorry')
        with pytest.raises(lean_bridge.LeanBridgeError):
            lean_bridge._assemble_source(attack, "ma_bad", nonce)

    def test_forbidden_substring_in_identifier_is_allowed(self):
        # Guard against false positives: a method named e.g. `printGoal` is not `#print`, and
        # identifiers that merely START WITH a forbidden keyword (elaborate/syntaxTree/macroName/
        # logarithm/prefixSum) are NOT redefinitions and must pass.
        lean_bridge._reject_if_forbidden("def myEvaluator := 1\ndef printer := 2")
        lean_bridge._reject_if_forbidden(
            "theorem logarithm_pos : True := trivial\n"
            "def elaborate := 1\ndef syntaxTree := 1\ndef macroName := 1\ndef prefixSum := 1")

    def test_assemble_source_sanitizes_and_appends_audit_after_body(self):
        nonce = lean_bridge.make_nonce()
        src = lean_bridge._assemble_source("theorem t : True := trivial", "t", nonce)
        body_pos = src.index("theorem t")
        audit_pos = src.index("#audit t")
        assert body_pos < audit_pos  # untrusted body precedes the trusted appended #audit
        assert f'#audit t "{nonce}"' in src
        # Assembly refuses a forging body.
        with pytest.raises(lean_bridge.LeanBridgeError):
            lean_bridge._assemble_source('theorem t : True := by trivial\n#eval 1', "t", nonce)
