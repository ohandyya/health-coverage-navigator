.PHONY: help lint format format-check fix check typecheck typecheck-watch test check-all \
        chunk chunk-check scan scan-staged scan-unstaged scan-selftest

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

test: ## Run the test suite (verbose: per-test names/results)
	uv run pytest -v

check-all: lint format-check typecheck test ## Run lint, format-check, typecheck, and tests together

# Also deliberately not part of check-all: chunk writes files, and check-all is a read-only gate.
# The correctness is already covered — `make test` builds the chunks in memory and checks them
# against the committed manifests, so a stale chunks.jsonl fails the suite without check-all
# having to rebuild it.
chunk: ## Chunk the three text corpora -> data/processed/<source>/chunks.jsonl
	uv run python -m health_coverage_navigator.chunking

chunk-check: ## Rebuild chunks in memory and verify they match the committed manifests
	uv run python -m health_coverage_navigator.chunking --check

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
