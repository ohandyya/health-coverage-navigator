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

*Updated 2026-08-01.*

- **Phase:** 0 — corpus + eval scaffold. Ingestion is done for all four bulk sources (three RAG
  corpora + the structured `exchange_puf` mirror) and the public-repo guardrail is enforced by
  `make scan`; chunking and the eval scaffold have not started.
- **Next up:** the chunking step (`data/processed/<source>/corpus.jsonl` → chunks) — the three
  **text** corpora only; `exchange_puf` is structured data and is not chunked or embedded. It is
  the last piece of the ingestion pipeline and the gold eval set depends on knowing what a chunk
  looks like. The three corpora share the `id`/`source`/`url`/`title`/`bite`/`text` field
  vocabulary specifically so one chunker can span them — but they do not chunk alike: a
  HealthCare.gov article is short prose, a `medicare_pubs` record is one PDF page, and an NCD
  is a whole document with `## ` section headings written into `text` so they can be split on
  and kept as citation labels ("NCD 30.3, *Indications and Limitations of Coverage*").
- **Open questions:**
  - [frontend_plan.md](frontend_plan.md) §10 — eval runs from the browser (read-only dashboard
    vs. HTTP-triggered runs) is marked "decide at F0" and is still undecided.
  - `plan.md`'s Exchange PUF paragraph still describes two tables as the ones that matter; the
    build fetches three (Service Area as well, for the ZIP→plan mapping). Minor, and the reason
    is recorded in [exchange_puf_data.md](exchange_puf_data.md) — correct it if the paragraph is
    ever touched for another reason.

### Phase 0 checklist

Backend (plan.md, Phase 0):

- [x] HealthCare.gov ingestion — 803 docs
- [x] Medicare publications ingestion — 83 pubs / 964 pages
- [x] Medicare NCD ingestion — 345 determinations
- [x] Exchange PUF ingestion — 3 tables, plan year 2026 (structured mirror, not a text corpus)
- [x] Public-repo guardrail enforced in code — `make scan` (secrets / PII / licensing)
- [ ] Chunking step → chunked corpus in `data/processed` (the three text corpora only)
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

### 2026-08-01 — Exchange PUFs + vector-backend choice

**Did:** added the fourth bulk source — the Exchange PUFs (Plan Attributes, Benefits & Cost
Sharing, Service Area for plan year 2026) — with a guide in [exchange_puf_data.md](exchange_puf_data.md).
Chose LanceDB for Phase 1-b and wrote the reasoning down in [lancedb.md](lancedb.md) before any
code exists. Promoted `/wrap-up` from a command to a skill so it can trigger on intent, not only
on the slash form. Closed out two long-standing drifts in `plan.md`: it now names the MCD
Coverage API as the NCD route (superseding the bulk ZIPs it had described since the original
survey) and LanceDB as the settled Phase 1-b store.

**Decided:** `processed/` no longer means one thing. The three text corpora emit an app-ready
`corpus.jsonl`; `exchange_puf` emits a **lossless mirror** — every column `VARCHAR`, values
byte-for-byte, no trimming, no type coercion. [data/README.md](../data/README.md) now names both
kinds rather than letting this source quietly stretch the old definition. The reason is that
every "numeric" column here is publisher-formatted text (`'$450 '`, `'70.88%'`, `'Not
Applicable'` beside an empty field, which mean *different* things), and that Plan Attributes
carries **36 max-out-of-pocket columns** — choosing which one is "the" MOOP needs real query
requirements from Phase 3/5, and guessing once at ingestion is worse than not guessing. DuckDB's
CSV reader would have collapsed the empty-vs-`Not Applicable` distinction by mapping empty
fields to `NULL`, so `nullstr` is overridden to a string that cannot occur in a CMS PUF.

Service Area is fetched even though `plan.md` names only two PUFs: without the ZIP-to-area
mapping, `ServiceAreaId` cannot answer the plan's own canonical structured-lookup example
("plans in ZIP 30076"), and the table is 44 KB.

Committed a one-state (AK) full-column sample instead of the real files — Benefits & Cost
Sharing alone is a 375 MB CSV — extending the manifest-not-blob bargain `medicare_pubs` already
made. The sample is **regenerated and re-scanned on every `normalize()`**, never hand-curated,
so a future plan year that introduces code-bearing text into Alaska's data fails the run rather
than resting in a stale fixture nobody re-checks. The full PUFs do contain CPT/CDT numbers in
issuer-authored free text (249 lines) — narrative reference, not a redistributed code table, and
git-ignored regardless; the only question that mattered was the sample, which is clean.

For Phase 1-b: **LanceDB**, on two grounds that outrank raw scale at a few thousand chunks —
it holds vector *and* BM25 search in one table, so the 1-a lexical baseline and the 1-b vector
run share a store instead of the comparison straddling two systems; and its versioned writes let
an eval score be pinned to the corpus/embedding snapshot it was measured against. Embeddings are
computed by us and handed over as plain vectors (LanceDB's registry never calls OpenAI on our
behalf), which keeps bulk ingestion eligible for the Batch API discount.

**Rejected:** Chroma — comparable setup cost, but in-memory-first and weaker on the
eval-reproducibility angle. pgvector — the better long-term fit *if* Phase 5 puts the PUFs in
Postgres rather than DuckDB, but not worth operating a server in Phase 1-b. Qdrant/Weaviate/
Pinecone — a server before we need one, or a managed service that reopens the BAA question,
which is the same reason OpenRouter was ruled out earlier: health-domain data should not leave
the machine. Also not fetched: the Rate PUF and five other tables (Phase 5 territory; the URL
pattern is uniform, so adding one is a dict entry).

**Commits:** `a0b6147`, `813123c`, `5afcbdd` (the `plan.md` and `glossary.md` corrections above
are uncommitted)

### 2026-07-31 — sensitive-data scanner

**Did:** replaced the ad-hoc, per-source licensing check with one scanner
(`scripts/scan_sensitive.py` + a `scan-sensitive` skill) covering all three halves of the
public-repo guardrail — credentials, PII/PHI, licence-restricted content — across every file
that is or would become public. Runs clean today; baselines recorded.

**Decided:** three severities, not two. The NCD session had already established that a check
which fails on legitimate narrative `CPT` mentions "would just get switched off"; that lesson is
now the scanner's architecture rather than one function's local behaviour. **Blocking** exits 1
with no judgement call available, **advisory** compares against a recorded count so a *jump* is
reported rather than a nonzero, **allowlisted** suppresses but still names and counts the
suppression. The advisory tier is not a softer blocking tier — it exists because this corpus
genuinely contains code mentions and agency contact details, and because Phase 3's NPPES data
will genuinely contain real provider names and NPIs that are FOIA-disclosable.

The scan is deliberately **not** part of `make check-all`. `check-all` is the fast inner-loop
command; scanning the whole corpus is a pre-publish gate you invoke on purpose, and burying a
slow check inside a fast one is how the fast one stops getting run.

Licensing markers are scoped to `data/raw/**` and `data/processed/**` rather than the whole
repo, because `data/README.md`, `docs/glossary.md`, and the scanner itself all discuss CPT, CDT
and the AMA by name. **Prose about the guardrail must not trip the guardrail** — the risk being
detected is a code table inside ingested data, not the word.

Baselines are whole-repo totals, so `--staged` / `--unstaged` / `--paths` runs list advisory
hits without comparing them and cannot exit 2. Comparing a whole-repo baseline against a subset
would report every marker as "below baseline" and advise lowering it, which is actively wrong.

Allowlist entries are principled classes, not enumerations, where a principle exists: `*@*.gov`
is a government contact point by definition, and a toll-free area code cannot be a personal
number — that one suppresses 1,429 helpline hits so the reviewable remainder is 39 numbers
rather than a haystack. The four non-government domains are listed individually on purpose, so
a *new* third-party domain surfaces as drift instead of being absorbed by a wildcard.

**Rejected:** a date-of-birth detector. In a corpus built on effective dates, transmittal dates
and revision histories it is pure noise, and would teach everyone to ignore the PII layer
wholesale. A DOB here would arrive attached to a name, which the e-mail/phone/NPI markers
already catch. Also rejected importing the licensing markers from `download_medicare_ncd.py`:
`scripts/` is not a package and that module pulls in `requests`/`bs4`, so they are duplicated
with a "change one, change the other" note — a dependency-free scanner was worth the copy.

**Dead end:** two regex shapes that had to be tightened rather than allowlisted, both now
pinned as anti-canaries. A bare `sk-[A-Za-z0-9_-]{20,}` matched inside the ordinary URL slug
`ask-about-preventive-services`; fixed by anchoring to real vendor prefixes plus a lookbehind.
`pii:npi` as "any Luhn-valid 10-digit run" reported a UUID fragment, a vendor PDF filename and
an IRS publink anchor — Luhn alone rejects only ~90% of arbitrary digit runs — so the
identifier must now also be *labelled* NPI. The general rule this establishes: allowlisting a
regex bug hides the next true positive that shares its shape.

The scanner then caught a live one during this session's own glossary edit: an illustrative
SSN written out in the `SSN` entry blocked the scan. Fixed by writing the *pattern* instead of
a specimen — the standing convention for documenting any blocking shape.

**Commits:** `699fdf3`

### 2026-07-31 — Medicare NCD corpus + glossary

**Did:** added the third and last Phase 0 bulk source — Medicare Coverage Database NCDs, 345
determinations — and [glossary.md](glossary.md), with a standing "keep it current" rule in
`CLAUDE.md` and a matching sweep step in `/wrap-up`.

**Decided:** fetch NCDs from the **MCD Coverage API**, not the bulk ZIPs `plan.md` points at.
The API's auth boundary falls exactly on this repo's licensing boundary — National coverage
endpoints answer keyless, LCD and Article endpoints `401` — so "NCDs only" stops being a rule
the script has to police and becomes the set of endpoints that answer at all. The fetcher
therefore never requests a license token, and `fetch_json()` treats `401` as fatal rather than
retryable so a mistake is loud.

The licensing scan **reports** `CPT`/`HCPCS` hits instead of failing on them. `medicare_pubs`
can claim zero occurrences; this corpus cannot — 22 records mention codes narratively in
revision histories. A scan that failed on those would have been switched off within a week, so
it distinguishes blocking markers (`©`, `CDT`, AMA/ADA notices — all zero) from advisory ones.

`effective_date` is `null` for the 75 longstanding NCDs, with CMS's explanatory sentence moved
to `effective_date_note`, rather than storing prose in a date field. `null` means "in force,
date unknown". `revision_history` is kept as a record field but excluded from `text` — it is
retrieval noise that would match date and number queries without answering them.

The glossary was scoped to *domain* vocabulary only, with each entry required to say what the
term means **in this repo** (licensing status, routing lane, schema field, correctness rule).
A bare dictionary expansion is not a useful entry, and the 256 vendored HealthCare.gov consumer
definitions are pointed at, not restated.

**Rejected:** the MCD Downloads-page ZIPs — 403 to non-browser clients, and their single
license click covers the AMA/ADA/AHA terms for Local coverage data sitting beside the National
data, which is precisely the conflation the guardrail exists to prevent. Also skipped the other
license-clean National document types the API serves (NCAs, CALs, MEDCAC materials, Technology
Assessments): they are the *process* behind a decision, and Phase 0 wants the rule.

**Stopped at:** clean. `plan.md` still describes the bulk ZIPs as the NCD route, which this
session superseded — see *Open questions*.

**Commits:** `58ed14a`, `d0b861c`

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
