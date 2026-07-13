"""Categorical user-facing proof/certification reporting, independent of CLI entry points."""
from __future__ import annotations

import re
from enum import Enum

_MAX_AUDIT_ITEMS = 100_000
_MAX_PROVENANCE_TEXT = 4096


class ReportStatus(Enum):
    """Ordered outcome categories; search fitness is intentionally not represented."""

    REJECTED = "rejected"
    CANDIDATE_INCOMPLETE = "candidate_incomplete"
    SOFT_PROVEN = "soft_proven"
    AUDITED_NOT_CERTIFIED = "audited_not_certified"
    # Source-compatible alias for the pre-ladder name. It deliberately aliases the audited bucket
    # instead of creating a sixth category.
    FORMALIZED_NOT_ELEMENTARY = "audited_not_certified"
    AUTHORITATIVE_ELEMENTARY = "authoritative_elementary"

    @property
    def label(self) -> str:
        return self.value


def report_status(*, proven: bool, has_candidate: bool = False,
                  audited: bool = False,
                  authoritative_elementary: bool = False,
                  formalized: bool | None = None) -> ReportStatus:
    """Map coherent outcome facts to the certification ladder, never from a search score.

    ``formalized`` is the deprecated spelling retained for callers of the former public helper. A
    literal ``True`` means the audit/formalization tier was reached; malformed truthy values remain
    non-authoritative just like every other boundary flag.
    """
    if formalized is True:
        audited = True
    if proven is True and audited is True and authoritative_elementary is True:
        return ReportStatus.AUTHORITATIVE_ELEMENTARY
    if proven is True and audited is True:
        return ReportStatus.AUDITED_NOT_CERTIFIED
    if proven is True:
        return ReportStatus.SOFT_PROVEN
    if has_candidate is True:
        return ReportStatus.CANDIDATE_INCOMPLETE
    return ReportStatus.REJECTED


def result_has_candidate(result: object) -> bool:
    """Return whether this run retained a non-empty textual candidate.

    ``DagDriver`` can retain memoized nodes across runs, so a whole-DAG scan would let an unrelated
    prior goal relabel the current failed run as ``candidate_incomplete``.  Explicit result fields are
    current-run data; the compatibility fallback is therefore limited to the current goal's reachable
    subgraph.  Malformed non-string payloads fail closed rather than becoming candidates by presence.
    """
    for attr in ("candidate", "ledger"):
        value = getattr(result, attr, None)
        if isinstance(value, str) and value.strip():
            return True
    dag = getattr(result, "dag", None)
    nodes = getattr(dag, "nodes", None)
    goal = getattr(result, "goal", None)
    if isinstance(nodes, dict) and isinstance(goal, str) and goal.strip():
        # Lazy import keeps this small reporting module independent at import time while using the
        # DAG's exact authority-bearing identity for the current root.
        from agent.orchestrator.dag import goal_hash

        seen: set[str] = set()
        stack = [goal_hash(goal)]
        while stack:
            key = stack.pop()
            if key in seen:
                continue
            seen.add(key)
            node = nodes.get(key)
            if node is None:
                continue
            proof = getattr(node, "proof", None)
            if isinstance(proof, str) and proof.strip():
                return True
            children = getattr(node, "children", ())
            if isinstance(children, (list, tuple, set)):
                stack.extend(child for child in children if isinstance(child, str))
    return False


def result_role_provenance(result: object) -> dict[str, dict[str, object]]:
    """Return a JSON-safe defensive copy of actual registry selections for this run."""
    raw = getattr(result, "resolved_roles", None)
    if not isinstance(raw, dict) or len(raw) > 64:
        return {}
    out: dict[str, dict[str, object]] = {}
    for role, metadata in raw.items():
        if not isinstance(role, str) or not role or len(role) > 64 or not isinstance(metadata, dict):
            continue
        provider = metadata.get("provider")
        if not isinstance(provider, str) or not provider or len(provider) > 64:
            continue
        item: dict[str, object] = {"provider": provider}
        for key in ("role", "model", "effort"):
            value = metadata.get(key)
            if value is None or (isinstance(value, str) and len(value) <= 256):
                item[key] = value
        timeout_s = metadata.get("timeout_s")
        if timeout_s is None or (isinstance(timeout_s, int) and not isinstance(timeout_s, bool)
                                 and timeout_s >= 0):
            item["timeout_s"] = timeout_s
        fallback = metadata.get("fallback_selected")
        if isinstance(fallback, bool):
            item["fallback_selected"] = fallback
        out[role] = item
    return out


def result_proof_context(result: object) -> str | None:
    """Return the full authority-bearing proof-context digest for the executed DAG, if present."""
    value = getattr(getattr(result, "dag", None), "context", None)
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) else None


def result_audit_record(result: object, *,
                        terminal_override: object | None = None) -> dict[str, object] | None:
    """Serialize the terminal Lean decision and its dependency/toolchain report for durable artifacts.

    The helper is deliberately defensive because benchmark runners also accept injected builders. A
    malformed custom result yields ``None`` rather than crashing artifact publication or fabricating
    certificate provenance.
    """
    terminal = (terminal_override if terminal_override is not None
                else getattr(result, "terminal", None))
    audit = getattr(terminal, "audit", None) if terminal is not None else None
    if audit is None:
        return None
    try:
        verdict_obj = getattr(audit, "verdict", None)
        verdict = getattr(verdict_obj, "value", None)
        if not isinstance(verdict, str) or len(verdict) > 64:
            return None
        findings_raw = getattr(audit, "findings", None)
        if not isinstance(findings_raw, list) or len(findings_raw) > _MAX_AUDIT_ITEMS:
            return None
        findings: list[dict[str, object]] = []
        for finding in findings_raw:
            severity = getattr(getattr(finding, "severity", None), "value", None)
            layer = getattr(finding, "layer", None)
            code = getattr(finding, "code", None)
            message = getattr(finding, "message", None)
            step_id = getattr(finding, "step_id", None)
            if not all(isinstance(value, str) for value in (severity, layer, code, message)):
                return None
            if any(len(value) > _MAX_PROVENANCE_TEXT
                   for value in (severity, layer, code, message)):
                return None
            if step_id is not None and (not isinstance(step_id, str)
                                        or len(step_id) > _MAX_PROVENANCE_TEXT):
                return None
            findings.append({
                "severity": severity, "layer": layer, "code": code,
                "message": message, "step_id": step_id,
            })

        report_obj = getattr(audit, "report", None)
        report: dict[str, object] | None = None
        if report_obj is not None:
            theorem = getattr(report_obj, "theorem", None)
            axioms_raw = getattr(report_obj, "axioms", None)
            constants_raw = getattr(report_obj, "constants", None)
            toolchain = getattr(report_obj, "toolchain", None)
            manifest = getattr(report_obj, "manifest", None)
            provenance = getattr(report_obj, "provenance", None)
            if (not isinstance(theorem, str) or len(theorem) > _MAX_PROVENANCE_TEXT
                    or not isinstance(axioms_raw, list) or len(axioms_raw) > _MAX_AUDIT_ITEMS
                    or not isinstance(constants_raw, list)
                    or len(constants_raw) > _MAX_AUDIT_ITEMS
                    or toolchain is not None and (
                        not isinstance(toolchain, str) or len(toolchain) > _MAX_PROVENANCE_TEXT
                    )
                    or manifest is not None and (
                        not isinstance(manifest, str) or len(manifest) > _MAX_PROVENANCE_TEXT
                    )
                    or provenance is not None and (
                        not isinstance(provenance, str) or len(provenance) > _MAX_PROVENANCE_TEXT
                    )):
                return None
            if any(not isinstance(axiom, str) or len(axiom) > _MAX_PROVENANCE_TEXT
                   for axiom in axioms_raw):
                return None
            constants: list[dict[str, object]] = []
            for const in constants_raw:
                name = getattr(const, "name", None)
                kind = getattr(const, "kind", None)
                module = getattr(const, "module", None)
                if (not isinstance(name, str) or not isinstance(kind, str)
                        or len(name) > _MAX_PROVENANCE_TEXT or len(kind) > _MAX_PROVENANCE_TEXT
                        or module is not None and (
                            not isinstance(module, str) or len(module) > _MAX_PROVENANCE_TEXT
                        )):
                    return None
                constants.append({"name": name, "kind": kind, "module": module})
            report = {
                "theorem": theorem,
                "axioms": list(axioms_raw),
                "constants": constants,
                "toolchain": toolchain,
                "manifest": manifest,
                "provenance": provenance,
            }
        passed = getattr(audit, "passed", False) is True
        receipt_complete = bool(
            report is not None
            and isinstance(report.get("toolchain"), str) and report["toolchain"]
            and (report.get("manifest") == "core-only"
                 or (isinstance(report.get("manifest"), str)
                     and re.fullmatch(r"sha256:[0-9a-f]{64}", report["manifest"])))
            and report.get("provenance") == "mathagent-derived-v1"
        )
        provenance_verified = bool(
            getattr(audit, "provenance_verified", False) is True and receipt_complete)
        authoritative = bool(
            getattr(audit, "authoritative", False) is True
            and passed and provenance_verified)
        return {
            "verdict": verdict,
            "passed": passed,
            "provenance_verified": provenance_verified,
            "authoritative": authoritative,
            "findings": findings,
            "report": report,
        }
    except Exception:
        return None


def result_certification_state(
        result: object, *, terminal_override: object | None = None,
        ) -> tuple[bool, bool, bool, dict[str, object] | None]:
    """Return coherent ``(proven, audited, authoritative, audit_record)`` state.

    Benchmark runners accept custom builders and the proof CLI has a legacy post-hoc terminal path.
    Neither boundary may mint certification from loosely truthy annotations. Authority is therefore
    derived once from exact booleans *and* the same validated, provenance-complete record persisted in
    the artifact. An invalid/unserializable audit is not reported as a completed audit.
    """
    sentinel = object()
    declared_proven = getattr(result, "proven", sentinel)
    proven = (declared_proven is True if declared_proven is not sentinel
              else getattr(result, "success", False) is True)
    terminal = (terminal_override if terminal_override is not None
                else getattr(result, "terminal", None))
    record = result_audit_record(result, terminal_override=terminal_override)
    audited = bool(
        proven
        and terminal is not None
        and getattr(terminal, "compiled", False) is True
        and isinstance(record, dict)
    )
    declared_authoritative = (
        getattr(terminal, "authoritative", False) is True
        if terminal_override is not None
        else getattr(result, "authoritative_elementary", False) is True
    )
    authoritative = bool(
        declared_authoritative
        and audited
        and getattr(terminal, "authoritative", False) is True
        and getattr(terminal, "certification_trusted", False) is True
        and record is not None
        and record.get("authoritative") is True
    )
    return proven, audited, authoritative, record
