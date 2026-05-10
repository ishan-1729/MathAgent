.PHONY: check tree

check:
	python scripts/check_repo_skeleton.py

tree:
	@if command -v tree >/dev/null 2>&1; then \
		tree -a -I '.git|.venv|__pycache__|.lake'; \
	else \
		find . -maxdepth 4 -print | sort; \
	fi
