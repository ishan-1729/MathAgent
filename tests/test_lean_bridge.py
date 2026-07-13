"""Tests for the Lean Layer-4 bridge (compile -> extract -> audit).

Two flavors:
  - LIVE tests (`@live`): opt-in, skipped unless `lean` is installed AND env var
    MATHAGENT_LEAN_TESTS=1 is set, so the default suite needs no Lean toolchain. Validated against
    Lean 4.30.0 during development (an elementary proof passes; a `sorry` is rejected via sorryAx).
  - OFFLINE tests: the sentinel-injection guards (report parsing, proof sanitization, nonce) run
    with no Lean toolchain at all.
"""
import json
import os
import sys

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
        internal = lean_bridge._internal_theorem_name("t", nonce)
        audit_pos = src.index(f"#audit {internal}")
        assert body_pos < audit_pos  # untrusted body precedes the trusted appended #audit
        assert f'#audit {internal} "{nonce}"' in src
        assert f"namespace {lean_bridge._generated_namespace(nonce)}" in src
        # Assembly refuses a forging body.
        with pytest.raises(lean_bridge.LeanBridgeError):
            lean_bridge._assemble_source('theorem t : True := by trivial\n#eval 1', "t", nonce)


class TestInertLeanLanguageProfile:
    """Model-authored Lean must remain data until it passes the non-executing source validator."""

    def test_extractor_derives_toolchain_from_running_lean_not_environment(self):
        source = lean_bridge._extractor_src()
        assert "Lean.toolchain" in source
        assert "Lean.versionString" in source
        assert "MATHAGENT_TOOLCHAIN" not in source

    @pytest.mark.parametrize("escape", [
        "run_tac do pure ()",
        "conv => run_conv do pure ()",
        "by_elab do pure (.const ``True.intro [])",
        "exact eval% True.intro",
        "run_meta do pure ()",
        "run_term_elab do pure ()",
        "include_str `payloadPath`",
        "native_decide",
        "decide +native",
        "bv_decide",
        "bv_check payloadPath",
        "compile_def% payload",
        "compile_inductive% Payload",
        "initialize payload : Unit <- pure ()",
        "unsafe def payload := 1",
        "meta def payload := 1",
        "unsuppress_compilation in theorem nested : True := trivial",
        "set_option maxHeartbeats 0 in trivial",
        "@[implemented_by payload] def visible := 1",
        "#eval! 1",
    ])
    def test_meta_elaboration_native_and_evaluation_escapes_are_rejected(self, escape):
        src = f"theorem ma_target : True := by\n  {escape}\n  trivial"
        with pytest.raises(lean_bridge.LeanBridgeError):
            lean_bridge._assemble_source(src, "ma_target", lean_bridge.make_nonce())

    def test_run_tac_io_shape_is_rejected_before_any_subprocess(self, monkeypatch):
        called = {"subprocess": False}

        def _must_not_run(*_args, **_kwargs):
            called["subprocess"] = True
            raise AssertionError("untrusted source reached subprocess")

        monkeypatch.setattr(lean_bridge, "_run_lean_process", _must_not_run)
        payload = (
            "theorem ma_target : True := by\n"
            "  run_tac do\n"
            "    liftIO (IO.println \"must never execute\")\n"
            "  trivial"
        )
        with pytest.raises(lean_bridge.LeanBridgeError):
            lean_bridge.run_extractor(payload, "ma_target")
        assert called["subprocess"] is False

    def test_untrusted_local_import_is_rejected_but_library_imports_pass(self):
        for bad_import in ("LocalPayload", "Mathlib.LocalPayload", "Std.Data.HashMap"):
            with pytest.raises(lean_bridge.LeanBridgeError):
                lean_bridge._assemble_source(
                    f"import {bad_import}\ntheorem ma_target : True := trivial",
                    "ma_target", lean_bridge.make_nonce())
        lean_bridge._assemble_source(
            "import Mathlib\nimport Std\ntheorem ma_target : True := trivial",
            "ma_target", lean_bridge.make_nonce())

    def test_import_looking_line_inside_block_comment_is_never_hoisted(self):
        src = "/- inert comment\nimport LocalPayload\n-/\ntheorem ma_target : True := trivial"
        imports, body = lean_bridge._split_imports(src)
        assert imports == []
        assert "import LocalPayload" in body

        assembled = lean_bridge._assemble_source(src, "ma_target", lean_bridge.make_nonce())
        assert "/- inert comment\nimport LocalPayload\n-/" in assembled

    @pytest.mark.parametrize("separator", ["\r", "\v", "\f", "\x1c", "\u2028"])
    def test_non_lf_separator_cannot_alias_a_later_untrusted_import_line(self, separator):
        src = (
            f"import Mathlib{separator}import LocalPayload\n"
            "theorem ma_target : True := trivial"
        )
        with pytest.raises(lean_bridge.LeanBridgeError):
            lean_bridge._assemble_source(src, "ma_target", lean_bridge.make_nonce())

    def test_crlf_imports_remain_supported_with_lf_line_accounting(self):
        imports, body = lean_bridge._split_imports(
            "import Mathlib\r\ntheorem ma_target : True := trivial")
        assert imports == ["import Mathlib"]
        assert "theorem ma_target" in body

    def test_namespace_qualified_theorem_remains_allowed(self):
        src = "namespace Foo\ntheorem bar : True := trivial\nend Foo"
        nonce = lean_bridge.make_nonce()
        assembled = lean_bridge._assemble_source(src, "Foo.bar", nonce)
        assert f"#audit {lean_bridge._internal_theorem_name('Foo.bar', nonce)}" in assembled

    def test_dotted_namespace_partial_and_full_end_tracking_matches_lean(self):
        partial = (
            "namespace A.B\ntheorem x : True := trivial\nend B\n"
            "theorem y : True := trivial\nend A"
        )
        lean_bridge._assemble_source(partial, "A.y", lean_bridge.make_nonce())

        full = (
            "namespace A.B\ntheorem x : True := trivial\nend A.B\n"
            "theorem root_y : True := trivial"
        )
        lean_bridge._assemble_source(full, "root_y", lean_bridge.make_nonce())

    def test_verified_report_requires_nonce_scoped_local_declaration_before_restamping(self):
        nonce = lean_bridge.make_nonce()
        internal = lean_bridge._internal_theorem_name("ma_target", nonce)
        report = json.dumps({
            "theorem": internal,
            "toolchain": "leanprover/lean4:v4.30.0",
            "axioms": [],
            "constants": [{"name": internal, "kind": "theorem", "module": ""}],
        })
        restamped = json.loads(
            lean_bridge._restamp_verified_report(
                report, internal, "ma_target", provenance=lean_bridge._CORE_PROVENANCE))
        assert restamped["theorem"] == "ma_target"
        assert restamped["manifest"] == "core-only"
        assert restamped["provenance"] == lean_bridge._PROVENANCE_SCHEMA

        with pytest.raises(lean_bridge.LeanBridgeError, match="expected internal theorem"):
            lean_bridge._restamp_verified_report(
                json.dumps({"theorem": "Nat.add_comm", "constants": []}),
                internal, "ma_target", provenance=lean_bridge._CORE_PROVENANCE)
        imported = json.dumps({
            "theorem": internal,
            "constants": [{"name": internal, "kind": "theorem", "module": "Mathlib.Data.Nat"}],
        })
        with pytest.raises(lean_bridge.LeanBridgeError, match="not a declaration"):
            lean_bridge._restamp_verified_report(
                imported, internal, "ma_target", provenance=lean_bridge._CORE_PROVENANCE)

    def test_comment_declaration_cannot_substitute_imported_audit_target(self):
        # A regex-based formalizer may accidentally extract Nat.add_comm from the comment. The
        # bridge must bind the requested target to the exact generated declaration, not accept its
        # final component and audit Mathlib's pre-existing theorem instead.
        src = (
            "import Mathlib\n"
            "-- theorem Nat.add_comm\n"
            "theorem add_comm : True := trivial"
        )
        with pytest.raises(lean_bridge.LeanBridgeError, match="does not declare requested theorem"):
            lean_bridge._assemble_source(src, "Nat.add_comm", lean_bridge.make_nonce())

    def test_syntax_quotation_cannot_substitute_imported_audit_target(self):
        # Tokens inside a syntax quotation are data, not declarations.  A token-only declaration
        # tracker must not accept this decoy and let the appended command audit Mathlib's theorem.
        src = (
            "import Mathlib\n"
            "def decoy : Lean.Syntax := `(command| "
            "theorem Nat.add_comm : True := by trivial)"
        )
        with pytest.raises(lean_bridge.LeanBridgeError, match="syntax quotation"):
            lean_bridge._assemble_source(src, "Nat.add_comm", lean_bridge.make_nonce())

    def test_namespace_command_wrapper_cannot_substitute_imported_audit_target(self):
        src = (
            "import Mathlib\n"
            "with_weak_namespace Foo theorem Nat.add_comm : True := trivial"
        )
        with pytest.raises(lean_bridge.LeanBridgeError, match="with_weak_namespace"):
            lean_bridge._assemble_source(src, "Nat.add_comm", lean_bridge.make_nonce())

    def test_private_declaration_cannot_substitute_imported_raw_audit_target(self):
        # Audit.lean deliberately looks up the exact raw name.  Lean stores a private declaration
        # under a generated hashed name, so the textual decoy must not make the raw imported name
        # appear locally bound to the bridge.
        src = "import Mathlib\nprivate theorem Nat.add_comm : True := trivial"
        with pytest.raises(lean_bridge.LeanBridgeError, match="private"):
            lean_bridge._assemble_source(src, "Nat.add_comm", lean_bridge.make_nonce())

    def test_name_rewriting_command_cannot_substitute_imported_audit_target(self):
        src = (
            "import Mathlib\n"
            "deprecate to ma_fresh theorem Nat.add_comm : True := trivial"
        )
        with pytest.raises(lean_bridge.LeanBridgeError, match="deprecate"):
            lean_bridge._assemble_source(src, "Nat.add_comm", lean_bridge.make_nonce())

    def test_mutual_end_cannot_desynchronize_namespace_binding(self):
        src = (
            "import Mathlib\nnamespace Foo\nmutual\n"
            "def helper : Nat := 0\nend\n"
            "theorem Nat.add_comm : True := trivial\nend Foo"
        )
        with pytest.raises(lean_bridge.LeanBridgeError, match="mutual"):
            lean_bridge._assemble_source(src, "Nat.add_comm", lean_bridge.make_nonce())

    def test_lexer_is_exact_token_based_and_handles_nested_comments(self):
        # Escape words in comments and longer identifiers are inert and do not false-positive.
        lean_bridge._reject_if_forbidden(
            "/- outer /- run_tac; IO.Process.run -/ comment -/\n"
            "def run_tactic_count := 1\n"
            "theorem ma_target : True := trivial")
        # Quoted identifiers are intentionally outside the fail-closed language profile.
        with pytest.raises(lean_bridge.LeanBridgeError):
            lean_bridge._reject_if_forbidden("theorem «run_tac» : True := trivial")

    def test_requested_theorem_name_is_plain_and_declared(self):
        src = "theorem ma_target : True := trivial"
        for bad_name in ("ma_target\n#eval 1", "ma_target; run_cmd", "", "A..b"):
            with pytest.raises(lean_bridge.LeanBridgeError):
                lean_bridge._assemble_source(src, bad_name, lean_bridge.make_nonce())
        with pytest.raises(lean_bridge.LeanBridgeError):
            lean_bridge._assemble_source(src, "other", lean_bridge.make_nonce())

    def test_valid_elementary_proofs_and_pure_helpers_remain_allowed(self):
        src = (
            "import Mathlib\n"
            "def twice (n : Nat) := n + n\n"
            "theorem ma_target (n : Nat) : twice n = 2 * n := by\n"
            "  simp [twice, Nat.two_mul]"
        )
        assembled = lean_bridge._assemble_source(src, "ma_target", lean_bridge.make_nonce())
        assert "theorem ma_target" in assembled

    def test_lean_environment_does_not_inherit_provider_credentials(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "do-not-forward")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "do-not-forward")
        monkeypatch.setenv("MATHAGENT_TOOLCHAIN", "forged/toolchain:v0")
        monkeypatch.setenv("LEAN_PATH", "untrusted-module-search-path")
        monkeypatch.setenv("PATH", "trusted-path")
        env = lean_bridge._lean_env()
        assert env["PATH"] == "trusted-path"
        assert "OPENAI_API_KEY" not in env and "ANTHROPIC_API_KEY" not in env
        assert "MATHAGENT_TOOLCHAIN" not in env
        assert "LEAN_PATH" not in env

    def test_project_local_trusted_root_shadow_is_rejected(self, tmp_path):
        (tmp_path / "Mathlib.lean").write_text(
            "initialize payload : Unit <- pure ()\n", encoding="utf-8")
        with pytest.raises(lean_bridge.LeanBridgeError, match="shadows trusted import"):
            lean_bridge._reject_project_import_shadows(tmp_path)

    def test_compile_output_is_bounded_without_loading_it_all(self, tmp_path):
        argv = [sys.executable, "-c", "import sys; sys.stdout.write('x' * 4096); sys.stdout.flush()"]
        with pytest.raises(lean_bridge.LeanBridgeError, match="output exceeded"):
            lean_bridge._run_lean_process(argv, str(tmp_path), timeout_s=10, max_output=1024)

    def test_nonzero_lean_exit_cannot_certify_a_nonce_valid_report(self, monkeypatch):
        nonce = "fixednonce"
        report = '{"theorem":"ma_target","axioms":[],"constants":[]}'
        invoked = {}
        monkeypatch.setattr(lean_bridge, "make_nonce", lambda: nonce)
        monkeypatch.setattr(lean_bridge, "find_lean", lambda: "lean")

        def _failed_lean(argv, *_args, **_kwargs):
            invoked["argv"] = argv
            return 1, f"MATHAGENT_AUDIT_JSON {nonce} {report}"

        monkeypatch.setattr(lean_bridge, "_run_lean_process", _failed_lean)

        with pytest.raises(lean_bridge.LeanBridgeError, match="no audit JSON emitted"):
            lean_bridge.run_extractor("theorem ma_target : True := trivial", "ma_target")
        assert f"--memory={lean_bridge._LEAN_MAX_MEMORY_MB}" in invoked["argv"]

    def test_one_shot_report_stamps_runtime_toolchain_and_core_receipt(self, monkeypatch):
        nonce = "fixednonce"
        internal = lean_bridge._internal_theorem_name("ma_target", nonce)
        report = json.dumps({
            "theorem": internal,
            "toolchain": "leanprover/lean4:v4.30.0",
            "manifest": "attacker-controlled",
            "axioms": [],
            "constants": [{"name": internal, "kind": "theorem", "module": ""}],
        }, separators=(",", ":"))
        monkeypatch.setenv("MATHAGENT_TOOLCHAIN", "forged/toolchain:v0")
        monkeypatch.setattr(lean_bridge, "make_nonce", lambda: nonce)
        monkeypatch.setattr(lean_bridge, "find_lean", lambda: "lean")
        monkeypatch.setattr(
            lean_bridge, "_run_lean_process",
            lambda *_args, **_kwargs: (
                0, f"MATHAGENT_AUDIT_JSON {nonce} {report}"),
        )

        stamped = json.loads(lean_bridge.run_extractor(
            "theorem ma_target : True := trivial", "ma_target"))

        assert stamped["toolchain"] == "leanprover/lean4:v4.30.0"
        assert stamped["manifest"] == "core-only"
        assert stamped["provenance"] == lean_bridge._PROVENANCE_SCHEMA

    def test_project_manifest_change_during_compile_fails_closed(self, monkeypatch, tmp_path):
        nonce = "fixednonce"
        internal = lean_bridge._internal_theorem_name("ma_target", nonce)
        pin = "leanprover/lean4:v4.30.0"
        (tmp_path / "lean-toolchain").write_text(pin + "\n", encoding="utf-8")
        manifest = tmp_path / "lake-manifest.json"
        manifest.write_text('{"version":"1.2.0","packages":[]}\n', encoding="utf-8")
        report = json.dumps({
            "theorem": internal,
            "toolchain": pin,
            "axioms": [],
            "constants": [{"name": internal, "kind": "theorem", "module": ""}],
        }, separators=(",", ":"))
        monkeypatch.setattr(lean_bridge, "make_nonce", lambda: nonce)
        monkeypatch.setattr(lean_bridge, "find_lake", lambda: "lake")

        def mutate_then_report(*_args, **_kwargs):
            manifest.write_text(
                '{"version":"1.2.0","packages":[{"name":"mathlib"}]}\n',
                encoding="utf-8")
            return 0, f"MATHAGENT_AUDIT_JSON {nonce} {report}"

        monkeypatch.setattr(lean_bridge, "_run_lean_process", mutate_then_report)
        with pytest.raises(lean_bridge.LeanBridgeError, match="changed during audit"):
            lean_bridge.run_extractor(
                "theorem ma_target : True := trivial", "ma_target", project_dir=tmp_path)

    def test_missing_lean_is_resolved_before_a_temporary_directory_is_created(self, monkeypatch):
        called = {"mkdtemp": False}
        monkeypatch.setattr(lean_bridge, "find_lean", lambda: None)

        def _must_not_create(*_args, **_kwargs):
            called["mkdtemp"] = True
            raise AssertionError("temporary directory created before tool resolution")

        monkeypatch.setattr(lean_bridge.tempfile, "mkdtemp", _must_not_create)
        with pytest.raises(lean_bridge.LeanUnavailable):
            lean_bridge.run_extractor("theorem ma_target : True := trivial", "ma_target")
        assert called["mkdtemp"] is False

    def test_posix_tree_termination_escalates_the_process_group(self, monkeypatch):
        class _LeaderExitedButChildLives:
            pid = 4242

            def __init__(self):
                self.waits = 0

            def poll(self):
                return None if self.waits == 0 else 0

            def wait(self, timeout=None):
                self.waits += 1
                return 0  # the group leader exits promptly; a descendant may still ignore TERM

            def terminate(self):
                raise AssertionError("the fresh-session process group should be signalled")

            def kill(self):
                raise AssertionError("SIGKILL should target the whole process group")

        signals = []
        monkeypatch.setattr(lean_bridge.os, "name", "posix")
        monkeypatch.setattr(
            lean_bridge.os, "killpg", lambda pgid, sig: signals.append((pgid, sig)), raising=False)
        proc = _LeaderExitedButChildLives()

        lean_bridge._terminate_lean_tree(proc, grace_s=0.01)

        assert signals == [(proc.pid, 15), (proc.pid, 9)]
        assert proc.waits == 2

    @pytest.mark.parametrize(
        ("tree_terminated", "expect_taskkill"), ((True, False), (False, True)))
    def test_windows_resume_failure_preserves_primary_error_and_cleans_tree(
            self, monkeypatch, tree_terminated, expect_taskkill):
        class _Proc:
            pid = 4321

            def __init__(self):
                self.waits = 0
                self.killed = False

            def wait(self, timeout=None):
                self.waits += 1
                return 0

            def kill(self):
                self.killed = True

        class _Job:
            def __init__(self):
                self.closes = 0

            def close(self):
                self.closes += 1
                error = OSError("CloseHandle failed")
                error.tree_terminated = tree_terminated
                raise error

        proc, job = _Proc(), _Job()
        taskkills = []
        resume_error = OSError("NtResumeProcess failed")
        monkeypatch.setattr(lean_bridge.os, "name", "nt")
        monkeypatch.setattr(lean_bridge.subprocess, "Popen", lambda *_a, **_kw: proc)
        monkeypatch.setattr(lean_bridge, "WindowsMemoryJob", lambda *_a, **_kw: job)
        monkeypatch.setattr(
            lean_bridge, "resume_suspended_windows_process",
            lambda _proc: (_ for _ in ()).throw(resume_error))
        monkeypatch.setattr(
            lean_bridge.subprocess, "run",
            lambda argv, **kwargs: taskkills.append((argv, kwargs)))

        with pytest.raises(lean_bridge.LeanBridgeError, match="could not contain Lean process") as exc:
            lean_bridge._run_lean_process(["lean"], ".", timeout_s=1)

        assert exc.value.__cause__ is resume_error
        assert job.closes == 1 and proc.waits == 1 and not proc.killed
        assert bool(taskkills) is expect_taskkill
        if taskkills:
            argv, kwargs = taskkills[0]
            assert argv == ["taskkill.exe", "/PID", "4321", "/T", "/F"]
            assert 0 < kwargs["timeout"] <= 2

    def test_output_reader_start_failure_terminates_started_lean_process(self, monkeypatch):
        class _Stdout:
            def close(self):
                pass

        class _Proc:
            pid = 9876
            stdout = _Stdout()

            def poll(self):
                return None

        class _FailingThread:
            def __init__(self, *args, **kwargs):
                pass

            def start(self):
                raise RuntimeError("thread resources exhausted")

            def join(self, timeout=None):
                raise AssertionError("an unstarted thread must not be joined")

        proc = _Proc()
        terminated = []
        monkeypatch.setattr(lean_bridge.os, "name", "posix")
        monkeypatch.setattr(lean_bridge.subprocess, "Popen", lambda *_a, **_kw: proc)
        monkeypatch.setattr(lean_bridge.threading, "Thread", _FailingThread)
        monkeypatch.setattr(
            lean_bridge, "_terminate_lean_tree", lambda candidate, **_kw: terminated.append(candidate))

        with pytest.raises(lean_bridge.LeanBridgeError, match="output reader"):
            lean_bridge._run_lean_process(["lean"], ".", timeout_s=1)

        assert terminated == [proc]

    def test_normal_root_exit_still_tears_down_posix_process_group(self, monkeypatch):
        class _Stdout:
            def read(self, _size):
                return b""

            def close(self):
                pass

        class _Proc:
            pid = 8765
            stdout = _Stdout()
            returncode = 0

            def poll(self):
                return 0

        proc = _Proc()
        terminated = []
        monkeypatch.setattr(lean_bridge.os, "name", "posix")
        monkeypatch.setattr(lean_bridge.subprocess, "Popen", lambda *_a, **_kw: proc)
        monkeypatch.setattr(
            lean_bridge, "_terminate_lean_tree", lambda candidate, **_kw: terminated.append(candidate))

        assert lean_bridge._run_lean_process(["lean"], ".", timeout_s=1) == (0, "")
        assert terminated == [proc]

    def test_packaged_lake_scaffold_is_materialized_in_writable_cache(self, tmp_path):
        scaffold = tmp_path / "site-packages" / "formal" / "lean" / "mathagent_formal"
        (scaffold / "MathagentFormal").mkdir(parents=True)
        for name in lean_bridge._PROJECT_FILES:
            (scaffold / name).write_text(f"trusted {name}\n", encoding="utf-8")
        (scaffold / "MathagentFormal" / "Basic.lean").write_text(
            "theorem packaged : True := trivial\n", encoding="utf-8")
        cache = tmp_path / "user-cache"

        first = lean_bridge._materialize_packaged_project(scaffold, cache)
        second = lean_bridge._materialize_packaged_project(scaffold, cache)

        assert first is not None and first == second
        assert first.parent == cache / "lean"
        assert (first / "MathagentFormal" / "Basic.lean").is_file()
        assert os.access(first / "lake-manifest.json", os.W_OK)
        assert not list((cache / "lean").glob(".mathagent_formal-*"))
        # The immutable packaged scaffold itself is never used as Lake's writable project.
        assert first != scaffold and not (scaffold / ".lake").exists()

    def test_modified_content_addressed_scaffold_is_never_reused(self, tmp_path):
        scaffold = tmp_path / "site" / "mathagent_formal"
        (scaffold / "MathagentFormal").mkdir(parents=True)
        for name in lean_bridge._PROJECT_FILES:
            (scaffold / name).write_text(f"trusted {name}\n", encoding="utf-8")
        (scaffold / "MathagentFormal" / "Basic.lean").write_text(
            "theorem packaged : True := trivial\n", encoding="utf-8")
        cache = tmp_path / "cache"
        destination = lean_bridge._materialize_packaged_project(scaffold, cache)
        assert destination is not None
        (destination / "lake-manifest.json").write_text("tampered\n", encoding="utf-8")
        assert lean_bridge._materialize_packaged_project(scaffold, cache) is None

    def test_project_provenance_is_content_derived_and_manifest_sensitive(self, tmp_path):
        (tmp_path / "lean-toolchain").write_text(
            "leanprover/lean4:v4.30.0\n", encoding="utf-8")
        manifest = tmp_path / "lake-manifest.json"
        manifest.write_text('{"version":"1.2.0","packages":[]}\n', encoding="utf-8")

        first = lean_bridge._project_provenance(tmp_path)
        assert first.expected_toolchain == "leanprover/lean4:v4.30.0"
        assert first.manifest.startswith("sha256:") and len(first.manifest) == 71

        manifest.write_text(
            '{"version":"1.2.0","packages":[{"name":"mathlib","rev":"different"}]}\n',
            encoding="utf-8")
        second = lean_bridge._project_provenance(tmp_path)
        assert second.expected_toolchain == first.expected_toolchain
        assert second.manifest != first.manifest

    def test_report_provenance_rejects_missing_or_mismatched_runtime_toolchain(self):
        internal = "MathAgentGenerated_nonce.ma_target"
        local = {"name": internal, "kind": "theorem", "module": ""}
        provenance = lean_bridge._ProjectProvenance(
            manifest="sha256:" + "a" * 64,
            expected_toolchain="leanprover/lean4:v4.30.0",
        )
        missing = json.dumps({"theorem": internal, "constants": [local]})
        with pytest.raises(lean_bridge.LeanBridgeError, match="toolchain identity"):
            lean_bridge._restamp_verified_report(
                missing, internal, "ma_target", provenance=provenance)

        mismatched = json.dumps({
            "theorem": internal,
            "toolchain": "leanprover/lean4:v4.29.0",
            "constants": [local],
            "manifest": "attacker-controlled",
        })
        with pytest.raises(lean_bridge.LeanBridgeError, match="does not match project pin"):
            lean_bridge._restamp_verified_report(
                mismatched, internal, "ma_target", provenance=provenance)

        # Lean.toolchain omits elan's conventional release-tag `v`; that spelling difference alone
        # must not reject the exact same runtime release.
        equivalent = json.dumps({
            "theorem": internal,
            "toolchain": "leanprover/lean4:4.30.0",
            "constants": [local],
            "manifest": "attacker-controlled",
        })
        stamped = json.loads(lean_bridge._restamp_verified_report(
            equivalent, internal, "ma_target", provenance=provenance))
        assert stamped["toolchain"] == "leanprover/lean4:4.30.0"
        assert stamped["manifest"] == provenance.manifest

    def test_server_dispatch_validates_source_and_requires_explicit_trust(self):
        class UntrustedServer:
            def __init__(self):
                self.called = False

            def audit(self, *_args, **_kwargs):
                self.called = True
                return '{}'

        server = UntrustedServer()
        with pytest.raises(lean_bridge.LeanBridgeError):
            lean_bridge.run_extractor(
                "theorem ma_target : True := trivial", "ma_target", server=server)
        assert not server.called

        server.certification_trusted = True
        with pytest.raises(lean_bridge.LeanBridgeError):
            lean_bridge.run_extractor(
                "theorem ma_target : True := by\n  #eval 1\n  trivial",
                "ma_target", server=server)
        assert not server.called

    def test_server_dispatch_validates_report_shape_and_theorem(self):
        class TrustedServer:
            certification_trusted = True

            def __init__(self, report):
                self.report = report

            def audit(self, *_args, **_kwargs):
                return self.report

        source = "theorem ma_target : True := trivial"
        with pytest.raises(lean_bridge.LeanBridgeError, match="invalid report"):
            lean_bridge.run_extractor(
                source, "ma_target",
                server=TrustedServer('{"theorem":"other","axioms":[],"constants":[]}'))
        good = json.dumps({
            "theorem": "ma_target",
            "toolchain": "leanprover/lean4:v4.30.0",
            "manifest": "core-only",
            "provenance": lean_bridge._PROVENANCE_SCHEMA,
            "axioms": [],
            "constants": [],
        })
        assert json.loads(lean_bridge.run_extractor(
            source, "ma_target", server=TrustedServer(good)))["theorem"] == "ma_target"

    def test_lean_environment_preserves_standard_toolchain_selector(self, monkeypatch):
        monkeypatch.setenv("ELAN_TOOLCHAIN", "leanprover/lean4:v4.30.0")
        assert lean_bridge._lean_env()["ELAN_TOOLCHAIN"] == "leanprover/lean4:v4.30.0"
