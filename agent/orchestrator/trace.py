"""Machine-readable run trace + run-record rendering (PLAN.md Section 4.4).

Every run emits an append-only sequence of events (serializable to JSONL). The prose run record is a
rendered *view* of that trace, filling benchmarks/evaluation/run_record_TEMPLATE.md. A monotonic
`seq` makes traces deterministic for tests; the wall-clock `t` comes from an injectable clock.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Optional


@dataclass
class Event:
    seq: int
    t: float
    kind: str
    data: dict[str, Any] = field(default_factory=dict)


class RunTrace:
    def __init__(self, run_id: str, clock: Optional[Callable[[], float]] = None):
        self.run_id = run_id
        self._clock = clock or (lambda: 0.0)
        self.events: list[Event] = []
        self._seq = 0

    def emit(self, kind: str, **data: Any) -> Event:
        ev = Event(seq=self._seq, t=float(self._clock()), kind=kind, data=data)
        self.events.append(ev)
        self._seq += 1
        return ev

    def by_kind(self, kind: str) -> list[Event]:
        return [e for e in self.events if e.kind == kind]

    def to_jsonl(self) -> str:
        lines = []
        for e in self.events:
            row = {"run_id": self.run_id, "seq": e.seq, "t": e.t, "kind": e.kind, **e.data}
            lines.append(json.dumps(row, ensure_ascii=False, sort_keys=True))
        return "\n".join(lines)

    def write_jsonl(self, path: str | Path) -> None:
        Path(path).write_text(self.to_jsonl() + "\n", encoding="utf-8")

    # --- rendered view ---
    def render_run_record(self, fields: dict[str, Any]) -> str:
        """Render a markdown run record (mirrors run_record_TEMPLATE.md headings)."""
        f = dict(fields)
        return "\n".join([
            "# Run Record",
            "",
            f"## Run ID\n{self.run_id}",
            f"## Problem\n{f.get('problem', '')}",
            f"## Workflow\n{f.get('workflow', 'wf-flat-v1')}",
            f"## Paper/software configuration\n{f.get('config', 'training-free flat driver + deterministic gate')}",
            f"## Branch\n{f.get('branch', '')}",
            f"## Date\n{f.get('date', '')}",
            f"## Methods loaded\n{f.get('methods', '')}",
            f"## Library identities loaded\n{f.get('library', '')}",
            f"## Papers or systems referenced\n{f.get('papers', '')}",
            f"## Attempt summary\n{f.get('summary', '')}",
            f"## Proof status\n{f.get('proof_status', '')}",
            f"## Lean status\n{f.get('lean_status', 'n/a (advisory in v1)')}",
            f"## Metric scores\n{f.get('scores', 'gate-pending')}",
            f"## Notes\n{f.get('notes', '')}",
            f"## Next action\n{f.get('next_action', '')}",
            "",
            "## Trace (events)",
            "```",
            f"{len(self.events)} events; "
            f"prove={len(self.by_kind('prove'))}, gate={len(self.by_kind('gate'))}, "
            f"judge={len(self.by_kind('judge'))}, final={len(self.by_kind('final'))}",
            "```",
        ])
