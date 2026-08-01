# `data/`

Vendored datasets for the Health Coverage Navigator RAG corpus and (later) structured
backends. This directory **is committed to the repo** on purpose — the sources here are
cleared for public distribution (see [Licensing](#licensing)). Only the derived
vector/DB stores built *from* this data (Chroma/LanceDB, DuckDB/SQLite) are git-ignored.

## Layout

Data is organized by **stage** first, then by **source**:

```
data/
├── raw/<source>/         # exactly what was fetched — the fetch cache / provenance layer
└── processed/<source>/   # normalized, app-ready output derived from raw/
```

- **`raw/<source>/`** — the untouched download for a source, plus a `_meta.json`
  provenance record (fetch timestamp, counts, tool). Never hand-edited. Kept so the
  processed layer can be **rebuilt without re-downloading** (e.g. to change cleaning
  or add fields).
- **`processed/<source>/`** — the cleaned artifact the application actually consumes
  (chunk → embed for RAG, or load into a structured DB). Regenerable from `raw/`.

**Rule of thumb:** `raw/` is the source of truth for *what was fetched*; `processed/` is
the source of truth for *what the app reads*. If they disagree, re-run the source's
parse step to rebuild `processed/`.

> **One exception to "raw is committed":** `raw/medicare_pubs/pdf/` is git-ignored —
> ~46 MB of binary PDFs is not worth permanent git history when
> `raw/medicare_pubs/catalog.json` records each file's URL, `sha256`, and `Last-Modified`,
> making the directory exactly reproducible by re-running its fetcher. Every other file in
> `raw/`, including that manifest, is committed. Bulk binary sources added later
> (Exchange PUFs, the 4 GB NPPES file) should follow the same manifest-not-blob pattern.

## Source catalog

| Source | Stage(s) present | Type | Fetched by | Status |
| --- | --- | --- | --- | --- |
| `healthcare_gov` | `raw/`, `processed/` | RAG text corpus | [`scripts/download_healthcare_gov.py`](../scripts/download_healthcare_gov.py) | ✅ active |
| `medicare_pubs` | `raw/`, `processed/` | RAG text corpus | [`scripts/download_medicare_pubs.py`](../scripts/download_medicare_pubs.py) | ✅ active |
| `medicare_ncd` | `raw/`, `processed/` | RAG text corpus | [`scripts/download_medicare_ncd.py`](../scripts/download_medicare_ncd.py) | ✅ active |
| _(future)_ | | | | planned |

Together these cover the reference lane from both directions. `healthcare_gov` is the
ACA/Marketplace side and `medicare_pubs` the Medicare side — both *consumer explanations* of
coverage. `medicare_ncd` is the other kind of source entirely: the **operative coverage
rules** themselves, with effective dates and statutory benefit categories.

Planned future sources (see [`docs/plan.md`](../docs/plan.md)): Exchange PUFs
(Benefits/Cost-Sharing, Plan Attributes), Medicare Part D formulary files, NPPES provider
registry, openFDA drug labels.

---

## `healthcare_gov`

**Source:** the HealthCare.gov Content API (`https://www.healthcare.gov`), the same
consumer-education content that renders on the site, published as JSON explicitly for
third-party reuse. Keyless and CORS-enabled. See
[`docs/health_care_data.md`](../docs/health_care_data.md) for the full data guide.

**How it was fetched:** `uv run python scripts/download_healthcare_gov.py`. Discovery is
driven from the per-type collection endpoints (articles, glossary, states); each post's
individual `.json` object is then fetched for its richest metadata. The download is
idempotent/resumable and re-parseable via `--normalize-only`.

### Files

```
raw/healthcare_gov/
├── collections/<type>.json   # per-type listings used for discovery (articles, glossary, states, …)
├── posts/<slug>.json         # one raw content object per post (full HTML + all metadata)
└── _meta.json                # fetch provenance: timestamp, discovered/fetched/failed counts, tool

processed/healthcare_gov/
└── corpus.jsonl              # one normalized record per line (JSON Lines) — the RAG input
```

Posts whose body is empty (non-article/empty pages) are dropped during normalization, so
`corpus.jsonl` has slightly fewer rows than `posts/` has files.

### `corpus.jsonl` record schema

One JSON object per line, produced by `normalize_record` in the download script:

| Field | Meaning |
| --- | --- |
| `id` | Filesystem-safe slug derived from the post URL (unique per post). |
| `source` | Origin tag — `"healthcare_gov"`. |
| `url` | Post path on healthcare.gov (e.g. `/retirees`). |
| `title` | Page title. |
| `lang` | Content language (`en`, `es`, …). |
| `date` | Publish date if present (often empty for evergreen articles). |
| `categories`, `tags`, `topics` | Taxonomy arrays (often empty on articles). |
| `bite` | One-sentence editorial summary written by HealthCare.gov. |
| `text` | Full article body, HTML stripped to clean newline-separated text — the RAG payload. |

The raw `posts/<slug>.json` objects carry additional fields the normalizer currently
drops (e.g. `page_audience`, `page_lifecycle`, `state`, SEO metadata). To surface any of
them, extend `normalize_record` and re-run with `--normalize-only`.

---

## `medicare_pubs`

**Source:** the medicare.gov publications catalog (`https://www.medicare.gov/publications`)
— 83 English consumer guides, ~46 MB of PDF, **964 pages of text**. The anchor is the
annual **Medicare & You** handbook (product `10050`); the rest are the topic guides around
it (Your Medicare Benefits, Medicare Appeals, Choosing a Medigap Policy, the Part D drug
guide, hospice / SNF / home-health coverage, …). All U.S.-government public-domain works.
See [`docs/medicare_pubs_data.md`](../docs/medicare_pubs_data.md) for the full data guide.

**How it was fetched:** `uv run python scripts/download_medicare_pubs.py`. There is no API
— discovery scrapes the paginated search page and takes each card's *standard-print* PDF.
The download is idempotent/resumable and re-parseable via `--normalize-only`.

### Files

```
raw/medicare_pubs/
├── pdf/<filename>.pdf     # the untouched PDF downloads — GIT-IGNORED (see Layout above)
├── catalog.json           # per-file manifest: url, sha256, bytes, Last-Modified, title
└── _meta.json             # fetch provenance: timestamp, counts, tool

processed/medicare_pubs/
└── corpus.jsonl           # one normalized record per PDF page — the RAG input
```

### `corpus.jsonl` record schema

One JSON object per **page** (not per document — a 128-page handbook as a single record
isn't retrievable, and page numbers are the natural citation unit for a PDF). `id`,
`source`, `url`, `title`, `bite`, and `text` deliberately match the `healthcare_gov` names
so a later cross-source chunker can treat both corpora uniformly.

| Field | Meaning |
| --- | --- |
| `id` | `<pub_id>_p<page>` zero-padded, e.g. `10050_p031`. |
| `source` | Origin tag — `"medicare_pubs"`. |
| `pub_id` | CMS product number (e.g. `10050`). |
| `url` | Direct PDF URL. Stable but **unversioned** — always serves the current plan year. |
| `title` | Publication title (e.g. `Medicare & You 2026`). |
| `plan_year` | Parsed plan year, or `null` for evergreen publications. |
| `lang` | Always `en` (the non-English catalog is a separate listing we don't fetch). |
| `category` | CMS topic category (e.g. `Coverage and payment`). |
| `bite` | One-sentence catalog description. |
| `page` / `page_count` | 1-based page number, and total pages in the publication. |
| `section` | Breadcrumb from the PDF bookmark outline, or `null` — best-effort, see below. |
| `text` | The page's text, unwrapped from the PDF's visual line breaks — the RAG payload. |

**The discovery pages are parsed in memory and never saved.** Don't add a "keep the raw
HTML for provenance" step here: medicare.gov's HTML embeds an inline `drupalSettings` blob
containing live GovDelivery **prod and stage API keys**, plus an Akamai bot-sensor script
and Drupal form tokens, none of which may be re-published from a public repo. Saving them
would also gain nothing — unlike `healthcare_gov`'s collection endpoints, these pages carry
no publication text, and `catalog.json` already records every field the parser extracts.

Two more things to know before trusting this data — both documented at length in
[`docs/medicare_pubs_data.md`](../docs/medicare_pubs_data.md):

- **`plan_year` matters.** The URLs are unversioned, so the year comes from content, not
  the URL. Where it's set, a mismatched year makes an answer wrong, not merely stale.
- **`section` is best-effort.** Some CMS PDFs ship a real bookmark outline (Medicare & You)
  and others ship their accessibility structure tree as bookmarks (thousands of duplicated
  paragraph fragments). The parser gates on that and emits `null` rather than garbage, so
  only 5 of 83 publications currently carry sections. Page-level citation always works.

Pages that extract to nothing (covers, full-bleed artwork) are dropped, so a publication
has slightly fewer records than `page_count`; page numbers stay absolute.

---

## `medicare_ncd`

**Source:** the **Medicare Coverage Database**'s National Coverage Determinations, via the MCD
**Coverage API** (`https://api.coverage.cms.gov/v1/`) — **345 NCDs**, ~950 KB of policy text
across 29 chapters. An NCD is CMS's nationwide decision on whether Medicare covers an item or
service; it binds every MAC. Where the other two corpora *explain* coverage, this one *is* the
rule. See [`docs/medicare_ncd_data.md`](../docs/medicare_ncd_data.md) for the full data guide.

**How it was fetched:** `uv run python scripts/download_medicare_ncd.py`. Discovery reads the
National Coverage NCD report, then fetches each NCD's detail record. Keyless — CMS dropped the
API-key requirement in February 2024. Idempotent/resumable and re-parseable via
`--normalize-only`.

**The bulk ZIPs on the MCD Downloads page are deliberately not used.** That page 403s
non-browser clients, and its license-acceptance click covers the AMA/ADA/AHA terms for Local
coverage data sitting in the same place as the National data. The API avoids that entirely —
and enforces the split for us (see [Licensing](#licensing)).

### Files

```
raw/medicare_ncd/
├── report.json              # the National Coverage NCD report — the discovery index
├── ncd/<section>_v<n>.json  # one untouched API response per NCD, e.g. 30.3_v2.json
└── _meta.json               # fetch provenance: timestamp, counts, tool, license note

processed/medicare_ncd/
└── corpus.jsonl             # one normalized record per NCD — the RAG input
```

Raw files are named by **section number** (`30.3`), not the API's internal document ID,
because the section number is what a citation names. Nothing here is git-ignored — 2.6 MB raw
plus 1.8 MB processed, so the manifest-not-blob tradeoff `medicare_pubs` makes doesn't apply.

### `corpus.jsonl` record schema

One JSON object per **NCD** (they are short — median 1,540 characters). `id`, `source`, `url`,
`title`, `bite`, and `text` match the other two corpora's names so a cross-source chunker can
treat all three uniformly.

| Field | Meaning |
| --- | --- |
| `id` | `ncd_<section>_v<version>`, e.g. `ncd_30.3_v2`. |
| `source` | Origin tag — `"medicare_ncd"`. |
| `url` | The human-facing MCD page. **Not** the API path the report returns in its own `url` field. |
| `title` | NCD title (e.g. `Acupuncture`). |
| `bite` | **Always empty** — NCDs carry no editorial summary. Kept so every corpus has one shape. |
| `section_number` / `chapter` | The citable NCD number (`30.3`) and its chapter (`30`). |
| `ncd_id` / `ncd_version` | The API's identity for the document — what you need to re-fetch it. |
| `publication_number` | `100-3`, the Medicare NCD Manual. |
| `benefit_category` | The statutory benefit category the item falls under. |
| `effective_date` | `MM/DD/YYYY`, or `null` for a longstanding NCD (see below). |
| `effective_date_note` | CMS's explanation, non-empty only when `effective_date` is `null`. |
| `effective_end_date` / `implementation_date` / `last_updated` | Dates; `null` when absent. |
| `is_lab` | Whether the determination covers a laboratory test (23 of 345). |
| `transmittal_number` / `transmittal_url` | The CMS change instruction that implemented this version. |
| `revision_history` | Change history, HTML-stripped — **deliberately not part of `text`**. |
| `text` | The policy sections under `## ` headings — the RAG payload. |

Two things to know before trusting this data — both covered at length in
[`docs/medicare_ncd_data.md`](../docs/medicare_ncd_data.md):

- **`effective_date` is `null` for 75 of the 345.** They are longstanding determinations
  predating CMS's posting practice, and the API returns a *sentence* where the date belongs.
  That prose goes into `effective_date_note` rather than polluting a date field. Any date
  filter must handle `null` — which here means "in force, date unknown", not "not in force".
  NCDs are scoped by effective date, **not** by plan year; there is no `plan_year` field.
- **`revision_history` is excluded from `text` on purpose.** It is retrieval noise
  (transmittal numbers, rescinded-and-replaced chains). It stays as its own field.

---

## Adding a new data source

To keep this directory legible as it grows, follow the same convention for every source:

1. **Pick a short, stable source key** (lowercase, underscores), e.g. `medicare_ncd`.
2. **Write the fetcher** to `data/raw/<key>/`, including a `_meta.json` provenance record
   (at minimum: `fetched_at`, source URL, counts, tool). Keep the raw download untouched.
3. **Write the processed artifact** to `data/processed/<key>/` — `corpus.jsonl` for a RAG
   text corpus, or a DB/columnar file for structured data — and make it regenerable from
   `raw/` (a `--normalize-only`-style path).
4. **Register it** in the [Source catalog](#source-catalog) table above and add a
   per-source section documenting: source URL, how it was fetched, the files produced,
   and (for a corpus) the record schema.
5. **Clear it for the public repo first** — see the guardrail below.

## Licensing

`healthcare_gov` content is **freely reusable** (U.S.-government work, published for
third-party reuse) — safe to vendor and index.

`medicare_pubs` are **U.S.-government works in the public domain**. They're consumer
booklets rather than coverage policy, so they carry no AMA/ADA CPT/CDT code tables —
verified, not assumed: the corpus has zero occurrences of `CPT`, `CDT`, `HCPCS`, `©`, or
"all rights reserved".

`medicare_ncd` is the repo's **one partly-clean source**, and the reason the guardrail is
written the way it is. The MCD holds NCDs (cleared) directly alongside LCDs and Billing/Coding
Articles (blocked — they embed AMA CPT and ADA CDT code tables). We vendor only the cleared
subset, and **the API enforces that boundary for us**: every `/data/ncd/` and
`/reports/national-coverage-*` endpoint answers keyless, while every `/data/lcd/` and
`/data/article/` endpoint returns `401` demanding an AMA/ADA/AHA license token. The fetcher
never requests such a token — not holding one is its second line of defence.

That clearance is re-verified on every run rather than assumed: the corpus has zero
occurrences of `©`, "all rights reserved", `CDT`, or an AMA/ADA copyright notice, and the
API's `ama_statement` field is empty in all 345 NCDs. **Unlike `medicare_pubs`, it does not
have zero occurrences of `CPT`/`HCPCS`** — 22 records contain narrative mentions inside
revision histories ("Corrected CPT and ICD-9-CM codes"), along with a few individual HCPCS
Level II identifiers. Those are prose references in a government document, not a redistributed
code table, so the scan reports them for review instead of failing on them. Don't "fix" that
to zero; read [`docs/medicare_ncd_data.md`](../docs/medicare_ncd_data.md#licensing) first.

⚠️ **Before adding any new source, confirm it is cleared for public distribution.** This
repo is public. Notably, **do not** vendor Medicare Coverage Database **LCDs or
Billing/Coding Articles** (they embed AMA/ADA-copyrighted CPT/CDT codes) — index **NCDs
only** — and never commit PII/PHI, secrets, or API keys. See the
**"Public-repo data guardrail"** section in [`CLAUDE.md`](../CLAUDE.md) for the full
checklist.
