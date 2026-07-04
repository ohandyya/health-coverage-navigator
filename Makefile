.PHONY: help lint format format-check fix check typecheck typecheck-watch check-all

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
