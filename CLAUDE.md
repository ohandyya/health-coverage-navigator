# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

**Read [docs/progress.md](docs/progress.md) before doing anything else in a session.** It is the single source of truth for what is built, what is next, and why past decisions went the way they did.

Do not record status in this file. `CLAUDE.md` holds conventions that apply regardless of how far along the build is; `docs/progress.md` holds everything that changes as work happens. Three docs, three jobs: [docs/plan.md](docs/plan.md) says *what to build and when*, [docs/frontend_plan.md](docs/frontend_plan.md) says *how the web UI works*, [docs/progress.md](docs/progress.md) says *what is actually built*. The plans never record completion; progress never records design. [docs/glossary.md](docs/glossary.md) sits orthogonal to that split — it says *what the words mean*, and records neither schedule, design, nor status.

A `uv`-managed Python project: src layout, package `health_coverage_navigator`, Python 3.12.

## Commands

- `uv sync` — create/update the `.venv` and install dependencies from `pyproject.toml`/`uv.lock`.
- `uv run health-coverage-navigator` — run the CLI entry point (`src/health_coverage_navigator/__init__.py:main`).
- `uv add <package>` — add a runtime dependency (updates `pyproject.toml` + `uv.lock`).
- `uv add --dev <package>` — add a dev-only dependency (e.g. pytest, ruff).
- `uv run pytest` — run the test suite (once a `tests/` dir and pytest are added).
- `uv run pytest path/to/test_file.py::test_name` — run a single test.
- `uv run ruff check .` / `uv run ruff format .` — lint/format (ruff is configured in `pyproject.toml`).

Ruff is configured as a dev dependency (see `[tool.ruff]` in `pyproject.toml`); no test tooling is configured yet — add it as a `--dev` dependency when Phase 0 work begins, and update this section accordingly.

A `Makefile` wraps the lint/format/typecheck commands (`make help` lists them). Server and frontend targets (`make dev`, `make types`, `make ui-build`, …) do not exist yet — the intended set is specified in [docs/frontend_plan.md](docs/frontend_plan.md) §7.

## What this project is

Health Coverage Navigator: an agent that answers health-insurance questions ("is this treatment typically covered?", "what plans cover my doctor?", "what changed for this plan year?") by routing each sub-question to the right source type rather than relying on a single RAG pipeline. The core engineering problem is **tool routing**, not retrieval alone — every question decomposes into one of three lanes:

1. **"What does the rule/benefit say"** → RAG over a static public corpus
2. **"What's the specific fact for this plan/drug/provider"** → structured/deterministic API lookup
3. **"What's happening now / not in my corpus"** → general web search

Getting the agent to classify a sub-question into the correct lane (and combine lanes for compound questions) is the central thing being built and evaluated.

## Frontend (see docs/frontend_plan.md — do not re-derive these decisions)

The agent is fronted by a local web UI. **[docs/frontend_plan.md](docs/frontend_plan.md) is authoritative** for stack, API schemas, repo layout, UI specifics, and the F0–F4 build phases; read it before touching anything under `src/health_coverage_navigator/api/` or `frontend/`. Settled decisions, so they aren't relitigated:

- **Stack**: FastAPI backend + React / Vite / TypeScript / Tailwind / shadcn-ui frontend in a top-level `frontend/` directory. No state-management library, no data-fetching library, `npm` as the package manager.
- **Topology**: Vite dev server proxies `/api` to `uvicorn` in development (so **no CORS config anywhere**); FastAPI serves the built `frontend/dist` as static files otherwise. Localhost, single user, no auth.
- **The API contract is frozen in Phase 0 and is load-bearing.** Answers are structured, not strings: `source_type` (reference / structured_api / web), `citations`, `claims`, `trace`, and `abstained` as a **first-class boolean** — never inferred by pattern-matching the answer text. Later phases add *values* to these fields; they do not reshape them. Changing the contract is a deliberate act, not a convenience.
- **Types cross the boundary via codegen**: TypeScript types are generated from FastAPI's OpenAPI schema (`make types` → `frontend/src/api/schema.d.ts`). Never hand-edit the generated file; never hand-write a duplicate TS interface for a Pydantic model.
- **Streaming is SSE** consumed via `fetch` + a stream reader — *not* `EventSource`, which cannot send a POST body.
- **Secrets never reach the frontend.** API keys live in `.env` and are read server-side only. Anything in a `VITE_*` env var or the built bundle is public. Bind to `127.0.0.1`, never `0.0.0.0`.

## Data sources (see docs/plan.md for full details and links)

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

## Glossary (KEEP CURRENT — this is a standing rule, not a one-time task)

[docs/glossary.md](docs/glossary.md) defines every health-insurance, medical, and US-regulatory term this repo uses. Read it when an unfamiliar acronym appears rather than re-deriving the meaning, and treat its entries as the repo's settled usage — if a doc and the glossary disagree, that is a bug in one of them, not a matter of taste.

**When a change introduces a domain term the glossary does not already carry, add the entry in the same change — not later, not in a follow-up.** This applies equally to docs, code, comments, commit messages, and new data sources. A new source in particular almost always drags in several terms at once (its publisher, its file format, its identifiers); glossing them is part of adding the source, not a separate chore.

- **What qualifies:** health, medical, insurance, pharmacy, and US-healthcare-regulatory vocabulary — agencies, programs, coverage vehicles, code systems, benefit-design concepts, dataset and identifier names, clinical service categories.
- **What does not:** general software terms, library and framework names, and project-internal jargon that [docs/plan.md](docs/plan.md) or [docs/frontend_plan.md](docs/frontend_plan.md) already owns. Don't grow the glossary into a second copy of those docs.
- **What an entry must contain:** the expansion, and — the load-bearing part — what the term means *in this repo*: its licensing status, which routing lane it belongs to, which schema field or dataset column it maps to, or the correctness rule it carries. A bare dictionary expansion is not a useful entry.
- **What not to duplicate:** the 256 official HealthCare.gov consumer definitions vendored under `data/raw/healthcare_gov/posts/glossary_*.json`. The glossary points at them; it does not restate them.

Terms that gate what may be committed — CPT, CDT, HCPCS, ICD, NCD, LCD, PII, PHI — are glossed with their consequence spelled out. Keep it that way: a glossary that softens the blocklist below is worse than no glossary.

## Public-repo data guardrail (ACTION REQUIRED before committing data)

**This repo is public.** Before staging, committing, or writing any file under `data/` (or any new corpus/fixture directory), Claude MUST stop and verify the data is cleared for public distribution. If it is not — or if you are unsure — do NOT add it; flag it to the user and ask.

**Blocked from the public repo (do not vendor):**
- **Medicare Coverage Database LCDs and Billing/Coding Articles** — they embed **AMA/ADA-copyrighted CPT/CDT codes** under restricted license. Index **NCDs only** (NCDs contain no procedure codes).
- Any dataset containing **CPT, CDT, HCPCS Level II, or ICD proprietary code tables**, or other third-party-copyrighted/license-restricted content.
- **PII, PHI, secrets, or API keys** of any kind (e.g. a CMS Marketplace API key — those go in env/secrets, never in the repo).

**Cleared for the public repo (safe to vendor):**
- **HealthCare.gov** consumer-education content (Content API JSON) — published explicitly for third-party reuse.
- **Medicare Coverage Database NCDs** (no procedure codes).
- U.S.-government public-domain works (e.g. Medicare & You handbook, Exchange PUFs, openFDA, NPPES bulk files) — verify per-source before adding.

**Check before adding a new data source:** (1) Is it a U.S.-government/public-domain work or explicitly licensed for reuse? (2) Does it embed AMA/ADA/other proprietary code tables? (3) Any PII/PHI/secrets? If (1) is not a clear yes, or (2)/(3) is a yes, stop and ask the user rather than committing it. When a source is only *partly* clean (e.g. MCD, which has both NCDs and LCDs), vendor only the cleared subset.

## Architecture phases

The plan is staged so each phase ships something usable before adding complexity. Do not jump ahead of the current phase's scope unless asked. Each phase's *frontend* slice is noted below; the corresponding F-phase in [docs/frontend_plan.md](docs/frontend_plan.md) §6 has the detail.

- **Phase 0 — Corpus + eval scaffold.** Ingestion pipeline (download → parse → chunk → store to `data/processed`; **no embeddings, no vector store yet** — that's Phase 1b), plus a ~30-question gold eval set (question, expected source-type, expected answer) and an eval runner reporting recall@k/MRR. Build the harness before the agent. *Frontend (F0): freeze the API contract, FastAPI skeleton with a stubbed answer endpoint, UI scaffolded and rendering that stub — the whole interface proven before an agent exists.*
- **Phase 1 — RAG-only MVP.** Single `retrieve(query)` tool. Agent answers with citations, abstains when out-of-corpus. Extend eval to answer correctness + groundedness/faithfulness. Split into **1a** (lexical/full-text retrieval, no vector DB) and **1b** (embeddings + vector store behind the same `retrieve` interface, compared against the 1a baseline). *Frontend (F1): the Phase 0 stub is swapped for the real agent, streaming wired up, eval dashboard over real runs. 1b adds only an eval run-comparison view — the chat UI is untouched by design.*
- **Phase 2 — Add web search.** Agent now chooses between RAG and web. Add a routing-correctness eval slice, separate from answer correctness. Tag each answer with its source type. *Frontend (F2): the `web` source badge goes live; routing accuracy joins the eval dashboard.*
- **Phase 3 — Add structured-API tools.** Typed (Pydantic) wrappers for Marketplace API, openFDA, NPPES. Needs API-key/secrets management, rate-limit/retry handling, response caching, and synthetic fixtures so tests/evals don't depend on live APIs. Tri-modal routing eval (reference vs. API vs. web). *Frontend (F2): the `structured_api` badge goes live, completing the three-lane vocabulary.*
- **Phase 4 — Multi-step agent + provenance.** Plan → act → observe → synthesize loop with decomposition of compound questions, per-claim source-type tagging (indexed-reference / structured-API / web) with the retrieval chunk or URL behind each claim, observability/tracing (Logfire or similar), multi-hop correctness evals, and a hop ceiling / cycle detection for loop safety. *Frontend (F3): trace panel handles nested multi-hop steps; per-claim provenance highlighting.*
- **Phase 5 — Growth surface.** Plan comparison across the PUFs, formulary/drug-cost lookup, provider-network checks, No Surprises Act appeals guidance, and a scheduled "what changed this plan year" monitor. Needs a structured backend (DuckDB/SQLite) over the PUFs and a regression eval suite so later phases don't silently break earlier ones. *Frontend (F4): comparison tables, cost breakdowns, and a "what changed" view — the first UI work that is more than chat + provenance.*

## Working conventions implied by the plan

- **Eval-first**: every phase pairs new capability with a corresponding eval slice (retrieval quality → answer correctness/groundedness → routing correctness → multi-hop correctness/citation accuracy → regression suite). When implementing a phase, build or extend the eval alongside it, not after.
- **Provenance is not optional**: every synthesized claim must be traceable to a source type (indexed-reference, structured-API, or web) and the specific chunk/URL behind it. This is a hard requirement for a health-coverage tool, not polish to defer.
- **Grounding guardrail**: the RAG agent must answer only from retrieved context and explicitly abstain ("not in my reference material") rather than hallucinate when a question falls outside the corpus.
- **Fixtures over live calls in tests**: Phase 3+ structured-API tools should have synthetic fixtures so tests and evals don't depend on live, rate-limited, key-gated APIs.
- **The UI tracks the phases, it doesn't lead them**: build only the frontend surface for the capability the current phase ships. The contract is frozen early precisely so later phases add values to existing fields instead of reshaping the response — don't add UI for a lane or a metric that doesn't exist yet.
- **`docs/plan.md` says *when*, `docs/frontend_plan.md` says *how***: keep frontend stack/schema/layout detail out of `plan.md`, and keep phase scheduling out of `frontend_plan.md`. When something changes, update the one that owns it.
