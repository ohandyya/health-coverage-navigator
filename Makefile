.PHONY: help lint format format-check fix check typecheck typecheck-watch check-all \
        scan scan-staged scan-unstaged scan-selftest

.DEFAULT_GOAL := help

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*##/ {printf "  %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

lint: ## Check code for lint errors
	uv run ruff check .

fix: ## Check code for lint errors and auto-fix what's fixable
	uv run ruff check --fix .

format: ## Reformat code in place
	uv run ruff format .

format-check: ## Check code formatting without writing changes (CI-safe)
	uv run ruff format --check .

check: lint format-check ## Run lint and format-check together

typecheck: ## Check code for type errors
	uv run pyright

typecheck-watch: ## Re-run pyright on file changes
	uv run pyright --watch

check-all: lint format-check typecheck ## Run lint, format-check, and typecheck together

# Deliberately not part of check-all: check-all is the fast inner-loop command, and a scan
# of the whole 14 MB corpus is a pre-publish gate you invoke on purpose.
scan: ## Scan for secrets, PII/PHI, and licence-restricted content for the whole repo
	uv run python scripts/scan_sensitive.py

scan-unstaged: ## Scan only work in progress — unstaged changes plus untracked files
	uv run python scripts/scan_sensitive.py --unstaged

scan-staged: ## Scan only what is staged for the next commit
	uv run python scripts/scan_sensitive.py --staged

scan-selftest: ## Prove every scan detector still fires (run after changing a pattern)
	uv run python scripts/scan_sensitive.py --self-test
