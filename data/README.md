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
- **`processed/<source>/`** — the derived artifact built from `raw/`, regenerable at will.
  It comes in two kinds, and which one a source produces is a property of the source, not
  a formatting choice:
  - **app-ready** — the cleaned form the application reads directly (chunk → embed for
    RAG). `healthcare_gov`, `medicare_pubs`, and `medicare_ncd` all emit this, as
    `corpus.jsonl`.
  - **lossless mirror** — a faithful, queryable re-encoding that changes *format* but not
    *content*; still requires a modeling/typing layer above it before an application can
    read a value out of it. `exchange_puf` is the one source of this kind today: every
    column lands as `VARCHAR`, publisher-formatted text unchanged (`'$450 '`, `'Not
    Applicable'`, `'70.88%'`), because picking types — and which of a 36-column MOOP grid
    is "the" answer — is a Phase 5 modeling decision, not an ingestion one. See
    [`docs/exchange_puf_data.md`](../docs/exchange_puf_data.md#why-the-processed-layer-is-a-mirror-not-a-model).

**Rule of thumb:** `raw/` is the source of truth for *what was fetched*; `processed/` is
the source of truth for *what the app reads* — except for a lossless-mirror source, where
`processed/` is the source of truth for *what a later typed layer reads*. If `raw/` and
`processed/` disagree, re-run the source's parse step to rebuild `processed/`.

> **One exception to "raw is committed":** `raw/medicare_pubs/pdf/` is git-ignored —
> ~46 MB of binary PDFs is not worth permanent git history when
> `raw/medicare_pubs/catalog.json` records each file's URL, `sha256`, and `Last-Modified`,
> making the directory exactly reproducible by re-running its fetcher. Every other file in
> `raw/`, including that manifest, is committed. `exchange_puf` is the second instance of
> this pattern and `part_d_spuf` the third (both below); the 4 GB NPPES file will likely be
> a fourth. By `part_d_spuf` the bargain has teeth: its manifest records each member's byte
> offset and CRC32 inside the published container, so "reproducible" is checkable rather
> than asserted.

## Source catalog

| Source | Stage(s) present | Type | Fetched by | Status |
| --- | --- | --- | --- | --- |
| `healthcare_gov` | `raw/`, `processed/` | RAG text corpus | [`scripts/download_healthcare_gov.py`](../scripts/download_healthcare_gov.py) | ✅ active |
| `medicare_pubs` | `raw/`, `processed/` | RAG text corpus | [`scripts/download_medicare_pubs.py`](../scripts/download_medicare_pubs.py) | ✅ active |
| `medicare_ncd` | `raw/`, `processed/` | RAG text corpus | [`scripts/download_medicare_ncd.py`](../scripts/download_medicare_ncd.py) | ✅ active |
| `exchange_puf` | `raw/`, `processed/` | Structured plan data (lossless mirror) | [`scripts/download_exchange_puf.py`](../scripts/download_exchange_puf.py) | ✅ active |
| `part_d_spuf` | `raw/`, `processed/` | Structured plan data (lossless mirror) | [`scripts/download_part_d_spuf.py`](../scripts/download_part_d_spuf.py) | ✅ active |
| _(future)_ | | | | planned |

`healthcare_gov`, `medicare_pubs`, and `medicare_ncd` cover the reference lane from both
directions — `healthcare_gov` the ACA/Marketplace side and `medicare_pubs` the Medicare side,
both *consumer explanations* of coverage; `medicare_ncd` is the **operative coverage rules**
themselves, with effective dates and statutory benefit categories. `exchange_puf` is a
different kind of source altogether: not prose to retrieve but per-plan facts to query — the
backing data for Phase 3's plan/drug lookups and Phase 5's plan comparison.

Planned future sources (see [`docs/plan.md`](../docs/plan.md)): Medicare Part D formulary
files, NPPES provider registry, openFDA drug labels.

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

## `exchange_puf`

**Source:** CMS CCIIO's **Health Insurance Exchange Public Use Files** —
`https://download.cms.gov/marketplace-puf/<year>/<table>-puf.zip` — the bulk plan-level data
behind the ACA Marketplace, covering every plan sold on the federally-facilitated Exchange.
See [`docs/exchange_puf_data.md`](../docs/exchange_puf_data.md) for the full data guide.

**How it was fetched:** `uv run python scripts/download_exchange_puf.py`. No discovery step —
the download URLs are a fixed pattern per (year, table). Idempotent via real conditional GETs
(`If-None-Match`/`If-Modified-Since` against `download.cms.gov`'s ETag/Last-Modified, which
answers **304** when nothing changed) and re-parseable via `--normalize-only`.

**Three tables, not all nine CMS publishes:** Plan Attributes, Benefits & Cost Sharing, and
Service Area — the last included specifically because Plan Attributes' `ServiceAreaId` has no
meaning without it (it's what maps a ZIP/county to available plans). Rate, Network, Business
Rules, Plan ID Crosswalk, Machine Readable URL, and Transparency in Coverage PUFs are not
fetched; see the data guide for how to add one.

### Files

```
raw/exchange_puf/
├── catalog.json                    # per-(year, table) manifest: url, etag, row/column counts
├── _meta.json                      # fetch provenance
└── <year>/<slug>-puf.zip, *.csv    # GIT-IGNORED — see Layout above

processed/exchange_puf/
├── <year>/<table>.parquet          # GIT-IGNORED — the lossless mirror, see Layout above
└── sample/<table>_<state>_<year>.csv   # one state's full-column slice, regenerated + re-scanned
                                         #   for licensing markers on every normalize() run
```

This is the source `raw/`-is-committed makes its second exception for (after
`medicare_pubs/pdf/`): plan year 2026 alone is a 375 MB CSV (Benefits & Cost Sharing) plus a
32 MB CSV (Plan Attributes). `catalog.json` records enough (`url`, `etag`, `last_modified`,
`row_count`, `column_count`, ...) to reproduce the git-ignored files exactly by re-running the
downloader.

### Why `processed/` is a mirror here, not `corpus.jsonl`

This is a structured-data source, not RAG text — there is no `corpus.jsonl`. And unlike the
three text corpora, its Parquet output is not yet "the cleaned artifact the application
consumes": every column (deductibles, MOOP, actuarial value, yes/no flags) is publisher
-formatted text (`'$450 '` with a trailing space, `'Not Applicable'`, `'70.88%'`), written to
Parquet as `VARCHAR` with nothing trimmed or coerced. Typing it — and choosing which of the
36 MOOP columns is "the" answer for a plan — needs real query requirements and is deferred to
Phase 5. See [Layout](#layout) above and
[`docs/exchange_puf_data.md`](../docs/exchange_puf_data.md#why-the-processed-layer-is-a-mirror-not-a-model)
for the full reasoning.

---

## `part_d_spuf`

**Source:** CMS's **Quarterly Prescription Drug Plan Formulary, Pharmacy Network, and Pricing
Information Public Use File** — the Medicare Part D counterpart to `exchange_puf`, rebuilt each
quarter from the Medicare Plan Finder. It answers which drug (by NDC) a Part D plan covers, on
what cost-share tier, and with what prior-authorization / step-therapy / quantity-limit strings
attached. See [`docs/part_d_spuf_data.md`](../docs/part_d_spuf_data.md) for the full data guide.

**How it was fetched:** `uv run python scripts/download_part_d_spuf.py`. The download URL
embeds a rotating UUID and so **is** discovered — from CMS's DCAT catalog at
`https://data.cms.gov/data.json`, which is also where the licensing metadata comes from.
Idempotent via conditional GET (`If-Modified-Since`; this host sends `Last-Modified` but no
ETag) and re-parseable via `--normalize-only`.

**Seven files, not all ten CMS publishes** — and, unusually, **without downloading the
container.** The published quarterly zip is **2.49 GB**, but it is a container of 15 nested
per-file zips, and the six-part pharmacy-network file is 92% of that weight. The downloader
reads the zip's central directory over HTTP and range-fetches only the members it wants,
transferring **9.4 MB instead of 2.49 GB**, with every member verified against its recorded
CRC32 before it is written. Pharmacy Network is not wired up (Phase 5 territory); Pricing
(191 MB) is defined but opt-in via `--file pricing`.

### Files

```
raw/part_d_spuf/
├── catalog.json                    # per-(quarter, file) manifest: url, byte offset/length,
│                                   #   crc32, sha256, row/column counts
├── _meta.json                      # fetch provenance + the resolved sample anchor contracts
└── <quarter>/<stem>.txt            # GIT-IGNORED — pipe-delimited, Latin-1, ~86 MB/quarter

processed/part_d_spuf/
├── <quarter>/<stem>.parquet        # GIT-IGNORED — the lossless mirror, see Layout above
└── sample/<stem>_<quarter>.csv     # a few contracts' full-column slice, regenerated +
                                    #   re-scanned for licensing markers on every normalize()
```

Third exception to `raw/`-is-committed, on the same manifest-not-blob terms as
`medicare_pubs/pdf/` and `exchange_puf`: one quarter is ~86 MB of text, 58 MB of it the
formulary file's 1.12M rows. Here `catalog.json` additionally records each member's **byte
offset and CRC32 inside the container**, which makes the reproducibility claim checkable rather
than merely asserted.

### Three things to know before trusting this data

- **`STATE` is populated only for Medicare Advantage rows.** Every standalone PDP row leaves it
  (and `COUNTY_CODE`) blank and is located by `PDP_REGION_CODE` instead. Filtering this source
  by state silently drops every standalone drug plan — and Alaska, `exchange_puf`'s default
  sample state, has **zero** rows here. This is why the committed sample is anchored on
  `CONTRACT_ID`, taking one PDP and one MA contract so both plan shapes appear.
- **The files are Latin-1, not UTF-8**, and CMS's record layout never says so. Only three
  Spanish plan names in Plan Information exercise it, but that is enough to make a strict UTF-8
  read raise. The other six files are pure ASCII.
- **Suppressed plans appear in Plan Information only**, flagged `PLAN_SUPPRESSED_YN = "Y"`, so
  a formulary join legitimately finds nothing for them.

### Why `processed/` is a mirror here, not `corpus.jsonl`

Structured data, same as `exchange_puf` — no `corpus.jsonl`, not chunked, not embedded. Every
column is publisher-formatted text (`PREMIUM` is `'35.60'`, `MA_REGION_CODE` is a single space
for every PDP row, and the insulin copay columns use blank and `'0.00'` to mean different
things), written to Parquet as `VARCHAR` with nothing trimmed or coerced. The one
transformation applied is a character-set transcode from Latin-1 to UTF-8, which changes bytes
but not characters. See
[`docs/part_d_spuf_data.md`](../docs/part_d_spuf_data.md#why-the-processed-layer-is-a-mirror-not-a-model).

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

`exchange_puf` is **public domain** (CMS Exchange PUF Disclaimer-User Agreement; no
redistribution restriction — see [`docs/exchange_puf_data.md`](../docs/exchange_puf_data.md#licensing)
for the full text). The agreement's one real obligation is that altered/derived data not be
presented as CMS data, which is why the Parquet mirror is documented throughout as *derived*,
never as the CMS file itself. The full PUFs (never committed) contain issuer-authored free
text that names CPT/CDT codes narratively — an issuer describing their own plan's exclusions,
not a redistributed code table — but that question is moot for what's actually vendored: the
committed sample slice is re-scanned on every `normalize()` run and has zero blocking hits.

`part_d_spuf` is **public domain** and the cleanest of the five on this axis. CMS's own DCAT
catalog records `license: https://www.usa.gov/government-works`, `accessLevel: public`, and no
rights statement; there is no key, no registration, and no click-through agreement. It clears
the guardrail for two independent reasons: drugs are identified only by **NDC** and **RxCUI**,
so no AMA/ADA code table is anywhere in the container (this is drug coverage, not procedure
coding — the licensing line `medicare_ncd` has to navigate is never approached); and every row
is plan- or product-level, with no beneficiary data. The one identifier-shaped column in the
whole container, `PHARMACY_NUMBER`, belongs to the pharmacy-network file, which is not fetched.

One scanner interaction is worth knowing about, because it looks alarming and isn't: a Medicare
`CONTRACT_ID` is a letter plus four digits (`H1671`, `S5743`) — **exactly** the shape of an
HCPCS Level II code, and `H`/`R`/`S` are all real HCPCS letters. The `licensing:hcpcs-shaped`
detector therefore fires on all 1,099 of them in the sample. That is not a regex bug and the
pattern is deliberately left alone (tightening it globally would hide real `G0465`-style tokens
in the other corpora); it is handled by a narrow, path-scoped allowlist entry in
`scripts/sensitive_baseline.toml`. Any *other* letter appearing in these files still fires.

⚠️ **Before adding any new source, confirm it is cleared for public distribution.** This
repo is public. Notably, **do not** vendor Medicare Coverage Database **LCDs or
Billing/Coding Articles** (they embed AMA/ADA-copyrighted CPT/CDT codes) — index **NCDs
only** — and never commit PII/PHI, secrets, or API keys. See the
**"Public-repo data guardrail"** section in [`CLAUDE.md`](../CLAUDE.md) for the full
checklist.

Run **`make scan`** before committing anything here.
[`scripts/scan_sensitive.py`](../scripts/scan_sensitive.py) checks all three halves of that
guardrail — credentials, PII/PHI, and licence-restricted content — across every file that is
or would become public, not just the source being added. It exits non-zero on a blocking hit,
and compares the expected-but-benign findings (narrative `CPT` mentions, published agency
phone numbers) against a recorded baseline in `scripts/sensitive_baseline.toml`, so a *jump*
is reported rather than a nonzero. Note that its licensing markers cover
`data/raw/**` and `data/processed/**` only: prose *about* the guardrail, including this file,
must not trip it.
