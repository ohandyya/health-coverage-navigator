.PHONY: help lint format format-check fix check typecheck typecheck-watch test check-all \
        chunk chunk-check embed embed-check puf scan scan-staged scan-unstaged scan-selftest \
        ui-install ui-dev ui-build ui-test ui-check api-dev dev serve types types-check \
        eval eval-retrieval eval-retrieval-vector eval-lexical eval-vector eval-judge \
        eval-web eval-no-web smoke-web \
        eval-stub smoke smoke-abstain

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

# One real model call against the live provider. Outside check-all for the same reason `eval` is,
# and kept out of pytest deliberately: the test suite asserts our code is correct and is guaranteed
# never to reach a provider, while this asks whether the provider wiring still works — a question
# whose answer changes for reasons outside this repo. Mixing the two teaches you to ignore red.
#
# It covers the one property no offline test can: that `_partial_answer` still parses the output as
# a *real* provider fragments it. If that breaks, every test stays green and streaming silently
# degrades to one lump. See the module docstring.
#
# It runs the agent config.yaml describes — `agent.toolset`, `agent.structured_tools` AND
# `agent.web_tools` — so this smokes the same three-lane agent `make dev` serves, not a subset of
# it. Consequence: with `agent.web_tools: true` this needs TAVILY_API_KEY (it fails naming the fix
# rather than quietly dropping the lane, exactly as the API 503s), and the run may spend a Tavily
# credit if the model chooses to search. Set `agent.web_tools: false` to smoke without one.
smoke: ## One real question through the live agent, with the lanes config.yaml turns on (1 model call)
	uv run python scripts/smoke.py

smoke-abstain: ## Same lanes, but out-of-corpus — the agent must decline, not invent sources
	uv run python scripts/smoke.py --abstain

# The web lane's liveness check. Same three-rung logic as `smoke`: a failing test means this repo is
# wrong, a failing smoke check might mean the vendor changed something, and one command meaning
# either teaches you to shrug at red.
#
# What `--web` adds over plain `smoke` is the QUESTION and the CHECKS, not the lane: `smoke` already
# registers web_search when config.yaml says so, but it asks a definitional question the corpus
# answers, so the model never searches and a broken lane goes unnoticed. This asks one nothing on
# this machine can answer (a 2027 date, past every vendored publication) and then requires that the
# tool fired and cited a real URL. It force-opens the lane even with `agent.web_tools: false`.
smoke-web: ## Ask a current-events question live — the agent must reach the web and cite a real URL
	uv run python scripts/smoke.py --web

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

# The Phase 1b decision, and the reason it is a separate runner rather than two agent runs.
# Deterministic: same question, same vector, same neighbours. Seven agent runs at fixed config
# span recall@5 0.600-0.867 (docs/progress.md), so an agent A/B cannot resolve an effect the size
# of the one being looked for; this pair can, because no model is in either of them. Costs ~30
# short embedding calls — a fraction of a cent, but it does need a key, unlike eval-retrieval.
eval-retrieval-vector: ## Score semantic retrieval alone — the number 1b is decided on
	uv run python -m health_coverage_navigator.evals.runner --runner vector

# The toolset axis: one runner with a flag, not three code paths (docs/plan.md §1b). `make eval`
# is the third configuration — it runs whatever agent.toolset says, which is `both`. Read these as
# a sanity check on the agent's tool *choice*, not as the lexical-vs-vector comparison: that is
# what the two retrieval-only targets above are for.
eval-lexical: ## Agent restricted to the Phase 1a lexical tools (35 model calls)
	uv run python -m health_coverage_navigator.evals.runner --runner agent --toolset lexical

eval-vector: ## Agent restricted to vector_search (35 model calls)
	uv run python -m health_coverage_navigator.evals.runner --runner agent --toolset vector

# Answer correctness, graded per key fact by a second model. Opt-in and deliberately unreachable
# from the UI: a button that spends money on every click is the wrong affordance.
# Phase 2's axis. `eval-web` is what `make eval` already does when config.yaml has web_tools on;
# it exists as a named target so the pair reads as a pair. `eval-no-web` is the one that earns its
# keep: it answers "does the third lane cost anything on the questions that were already
# answerable", which is the same question `--no-structured` asked one lane earlier. Run them
# back-to-back and compare, and read both against progress.md's standing caveat that a single agent
# run at fixed config has a 0.200 spread.
eval-web: ## Agent with the web lane on (needs TAVILY_API_KEY; ~39 model calls + Tavily credits)
	uv run python -m health_coverage_navigator.evals.runner --runner agent --web

eval-no-web: ## Agent with the web lane off — the control for eval-web (~35 model calls)
	uv run python -m health_coverage_navigator.evals.runner --runner agent --no-web

# Phase 3's axis, and the same pair one lane later. `eval-no-live` is the control that answered the
# phase's real question: six more tools took the agent from nine to fifteen, and the risk was that
# it would start reaching for them on questions the other lanes already answered. It did not — the
# comparison is in README's measured table. `eval-live` needs no key for two of its three sources;
# without CMS_MARKETPLACE_API_KEY the three Marketplace tools go unregistered and their gold
# questions are dropped rather than scored as routing failures.
eval-live: ## Agent with the live-API tools on (~49 model calls; CMS key optional)
	uv run python -m health_coverage_navigator.evals.runner --runner agent --live

eval-no-live: ## Agent with the live-API tools off — the control for eval-live (~43 model calls)
	uv run python -m health_coverage_navigator.evals.runner --runner agent --no-live

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

# Outside check-all for all the usual reasons — writes files, needs a key — plus one of its own:
# it is the only build step in this repo that spends money. It is idempotent, so a re-run with an
# up-to-date store costs nothing and says so; --refresh forces the rebuild.
embed: ## Embed the chunked corpus into data/lancedb (~$0.04, ~2 min)
	uv run python -m health_coverage_navigator.vectors

# The vectors' answer to chunk-check. Cheap on purpose: it compares the snapshot id and the row
# count rather than re-embedding to compare vectors, and the snapshot id already covers every
# input that could change one.
embed-check: ## Verify the vector store matches the committed manifest — embeds nothing
	uv run python -m health_coverage_navigator.vectors --check

# The structured lane's data (Phase 1-c). Outside check-all because it downloads: ~24 MB over the
# wire for ~4.6 MB of Parquet. The mirrors are git-ignored — a fresh clone has none, and the app
# reports a 503 naming this target rather than answering plan questions from nothing. Both scripts
# are idempotent and conditional-GET aware, so a re-run with current data transfers almost nothing.
puf: ## Download + mirror the structured plan data (Exchange PUFs + Part D SPUF, ~24 MB)
	uv run python scripts/download_exchange_puf.py
	uv run python scripts/download_part_d_spuf.py

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
