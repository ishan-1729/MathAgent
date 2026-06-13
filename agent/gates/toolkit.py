"""Load and query the allowed-toolkit vocabulary and the denylist.

The toolkit is the *closed vocabulary* of step justifications (Layer 1a) plus the contested
boundary rulings (PLAN.md Section 2.1). The denylist drives the Layer-1b prose scanner and the
later Lean Layer-4 dependency audit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

_GATES_DIR = Path(__file__).resolve().parent
_TOOLKIT_PATH = _GATES_DIR / "allowed_toolkit.yaml"
_DENYLIST_PATH = _GATES_DIR / "denylist.yaml"

# Obligation kinds a justification may require (mirrors ledger.schema.json).
OBLIGATION_KINDS = {"none", "case_cover", "descent", "bounding", "split_coprimality"}


@dataclass(frozen=True)
class Justification:
    """One entry in the allowed justification vocabulary."""

    key: str
    obligation: str = "none"
    desc: str = ""
    method_ref: Optional[str] = None


@dataclass
class Toolkit:
    """The loaded elementary toolkit + denylist."""

    justifications: dict[str, Justification]
    boundary_rulings: dict[str, dict] = field(default_factory=dict)
    prose_terms: list[str] = field(default_factory=list)
    allow_context_terms: list[str] = field(default_factory=list)
    lean_denylist_decls: list[str] = field(default_factory=list)
    lean_infrastructure_allowlist: list[str] = field(default_factory=list)
    lean_elementary_by_fiat: list[str] = field(default_factory=list)
    lean_axiom_whitelist: list[str] = field(default_factory=list)

    # --- queries ---
    def allowed_keys(self) -> set[str]:
        return set(self.justifications)

    def is_allowed(self, justification: str) -> bool:
        return justification in self.justifications

    def obligation_for(self, justification: str) -> str:
        """Obligation kind required by a justification ('none' if unknown-but-present)."""
        j = self.justifications.get(justification)
        return j.obligation if j else "none"

    def method_ref_for(self, justification: str) -> Optional[str]:
        j = self.justifications.get(justification)
        return j.method_ref if j else None

    def ruling(self, tool: str) -> Optional[str]:
        entry = self.boundary_rulings.get(tool)
        return entry.get("ruling") if entry else None


def _coerce_justifications(raw: dict) -> dict[str, Justification]:
    out: dict[str, Justification] = {}
    for key, spec in (raw or {}).items():
        spec = spec or {}
        obligation = spec.get("obligation", "none")
        if obligation not in OBLIGATION_KINDS:
            raise ValueError(
                f"justification {key!r} has unknown obligation {obligation!r}; "
                f"expected one of {sorted(OBLIGATION_KINDS)}"
            )
        out[key] = Justification(
            key=key,
            obligation=obligation,
            desc=spec.get("desc", ""),
            method_ref=spec.get("method_ref"),
        )
    return out


def load_toolkit(
    toolkit_path: Optional[Path] = None,
    denylist_path: Optional[Path] = None,
) -> Toolkit:
    """Load the toolkit + denylist from disk (defaults to the packaged data files)."""
    toolkit_path = Path(toolkit_path) if toolkit_path else _TOOLKIT_PATH
    denylist_path = Path(denylist_path) if denylist_path else _DENYLIST_PATH

    tdata = yaml.safe_load(toolkit_path.read_text(encoding="utf-8")) or {}
    ddata = yaml.safe_load(denylist_path.read_text(encoding="utf-8")) or {}

    justifications = _coerce_justifications(tdata.get("justifications", {}))
    if not justifications:
        raise ValueError(f"no justifications found in {toolkit_path}")
    if "conclusion" not in justifications:
        raise ValueError("toolkit must define a 'conclusion' justification (terminal step)")

    return Toolkit(
        justifications=justifications,
        boundary_rulings=tdata.get("boundary_rulings", {}) or {},
        prose_terms=[t.lower() for t in (ddata.get("prose_terms") or [])],
        allow_context_terms=[t.lower() for t in (ddata.get("allow_context_terms") or [])],
        lean_denylist_decls=list(ddata.get("lean_denylist_decls") or []),
        lean_infrastructure_allowlist=list(ddata.get("lean_infrastructure_allowlist") or []),
        lean_elementary_by_fiat=list(ddata.get("lean_elementary_by_fiat") or []),
        lean_axiom_whitelist=list(ddata.get("lean_axiom_whitelist") or ["propext", "Classical.choice", "Quot.sound"]),
    )
