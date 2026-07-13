"""Close the loop: informal step-ledger -> Lean -> compile -> Layer-4 audit -> faithfulness -> verdict.

`formalize_and_audit` formalizes a gate-passed ledger (via a `Formalizer`, e.g. Codex), compiles it,
runs the authoritative Lean dependency/axiom audit on the proof term, and (optionally) runs an
ADVERSARIAL statement-faithfulness check so a compiling/audited proof of the WRONG statement is caught.

`full_verify` runs the whole stack: the fast informal gate (Layers 0-3) AND Lean Layer 4 AND
faithfulness, reporting `authoritative_elementary` = informal-gate-admitted AND compiled AND
dependency-audit-passed AND statement-faithful. This is the only place where "elementary" is *enforced*.

Faithfulness FAILS CLOSED: `authoritative` is True only when a faithfulness panel actually ran and
passed (see `FormalizeAuditResult.faithful`). A result with no faithfulness checker is audited but
NOT authoritative — callers that want certification MUST pass a `faithfulness_checker`.

`make_terminal_gate` packages this as a callable for use as the DAG driver's terminal gate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Protocol, runtime_checkable

from agent.gates import lean_bridge
from agent.gates.gate import GateReport, Verdict, evaluate
from agent.gates.ledger import parse_ledger, LedgerError
from agent.gates.lean_audit import LeanAuditResult
from agent.gates.toolkit import Toolkit, load_toolkit
from agent.orchestrator.faithfulness import FaithfulnessVerdict
from agent.tools.formalizer import FormalizationResult


@runtime_checkable
class Formalizer(Protocol):
    certification_trusted: bool
    model_call_cost: int

    def formalize(self, ledger_text: str) -> FormalizationResult:
        ...


@runtime_checkable
class SketchFormalizer(Protocol):
    def formalize_sketch(self, parent_goal: str, sketch_text: str,
                         child_goals: list[str]) -> FormalizationResult:
        ...


@runtime_checkable
class FaithfulnessChecker(Protocol):
    certification_trusted: bool
    model_call_cost: int

    def check(self, informal_claim: str, lean_source: str, theorem_name: str) -> FaithfulnessVerdict:
        ...


@dataclass
class FormalizeAuditResult:
    formalized: bool
    compiled: bool = False
    theorem_name: str = ""
    lean_source: Optional[str] = None
    audit: Optional[LeanAuditResult] = None
    faithfulness: Optional[FaithfulnessVerdict] = None
    error: Optional[str] = None
    attempts: int = 1   # formalization attempts used (1 + repair iterations consumed)
    # Actual provider-model calls made by this verification operation.  This is intentionally
    # separate from the DAG/Ralph search budget: certification and evolution have their own bounded
    # controls and must be observable rather than hidden inside the search-call counter.
    model_calls: int = 0
    notes: list[str] = field(default_factory=list)
    # Read-only annotation (per-node gate, P0/P2): True iff the Lean toolchain was UNAVAILABLE (server
    # down / `lean` not installed) so no compile could even be attempted. Distinct from a compile that
    # ran and FAILED. Purely additive — none of the existing `elementary_verified`/`faithful`/
    # `authoritative` properties read it, so their semantics are unchanged (a result with
    # lean_unavailable=True has compiled=False and is therefore not elementary_verified, as before).
    lean_unavailable: bool = False
    # Certification is a trust boundary, not merely a successful return value.  Test doubles and
    # scripted providers are useful for exercising orchestration, but must opt in explicitly before
    # they can mint an authoritative result.  Live formalizers/checkers advertise this capability;
    # unknown components fail closed.
    certification_trusted: bool = False

    @property
    def elementary_verified(self) -> bool:
        """Compiled, passed the dependency audit, and carries derived build provenance.

        A legacy/synthetic report may still be content-audited (`audit.passed`) for tooling, but it
        cannot promote a node or terminal result without the production bridge's independently
        derived runtime-toolchain + Lake-manifest receipt.
        """
        return bool(self.compiled is True and self.audit is not None
                    and getattr(self.audit, "authoritative", False) is True)

    # ---- per-node-gate outcome classification (read-only; P2 driver routing) --------------------
    # The DAG driver's optional per-node verifier routes a LEAF on FOUR mutually-exclusive outcomes.
    # These read-only helpers name them off the existing fields so the driver does not re-derive the
    # classification inline (and so the semantics live next to the data). They DO NOT change any gate.

    @property
    def lean_compiled_but_rejected(self) -> bool:
        """Compiled, the audit RAN, and it REJECTED (sorry / denylist / non-whitelist axiom). This
        REFUTES elementarity for the leaf (a hard, fail-CLOSED signal), distinct from a proof that
        could not be formalized/compiled at all."""
        return bool(self.compiled is True and self.audit is not None
                    and self.audit.passed is not True)

    @property
    def lean_provenance_unverified(self) -> bool:
        """Content passed, but no bridge-verified runtime/toolchain receipt backs the result.

        This is an operational certification failure, not evidence that the proof is
        non-elementary. It therefore must not be routed as ``lean_compiled_but_rejected`` or stamped
        ``LEAN_VERIFIED``.
        """
        return bool(
            self.compiled is True and self.audit is not None
            and self.audit.passed is True
            and getattr(self.audit, "authoritative", False) is not True
        )

    @property
    def lean_could_not_formalize(self) -> bool:
        """The toolchain was available but the proof could not be turned into a compiling Lean term
        (no Lean produced, or a compile error). NOT a refutation of elementarity — a fail-OPEN signal:
        the leaf stays softly proven and is re-attemptable. Excludes the unavailable case."""
        return self.lean_unavailable is not True and self.compiled is not True

    @property
    def faithful(self) -> bool:
        """FAIL CLOSED: True only if a faithfulness panel actually ran AND passed.

        If faithfulness was never checked (`self.faithfulness is None`) this is False, so a
        compiling/audited proof of the WRONG statement is never silently treated as authoritative.
        """
        return (self.faithfulness is not None
                and self.faithfulness.faithful is True)

    @property
    def authoritative(self) -> bool:
        """The proof is authoritatively elementary: audited elementary AND a faithfulness panel
        ran and confirmed the statement faithfully captures the claim. Cannot be True unless a
        faithfulness checker was supplied and passed, and both certification components were trusted
        (fail closed)."""
        return (self.elementary_verified is True and self.faithful is True
                and self.certification_trusted is True)

    def summary(self) -> str:
        if not self.formalized:
            return f"formalize: failed (no Lean produced; model_calls={self.model_calls})"
        if not self.compiled:
            return (f"formalize: ok, compile/audit: failed ({self.error}; "
                    f"model_calls={self.model_calls})")
        f = "n/a" if self.faithfulness is None else self.faithfulness.summary()
        return (f"formalize: ok, compiled, {self.audit.summary()}, faithfulness[{f}], "
                f"certification_trusted={self.certification_trusted}, "
                f"model_calls={self.model_calls}, "
                f"authoritative={self.authoritative}")


def _certification_trusted(component: object | None) -> bool:
    """Return whether *component* may participate in an authoritative certificate.

    The default is deliberately false: an arbitrary duck-typed object must not gain authority just
    because it implements the right method.  Production providers declare the capability explicitly,
    while scripted fixtures opt in only in tests that are specifically exercising the trusted path.
    """
    return bool(component is not None and getattr(component, "certification_trusted", False) is True)


def _model_call_cost(component: object | None) -> int:
    """Declared provider calls per invocation (unknown/custom components conservatively report zero)."""
    cost = getattr(component, "model_call_cost", 0) if component is not None else 0
    if isinstance(cost, bool) or not isinstance(cost, int) or cost < 0:
        return 0
    return cost


_MAX_REPAIR_ITERS = 1000


def _validate_repair_iters(repair_iters: int) -> None:
    if (isinstance(repair_iters, bool) or not isinstance(repair_iters, int)
            or not 0 <= repair_iters <= _MAX_REPAIR_ITERS):
        raise ValueError(f"repair_iters must be an integer in [0, {_MAX_REPAIR_ITERS}]")


def _claim_of(ledger_text: str) -> str:
    try:
        return parse_ledger(ledger_text).claim
    except LedgerError:
        return ledger_text


def formalize_and_audit(ledger_text: str, formalizer: Formalizer,
                        toolkit: Optional[Toolkit] = None,
                        project_dir: Optional[str | Path] = None,
                        timeout_s: int = 600,
                        informal_claim: Optional[str] = None,
                        faithfulness_checker: Optional[FaithfulnessChecker] = None,
                        server: Optional[object] = None,
                        retriever: Optional[object] = None,
                        repair_iters: int = 0) -> FormalizeAuditResult:
    """Formalize -> compile+audit, with an optional Lean-error repair loop.

    When `repair_iters > 0`, each failed compile feeds the Lean errors (and, if a `retriever` is given,
    retrieved real Mathlib lemmas) back to the formalizer for another attempt. Pass a persistent
    `server` so each compile is ~0.1s instead of a full Mathlib reload.
    """
    _validate_repair_iters(repair_iters)
    toolkit = toolkit or load_toolkit()
    if project_dir is None and server is None:
        project_dir = lean_bridge.find_mathlib_project()
    claim = informal_claim if informal_claim is not None else _claim_of(ledger_text)
    certification_trusted = (_certification_trusted(formalizer)
                             and _certification_trusted(faithfulness_checker))

    model_calls = _model_call_cost(formalizer)
    fr = formalizer.formalize(ledger_text)
    if not fr.ok:
        return FormalizeAuditResult(formalized=False, notes=fr.notes, model_calls=model_calls,
                                    certification_trusted=certification_trusted)
    source, name = fr.lean_source, fr.theorem_name
    last_error: Optional[str] = None

    for i in range(repair_iters + 1):
        attempts = i + 1
        try:
            audit = lean_bridge.audit_lean_source(
                source, name, toolkit=toolkit,
                project_dir=project_dir, timeout_s=timeout_s, server=server)
        except lean_bridge.LeanUnavailable as e:
            return FormalizeAuditResult(formalized=True, compiled=False, theorem_name=name,
                                        lean_source=source, error=str(e), attempts=attempts,
                                        model_calls=model_calls,
                                        lean_unavailable=True,
                                        certification_trusted=certification_trusted)
        except lean_bridge.LeanBridgeError as e:
            last_error = str(e)
            if i >= repair_iters:
                break
            lemmas = retriever.retrieve(claim, last_error) if retriever is not None else []
            model_calls += _model_call_cost(formalizer)
            fr2 = formalizer.formalize(ledger_text, prior_source=source,
                                       errors=[last_error], lemmas=lemmas)
            if not fr2.ok:
                break
            source, name = fr2.lean_source, fr2.theorem_name
            continue

        # Compiled. If the audit REJECTED (sorry / non-elementary dependency) and we still have repair
        # budget, feed the reject reasons back and try for a complete, elementary proof.
        if not audit.passed and i < repair_iters:
            last_error = "; ".join(str(f) for f in audit.rejects())
            lemmas = retriever.retrieve(claim, last_error) if retriever is not None else []
            model_calls += _model_call_cost(formalizer)
            fr2 = formalizer.formalize(ledger_text, prior_source=source,
                                       errors=[last_error], lemmas=lemmas)
            if fr2.ok:
                source, name = fr2.lean_source, fr2.theorem_name
                continue

        # Accepted, or out of repair budget. Check statement faithfulness on the final proof.
        faith: Optional[FaithfulnessVerdict] = None
        if faithfulness_checker is not None:
            model_calls += _model_call_cost(faithfulness_checker)
            faith = faithfulness_checker.check(claim, source, name)
        return FormalizeAuditResult(formalized=True, compiled=True, theorem_name=name,
                                    lean_source=source, audit=audit, faithfulness=faith,
                                    attempts=attempts, model_calls=model_calls,
                                    certification_trusted=certification_trusted)

    return FormalizeAuditResult(formalized=True, compiled=False, theorem_name=name,
                                lean_source=source, error=last_error, attempts=repair_iters + 1,
                                model_calls=model_calls,
                                certification_trusted=certification_trusted)


@dataclass
class FullVerifyResult:
    gate: GateReport
    lean: Optional[FormalizeAuditResult] = None

    @property
    def authoritative_elementary(self) -> bool:
        # Fail closed on the informal gate: a NEEDS_REVIEW verdict (e.g. an elastic justification routed
        # to Layer 2) must NOT be authoritative when nothing resolved that review. Only a fully-passing
        # deterministic gate qualifies — `admitted_deterministically` also accepts NEEDS_REVIEW and is
        # therefore too weak for the authoritative verdict.
        return bool(self.gate.verdict is Verdict.PASSED_DETERMINISTIC
                    and self.lean is not None and self.lean.authoritative is True)

    def summary(self) -> str:
        g = self.gate.summary()
        l = self.lean.summary() if self.lean else "lean: skipped (informal gate rejected)"
        return f"[informal] {g} | [lean] {l} | authoritative_elementary={self.authoritative_elementary}"


def full_verify(ledger_text: str, formalizer: Formalizer,
                toolkit: Optional[Toolkit] = None,
                project_dir: Optional[str | Path] = None,
                timeout_s: int = 600,
                faithfulness_checker: Optional[FaithfulnessChecker] = None,
                server: Optional[object] = None,
                retriever: Optional[object] = None,
                repair_iters: int = 0) -> FullVerifyResult:
    toolkit = toolkit or load_toolkit()
    gate = evaluate(ledger_text, toolkit)
    if gate.rejected:
        return FullVerifyResult(gate=gate, lean=None)
    lean = formalize_and_audit(ledger_text, formalizer, toolkit=toolkit,
                               project_dir=project_dir, timeout_s=timeout_s,
                               informal_claim=_claim_of(ledger_text),
                               faithfulness_checker=faithfulness_checker, server=server,
                               retriever=retriever, repair_iters=repair_iters)
    return FullVerifyResult(gate=gate, lean=lean)


def make_terminal_gate(formalizer: Formalizer, toolkit: Optional[Toolkit] = None,
                       faithfulness_checker: Optional[FaithfulnessChecker] = None,
                       project_dir: Optional[str | Path] = None,
                       server: Optional[object] = None,
                       retriever: Optional[object] = None,
                       repair_iters: int = 0,
                       timeout_s: int = 600) -> Callable[[str, str], FormalizeAuditResult]:
    """A terminal gate for DagDriver: (root_goal, proof_text) -> FormalizeAuditResult.

    Formalizes the assembled proof (with an optional Lean-error repair loop + Mathlib retrieval),
    audits it (Layer 4), and checks the statement faithfully captures the root goal. The DagDriver
    treats the run as authoritatively elementary iff this passes.
    """
    toolkit = toolkit or load_toolkit()

    def gate(root_goal: str, proof_text: str) -> FormalizeAuditResult:
        return formalize_and_audit(proof_text, formalizer, toolkit=toolkit,
                                   project_dir=project_dir, timeout_s=timeout_s,
                                   informal_claim=root_goal,
                                   faithfulness_checker=faithfulness_checker, server=server,
                                   retriever=retriever, repair_iters=repair_iters)

    return gate


def _lean_available(server: Optional[object], project_dir: Optional[str | Path]) -> bool:
    """Is the Lean toolchain reachable for a per-node audit? A persistent `server` whose `available()`
    reports True, OR (no server) a `lean`/`lake` on PATH. Best-effort + fail-SAFE: any probe error is
    treated as UNAVAILABLE (the node gate then routes the leaf to a retryable 'lean_unavailable', never
    a spurious refutation). Read-only: no compile is run here."""
    if server is not None:
        avail = getattr(server, "available", None)
        if callable(avail):
            try:
                return bool(avail())
            except Exception:
                return False
        # A server object without an availability probe is assumed usable (its audit() will surface a
        # LeanUnavailable if not, which the caller maps to lean_unavailable).
        return True
    try:
        if project_dir is not None:
            return lean_bridge.find_lake() is not None
        return lean_bridge.available()
    except Exception:
        return False


def make_node_gate(formalizer: Formalizer, toolkit: Optional[Toolkit] = None,
                   project_dir: Optional[str | Path] = None,
                   server: Optional[object] = None,
                   retriever: Optional[object] = None,
                   repair_iters: int = 0,
                   timeout_s: int = 600) -> Callable[[str, str], FormalizeAuditResult]:
    """A PER-LEAF verifier for DagDriver: (node_goal, node_proof_text) -> FormalizeAuditResult.

    A SIBLING of `make_terminal_gate` (whose semantics are untouched). It formalizes a SINGLE node's
    proof, compiles it, and runs the Layer-4 dependency/axiom audit — but per the open-decision default
    its per-leaf AUTHORITY is `elementary_verified` ONLY (compiled, audit-passed, and carrying a
    bridge-verified toolchain/manifest receipt). It deliberately
    passes `faithfulness_checker=None`: per-leaf statement-faithfulness is DEFERRED to the root terminal
    gate (`make_terminal_gate`), which checks the assembled proof faithfully captures the ROOT goal. A
    leaf only needs to be *audited elementary*; running an expensive faithfulness panel at every leaf
    would be redundant with the root check and is intentionally omitted here.

    Unavailability is detected up front: if no Lean toolchain is reachable, the gate returns a result
    flagged `lean_unavailable=True` (compiled=False) WITHOUT attempting a compile, so the driver can
    route the leaf to a retryable 'lean_unavailable' rather than misclassifying it as a non-compile.
    This keeps `formalize_and_audit`'s own behaviour unchanged (we never rely on it to disambiguate
    unavailable-vs-compile-failure).
    """
    toolkit = toolkit or load_toolkit()

    def gate(node_goal: str, node_proof_text: str) -> FormalizeAuditResult:
        if not _lean_available(server, project_dir):
            return FormalizeAuditResult(formalized=False, compiled=False,
                                        error="lean toolchain unavailable",
                                        lean_unavailable=True)
        # faithfulness_checker=None: per-leaf authority is elementary_verified only (see docstring).
        return formalize_and_audit(node_proof_text, formalizer, toolkit=toolkit,
                                   project_dir=project_dir, timeout_s=timeout_s,
                                   informal_claim=node_goal,
                                   faithfulness_checker=None, server=server,
                                   retriever=retriever, repair_iters=repair_iters)

    return gate


# --------------------------------------------------------------------------------------------------
# P4: AND-node sorry-sketch composition gate (LEAP §2.2/§2.5 COMPOSITION check).
#
# A decomposition sketch is formalized as a SORRY-FREE Lean theorem proving the PARENT goal ASSUMING
# the child goals as named hypotheses h0, h1, ... (children-as-hypotheses, MODE A). Because the body
# is sorry-free, the SAME `audit_lean_source` path applies VERBATIM: `elementary_verified` then means
# the SKETCH compiled (the composition is Lean-valid) AND the body's own axioms/deps are elementary.
# faithfulness is None here (deferred to the root terminal gate); the per-CHILD lemmas are verified by
# their own per-node gate. This is the AND-node twin of `make_node_gate` (the LEAF gate).
# --------------------------------------------------------------------------------------------------

def formalize_sketch_and_audit(parent_goal: str, sketch_text: str, child_goals: list[str],
                               formalizer: SketchFormalizer,
                               toolkit: Optional[Toolkit] = None,
                               project_dir: Optional[str | Path] = None,
                               timeout_s: int = 600,
                               faithfulness_checker: Optional[FaithfulnessChecker] = None,
                               server: Optional[object] = None,
                               retriever: Optional[object] = None,
                               repair_iters: int = 0) -> FormalizeAuditResult:
    """Formalize a DECOMPOSITION sketch (children-as-hypotheses) -> compile + Layer-4 audit, with an
    optional Lean-error repair loop. Mirrors `formalize_and_audit` but drives the formalizer's
    `formalize_sketch(parent_goal, sketch_text, child_goals)` path. The emitted theorem is SORRY-FREE
    (the children are HYPOTHESES, not sorries), so `audit_lean_source` is reused verbatim and
    `.elementary_verified` means the COMPOSITION compiled, the body's deps/axioms are elementary,
    and the toolchain/manifest receipt was bridge-verified."""
    _validate_repair_iters(repair_iters)
    toolkit = toolkit or load_toolkit()
    if project_dir is None and server is None:
        project_dir = lean_bridge.find_mathlib_project()

    model_calls = _model_call_cost(formalizer)
    fr = formalizer.formalize_sketch(parent_goal, sketch_text, child_goals)
    if not fr.ok:
        return FormalizeAuditResult(formalized=False, notes=fr.notes, model_calls=model_calls)
    source, name = fr.lean_source, fr.theorem_name
    last_error: Optional[str] = None

    for i in range(repair_iters + 1):
        attempts = i + 1
        try:
            audit = lean_bridge.audit_lean_source(
                source, name, toolkit=toolkit,
                project_dir=project_dir, timeout_s=timeout_s, server=server)
        except lean_bridge.LeanUnavailable as e:
            return FormalizeAuditResult(formalized=True, compiled=False, theorem_name=name,
                                        lean_source=source, error=str(e), attempts=attempts,
                                        model_calls=model_calls, lean_unavailable=True)
        except lean_bridge.LeanBridgeError as e:
            last_error = str(e)
            if i >= repair_iters:
                break
            lemmas = retriever.retrieve(parent_goal, last_error) if retriever is not None else []
            model_calls += _model_call_cost(formalizer)
            fr2 = formalizer.formalize_sketch(parent_goal, sketch_text, child_goals,
                                              prior_source=source, errors=[last_error], lemmas=lemmas)
            if not fr2.ok:
                break
            source, name = fr2.lean_source, fr2.theorem_name
            continue

        # Compiled. If the audit REJECTED (sorry in the BODY / non-elementary dependency) and repair
        # budget remains, feed the reject reasons back for a clean, elementary composition.
        if not audit.passed and i < repair_iters:
            last_error = "; ".join(str(f) for f in audit.rejects())
            lemmas = retriever.retrieve(parent_goal, last_error) if retriever is not None else []
            model_calls += _model_call_cost(formalizer)
            fr2 = formalizer.formalize_sketch(parent_goal, sketch_text, child_goals,
                                              prior_source=source, errors=[last_error], lemmas=lemmas)
            if fr2.ok:
                source, name = fr2.lean_source, fr2.theorem_name
                continue

        faith: Optional[FaithfulnessVerdict] = None
        if faithfulness_checker is not None:
            model_calls += _model_call_cost(faithfulness_checker)
            faith = faithfulness_checker.check(parent_goal, source, name)
        return FormalizeAuditResult(formalized=True, compiled=True, theorem_name=name,
                                    lean_source=source, audit=audit, faithfulness=faith,
                                    attempts=attempts, model_calls=model_calls)

    return FormalizeAuditResult(formalized=True, compiled=False, theorem_name=name,
                                lean_source=source, error=last_error, attempts=repair_iters + 1,
                                model_calls=model_calls)


def make_sketch_gate(formalizer: SketchFormalizer, toolkit: Optional[Toolkit] = None,
                     project_dir: Optional[str | Path] = None,
                     server: Optional[object] = None,
                     retriever: Optional[object] = None,
                     repair_iters: int = 0,
                     timeout_s: int = 600) -> Callable[[str, str, list[str]], FormalizeAuditResult]:
    """A PER-AND-NODE composition verifier for DagDriver:
    ``(parent_goal, sketch_text, child_goals) -> FormalizeAuditResult``.

    A SIBLING of `make_node_gate` (the LEAF gate, whose semantics are untouched). It formalizes the
    decomposition SKETCH as a SORRY-FREE Lean theorem proving the parent ASSUMING the children as
    hypotheses (the LEAP §2.2/§2.5 composition step), compiles it, and runs the Layer-4 audit. Per the
    open-decision default its authority is `elementary_verified` ONLY (compiled, audit-passed, and
    provenance-verified): the
    sketch COMPILED (the composition is Lean-valid) AND the body's axioms/deps are elementary.

    It passes `faithfulness_checker=None`: composition-level faithfulness is DEFERRED to the root
    terminal gate (`make_terminal_gate`), which checks the assembled proof faithfully captures the ROOT
    goal. The per-CHILD lemmas are verified by their own per-node gate, so a sketch only needs to be
    *audited elementary* here.

    Unavailability is detected up front (same as `make_node_gate`): with no Lean toolchain reachable
    the gate returns `lean_unavailable=True` (compiled=False) WITHOUT a compile, so the driver can fall
    back to the byte-identical offline path rather than misclassifying it.
    """
    toolkit = toolkit or load_toolkit()

    def gate(parent_goal: str, sketch_text: str, child_goals: list[str]) -> FormalizeAuditResult:
        if not _lean_available(server, project_dir):
            return FormalizeAuditResult(formalized=False, compiled=False,
                                        error="lean toolchain unavailable",
                                        lean_unavailable=True)
        # faithfulness_checker=None: composition-level faithfulness is deferred to the root gate.
        return formalize_sketch_and_audit(parent_goal, sketch_text, child_goals, formalizer,
                                          toolkit=toolkit, project_dir=project_dir,
                                          timeout_s=timeout_s, faithfulness_checker=None,
                                          server=server, retriever=retriever,
                                          repair_iters=repair_iters)

    return gate
