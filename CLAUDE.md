# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

This repository is at the scaffolding stage: a `uv`-managed Python project (src layout, package `health_coverage_navigator`, Python 3.12, no dependencies yet) plus [plan.md](plan.md), the design document. No agent/RAG code, tests, or dependencies have been added yet — those come in with Phase 0.

## Commands

- `uv sync` — create/update the `.venv` and install dependencies from `pyproject.toml`/`uv.lock`.
- `uv run health-coverage-navigator` — run the CLI entry point (`src/health_coverage_navigator/__init__.py:main`).
- `uv add <package>` — add a runtime dependency (updates `pyproject.toml` + `uv.lock`).
- `uv add --dev <package>` — add a dev-only dependency (e.g. pytest, ruff).
- `uv run pytest` — run the test suite (once a `tests/` dir and pytest are added).
- `uv run pytest path/to/test_file.py::test_name` — run a single test.
- `uv run ruff check .` / `uv run ruff format .` — lint/format (once ruff is added as a dev dependency).

No lint/test tooling is configured yet — add it as `--dev` dependencies when Phase 0 work begins, and update this section accordingly.

## What this project is

Health Coverage Navigator: an agent that answers health-insurance questions ("is this treatment typically covered?", "what plans cover my doctor?", "what changed for this plan year?") by routing each sub-question to the right source type rather than relying on a single RAG pipeline. The core engineering problem is **tool routing**, not retrieval alone — every question decomposes into one of three lanes:

1. **"What does the rule/benefit say"** → RAG over a static public corpus
2. **"What's the specific fact for this plan/drug/provider"** → structured/deterministic API lookup
3. **"What's happening now / not in my corpus"** → general web search

Getting the agent to classify a sub-question into the correct lane (and combine lanes for compound questions) is the central thing being built and evaluated.

## Data sources (see plan.md for full details and links)

**Bulk corpus (for RAG ingestion):**
- HealthCare.gov consumer-education content — published as JSON (append `.json` to any post URL), explicitly licensed for reuse. Best MVP corpus.
- Medicare & You handbook (public domain PDF).
- Medicare Coverage Database — **NCDs only**. Do not vendor LCDs/Billing Articles into the public corpus; they contain AMA/ADA-copyrighted CPT codes.
- Health Insurance Exchange Public Use Files (Benefits and Cost Sharing PUF, Plan Attributes PUF) — large CSV/ZIP dumps, need DuckDB/SQLite rather than Excel.
- Medicare Part D formulary/pharmacy/pricing files (quarterly).
- NPPES provider registry bulk file (4GB+; use the NBER "core" mirror for a dev fixture).
- openFDA bulk drug-label JSON (optional, for offline indexing).

**Live APIs (structured lookups):**
- Marketplace API (`https://marketplace.api.healthcare.gov/api/v1/`) — plan search, drug coverage checks, cost estimates. Requires an API key from the CMS developer portal; rate-limited.
- openFDA (`https://api.fda.gov/`) — drug labels, recalls, shortages. No key needed.
- NPPES NPI Registry (`https://npiregistry.cms.hhs.gov/api/`) — live provider lookup, no bulk download needed for single queries.
- HealthCare.gov Content API — same content as the bulk JSON, usable live; CORS-enabled.

**Licensing caveat:** CPT/procedure codes are AMA/ADA-copyrighted — keep the public corpus/repo limited to NCDs, not LCDs or coding Articles.

## Architecture phases

The plan is staged so each phase ships something usable before adding complexity. Do not jump ahead of the current phase's scope unless asked.

- **Phase 0 — Corpus + eval scaffold.** Ingestion pipeline (download → parse → chunk → embed → store) into a local vector DB (Chroma/LanceDB/pgvector), plus a ~30-question gold eval set (question, expected source-type, expected answer) and an eval runner reporting recall@k/MRR. Build the harness before the agent.
- **Phase 1 — RAG-only MVP.** Single `retrieve(query)` tool. Agent answers with citations, abstains when out-of-corpus. Extend eval to answer correctness + groundedness/faithfulness.
- **Phase 2 — Add web search.** Agent now chooses between RAG and web. Add a routing-correctness eval slice, separate from answer correctness. Tag each answer with its source type.
- **Phase 3 — Add structured-API tools.** Typed (Pydantic) wrappers for Marketplace API, openFDA, NPPES. Needs API-key/secrets management, rate-limit/retry handling, response caching, and synthetic fixtures so tests/evals don't depend on live APIs. Tri-modal routing eval (reference vs. API vs. web).
- **Phase 4 — Multi-step agent + provenance.** Plan → act → observe → synthesize loop with decomposition of compound questions, per-claim source-type tagging (indexed-reference / structured-API / web) with the retrieval chunk or URL behind each claim, observability/tracing (Logfire or similar), multi-hop correctness evals, and a hop ceiling / cycle detection for loop safety.
- **Phase 5 — Growth surface.** Plan comparison across the PUFs, formulary/drug-cost lookup, provider-network checks, No Surprises Act appeals guidance, and a scheduled "what changed this plan year" monitor. Needs a structured backend (DuckDB/SQLite) over the PUFs and a regression eval suite so later phases don't silently break earlier ones.

## Working conventions implied by the plan

- **Eval-first**: every phase pairs new capability with a corresponding eval slice (retrieval quality → answer correctness/groundedness → routing correctness → multi-hop correctness/citation accuracy → regression suite). When implementing a phase, build or extend the eval alongside it, not after.
- **Provenance is not optional**: every synthesized claim must be traceable to a source type (indexed-reference, structured-API, or web) and the specific chunk/URL behind it. This is a hard requirement for a health-coverage tool, not polish to defer.
- **Grounding guardrail**: the RAG agent must answer only from retrieved context and explicitly abstain ("not in my reference material") rather than hallucinate when a question falls outside the corpus.
- **Fixtures over live calls in tests**: Phase 3+ structured-API tools should have synthetic fixtures so tests and evals don't depend on live, rate-limited, key-gated APIs.
