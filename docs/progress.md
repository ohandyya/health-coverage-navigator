# Progress

Status of the build against [plan.md](plan.md) and [frontend_plan.md](frontend_plan.md).
**Those docs say what to build; this one says what is built.** They never record completion;
this one never records design.

**How to read this:** the *Current state* block below is the one-read answer to "where am I".
The *Log* is append-only, newest first — read down as far as you need for the reasoning behind
the current state. A log entry is true as of its date and is never edited.

**What does not belong here:** anything git history or the code already says. No file
inventories, no restating what a script does. The value of this file is entirely in what cannot
be reconstructed — decisions, rejected alternatives, dead ends, and where work stopped
mid-stream.

---

## Current state

*Updated 2026-07-31.*

- **Phase:** 0 — corpus + eval scaffold. Corpus ingestion done; nothing else in Phase 0 started.
- **Next up:** the chunking step (`data/processed/<source>/corpus.jsonl` → chunks). It is the
  last piece of the ingestion pipeline and the gold eval set depends on knowing what a chunk
  looks like.
- **Open questions blocking nothing yet:** see [frontend_plan.md](frontend_plan.md) §10 — the
  eval-runs-from-the-browser question (read-only dashboard vs. HTTP-triggered runs) is marked
  "decide at F0" and is still undecided.

### Phase 0 checklist

Backend (plan.md, Phase 0):

- [x] HealthCare.gov ingestion — 803 docs
- [x] Medicare publications ingestion — 83 pubs / 964 pages
- [ ] Chunking step → chunked corpus in `data/processed`
- [ ] Gold eval set, ~30 questions (question → expected source-type → expected answer)
- [ ] Eval dataset loader / schema
- [ ] Test tooling (no pytest configured yet)

Frontend (frontend_plan.md, Phase F0):

- [ ] `api/models.py` — the frozen contract models
- [ ] `api/app.py` — `/api/health` + stubbed `POST /api/chat`
- [ ] `frontend/` scaffold (Vite + React + TS + Tailwind + shadcn), `/api` proxy
- [ ] `make types` → `frontend/src/api/schema.d.ts`
- [ ] Chat page rendering the stub end to end
- [ ] Eval endpoints (after the gold set exists)

---

## Log

### 2026-07-31 — progress tracking

**Did:** added this file. Removed the `## Project status` section from `CLAUDE.md`, which now
carries only a pointer here. Added a `/wrap-up` command (`.claude/commands/wrap-up.md`) to
append log entries at the end of a working session.

**Decided:** status lives in exactly one place. `CLAUDE.md` is for conventions that must be
obeyed every session; status is read every session but obeyed never, and mixing the two
dilutes the guardrails and makes every status change look like a rules change in the diff.

**Rejected:** keeping a short status summary in `CLAUDE.md` alongside the full version here —
two sources of truth drift. Also rejected a `Stop` hook for auto-updating this file: it fires
on every pause, not on session end, so it would produce constant low-value entries.

### 2026-07-31 — frontend design

**Did:** wrote [frontend_plan.md](frontend_plan.md) (design only — no code). Added the frontend
slices to each phase in `plan.md` and the frontend section to `CLAUDE.md`.

**Decided:** FastAPI + React/Vite/TS/Tailwind/shadcn in a top-level `frontend/`. Vite proxies
`/api` to uvicorn in dev, so no CORS config anywhere. TS types are generated from the OpenAPI
schema rather than hand-written. Streaming is SSE over `fetch`, not `EventSource` — the latter
cannot send a POST body. The API contract is frozen now: `source_type`, `citations`, `claims`,
`trace`, and `abstained` as a first-class boolean, never inferred from answer text.

**Rejected:** a state-management library and a data-fetching library — there is no server state
worth caching yet. A corpus-browser page — citation drill-down covers most of the need.

**Left open:** frontend_plan.md §10 (multi-turn intent, eval runs over HTTP, corpus browser).

**Commits:** `87e2dc4`

### 2026-07-27 — Medicare publications corpus

**Did:** `scripts/download_medicare_pubs.py` → 83 publications / 964 pages into
`data/{raw,processed}/medicare_pubs/`. Guide in [medicare_pubs_data.md](medicare_pubs_data.md).

**Decided:** the second downloader mirrors the first one's shape rather than being generalized
into a framework — argparse CLI, `raw/` → `processed/` split, idempotent, `--normalize-only`
re-parse. Both emit `corpus.jsonl` with the same field vocabulary (`id`, `source`, `url`,
`title`, `bite`, `text`), which is what makes the sources interchangeable downstream.

**Commits:** `0bbc7b2`

### 2026-07-06 — HealthCare.gov corpus

**Did:** `scripts/download_healthcare_gov.py` → 803 articles/glossary/state pages. Guide in
[health_care_data.md](health_care_data.md). Revised the Phase 0/1 sections of `plan.md`.

**Decided:** Phase 1 splits into 1a (lexical retrieval, no vector DB) and 1b (embeddings behind
the same `retrieve` interface), so 1b has a baseline to be compared against.

**Commits:** `673e2ca`, `023c611`

### 2026-07-04 — tooling

**Did:** ruff, pyright, `Makefile`, and a pre-commit hook running both.

**Commits:** `c31edf5`, `d038216`, `97fb8f8`
