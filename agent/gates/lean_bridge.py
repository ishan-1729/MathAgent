"""Bridge: compile a Lean proof, extract its dependency report, and run the Layer-4 audit.

Given Lean source defining a sorry-free theorem, this prepends the `#audit` extractor
(`agent/gates/lean/Audit.lean`), appends `#audit <theorem>`, runs `lean <file>`, parses the emitted
`MATHAGENT_AUDIT_JSON` line, and hands it to `agent.gates.lean_audit.audit_report`.

Self-contained (`import Lean` only), so core-only proofs need no Mathlib build. For Mathlib proofs a
lake project with a Mathlib dependency is required (later); the extractor + auditor are unchanged.

Guarded: if `lean` is not installed, `available()` is False and `audit_lean_source` raises
LeanUnavailable, so the rest of the harness runs without Lean.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agent.gates.lean_audit import LeanAuditResult, audit_json
from agent.gates.toolkit import Toolkit
from agent.gates.windows_job import WindowsMemoryJob, resume_suspended_windows_process

_AUDIT_LEAN = Path(__file__).resolve().parent / "lean" / "Audit.lean"
_SENTINEL = "MATHAGENT_AUDIT_JSON"
# The bare sentinel token. A trusted report is emitted as `MATHAGENT_AUDIT_JSON <nonce> {json}`,
# where <nonce> is a fresh per-run secret the bridge injects into the `#audit` command and that an
# untrusted proof body cannot predict. We additionally count EVERY occurrence of the bare sentinel:
# more than one means the proof tried to forge a report (e.g. via `#eval IO.println "..."`), so we
# reject. The report JSON is one line (Json.compress).
_SENTINEL_RE = re.compile(re.escape(_SENTINEL))
# Lean diagnostic format: `file:line:col: error: ...` — distinguishes a real compile error from a
# (recoverable) warning like "declaration uses 'sorry'" (which the axiom gate catches separately).
_ERROR_RE = re.compile(r":\s*error:")

# Generated Lean is an untrusted data format.  Checking it with a regex is not an execution
# boundary: Lean has nested comments, quoted identifiers and many elaboration-time escape hatches
# (`run_tac`, `by_elab`, `run_meta`, `include_str`, ...).  The validator below first lexes the
# source without executing Lean, then applies an exact-token allow/deny policy.  In particular:
#
# * imports are restricted to installed, trusted library roots (a writable local module may not be
#   imported);
# * quoted identifiers, string/character literals, attributes and every `#command` are rejected;
# * all syntax/elaborator definition forms and all direct meta/IO execution bridges are rejected;
# * the requested theorem name must be a plain qualified Lean identifier actually declared by the
#   source.
#
# This deliberately accepts ordinary theorem/lemma/def proof terms and common tactics while
# declining metaprogramming.  It is a fail-closed language profile, not a best-effort report-forgery
# regex.  The Layer-4 proof-term audit remains the separate soundness/elementarity authority.
_MAX_PROOF_SOURCE = 250_000
_MAX_LEAN_OUTPUT = 4 * 1024 * 1024
_LEAN_MAX_MEMORY_MB = 4_096
_MAX_PROVENANCE_FILE_BYTES = 16 * 1024 * 1024
_PROVENANCE_SCHEMA = "mathagent-derived-v1"
_PLAIN_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*")
_IMPORT_LINE_RE = re.compile(
    r"^\s*import\s+"
    r"(?P<modules>[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*"
    r"(?:\s+[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*)*)"
    r"\s*(?:--[^\r\n]*)?$"
)
# Generated proofs need one umbrella import at most.  Allowing arbitrary submodules by textual root
# (`Mathlib.LocalPayload`) does not establish provenance: a writable project or LEAN_PATH entry could
# shadow that name with an attacker-controlled module whose initializer runs during import.
_TRUSTED_IMPORTS = frozenset({"Lean", "Mathlib", "Std", "Batteries"})

# Exact lexer tokens.  Matching components (rather than dotted substrings) catches both `IO.print`
# and `_root_.IO.print`, but still permits benign names such as `List.prefix_append` and
# `myEvaluator`.
_FORBIDDEN_IDENTIFIERS = frozenset({
    # Direct command/term/tactic execution bridges.
    "run_tac", "run_conv", "run_cmd", "run_elab", "run_meta", "run_term_elab", "by_elab",
    "include_str", "eval", "eval_expr", "native_decide", "bv_decide", "bv_check", "native",
    "eval_tactic", "evalTactic", "evalTerm",
    "elabTerm", "evalConst", "evalConstCheck", "liftIO", "unsafeCast",
    "compile_def", "compile_inductive",
    # User-defined parser, macro and elaborator registration/shadowing.
    "elab", "elab_rules", "macro", "macro_rules", "syntax", "declare_syntax_cat", "deprecate",
    "notation", "infix", "infixl", "infixr", "prefix", "postfix", "attribute",
    "initialize", "builtin_initialize", "register_option", "implemented_by", "extern",
    "foreign", "unsafe", "partial", "private", "meta", "mutual", "set_option",
    "with_weak_namespace",
    "suppress_compilation", "unsuppress_compilation",
    # Meta monads and host-effect namespaces.  Elementary number-theory theorem source has no
    # reason to mention these; rejecting the namespace component also defeats qualification.
    "IO", "BaseIO", "EIO", "ST", "EST", "RealWorld", "Process", "FilePath", "System",
    "TacticM", "TermElabM", "CommandElabM", "MetaM", "CoreM", "MacroM",
    # Message/sentinel emitters.
    "logInfoAt", "logInfo", "logWarningAt", "logWarning", "logErrorAt", "logError",
    "logAt", "println", "print", "dbg_trace",
})


def _is_ident_start(ch: str) -> bool:
    return ch == "_" or ch.isalpha()


def _is_ident_continue(ch: str) -> bool:
    return ch == "_" or ch == "'" or ch.isalpha() or ch.isdigit()


def _lex_untrusted_lean(source: str) -> list[tuple[str, str, int]]:
    """Return ``(kind, value, line)`` tokens without running Lean.

    Comments are skipped (including nested block comments).  Literal contents are never treated as
    code, but literals themselves are tokens so the policy can reject them.  Malformed comments,
    literals and quoted identifiers fail closed here instead of being handed to the elaborator.
    """
    tokens: list[tuple[str, str, int]] = []
    i, line, n = 0, 1, len(source)
    while i < n:
        ch = source[i]
        if ch.isspace():
            if ch == "\n":
                line += 1
            i += 1
            continue
        if source.startswith("--", i):
            end = source.find("\n", i + 2)
            if end < 0:
                break
            i = end
            continue
        if source.startswith("/-", i):
            start_line, depth = line, 1
            i += 2
            while i < n and depth:
                if source.startswith("/-", i):
                    depth += 1
                    i += 2
                elif source.startswith("-/", i):
                    depth -= 1
                    i += 2
                else:
                    if source[i] == "\n":
                        line += 1
                    i += 1
            if depth:
                raise LeanBridgeError(
                    f"proof_src has an unterminated block comment starting on line {start_line}")
            continue
        if ch == "\u00ab":
            raise LeanBridgeError(
                f"proof_src uses a quoted identifier on line {line}; audited source requires "
                "plain identifiers")
        if ch in {'"', "'"}:
            # Apostrophes inside ordinary identifiers were consumed by _is_ident_continue.  A quote
            # beginning a token is therefore a character/string literal.
            quote, start_line = ch, line
            i += 1
            escaped = False
            while i < n:
                cur = source[i]
                if cur == "\n":
                    line += 1
                if escaped:
                    escaped = False
                elif cur == "\\":
                    escaped = True
                elif cur == quote:
                    i += 1
                    break
                i += 1
            else:
                raise LeanBridgeError(
                    f"proof_src has an unterminated literal starting on line {start_line}")
            tokens.append(("literal", quote, start_line))
            continue
        if _is_ident_start(ch):
            start = i
            i += 1
            while i < n and _is_ident_continue(source[i]):
                i += 1
            tokens.append(("ident", source[start:i], line))
            continue
        if ch == "#":
            tokens.append(("hash", ch, line))
            i += 1
            continue
        if source.startswith("@[", i):
            tokens.append(("attribute", "@[", line))
            i += 2
            continue
        tokens.append(("symbol", ch, line))
        i += 1
    return tokens


def _validated_import_lines(source: str, tokens: list[tuple[str, str, int]]) -> set[int]:
    """Validate every import and return its 1-based line number.

    An import is accepted only as a complete line and only from a trusted library root.  This
    prevents source such as ``import LocalPayload`` from loading attacker-controlled `.olean` code.
    """
    # The lexer increments only on LF.  Use that exact representation here; str.splitlines() also
    # splits lone CR/Unicode separators and can map a later token onto an earlier validated line.
    lines = source.split("\n")
    import_lines: set[int] = set()
    for kind, value, lineno in tokens:
        if kind != "ident" or value != "import":
            continue
        if lineno > len(lines):
            raise LeanBridgeError("proof_src contains a malformed import")
        match = _IMPORT_LINE_RE.fullmatch(lines[lineno - 1])
        if match is None:
            raise LeanBridgeError(
                f"proof_src import on line {lineno} is not a complete, plain module import")
        for module in match.group("modules").split():
            if module not in _TRUSTED_IMPORTS:
                raise LeanBridgeError(
                    f"proof_src imports untrusted or non-umbrella module {module!r}; allowed imports "
                    f"are {', '.join(sorted(_TRUSTED_IMPORTS))}")
        import_lines.add(lineno)
    return import_lines


def _declared_theorem_names(tokens: list[tuple[str, str, int]],
                            import_lines: set[int]) -> set[str]:
    """Track namespace/section commands and return exact fully-qualified theorem names."""
    names: set[str] = set()
    scopes: list[tuple[str, list[str]]] = []

    def _qualified_from(start: int) -> tuple[list[str], int]:
        if start >= len(tokens) or tokens[start][0] != "ident":
            return [], start
        parts = [tokens[start][1]]
        j = start + 1
        while (j + 1 < len(tokens) and tokens[j][0] == "symbol" and tokens[j][1] == "."
               and tokens[j + 1][0] == "ident"):
            parts.append(tokens[j + 1][1])
            j += 2
        return parts, j

    i = 0
    while i < len(tokens):
        kind, value, lineno = tokens[i]
        if lineno in import_lines or kind != "ident":
            i += 1
            continue
        if value == "namespace":
            parts, end = _qualified_from(i + 1)
            if parts and tokens[i + 1][2] == lineno:
                if "_root_" in parts:
                    raise LeanBridgeError(
                        "proof_src may not use _root_ in a namespace header")
                # Lean pushes one scope per dotted component; a later `end B` closes only B from
                # `namespace A.B`, while `end A.B` closes both.
                scopes.extend(("namespace", [part]) for part in parts)
                i = end
                continue
        elif value == "section":
            parts, end = _qualified_from(i + 1)
            same_line = bool(parts and tokens[i + 1][2] == lineno)
            if same_line and "_root_" in parts:
                raise LeanBridgeError("proof_src may not use _root_ in a section header")
            if same_line:
                scopes.extend(("section", [part]) for part in parts)
                i = end
                continue
            scopes.append(("section", []))
        elif value == "end":
            parts, end = _qualified_from(i + 1)
            # An end label belongs to this command only, never to the first identifier on the next
            # line. Its component count is Lean's endSize.
            if parts and tokens[i + 1][2] == lineno:
                for _ in parts:
                    if scopes:
                        scopes.pop()
                i = end
                continue
            if scopes:
                scopes.pop()
        elif value in {"theorem", "lemma"}:
            parts, end = _qualified_from(i + 1)
            if parts:
                prefix = [part for scope_kind, scope_parts in scopes if scope_kind == "namespace"
                          for part in scope_parts]
                if parts[0] == "_root_":
                    full = parts[1:]
                else:
                    full = prefix + parts
                if full:
                    names.add(".".join(full))
                i = end
                continue
        i += 1
    return names


def _validate_theorem_name(theorem_name: str) -> None:
    if not isinstance(theorem_name, str) or _PLAIN_NAME_RE.fullmatch(theorem_name) is None:
        raise LeanBridgeError(
            f"invalid theorem_name {theorem_name!r}: expected a plain qualified Lean identifier")


def make_nonce() -> str:
    """A fresh, unguessable token stamped into the audit sentinel for one run."""
    return secrets.token_hex(16)


def _audit_re(nonce: Optional[str]) -> re.Pattern:
    """Regex capturing the report JSON after the sentinel (and the run nonce, if provided)."""
    if nonce:
        return re.compile(re.escape(_SENTINEL) + r"\s+" + re.escape(nonce) + r"\s+(\{.*\})")
    return re.compile(re.escape(_SENTINEL) + r"\s+(\{.*\})")


def extract_report_json(text: str, nonce: Optional[str] = None) -> Optional[str]:
    """Return the trusted report JSON, or None if absent/forged.

    Fails CLOSED: if the bare sentinel appears more than once anywhere in `text`, a proof body
    forged an extra report line, so we reject (return None). When a `nonce` is given, only a line
    carrying that exact nonce is accepted.
    """
    if len(_SENTINEL_RE.findall(text)) > 1:
        return None  # forged second sentinel — reject
    matches = _audit_re(nonce).findall(text)
    if len(matches) != 1:
        return None
    return matches[0]


def _reject_if_forbidden(proof_src: str) -> None:
    """Reject source outside the inert audited-Lean language profile.

    This function intentionally works on lexer tokens, not substrings.  It is also used directly by
    the persistent server, so no Lean process receives source until the same policy has passed.
    """
    if not isinstance(proof_src, str):
        raise LeanBridgeError("proof_src must be text")
    if not proof_src.strip():
        raise LeanBridgeError("proof_src is empty")
    if len(proof_src) > _MAX_PROOF_SOURCE:
        raise LeanBridgeError(
            f"proof_src is too large ({len(proof_src)} bytes; limit {_MAX_PROOF_SOURCE})")
    if "#audit" in proof_src:
        raise LeanBridgeError(
            "proof_src references the trusted '#audit' command: it may not name or redefine the "
            "audit command (sentinel-injection guard)"
        )
    if _SENTINEL in proof_src:
        raise LeanBridgeError(
            f"proof_src contains the audit sentinel {_SENTINEL!r}: it may not embed the report "
            "sentinel string (sentinel-injection guard)"
        )
    tokens = _lex_untrusted_lean(proof_src)
    import_lines = _validated_import_lines(proof_src, tokens)
    first_decl_line: Optional[int] = None
    for kind, value, lineno in tokens:
        if lineno in import_lines:
            if first_decl_line is not None:
                raise LeanBridgeError(
                    f"proof_src import on line {lineno} appears after a declaration")
            continue
        if kind == "ident" and value in {
            "theorem", "lemma", "def", "abbrev", "example", "instance", "structure",
            "class", "inductive", "opaque", "axiom", "constant",
        } and first_decl_line is None:
            first_decl_line = lineno
        if kind == "literal":
            raise LeanBridgeError(
                f"proof_src contains a string/character literal on line {lineno}; literals are "
                "not part of the audited theorem language")
        if kind == "hash":
            raise LeanBridgeError(
                f"proof_src contains a #command/quoted literal on line {lineno}; commands are not "
                "part of the audited theorem language")
        if kind == "symbol" and value == "`":
            # Lean syntax quotations can contain declaration-looking tokens which are data, not
            # declarations.  Besides invoking elaborator machinery, accepting one here would let a
            # body such as `` `(command| theorem Nat.add_comm ...) `` fool the lightweight binding
            # tracker and make the appended audit resolve an imported theorem instead.  Elementary
            # proof terms do not need syntax quotations, so decline them at the language boundary.
            raise LeanBridgeError(
                f"proof_src contains a syntax quotation on line {lineno}; generated source may not "
                "construct or quote Lean syntax")
        if kind == "attribute":
            raise LeanBridgeError(
                f"proof_src contains an attribute on line {lineno}; generated source may not "
                "register elaborators or compiler hooks")
        if kind == "ident" and value in _FORBIDDEN_IDENTIFIERS:
            raise LeanBridgeError(
                f"proof_src contains forbidden token {value!r} on line {lineno}: generated "
                "theorems may not execute IO, evaluation, native code, macros, or elaborator code")


def _validate_audited_source(proof_src: str, theorem_name: str) -> None:
    """Validate both the language profile and binding to the requested theorem declaration."""
    _validate_theorem_name(theorem_name)
    _reject_if_forbidden(proof_src)
    tokens = _lex_untrusted_lean(proof_src)
    imports = _validated_import_lines(proof_src, tokens)
    declared = _declared_theorem_names(tokens, imports)
    if theorem_name not in declared:
        raise LeanBridgeError(
            f"proof_src does not declare requested theorem {theorem_name!r}; declared: "
            f"{', '.join(sorted(declared)) or '(none)'}")


class LeanUnavailable(RuntimeError):
    pass


class LeanBridgeError(RuntimeError):
    pass


def _find_tool(name: str) -> Optional[str]:
    p = shutil.which(name)
    if p:
        return p
    exe = f"{name}.exe" if os.name == "nt" else name
    guess = Path.home() / ".elan" / "bin" / exe
    return str(guess) if guess.exists() else None


def find_lean() -> Optional[str]:
    return _find_tool("lean")


def find_lake() -> Optional[str]:
    return _find_tool("lake")


def available() -> bool:
    return find_lean() is not None


_PROJECT_FILES = ("lakefile.toml", "lake-manifest.json", "lean-toolchain", "MathagentFormal.lean")


@dataclass(frozen=True)
class _ProjectProvenance:
    """Host-derived identity for the environment that compiled one audit.

    ``manifest`` is a full SHA-256 receipt over the exact Lake manifest bytes, or ``core-only``
    when no Lake project participates. ``expected_toolchain`` is the project's exact elan pin; the
    extractor's binary-derived ``Lean.toolchain`` must match it before the receipt can be stamped.
    """

    manifest: str
    expected_toolchain: Optional[str]


_CORE_PROVENANCE = _ProjectProvenance(manifest="core-only", expected_toolchain=None)


def _read_bounded_provenance_file(path: Path) -> bytes:
    try:
        if not path.is_file():
            raise LeanBridgeError(f"required provenance file is missing: {path.name}")
        size = path.stat().st_size
        if size < 0 or size > _MAX_PROVENANCE_FILE_BYTES:
            raise LeanBridgeError(
                f"provenance file {path.name} exceeds {_MAX_PROVENANCE_FILE_BYTES} bytes")
        data = path.read_bytes()
    except LeanBridgeError:
        raise
    except OSError as exc:
        raise LeanBridgeError(f"could not read provenance file {path.name}: {exc}") from exc
    if len(data) > _MAX_PROVENANCE_FILE_BYTES:
        raise LeanBridgeError(
            f"provenance file {path.name} exceeds {_MAX_PROVENANCE_FILE_BYTES} bytes")
    return data


def _project_provenance(project_dir: str | Path) -> _ProjectProvenance:
    """Derive the exact elan pin + Lake-manifest receipt from a project, fail closed.

    The manifest is hashed as bytes rather than re-serialized JSON, so even semantically similar
    but distinct inputs have distinct receipts. A pre/post comparison around compilation detects
    Lake or another process changing either input while an authoritative result is being produced.
    """
    project = Path(project_dir)
    pin_bytes = _read_bounded_provenance_file(project / "lean-toolchain")
    manifest_bytes = _read_bounded_provenance_file(project / "lake-manifest.json")
    try:
        pin = pin_bytes.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise LeanBridgeError("lean-toolchain is not valid UTF-8") from exc
    if (not pin or len(pin) > 256 or any(ch.isspace() for ch in pin)):
        raise LeanBridgeError("lean-toolchain must contain one non-blank elan toolchain identifier")
    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LeanBridgeError("lake-manifest.json is not valid UTF-8 JSON") from exc
    if not isinstance(manifest, dict):
        raise LeanBridgeError("lake-manifest.json must contain a JSON object")
    return _ProjectProvenance(
        manifest="sha256:" + hashlib.sha256(manifest_bytes).hexdigest(),
        expected_toolchain=pin,
    )


def _valid_stamped_provenance(value: dict) -> bool:
    toolchain = value.get("toolchain")
    manifest = value.get("manifest")
    return bool(
        isinstance(toolchain, str) and toolchain and len(toolchain) <= 256
        and all(ord(ch) >= 32 and ch not in "\r\n" for ch in toolchain)
        and isinstance(manifest, str)
        and (manifest == "core-only" or re.fullmatch(r"sha256:[0-9a-f]{64}", manifest))
        and value.get("provenance") == _PROVENANCE_SCHEMA
    )


def _canonical_toolchain_identity(value: str) -> str:
    """Normalize elan's optional release-tag ``v`` without weakening version equality.

    Lean 4's compiled ``Lean.toolchain`` currently renders releases as ``...:4.30.0`` while the
    standard elan pin is ``...:v4.30.0``. They identify the same binary release; every other byte
    remains significant.
    """
    return re.sub(r":v(?=[0-9])", ":", value, count=1)


def _project_scaffold_valid(path: Path) -> bool:
    try:
        library = path / "MathagentFormal"
        return (all((path / name).is_file() for name in _PROJECT_FILES)
                and library.is_dir() and any(library.rglob("*.lean")))
    except OSError:
        return False


def _project_fingerprint(scaffold: Path) -> str:
    h = hashlib.sha256()
    files = [scaffold / name for name in _PROJECT_FILES]
    files.extend(sorted((scaffold / "MathagentFormal").rglob("*.lean")))
    for path in files:
        rel = path.relative_to(scaffold).as_posix().encode("utf-8")
        data = path.read_bytes()
        h.update(len(rel).to_bytes(4, "big")); h.update(rel)
        h.update(len(data).to_bytes(8, "big")); h.update(data)
    return h.hexdigest()[:16]


def _mathagent_cache_root() -> Path:
    override = os.environ.get("MATHAGENT_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        return (Path(base) if base else Path.home() / "AppData" / "Local") / "MathAgent" / "Cache"
    base = os.environ.get("XDG_CACHE_HOME")
    return (Path(base).expanduser() if base else Path.home() / ".cache") / "mathagent"


def _materialize_packaged_project(scaffold: Path, cache_root: Optional[Path] = None) -> Optional[Path]:
    """Copy a wheel's read-only Lake scaffold to a content-addressed user-cache directory.

    Lake creates `.lake` metadata and dependency checkouts beside `lakefile.toml`; system
    site-packages may be read-only and must never be mutated.  The directory is assembled off to the
    side and renamed into place, so concurrent readers see either a complete scaffold or no scaffold.
    """
    if not _project_scaffold_valid(scaffold):
        return None
    try:
        root = (cache_root or _mathagent_cache_root()) / "lean"
        root.mkdir(parents=True, exist_ok=True)
        expected_fingerprint = _project_fingerprint(scaffold)
        destination = root / f"mathagent_formal-{expected_fingerprint}"
        if destination.exists():
            # Shape alone is not a content-address receipt: a stale or modified manifest/scaffold at
            # the expected path must never be trusted. Fail closed rather than racing a destructive
            # replacement against another Lake process using this directory.
            try:
                valid = (_project_scaffold_valid(destination)
                         and _project_fingerprint(destination) == expected_fingerprint)
            except OSError:
                valid = False
            return destination if valid else None
        staging = Path(tempfile.mkdtemp(prefix=".mathagent_formal-", dir=root))
        try:
            for name in _PROJECT_FILES:
                # copyfile intentionally does not preserve a system install's read-only mode bits;
                # Lake may update its manifest and must own the cached copy.
                shutil.copyfile(scaffold / name, staging / name)
            source_lib = scaffold / "MathagentFormal"
            if source_lib.is_dir():
                for source in sorted(source_lib.rglob("*.lean")):
                    target = staging / source.relative_to(scaffold)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
            try:
                os.replace(staging, destination)
            except OSError:
                # Another process may have won the same content-addressed race.
                try:
                    valid = (_project_scaffold_valid(destination)
                             and _project_fingerprint(destination) == expected_fingerprint)
                except OSError:
                    valid = False
                if not valid:
                    return None
            try:
                valid = (_project_scaffold_valid(destination)
                         and _project_fingerprint(destination) == expected_fingerprint)
            except OSError:
                valid = False
            return destination if valid else None
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
    except OSError:
        return None


def find_mathlib_project() -> Optional[str]:
    """Return a writable Lake project for `import Mathlib` proofs, if its scaffold is installed.

    A source checkout owns its project and may use its existing `.lake` tree in place.  An installed
    wheel is immutable runtime data, so its scaffold is copied to the per-user cache before Lake is
    invoked; subsequent calls reuse the content-addressed copy.
    """
    root = Path(__file__).resolve().parents[2]
    scaffold = root / "formal" / "lean" / "mathagent_formal"
    if not _project_scaffold_valid(scaffold):
        return None
    if (root / "pyproject.toml").is_file():
        return str(scaffold)  # source checkout: preserve its already-built `.lake` dependency tree
    materialized = _materialize_packaged_project(scaffold)
    return str(materialized) if materialized is not None else None


def _extractor_src() -> str:
    return _AUDIT_LEAN.read_text(encoding="utf-8")


def _split_imports(src: str) -> tuple[list[str], str]:
    """Return lexer-confirmed imports and a body with only those exact source lines blanked.

    A regex over physical lines is unsafe here: an ``import``-looking line inside a block comment
    would be hoisted out of the comment and become executable.  Reuse the same comment-aware lexer
    and exact umbrella allowlist as the pre-execution validator.
    """
    tokens = _lex_untrusted_lean(src)
    import_lines = _validated_import_lines(src, tokens)
    imports: list[str] = []
    body: list[str] = []
    for lineno, line in enumerate(src.split("\n"), start=1):
        if lineno in import_lines:
            imports.append(line.strip())
            body.append("")
        else:
            body.append(line)
    return imports, "\n".join(body)


def _audit_command(theorem_name: str, nonce: Optional[str]) -> str:
    """The `#audit` invocation. With a nonce, the extractor stamps it into the emitted sentinel so
    the bridge can distinguish a trusted report from one a proof body forged."""
    _validate_theorem_name(theorem_name)
    if nonce:
        return f'#audit {theorem_name} "{nonce}"'
    return f"#audit {theorem_name}"


def _generated_namespace(nonce: str) -> str:
    """Fresh plain identifier that makes imported-target fallback impossible."""
    if not isinstance(nonce, str) or re.fullmatch(r"[A-Za-z0-9_]+", nonce) is None:
        raise LeanBridgeError("invalid audit nonce for generated namespace")
    return f"MathAgentGenerated_{nonce}"


def _internal_theorem_name(theorem_name: str, nonce: str) -> str:
    _validate_theorem_name(theorem_name)
    return f"{_generated_namespace(nonce)}.{theorem_name}"


def _wrap_generated_body(body: str, nonce: str) -> str:
    namespace = _generated_namespace(nonce)
    return f"namespace {namespace}\n{body.strip()}\nend {namespace}"


def _restamp_verified_report(
        report: str, internal_name: str, public_name: str, *,
        provenance: _ProjectProvenance) -> str:
    """Verify the nonce-scoped report and stamp host-derived build provenance.

    The toolchain string is produced inside the running Lean binary. For a Lake build it must equal
    the project's elan pin. The manifest receipt is always overwritten here, after report/theorem
    validation; neither generated Lean nor ``MATHAGENT_TOOLCHAIN`` can choose it.
    """
    try:
        value = json.loads(report)
    except json.JSONDecodeError as e:
        raise LeanBridgeError(f"malformed audit JSON: {e}") from e
    if not isinstance(value, dict) or value.get("theorem") != internal_name:
        stamped = value.get("theorem") if isinstance(value, dict) else None
        raise LeanBridgeError(
            f"audit report is for {stamped!r}, expected internal theorem {internal_name!r}")
    constants = value.get("constants")
    local_entry = next(
        (entry for entry in constants
         if isinstance(entry, dict) and entry.get("name") == internal_name),
        None,
    ) if isinstance(constants, list) else None
    if local_entry is None or local_entry.get("module") != "":
        module = local_entry.get("module") if isinstance(local_entry, dict) else None
        raise LeanBridgeError(
            f"audited theorem {internal_name!r} is not a declaration from the generated source "
            f"(declaring module {module!r})")
    toolchain = value.get("toolchain")
    if (not isinstance(toolchain, str) or not toolchain or len(toolchain) > 256
            or any(ord(ch) < 32 or ch in "\r\n" for ch in toolchain)):
        raise LeanBridgeError("audit report has no valid binary-derived Lean toolchain identity")
    if (provenance.expected_toolchain is not None
            and _canonical_toolchain_identity(toolchain)
            != _canonical_toolchain_identity(provenance.expected_toolchain)):
        raise LeanBridgeError(
            f"running Lean toolchain {toolchain!r} does not match project pin "
            f"{provenance.expected_toolchain!r}")
    value["theorem"] = public_name
    value["manifest"] = provenance.manifest
    value["provenance"] = _PROVENANCE_SCHEMA
    if not _valid_stamped_provenance(value):
        raise LeanBridgeError("could not establish complete Lean/toolchain manifest provenance")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _assemble_source(proof_src: str, theorem_name: str, nonce: Optional[str] = None) -> str:
    """Combine the extractor + proof into one file with all imports hoisted to the top (deduped).

    The untrusted proof body is sanitized first (no output/reduction commands), and the appended
    `#audit` line — emitted AFTER the body so a clean body cannot suppress it — carries the run nonce.
    """
    _validate_audited_source(proof_src, theorem_name)
    scope_nonce = nonce or make_nonce()
    ext_imports, ext_body = _split_imports(_extractor_src())
    pf_imports, pf_body = _split_imports(proof_src)
    seen: set[str] = set()
    imports: list[str] = []
    for imp in ext_imports + pf_imports:
        if imp not in seen:
            seen.add(imp)
            imports.append(imp)
    internal_name = _internal_theorem_name(theorem_name, scope_nonce)
    return ("\n".join(imports) + "\n\n" + ext_body.strip() + "\n\n"
            + _wrap_generated_body(pf_body, scope_nonce)
            + f"\n\n{_audit_command(internal_name, nonce)}\n")


_LEAN_ENV_ALLOWLIST = frozenset({
    "path", "pathext", "systemroot", "windir", "comspec", "temp", "tmp", "tmpdir",
    "home", "userprofile", "homedrive", "homepath", "localappdata", "appdata", "programdata",
    "elan_home", "elan_toolchain", "lake_home", "lang", "lc_all",
})


def _lean_env() -> dict[str, str]:
    """Minimal environment for Lean/lake; model-provider credentials are never inherited."""
    return {key: value for key, value in os.environ.items()
            if key.lower() in _LEAN_ENV_ALLOWLIST}


def _reject_project_import_shadows(project_dir: str | Path) -> None:
    """Fail if a writable Lake project shadows one of the fixed umbrella imports locally."""
    project = Path(project_dir)
    search_roots = [project, project / ".lake" / "build" / "lib" / "lean"]
    for search_root in search_roots:
        for module in _TRUSTED_IMPORTS:
            candidates = (
                search_root / f"{module}.lean",
                search_root / f"{module}.olean",
                search_root / module,
            )
            if any(candidate.exists() for candidate in candidates):
                raise LeanBridgeError(
                    f"Lake project {str(project)!r} locally shadows trusted import {module!r}; "
                    "audited generated source requires dependency-provided umbrella modules")


def _terminate_lean_tree(
        proc: subprocess.Popen, grace_s: float = 2.0,
        job: Optional[WindowsMemoryJob] = None) -> None:
    """Best-effort termination of one Lean process tree without masking a primary error.

    On Windows, a successfully assigned Job Object is the authoritative containment unit.  If
    closing it reports that ``TerminateJobObject`` already killed the tree, do not run ``taskkill``
    against a root PID which may already have been reaped and reused.  Assignment/resume failures,
    and genuine job-termination failures, use a bounded ``taskkill /T /F`` fallback.  The root
    process handle is always waited and, if necessary, killed directly.
    """
    if os.name == "nt":
        job_failed = False
        if job is not None:
            try:
                job.close()
            except Exception as exc:
                # WindowsMemoryJob annotates CloseHandle failures with the independent result of
                # TerminateJobObject.  A terminated tree needs only root-handle reaping; taskkill by
                # PID would introduce a stale-PID race.
                job_failed = not bool(getattr(exc, "tree_terminated", False))
        if job is None or job_failed:
            try:
                subprocess.run(
                    ["taskkill.exe", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                    timeout=max(grace_s, 0.1),
                )
            except Exception:
                pass
        try:
            proc.wait(timeout=grace_s)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=grace_s)
            except Exception:
                pass
        return

    # Both callers create a fresh POSIX session whose process-group id is the launcher's pid.  The
    # leader can exit on SIGTERM while a descendant ignores it, so waiting only for `proc` is not a
    # tree guarantee.  Always escalate the *group* after the grace period (or after an early leader
    # exit), then reap the leader.  A direct kill remains a fallback if the expected group vanished.
    try:
        os.killpg(proc.pid, 15)
    except Exception:
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:
            pass
    try:
        proc.wait(timeout=grace_s)
    except Exception:
        pass
    try:
        os.killpg(proc.pid, 9)
    except Exception:
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass
    try:
        proc.wait(timeout=grace_s)
    except Exception:
        pass


def _run_lean_process(argv: list[str], cwd: str, timeout_s: int,
                      max_output: int = _MAX_LEAN_OUTPUT) -> tuple[int, str]:
    """Run Lean with bounded captured output and a hard wall timeout.

    `subprocess.run(capture_output=True)` grows memory without limit.  A reader thread caps the
    combined stream; the main thread tears down the process group on timeout or overflow.
    """
    kwargs: dict = {}
    if os.name == "nt":
        # Suspend before any user-space child can escape the aggregate-memory, kill-on-close job.
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | 0x00000004)
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(
            argv, cwd=cwd, env=_lean_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=0, **kwargs,
        )
    except OSError as e:
        raise LeanBridgeError(f"could not start Lean: {e}") from e

    job: Optional[WindowsMemoryJob] = None
    if os.name == "nt":
        try:
            job = WindowsMemoryJob(proc, _LEAN_MAX_MEMORY_MB)
            resume_suspended_windows_process(proc)
        except Exception as e:
            # Cleanup is deliberately non-raising so a Job/CloseHandle failure cannot replace the
            # actual assignment/resume error the caller needs to diagnose.
            _terminate_lean_tree(proc, job=job)
            raise LeanBridgeError(f"could not contain Lean process: {e}") from e

    output = bytearray()
    overflow = threading.Event()

    def _drain() -> None:
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(64 * 1024)
            if not chunk:
                return
            remaining = max_output - len(output)
            if remaining > 0:
                output.extend(chunk[:remaining])
            if len(chunk) > remaining:
                overflow.set()

    reader = threading.Thread(target=_drain, name=f"mathagent-lean-output-{proc.pid}", daemon=True)
    deadline = time.monotonic() + timeout_s
    timed_out = False
    reader_started = False
    tree_stopped = False

    def _stop_tree() -> None:
        nonlocal job, tree_stopped
        if tree_stopped:
            return
        tree_stopped = True
        contained_job, job = job, None
        _terminate_lean_tree(proc, job=contained_job)

    try:
        try:
            reader.start()
            reader_started = True
        except Exception as e:
            raise LeanBridgeError(f"could not start Lean output reader: {e}") from e
        while proc.poll() is None:
            if overflow.is_set():
                _stop_tree()
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                _stop_tree()
                break
            try:
                proc.wait(timeout=min(0.1, remaining))
            except subprocess.TimeoutExpired:
                pass
    finally:
        # Always tear down the launch container exactly once. On POSIX the session leader may have
        # exited while a descendant survives; on Windows the Job may still contain descendants after
        # the root exits. The idempotence guard also prevents a stale-PID second cleanup after timeout.
        _stop_tree()
        if reader_started:
            reader.join(timeout=5)
        try:
            if proc.stdout is not None:
                proc.stdout.close()
        except OSError:
            pass
    if timed_out:
        raise LeanBridgeError(f"lean timed out after {timeout_s}s")
    if overflow.is_set():
        raise LeanBridgeError(f"lean output exceeded {max_output} bytes")
    return int(proc.returncode or 0), output.decode("utf-8", errors="replace")


def run_extractor(proof_src: str, theorem_name: str, timeout_s: int = 300,
                  project_dir: Optional[str | Path] = None, server: Optional[object] = None) -> str:
    """Compile `proof_src` and return the raw dependency-report JSON for `theorem_name`.

    - `server` (a LeanServer): reuse a persistent Mathlib-loaded process (fast). Preferred when set.
    - `project_dir` (a lake project): run `lake env lean` so `import Mathlib` resolves.
    - otherwise a bare `lean` runs core-only proofs.
    """
    # This validation is a bridge invariant, not an implementation detail of the bundled server.
    # Alternate transports must never receive source that the one-shot path would reject.
    _validate_audited_source(proof_src, theorem_name)
    if server is not None:
        if getattr(server, "certification_trusted", False) is not True:
            raise LeanBridgeError(
                "persistent audit server is not marked certification_trusted")
        report = server.audit(proof_src, theorem_name, timeout_s=timeout_s)
        try:
            value = json.loads(report)
        except (TypeError, json.JSONDecodeError) as e:
            raise LeanBridgeError(f"persistent server returned malformed audit JSON: {e}") from e
        if (not isinstance(value, dict) or value.get("theorem") != theorem_name
                or not isinstance(value.get("axioms"), list)
                or not isinstance(value.get("constants"), list)
                or not _valid_stamped_provenance(value)):
            stamped = value.get("theorem") if isinstance(value, dict) else None
            raise LeanBridgeError(
                f"persistent server returned an invalid report or unprovenanced report for {stamped!r}; "
                f"expected theorem {theorem_name!r} with derived toolchain/manifest identity")
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    nonce = make_nonce()
    source = _assemble_source(proof_src, theorem_name, nonce)
    provenance = _CORE_PROVENANCE
    if project_dir is not None:
        _reject_project_import_shadows(project_dir)
        provenance = _project_provenance(project_dir)
        lake = find_lake()
        if not lake:
            raise LeanUnavailable("lake not found on PATH or ~/.elan/bin")
        launcher = [lake, "env", "lean", f"--memory={_LEAN_MAX_MEMORY_MB}"]
        fixed_cwd: Optional[str] = str(project_dir)
    else:
        lean = find_lean()
        if not lean:
            raise LeanUnavailable("lean not found on PATH or ~/.elan/bin")
        launcher = [lean, f"--memory={_LEAN_MAX_MEMORY_MB}"]
        fixed_cwd = None

    workdir = tempfile.mkdtemp(prefix="lean_audit_")
    try:
        lean_file = Path(workdir) / "Target.lean"
        lean_file.write_text(source, encoding="utf-8")
        argv = [*launcher, str(lean_file)]
        cwd = fixed_cwd or workdir
        _returncode, combined = _run_lean_process(argv, cwd, timeout_s)
        report = extract_report_json(combined, nonce)
        # ERROR diagnostics => broken even if Lean error-recovered a declaration and emitted a report.
        if _returncode == 0 and report is not None and not _ERROR_RE.search(combined):
            if project_dir is not None and _project_provenance(project_dir) != provenance:
                raise LeanBridgeError(
                    "Lean project toolchain/manifest changed during audit compilation")
            return _restamp_verified_report(
                report, _internal_theorem_name(theorem_name, nonce), theorem_name,
                provenance=provenance)
        # No sentinel, or errors present => the proof did not compile; surface diagnostics.
        tail = combined.strip()[-1200:]
        raise LeanBridgeError(f"no audit JSON emitted (proof failed to compile?):\n{tail}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def audit_lean_source(proof_src: str, theorem_name: str,
                      toolkit: Optional[Toolkit] = None, timeout_s: int = 300,
                      project_dir: Optional[str | Path] = None,
                      server: Optional[object] = None) -> LeanAuditResult:
    """Compile + extract + audit a Lean proof. Raises LeanUnavailable/LeanBridgeError on setup issues;
    a compiling-but-non-elementary proof returns a REJECT result (not an exception). Pass `project_dir`
    (a Mathlib lake project) to audit `import Mathlib` proofs, or `server` (a LeanServer) to reuse a
    persistent Mathlib-loaded process."""
    report_json = run_extractor(proof_src, theorem_name, timeout_s=timeout_s,
                                project_dir=project_dir, server=server)
    return audit_json(
        report_json, toolkit, theorem_name=theorem_name, provenance_verified=True)
