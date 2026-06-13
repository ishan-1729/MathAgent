from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# Top-level categories after the role-based reorg:
#   agent/      - the agentic system (orchestrator, roles, gates, tools, instructions, workflows)
#   knowledge/  - the math knowledge base (methods, library, examples)
#   benchmarks/ - targets + scoring (problems, evaluation)
#   formal/     - Lean (advisory in v1)
#   research/   - literature + design docs (papers, docs)
#   scripts/    - infra ; .agents/ - skill discovery convention
REQUIRED_TOP_LEVEL_DIRS = [
    ".agents",
    "agent",
    "agent/orchestrator",
    "agent/roles",
    "agent/gates",
    "agent/tools",
    "agent/instructions",
    "agent/workflows",
    "benchmarks",
    "benchmarks/problems",
    "benchmarks/evaluation",
    "formal",
    "knowledge",
    "knowledge/methods",
    "knowledge/library",
    "knowledge/examples",
    "research",
    "research/papers",
    "research/docs",
    "scripts",
    "tests",
]

KEY_FILES = [
    "README.md",
    "AGENTS.md",
    "CONTRIBUTING.md",
    "Makefile",
    "pyproject.toml",
    "agent/README.md",
    "agent/PLAN.md",
    "agent/gates/README.md",
    "agent/gates/allowed_toolkit.yaml",
    "agent/gates/denylist.yaml",
    "agent/gates/ledger.schema.json",
    "agent/gates/gate.py",
    "agent/gates/lean_audit.py",
    "agent/gates/lean_bridge.py",
    "agent/gates/lean/Audit.lean",
    "agent/tools/numeric.py",
    "agent/tools/codex_prover.py",
    "agent/orchestrator/driver.py",
    "agent/orchestrator/dag.py",
    "agent/orchestrator/ralph.py",
    "agent/orchestrator/dag_driver.py",
    "agent/orchestrator/population.py",
    "agent/roles/prover.md",
    "agent/roles/critic_judge.md",
    "scripts/prove.py",
    "research/papers/INDEX.md",
    "research/docs/literature_design_implications.md",
    "knowledge/methods/TEMPLATE.md",
    "knowledge/library/identity_TEMPLATE.md",
    "benchmarks/problems/TEMPLATE/problem.md",
    "benchmarks/evaluation/run_record_TEMPLATE.md",
    "formal/lean/MathAgent/Scratch.lean",
    ".agents/skills/elementary-proof-attempt/SKILL.md",
    ".agents/skills/method-file-authoring/SKILL.md",
    ".agents/skills/evaluation-record/SKILL.md",
]

REQUIRED_FRONTMATTER_KEYS = {"name", "type", "status"}


def read_frontmatter(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return {}

    if not lines or lines[0].strip() != "---":
        return {}

    end = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = index
            break

    if end is None:
        return {}

    fields: dict[str, str] = {}
    for line in lines[1:end]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        fields[key.strip()] = value.strip()
    return fields


def check_frontmatter(markdown_files: list[Path], skipped_names: set[str]) -> list[str]:
    failures: list[str] = []
    for path in markdown_files:
        if path.name in skipped_names:
            continue
        frontmatter = read_frontmatter(path)
        missing = REQUIRED_FRONTMATTER_KEYS - set(frontmatter)
        if missing:
            rel = path.relative_to(ROOT)
            failures.append(f"{rel}: missing frontmatter keys {sorted(missing)}")
    return failures


def main() -> int:
    failures: list[str] = []

    for dirname in REQUIRED_TOP_LEVEL_DIRS:
        if not (ROOT / dirname).is_dir():
            failures.append(f"missing directory: {dirname}")

    for filename in KEY_FILES:
        if not (ROOT / filename).is_file():
            failures.append(f"missing file: {filename}")

    method_files = sorted((ROOT / "knowledge" / "methods").glob("*.md"))
    failures.extend(check_frontmatter(method_files, {"README.md", "TEMPLATE.md"}))

    library_files = sorted((ROOT / "knowledge" / "library").rglob("*.md"))
    failures.extend(check_frontmatter(library_files, {"README.md", "identity_TEMPLATE.md"}))

    if failures:
        print("MathAgent skeleton check: FAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("MathAgent skeleton check: PASS")
    print(f"Checked {len(REQUIRED_TOP_LEVEL_DIRS)} top-level directories.")
    print(f"Checked {len(KEY_FILES)} key files.")
    print(f"Checked {len(method_files)} method markdown files.")
    print(f"Checked {len(library_files)} library markdown files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
