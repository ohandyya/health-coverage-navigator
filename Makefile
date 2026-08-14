.PHONY: help lint format format-check fix check typecheck typecheck-watch test check-all \
        chunk chunk-check scan scan-staged scan-unstaged scan-selftest \
        ui-install ui-dev ui-build ui-test ui-check api-dev dev serve types types-check \
        eval eval-retrieval eval-judge eval-stub

.DEFAULT_GOAL := help

# The frontend toolchain is pinned by .nvmrc (node 22). If nvm is installed but not loaded by the
# shell that runs make, load it here so `make ui-*` uses the pinned version rather than whatever
# node happens to be first on PATH — a mismatch that surfaces as an opaque Vite engine error.
NVM_SH := $(HOME)/.nvm/nvm.sh
NODE_ENV_PREFIX := $(shell test -s $(NVM_SH) && echo 'export NVM_DIR="$$HOME/.nvm"; . "$$NVM_DIR/nvm.sh" >/dev/null; nvm use >/dev/null 2>&1;')

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

check-all: lint format-check typecheck test ui-check ## Run every gate: ruff, pyright, pytest, tsc, oxlint, vitest

# ---------------------------------------------------------------- frontend -------------------

ui-install: ## Install frontend dependencies (needs node >= 22.12 — see .nvmrc)
	@$(NODE_ENV_PREFIX) cd frontend && npm install

ui-dev: ## Vite dev server with hot reload (:5173) — this is the URL you open
	@$(NODE_ENV_PREFIX) cd frontend && npm run dev

ui-build: ## Compile the frontend into frontend/dist
	@$(NODE_ENV_PREFIX) cd frontend && npm run build

ui-test: ## Vitest over the SSE parser (the one frontend module with real logic)
	@$(NODE_ENV_PREFIX) cd frontend && npx vitest run

# Part of check-all, but it *skips* rather than fails when the frontend has never been installed.
# check-all is the command this repo trusts as its correctness gate, and a fresh clone or a
# Python-only session must still be able to run it green. Once `make ui-install` has run, the
# full gate applies. The rejected alternative was leaving the frontend out of check-all entirely
# (as `scan` and `chunk` are) — but those are excluded for being slow or for writing files, and
# tsc is neither; it is a correctness check that belongs with the others.
#
# Vitest runs here for the same reason, and the argument is stronger: `stream.ts` is the one
# frontend module with real logic, and the bugs its tests cover (a frame or a multi-byte character
# split across a chunk boundary) are invisible in normal use, so the suite is the only feedback
# there is. It writes nothing and takes about a second. `make ui-test` still runs it alone.
#
# `types-check` runs last, for the same reason: sub-second, writes only to /tmp, and it is what
# catches the one seam tsc cannot — `client.ts` hand-writes its route strings, so a renamed
# FastAPI route compiles clean and 404s at runtime. Folded in here rather than added to
# check-all's own dependency list so it shares this same node_modules skip-guard instead of
# duplicating it.
ui-check: ## Typecheck, lint, test, and verify schema.d.ts is current (skipped when deps aren't installed)
	@if [ -d frontend/node_modules ]; then \
		$(NODE_ENV_PREFIX) cd frontend \
			&& npx tsc --noEmit -p tsconfig.app.json \
			&& npm run lint \
			&& npx vitest run; \
	else \
		echo "frontend/node_modules missing — run 'make ui-install'; skipping ui-check"; \
	fi
	@# A fresh recipe line, not backslash-joined to the block above: that block ends inside
	@# `frontend/` (the `cd` carries across a single shell), and `$(MAKE)` run from there would
	@# look for a Makefile in `frontend/` instead of here. A new line starts a new shell back at
	@# this Makefile's own directory. Silently skipped alongside the block above — no need for a
	@# second "node_modules missing" message.
	@if [ -d frontend/node_modules ]; then $(MAKE) types-check; fi

api-dev: ## FastAPI with autoreload (:8000). Binds 127.0.0.1: there is no auth.
	uv run uvicorn health_coverage_navigator.api.app:app --reload --host 127.0.0.1 --port 8000

dev: ## Both servers side by side — then open http://127.0.0.1:5173
	$(MAKE) -j2 api-dev ui-dev

serve: ui-build ## Single-process mode: FastAPI serving the built UI on :8000
	uv run uvicorn health_coverage_navigator.api.app:app --host 127.0.0.1 --port 8000

types: ## Regenerate frontend TS types from the FastAPI OpenAPI schema
	uv run python -m health_coverage_navigator.api.dump_openapi --out /tmp/hcn-openapi.json
	@$(NODE_ENV_PREFIX) cd frontend && npx openapi-typescript /tmp/hcn-openapi.json -o src/api/schema.d.ts

types-check: ## Verify schema.d.ts is current with the Pydantic models (writes nothing)
	@uv run python -m health_coverage_navigator.api.dump_openapi --out /tmp/hcn-openapi.json
	@$(NODE_ENV_PREFIX) cd frontend && npx openapi-typescript /tmp/hcn-openapi.json -o /tmp/hcn-schema.d.ts >/dev/null \
		&& diff -u src/api/schema.d.ts /tmp/hcn-schema.d.ts \
		&& echo "schema.d.ts is current" \
		|| { echo "schema.d.ts is stale — run 'make types'"; exit 1; }

# All three write to data/eval_runs/, so deliberately not in check-all for the same reason as
# `chunk`. `eval` and `eval-judge` also cost money — 35 model calls each, doubled with the judge —
# which is the second reason a fast inner-loop gate must not run them.
eval: ## Run the gold set through the agent (35 model calls) -> data/eval_runs/
	uv run python -m health_coverage_navigator.evals.runner --runner agent

# The loop for tuning bm25_b / bm25_k1: no agent, no model, no key, sub-second over all 30
# in-corpus questions. Sweeping those parameters through the agent would cost 35 model calls per
# data point AND entangle retrieval quality with the agent's tool-choice behaviour, which are the
# two things this separation exists to keep apart.
eval-retrieval: ## Score BM25 retrieval alone against the gold set — free and instant
	uv run python -m health_coverage_navigator.evals.runner --runner bm25

# Answer correctness, graded per key fact by a second model. Opt-in and deliberately unreachable
# from the UI: a button that spends money on every click is the wrong affordance.
eval-judge: ## Run the gold set through the agent AND grade answers with the LLM judge
	uv run python -m health_coverage_navigator.evals.runner --runner agent --judge

eval-stub: ## Re-measure the Phase 0 canned answerer — the baseline real scores are read against
	uv run python -m health_coverage_navigator.evals.runner --runner stub

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
