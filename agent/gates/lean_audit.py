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

The decision is **deterministic and model-independent**. Producing the report requires Lean
(see `agent/gates/lean/Audit.lean` + `agent/gates/lean_bridge.py`); this module only judges it, so it
is fully testable offline against synthetic reports.
"""
from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from agent.gates.report import Finding, Severity, LAYER_LEAN
from agent.gates.toolkit import Toolkit, load_toolkit


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

    @staticmethod
    def from_dict(d: dict) -> "DependencyReport":
        consts = []
        for c in d.get("constants", []):
            if isinstance(c, str):
                consts.append(ConstDep(name=c))
            else:
                consts.append(ConstDep(name=c["name"], kind=c.get("kind", "theorem"),
                                       module=c.get("module")))
        return DependencyReport(
            theorem=d["theorem"],
            axioms=list(d.get("axioms", [])),
            constants=consts,
            toolchain=d.get("toolchain"),
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

    @property
    def passed(self) -> bool:
        return self.verdict is LeanVerdict.PASS

    def rejects(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.REJECT]

    def summary(self) -> str:
        return f"lean-audit: {self.verdict.value} (rejects={len(self.rejects())})"


def _name_matches(name: str, pattern: str) -> bool:
    """A dotted-prefix match for namespace patterns, or a component match for bare identifiers.

    'Mathlib.NumberTheory.ClassNumber' matches itself and any 'Mathlib.NumberTheory.ClassNumber.*'.
    'EllipticCurve' (bare) matches any name with 'EllipticCurve' as a dotted component, e.g.
    'Mathlib.AlgebraicGeometry.EllipticCurve.j'.
    """
    if "." in pattern:
        return name == pattern or name.startswith(pattern + ".")
    return pattern in name.split(".")


def _matches_any(name: str, patterns: list[str]) -> Optional[str]:
    for p in patterns:
        if _name_matches(name, p):
            return p
    return None


def audit_report(report: DependencyReport, toolkit: Optional[Toolkit] = None) -> LeanAuditResult:
    """Audit a dependency report. Deterministic; REJECT findings are authoritative."""
    toolkit = toolkit or load_toolkit()
    findings: list[Finding] = []

    # 1. Axiom integrity (catches sorry/admit/injected axioms).
    whitelist = set(toolkit.lean_axiom_whitelist)
    for ax in report.axioms:
        if ax not in whitelist:
            sev_code = "sorry_axiom" if ax in ("sorryAx", "Lean.sorryAx") else "axiom_not_whitelisted"
            findings.append(Finding(LAYER_LEAN, Severity.REJECT, sev_code,
                                    f"proof uses non-whitelisted axiom {ax!r}"))

    # 2. Content denylist over the transitive constant closure (allowlists win).
    deny = toolkit.lean_denylist_decls
    allow = toolkit.lean_infrastructure_allowlist + toolkit.lean_elementary_by_fiat
    for c in report.constants:
        hit = _matches_any(c.name, deny)
        if hit is None:
            continue
        exempt = _matches_any(c.name, allow)
        if exempt is not None:
            findings.append(Finding(LAYER_LEAN, Severity.INFO, "denylist_exempted",
                                    f"{c.name} matches denylist {hit!r} but is allowlisted ({exempt!r})"))
            continue
        findings.append(Finding(LAYER_LEAN, Severity.REJECT, "denylisted_dependency",
                                f"non-elementary dependency {c.name!r} ({c.kind}) matches denylist {hit!r}"))

    verdict = LeanVerdict.REJECT if any(f.severity is Severity.REJECT for f in findings) else LeanVerdict.PASS
    return LeanAuditResult(verdict=verdict, findings=findings, report=report)


def audit_json(text: str, toolkit: Optional[Toolkit] = None) -> LeanAuditResult:
    return audit_report(DependencyReport.from_json(text), toolkit)


def audit_file(path: str | Path, toolkit: Optional[Toolkit] = None) -> LeanAuditResult:
    return audit_json(Path(path).read_text(encoding="utf-8"), toolkit)
