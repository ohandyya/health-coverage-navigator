# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working in this repository. This file holds only the
**invariants** — things true regardless of how far along the build is. Everything else lives in
`docs/` and is linked from here; read the linked doc when the task touches it.

## Read first

**[docs/progress.md](docs/progress.md) is the single source of truth for what is built, what is
next, and why past decisions went the way they did. Read it before doing anything else in a
session.** Do not record status in this file.

Four docs, four jobs — keep material in the one that owns it:

| Doc | Says | Never contains |
|---|---|---|
| [docs/plan.md](docs/plan.md) | what to build, and when | completion status, frontend specifics |
| [docs/frontend_plan.md](docs/frontend_plan.md) | how the web UI works | phase scheduling |
| [docs/progress.md](docs/progress.md) | what is actually built | design rationale for unbuilt things |
| [docs/glossary.md](docs/glossary.md) | what the words mean | schedule, design, or status |

Reference docs: [development.md](docs/development.md) (commands, gates, toolchain) ·
[configuration.md](docs/configuration.md) · [chunking.md](docs/chunking.md) ·
[agent.md](docs/agent.md) (toolset, grounding guardrail, loop limits, grading) ·
[lancedb.md](docs/lancedb.md) · [relational-tool.md](docs/relational-tool.md) (Phase 1-c: the
structured lane's design — tools, SQL guard, row citations) ·
[web_search_tool.md](docs/web_search_tool.md) (Phase 2: the web lane's design — Tavily client,
budgets, web citations, three-lane routing evals) · per-source data guides
(`*_data.md`) · [data/README.md](data/README.md) (layout + per-source licensing).

**Presentation, not reference:** [technical_highlights.md](docs/technical_highlights.md) indexes
[docs/highlights/](docs/highlights/), one page per mechanism worth showing off, and the README links
it. It is *derived* from the docs above — never the source of truth for a design, never a place to
record status. A change that invalidates a highlight updates the owning doc first, then the page.

## What this project is

Health Coverage Navigator: an agent answering health-insurance questions by routing each
sub-question to the right source type. The core engineering problem is **tool routing**, not
retrieval alone. Three lanes:

1. **"What does the rule/benefit say"** → search tools over a static public reference corpus
2. **"What's the specific fact for this plan/drug/provider"** → structured/deterministic API lookup
3. **"What's happening now / not in my corpus"** → general web search

**One agent, more tools.** Phase 1 stands up a single PydanticAI agent; every later phase
registers more tools on that same agent rather than building a parallel system. Nothing chains
retrieve → stuff-context → generate.

## Commands

`uv sync` · `uv run pytest` · `uv run ruff check .` · `make check-all` (ruff, pyright, pytest,
frontend gate) · `make dev` · `make types` · `make chunk` · `make embed` · `make eval` ·
`make smoke` · `make scan` · `make help` for the rest. Full list, toolchain pins, and the gotchas:
[docs/development.md](docs/development.md).

**`check-all` never reaches a model provider** — the suite sets `ALLOW_MODEL_REQUESTS = False`, so
it needs no key and costs nothing. Only `smoke*` and `eval*` spend money, and both stay outside it.

## Configuration

Split by **where a value is allowed to live**, not by how sensitive it feels — see
[docs/configuration.md](docs/configuration.md) for the reasoning and the three easy-to-break
rules. Two that must never be broken:

- **Never add a tunable to `Secrets`** (`settings.py`). Every `BaseSettings` field is env-var
  populated, and an env var is invisible to git — an eval run it configured cannot be reproduced
  from the repo. Tunables go in the committed `config.yaml` (`config.py`), which carries no
  Python defaults.
- **Never put a secret in `config.yaml`.** It is committed; `make scan` treats a
  credential-shaped assignment there as a blocking error.

## Frontend

**[docs/frontend_plan.md](docs/frontend_plan.md) is authoritative** for stack, API schemas, repo
layout, UI specifics, and the F0–F4 phases. Read it before touching
`src/health_coverage_navigator/api/` or `frontend/`; do not re-derive its decisions. The four
invariants that outlive any of them:

- **The API contract is frozen (Phase 0) and load-bearing.** Answers are structured, not strings:
  `source_type`, `citations`, `claims`, `trace`, and `abstained` as a **first-class boolean**,
  never inferred from answer text. Later phases add *values* to these fields, never reshape them.
- **Types cross the boundary via codegen** (`make types` → `frontend/src/api/schema.d.ts`). Never
  hand-edit it; never hand-write a duplicate TS interface for a Pydantic model.
- **Secrets never reach the frontend.** Anything in a `VITE_*` var or the built bundle is public.
- **Bind to `127.0.0.1`, never `0.0.0.0`.** Vite proxies `/api` in dev — **no CORS config anywhere**.

## Data sources

Full list, links, and rationale: [docs/plan.md](docs/plan.md) → *Data sources*. The rules:

- **Never chunk or embed a structured source.** Text corpora (HealthCare.gov, Medicare & You,
  NCDs) are chunked for the reference lane; the PUFs and Part D SPUF land as a lossless columnar
  mirror that Phase 1-c's relational tools query in place — read, never reshaped.
- **NPPES and openFDA are used live via their APIs and never vendored.** Both publish bulk
  downloads; both are per-record lookups, so a mirror buys staleness and storage for nothing. **Do
  not add a bulk downloader for either.** Consequence: no provider-level data is ever vendored here.

## Glossary — KEEP CURRENT (a standing rule, not a one-time task)

[docs/glossary.md](docs/glossary.md) defines every health-insurance, medical, and US-regulatory
term this repo uses. **When a change introduces a domain term it does not already carry, add the
entry in the same change** — not later, not in a follow-up. Read it when an unfamiliar acronym
appears rather than re-deriving the meaning. What qualifies, what an entry must contain, and what
not to duplicate: [*Maintaining this glossary*](docs/glossary.md#maintaining-this-glossary).

## Version control — do not stage or commit unless asked

**Claude does not run `git add`, `git commit`, `git push`, or `gh pr create` unless the user asks.**
Do the work, run the gates, say what changed, and stop there. An unstaged diff is how the user reads
what was written; committing on Claude's initiative takes that reading away and has to be undone
before it can be redone properly. A request to commit covers the commit it was made for — it is not
standing permission for the rest of the session, unless the user says so.

When the user does ask:

- **Stage explicit paths. Never `git add -A` or `git add .`** — `docs/human_worklog.md` is the
  author's file, and a `git add -A` has already swept it into a commit whose message did not mention
  it ([docs/progress.md](docs/progress.md) records the incident). A path list cannot do that.
- **Run `make scan` first** if anything under `data/` is involved — see the guardrail below.

## Public-repo data guardrail (ACTION REQUIRED before committing data)

**This repo is public.** Before staging, committing, or writing any file under `data/` (or any new
corpus/fixture directory), Claude MUST stop and verify the data is cleared for public
distribution. If it is not — or if you are unsure — do NOT add it; flag it to the user and ask.

**Blocked (do not vendor):**
- **Medicare Coverage Database LCDs and Billing/Coding Articles** — they embed
  **AMA/ADA-copyrighted CPT/CDT codes** under restricted license. Index **NCDs only** (NCDs carry
  no procedure codes).
- Any dataset containing **CPT, CDT, HCPCS Level II, or ICD proprietary code tables**, or other
  third-party-copyrighted / license-restricted content.
- **PII, PHI, secrets, or API keys** of any kind — a CMS Marketplace API key goes in `.env`, never
  in the repo.

**Cleared:** HealthCare.gov consumer content · MCD **NCDs** · Medicare Part D SPUF (the seven
small files; the pharmacy-network file is not fetched) · U.S.-government public-domain works
(Medicare & You, Exchange PUFs) — verify per-source before adding. Per-source clearance evidence:
[data/README.md](data/README.md#licensing).

**Check before adding a new source:** (1) U.S.-government/public-domain or explicitly licensed for
reuse? (2) Does it embed AMA/ADA/other proprietary code tables? (3) Any PII/PHI/secrets? If (1) is
not a clear yes, or (2)/(3) is a yes, stop and ask. When a source is only *partly* clean (e.g. the
MCD), vendor only the cleared subset. Run `make scan` before committing anything under `data/`.

## Architecture phases

Staged so each phase ships something usable before adding complexity. **Do not jump ahead of the
current phase's scope unless asked.** Full detail — acceptance tests, capability checklists,
rationale — in [docs/plan.md](docs/plan.md); the frontend slice of each is F0–F4 in
[docs/frontend_plan.md](docs/frontend_plan.md) §6.

**No completion marks in this table** — which phases are done is
[docs/progress.md](docs/progress.md)'s job, per *Read first* above. This table says what each phase
*is*, so it changes only when a phase's scope does.

| Phase | Adds | Eval slice |
|---|---|---|
| **0** | Corpus + eval scaffold, before any agent: ingestion, gold set, runner over a **pluggable answerer**, frozen contract, UI on a stub | retrieval quality |
| **1a** | **The agent itself** — one PydanticAI agent, small full-text toolset it composes (`list_documents` / `grep_corpus` / `search_corpus` / `get_chunk`), **no database of any kind** (stdlib BM25, no vector store, no DuckDB/SQLite FTS, no embeddings). Output schema, provenance, grounding rule, step limits written once, here | answer correctness + groundedness |
| **1b** | `vector_search` over LanceDB, **alongside** the 1a tools, not replacing them. Toolset (`lexical`/`vector`/`both`) is a per-run flag | lexical vs. vector vs. both |
| **1c** | The **structured lane**: `list_tables` / `describe_table` / `query_structured` over the vendored PUF mirrors, DuckDB querying Parquet in place. Design: [relational-tool.md](docs/relational-tool.md) | structured-lookup correctness + prose-vs-rows routing |
| **2** | Web search — `web_search` over **Tavily** (decided, not measured against Exa; not a scraped SERP) as a third lane. Design: [web_search_tool.md](docs/web_search_tool.md) | routing across three lanes |
| **3** | Typed tools for Marketplace API, openFDA, NPPES + rate limits, caching, fixtures | tri-modal routing |
| **4** | Planning and decomposition, per-claim provenance, tracing, cycle detection + hop ceiling | multi-hop + citation accuracy |
| **5** | Plan comparison, drug costs, network checks, appeals, "what changed" monitor — over the Phase 1-c query tools, not a new backend | regression suite |

## Working conventions

- **Async-first.** This is an I/O-bound application — model calls, and from Phase 2 web search and
  rate-limited APIs. Anything that can reach the network is `async def`, and so is any **seam** it
  is called through: if a `Callable` type alias, a protocol, or a registry entry might one day be
  backed by a network call, declare it `Awaitable` now. Three rules follow, and each has already
  cost this repo real work:
  - **Never `asyncio.run()` in library code** — it raises inside a running loop, at *runtime*, not
    at typecheck. It belongs only at an entrypoint: a CLI `main`, a script, a sync test boundary.
  - **Bound concurrency with `asyncio.Semaphore`, not a thread pool.** Threads are for genuinely
    blocking work that has no async form (file I/O — `asyncio.to_thread` is right there).
  - **A synchronous seam is not a local choice.** Making one async later converts every
    implementation, every caller, and every test at once — `evals/answerers.py` and
    `evals/grading.py` had to move together because a sync `Grader` made an async judge impossible.
    Sync is still correct for code that is purely CPU-bound and will stay that way; say so where it
    is not obvious.
- **Eval-first**: build or extend a phase's eval slice alongside the feature, not after.
- **Provenance is not optional**: every claim traces to a source type *and* the chunk or URL
  behind it. A hard requirement for a health tool, not polish to defer.
- **Grounding guardrail**: answer only from what the tools returned; abstain explicitly ("not in
  my reference material") rather than hallucinate outside the corpus.
- **Fixtures over live calls in tests**, so tests and evals never depend on rate-limited,
  key-gated APIs.
- **The UI tracks the phases, it doesn't lead them**: no UI for a lane or metric that doesn't
  exist yet.
