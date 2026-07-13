"""Runnable demo of the MathAgent constraint engine (no LLM / no network).

    python -m agent.demo

Shows: (1) the deterministic gate accepting a valid elementary ledger, (2) the gate rejecting a
planted non-elementary ledger, and (3) the flat driver driving a scripted prover through a repair
loop to PROVEN, with a rendered run record.
"""
from __future__ import annotations

import json
from pathlib import Path

from agent.gates.gate import evaluate
from agent.gates.toolkit import load_toolkit
from agent.orchestrator import FlatDriver, RunTrace, ScriptedProver, ScriptedJudge, JudgeVerdict

EXAMPLE = Path(__file__).resolve().parent / "gates" / "examples" / "squares_mod4.json"

PLANTED_NON_ELEMENTARY = {
    "problem": "demo_bad",
    "claim": "Some Diophantine claim.",
    "steps": [
        {"id": "s1", "claim": "Pass to the class group of the ring of integers and use class field theory.",
         "justification": "class_field_theory", "depends_on": []},
        {"id": "s2", "claim": "Hence done.", "justification": "conclusion", "depends_on": ["s1"]},
    ],
}


def _rule(title: str) -> None:
    print("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


def main() -> int:
    toolkit = load_toolkit()

    _rule("1. Deterministic gate ACCEPTS a valid elementary ledger (squares_mod4)")
    good = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    rep = evaluate(good, toolkit)
    print("verdict:", rep.summary())
    for f in rep.findings:
        print("  -", f)

    _rule("2. Deterministic gate REJECTS a planted non-elementary ledger")
    bad = evaluate(PLANTED_NON_ELEMENTARY, toolkit)
    print("verdict:", bad.summary())
    for f in bad.rejects():
        print("  REJECT:", f)

    _rule("3. Flat driver: scripted prover repairs a rejected attempt, judge confirms -> PROVEN")
    goal = good["claim"]
    # The flat certificate binds the theorem claim and operative conclusion. The example's
    # human-friendly conclusion starts with "Hence", so normalize a private copy for this demo;
    # ``problem`` correctly remains the dataset identifier ``squares_mod4``.
    bound_good = json.loads(json.dumps(good))
    for step in bound_good["steps"]:
        if step.get("justification") == "conclusion":
            step["claim"] = goal
    prover = ScriptedProver([json.dumps(PLANTED_NON_ELEMENTARY), json.dumps(bound_good)])
    judge = ScriptedJudge("critic", [JudgeVerdict("critic", elementary=True, no_gaps=True,
                                                  confidence=0.9)])
    driver = FlatDriver(prover, judges=[judge], toolkit=toolkit, trace=RunTrace("demo-run"))
    result = driver.run(goal)
    print("final state:", result.state.value, "| attempts:", result.attempts,
          "| proven:", result.proven)

    _rule("Rendered run record")
    print(result.trace.render_run_record({
        "problem": goal,
        "proof_status": "soft_proven (deterministic gate only; no Layer-4 certificate)",
        "summary": "Repaired a non-elementary first attempt into an elementary case-split proof.",
        "scores": "gate: PASSED_DETERMINISTIC; judges: 1/1 pass",
        "next_action": "rerun with profiles/authoritative.yaml and live providers for Layer-4 certification",
    }))
    return 0 if result.proven else 1


if __name__ == "__main__":
    raise SystemExit(main())
