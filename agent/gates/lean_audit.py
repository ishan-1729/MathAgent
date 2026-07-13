"""Layer 4: the authoritative elementary gate — a Lean proof-term dependency + axiom audit.

This is the only NON-GAMEABLE gate (PLAN.md Section 5). Given a *dependency report* for a compiled,
sorry-free Lean proof — its transitive constant-dependency closure (each with its `ConstantInfo` kind)
and the axioms it uses — this module decides whether the proof is elementary:

  1. **Axiom integrity**: the used axioms must be a subset of the whitelist
     ({propext, Classical.choice, Quot.sound}). Anything else (notably `sorryAx`) is a hard reject —
     this is how a `sorry`/`admit`/injected-axiom is caught (kernel `collectAxioms`).
  2. **Content denylist over the closure**: reject if any dependency is a denylisted declaration /
     namespace (class groups, Dedekind domains, elliptic curves, number fields, modular forms,
     cyclotomic theory, ...), UNLESS it is on the infrastructure allowlist (`WellFounded.fix`,
     `Decidable`, ...) or the elementary-by-fiat allowlist (Legendre/QR API, gcd, ...). The allowlists
     exist so the closure audit does not over-reject plumbing or heavy-impl-but-elementary APIs.

The content decision is **deterministic and model-independent**. Producing the report requires Lean
(see `agent/gates/lean/Audit.lean` + `agent/gates/lean_bridge.py`); this module only judges it, so it
is fully testable offline against synthetic reports. Such reports are classification-only: Layer-4
authority additionally requires the production bridge's verified runtime-toolchain + manifest receipt.
"""
from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from agent.gates.report import Finding, Severity, LAYER_LEAN
from agent.gates.toolkit import Toolkit, load_toolkit


DERIVED_PROVENANCE_SCHEMA = "mathagent-derived-v1"


@dataclass(frozen=True)
class ConstDep:
    """One constant in a proof term's transitive dependency closure."""
    name: str
    kind: str = "theorem"            # axiom | theorem | definition | opaque | ctor | recursor | ...
    module: Optional[str] = None     # the Lean module it is declared in, if known


@dataclass
class DependencyReport:
    """The Lean-side facts about a compiled, sorry-free proof of `theorem`."""
    theorem: str
    axioms: list[str] = field(default_factory=list)
    constants: list[ConstDep] = field(default_factory=list)
    toolchain: Optional[str] = None  # e.g. "leanprover/lean4:v4.x" — recorded for reproducibility
    manifest: Optional[str] = None   # host-derived sha256:<Lake manifest> or explicit core-only
    provenance: Optional[str] = None  # exact derived-provenance schema marker

    @property
    def has_derived_provenance(self) -> bool:
        """Whether the production bridge stamped a complete toolchain/manifest receipt."""
        manifest_ok = (self.manifest == "core-only"
                       or (isinstance(self.manifest, str)
                           and self.manifest.startswith("sha256:")
                           and len(self.manifest) == len("sha256:") + 64
                           and all(ch in "0123456789abcdef" for ch in self.manifest[7:])))
        toolchain_ok = bool(
            isinstance(self.toolchain, str) and self.toolchain
            and len(self.toolchain) <= 256
            and all(ord(ch) >= 32 and ch not in "\r\n" for ch in self.toolchain)
        )
        return bool(
            toolchain_ok
            and self.provenance == DERIVED_PROVENANCE_SCHEMA
            and manifest_ok
        )

    @staticmethod
    def from_dict(d: dict) -> "DependencyReport":
        consts = []
        for c in d.get("constants", []):
            if isinstance(c, str):
                consts.append(ConstDep(name=c))
            else:
                consts.append(ConstDep(name=c["name"], kind=c.get("kind", "theorem"),
                                       module=c.get("module") or None))  # "" (no module) -> None
        return DependencyReport(
            theorem=d["theorem"],
            axioms=list(d.get("axioms", [])),
            constants=consts,
            toolchain=d.get("toolchain"),
            manifest=d.get("manifest"),
            provenance=d.get("provenance"),
        )

    @staticmethod
    def from_json(text: str) -> "DependencyReport":
        return DependencyReport.from_dict(json.loads(text))


class LeanVerdict(enum.Enum):
    PASS = "pass"
    REJECT = "reject"


@dataclass
class LeanAuditResult:
    verdict: LeanVerdict
    findings: list[Finding] = field(default_factory=list)
    report: Optional[DependencyReport] = None
    # True only when the production compile bridge derived and validated the runtime toolchain +
    # project-manifest receipt. Offline/synthetic report auditing remains useful, but cannot mint
    # Layer-4 authority merely by supplying a plausible string in `toolchain`.
    provenance_verified: bool = False

    @property
    def passed(self) -> bool:
        return self.verdict is LeanVerdict.PASS

    @property
    def authoritative(self) -> bool:
        return self.passed is True and self.provenance_verified is True

    def rejects(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.REJECT]

    def summary(self) -> str:
        return (f"lean-audit: {self.verdict.value} (rejects={len(self.rejects())}, "
                f"provenance_verified={self.provenance_verified})")


def _match_span(name: str, pattern: str) -> Optional[tuple[int, int]]:
    """The dotted-component range ``[start, end)`` `pattern` matches in `name`, or None.

    A dotted namespace pattern is prefix-anchored: 'Mathlib.NumberTheory.ClassNumber' matches itself
    and any 'Mathlib.NumberTheory.ClassNumber.*' at span (0, 3). A bare identifier matches any single
    dotted component: 'EllipticCurve' matches 'Mathlib.AlgebraicGeometry.EllipticCurve.j' at the
    component where it appears (so a denylisted component is caught regardless of namespace).
    """
    comps = name.split(".")
    if "." in pattern:
        if name == pattern or name.startswith(pattern + "."):
            return (0, len(pattern.split(".")))
        return None
    try:
        i = comps.index(pattern)
    except ValueError:
        return None
    return (i, i + 1)


def _name_matches(name: str, pattern: str) -> bool:
    """Whether `pattern` matches `name` (dotted-prefix for namespaces, component for bare ids)."""
    return _match_span(name, pattern) is not None


def _matches_any(name: str, patterns: list[str]) -> Optional[str]:
    for p in patterns:
        if _name_matches(name, p):
            return p
    return None


def _best_deny_span(name: str, patterns: list[str]) -> Optional[tuple[str, tuple[int, int]]]:
    """The most-specific (widest, earliest) denylist match: ``(pattern, span)`` or None."""
    best: Optional[tuple[str, tuple[int, int]]] = None
    for p in patterns:
        span = _match_span(name, p)
        if span is None:
            continue
        if best is None or (span[1] - span[0], -span[0]) > (best[1][1] - best[1][0], -best[1][0]):
            best = (p, span)
    return best


def _dominating_allow(name: str, deny_span: tuple[int, int],
                      patterns: list[str]) -> Optional[str]:
    """An allowlist pattern whose prefix-anchored match DOMINATES `deny_span`, or None.

    An exemption only counts if it is anchored at the name's start AND spans at least as far as the
    denylist hit. This prevents an unrelated bare infra component (e.g. 'Decidable' deep inside
    'Mathlib.NumberTheory.ClassNumber.Decidable.foo') from exempting denylisted CONTENT. Fails CLOSED.
    """
    for p in patterns:
        span = _match_span(name, p)
        if span is None:
            continue
        if span[0] == 0 and span[0] <= deny_span[0] and span[1] >= deny_span[1]:
            return p
    return None


def _module_matches(module: Optional[str], pattern: str) -> bool:
    """Module-FAMILY ban: a ``Mathlib.*``-prefixed denylist entry matches the DECLARING MODULE by
    dotted prefix.

    Lean declaration names never start with ``Mathlib.`` (module names do), so these entries can only
    ever bind via the report's per-constant ``module`` field — before module matching existed they
    were inert. Scoped to ``Mathlib.``-prefixed patterns ONLY so no currently-live decl-name entry
    (bare or dotted) changes meaning. Reports without a ``module`` field (older extractor) simply
    never match here — decl-name entries remain the backstop."""
    if not module or not pattern.startswith("Mathlib."):
        return False
    return module == pattern or module.startswith(pattern + ".")


def _anchored_allow(name: str, patterns: list[str]) -> Optional[str]:
    """An allowlist pattern anchored at the START of `name`, or None. Used to exempt a module-family
    hit: an elementary-by-fiat API (e.g. ``Nat.gcd``) stays permitted even if it is declared inside a
    banned module family. Fails CLOSED (no anchor -> no exemption)."""
    for p in patterns:
        span = _match_span(name, p)
        if span is not None and span[0] == 0:
            return p
    return None


def audit_report(report: DependencyReport, toolkit: Optional[Toolkit] = None,
                 theorem_name: Optional[str] = None, *,
                 provenance_verified: bool = False) -> LeanAuditResult:
    """Audit a dependency report. Deterministic; REJECT findings are authoritative.

    If `theorem_name` is given, the report's stamped `theorem` MUST equal it (the extractor stamps
    the audited declaration); a mismatch is a hard REJECT. This closes the stale-report and
    forged-report-for-a-different-theorem soundness hazards (the extractor stamps it but it was never
    compared before).
    """
    toolkit = toolkit or load_toolkit()
    findings: list[Finding] = []

    # 0. Theorem cross-check: the report must be ABOUT the theorem we asked to audit.
    if theorem_name is not None and report.theorem != theorem_name:
        findings.append(Finding(LAYER_LEAN, Severity.REJECT, "theorem_mismatch",
                                f"audit report is for {report.theorem!r}, expected {theorem_name!r}"))

    # 1. Axiom integrity (catches sorry/admit/injected axioms). The named denylist is checked
    #    BEFORE the whitelist: native_decide / compiler-trust axioms (Lean.ofReduceBool, ...) are a
    #    deliberate, intent-carrying ban that must hold even if the whitelist were ever widened. The
    #    whitelist stays as the backstop for everything else (notably sorryAx).
    denylist = set(toolkit.lean_axiom_denylist)
    whitelist = set(toolkit.lean_axiom_whitelist)
    for ax in report.axioms:
        if ax in denylist:
            findings.append(Finding(LAYER_LEAN, Severity.REJECT, "axiom_denylisted",
                                    f"proof uses denylisted axiom {ax!r} (native_decide / compiler-trust)"))
        elif ax not in whitelist:
            sev_code = "sorry_axiom" if ax in ("sorryAx", "Lean.sorryAx") else "axiom_not_whitelisted"
            findings.append(Finding(LAYER_LEAN, Severity.REJECT, sev_code,
                                    f"proof uses non-whitelisted axiom {ax!r}"))

    # 2. Content denylist over the transitive constant closure. An exemption only wins if a
    #    prefix-anchored allowlist match DOMINATES the denylist span (a stray infra component
    #    elsewhere in the name does not exempt denylisted content). Fails CLOSED.
    deny = toolkit.lean_denylist_decls
    allow = toolkit.lean_infrastructure_allowlist + toolkit.lean_elementary_by_fiat
    for c in report.constants:
        hit = _best_deny_span(c.name, deny)
        if hit is not None:
            hit_pat, deny_span = hit
            exempt = _dominating_allow(c.name, deny_span, allow)
            if exempt is not None:
                findings.append(Finding(LAYER_LEAN, Severity.INFO, "denylist_exempted",
                                        f"{c.name} matches denylist {hit_pat!r} but is allowlisted ({exempt!r})"))
                continue
            findings.append(Finding(LAYER_LEAN, Severity.REJECT, "denylisted_dependency",
                                    f"non-elementary dependency {c.name!r} ({c.kind}) matches denylist {hit_pat!r}"))
            continue
        # Module-family ban: a `Mathlib.*` entry matches the constant's DECLARING MODULE by prefix,
        # catching whole families (e.g. every decl in Mathlib.Analysis.SpecialFunctions.Trigonometric.*)
        # so sibling decls the per-name entries missed cannot slip through. Name-anchored allowlist
        # entries still exempt (by-fiat APIs keep working wherever they are declared). Fails CLOSED.
        mod_pat = next((p for p in deny if _module_matches(c.module, p)), None)
        if mod_pat is None:
            continue
        exempt = _anchored_allow(c.name, allow)
        if exempt is not None:
            findings.append(Finding(LAYER_LEAN, Severity.INFO, "denylist_exempted",
                                    f"{c.name} (module {c.module}) matches denylist {mod_pat!r} "
                                    f"but is allowlisted ({exempt!r})"))
            continue
        findings.append(Finding(LAYER_LEAN, Severity.REJECT, "denylisted_dependency",
                                f"non-elementary dependency {c.name!r} ({c.kind}) declared in banned "
                                f"module {c.module!r} matches denylist {mod_pat!r}"))

    # 3. Anti-vacuity: a report carrying NEITHER axioms NOR constants audited nothing — a real
    #    compiled proof always has a non-empty closure (it contains at least the audited theorem
    #    itself). Passing such a report would certify on zero evidence; fail CLOSED instead.
    if not report.constants and not report.axioms:
        findings.append(Finding(LAYER_LEAN, Severity.REJECT, "vacuous_report",
                                "report has no axioms and no constants — nothing was audited"))

    verdict = LeanVerdict.REJECT if any(f.severity is Severity.REJECT for f in findings) else LeanVerdict.PASS
    verified = provenance_verified is True and report.has_derived_provenance is True
    return LeanAuditResult(
        verdict=verdict, findings=findings, report=report,
        provenance_verified=verified)


def audit_json(text: str, toolkit: Optional[Toolkit] = None,
               theorem_name: Optional[str] = None, *,
               provenance_verified: bool = False) -> LeanAuditResult:
    return audit_report(
        DependencyReport.from_json(text), toolkit, theorem_name=theorem_name,
        provenance_verified=provenance_verified)


def audit_file(path: str | Path, toolkit: Optional[Toolkit] = None,
               theorem_name: Optional[str] = None) -> LeanAuditResult:
    """TEST/TOOLING ONLY — audits a report file with no nonce provenance. The production path is
    ``lean_bridge.audit_lean_source`` / ``LeanServer.audit``, which compile with a nonce-guarded
    ``#audit`` command and always pass ``theorem_name``. Pass ``theorem_name`` here too when the
    expected declaration is known."""
    return audit_json(Path(path).read_text(encoding="utf-8"), toolkit, theorem_name=theorem_name)
