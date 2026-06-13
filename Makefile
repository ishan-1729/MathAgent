.PHONY: check test demo all tree

# Repo skeleton + frontmatter check.
check:
	python scripts/check_repo_skeleton.py

# Constraint-engine test suite.
test:
	python -m pytest

# End-to-end demo of the gate + flat driver (no LLM / no network).
demo:
	python -m agent.demo

# Everything CI should run.
all: check test

tree:
	@if command -v tree >/dev/null 2>&1; then \
		tree -a -I '.git|.venv|__pycache__|.lake|*.egg-info'; \
	else \
		find . -maxdepth 4 -print | sort; \
	fi
