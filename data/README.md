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

## Source catalog

| Source | Stage(s) present | Type | Fetched by | Status |
| --- | --- | --- | --- | --- |
| `healthcare_gov` | `raw/`, `processed/` | RAG text corpus | [`scripts/download_healthcare_gov.py`](../scripts/download_healthcare_gov.py) | ✅ active |
| _(future)_ | | | | planned |

Planned future sources (see [`plan.md`](../plan.md) / [`docs/health_care_data.md`](../docs/health_care_data.md)):
Medicare & You handbook, Medicare Coverage Database **NCDs only**, Exchange PUFs
(Benefits/Cost-Sharing, Plan Attributes), Medicare Part D formulary files, NPPES
provider registry, openFDA drug labels.

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

⚠️ **Before adding any new source, confirm it is cleared for public distribution.** This
repo is public. Notably, **do not** vendor Medicare Coverage Database **LCDs or
Billing/Coding Articles** (they embed AMA/ADA-copyrighted CPT/CDT codes) — index **NCDs
only** — and never commit PII/PHI, secrets, or API keys. See the
**"Public-repo data guardrail"** section in [`CLAUDE.md`](../CLAUDE.md) for the full
checklist.
