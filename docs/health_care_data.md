# HealthCare.gov data

This document covers the **HealthCare.gov Content API** — the consumer-education
corpus that seeds Phase 0 (RAG). It explains what the API exposes, how to download it,
and how the download lands on disk. Other bulk sources in the roadmap (Medicare & You,
NCDs, the Exchange PUFs) are documented separately when we get to them.

> **This is not the Marketplace API.** The Content API (`www.healthcare.gov`) is open,
> keyless, and read-only educational text. The *Marketplace API*
> (`marketplace.api.healthcare.gov`) — plan search, drug-coverage, cost estimates — is a
> different service that **requires a rate-limited API key** and belongs to Phase 3.
> Nothing in this doc needs a key.

---

## What data is available

HealthCare.gov publishes all of its consumer-education content as machine-readable JSON,
explicitly for third-party reuse. The same content that renders as HTML pages on the site
is available as JSON by appending `.json` to any post URL. It is written at exactly the
"explain coverage to a human" level — clean glossary definitions, how-to articles, and
Q&A — which makes it the cleanest starting corpus for RAG.

The API has **three layers**, all served over plain `HTTP GET` with no authentication:

| Layer | Endpoint | Returns |
|---|---|---|
| **Index** | `https://www.healthcare.gov/api/index.json` | Abridged metadata records — **glossary terms only** (see the caveat below). |
| **Collections** | `https://www.healthcare.gov/api/{type}.json` | All full post objects for one content type. |
| **Objects** | `https://www.healthcare.gov/<post-path>.json` | The full body + richest metadata for a single post. |

> ### ⚠️ The index is **not** a site-wide list — it's glossary-only
> The developer docs describe `/api/index.json` as "a site-wide index of all posts," but
> empirically it contains **only the ~257 glossary terms**. Driving a download from it
> silently drops every article and state page. **Enumerate from the collection endpoints
> instead.** Verified counts (as of this writing):
>
> | Collection | Posts | In the index? |
> |---|---|---|
> | `articles` | ~436 | ❌ none |
> | `glossary` | ~256 | ✅ yes (this *is* the index) |
> | `states` | ~112 | ❌ none |
> | `questions` | — | ⚠️ endpoint returns **404** |
> | `blog`, `topics` | 0 | empty |
>
> So the real corpus is **~800 posts** from `articles` + `glossary` + `states`. The
> download script enumerates all six types and skips the empty/404 ones gracefully.

Collection endpoints already embed each post's **full `content`**, but the *individual*
object endpoint (append `.json` to a post URL) carries **richer metadata** — `bite`,
`excerpt`, `topics`, `state`, `meta-description`, etc. The script uses collections to
discover posts and then fetches each object individually to capture that metadata.

### Fields

An **index** record (glossary-only summary metadata):

| Field | Meaning |
|---|---|
| `url` | Path to the HTML post (append `.json` to get the object) |
| `title` / `es-title` | Post title (English / Spanish) |
| `bite` / `es-bite` | Short one-line summary (English / Spanish) |
| `categories` | Content types + language code |
| `tags` | Content tags (e.g. `promote`) |
| `topics` | Associated topics (articles) |
| `state` | Associated states |

A content **object** (the full post, what we actually index):

| Field | Meaning |
|---|---|
| `url` | The post's URL |
| `title` | Post title |
| `content` | The body — **HTML**, stripped to plain text at ingest |
| `author`, `date` | Provenance |
| `lang` | `en` (English) or `es` (Spanish) |
| `categories`, `tags`, `topics` | Same taxonomy as the index |
| `layout`, `order` | Display hints (not used for RAG) |

---

## How to download

### 1. Do you need an API key?

**No.** The Content API requires no key, no signup, and no auth header. It is CORS-enabled
and public. Be polite instead: send a descriptive `User-Agent`, space out requests, and
back off on `429`/`5xx`. The download script does all of this.

### 2. Install dependencies

`requests` and `beautifulsoup4` are already declared in `pyproject.toml`, so a normal sync
is all you need:

```bash
uv sync
```

### 3. Run the downloader

```bash
# Smoke test first — fetch just the first 20 posts and normalize them
uv run python scripts/download_healthcare_gov.py --limit 20

# Full download into ./data (idempotent; re-run to retry any failures)
uv run python scripts/download_healthcare_gov.py

# Keep only English text in the normalized corpus
uv run python scripts/download_healthcare_gov.py --lang en
```

The script is **collection-driven**: it enumerates posts from the collection endpoints,
fetches each post's object, then builds a normalized corpus from what it downloaded. Key
flags:

| Flag | Purpose |
|---|---|
| `--out DIR` | Output root (default `./data`) |
| `--limit N` | Only fetch the first N discovered posts (smoke test) |
| `--lang en,es` | Comma-separated langs to keep in the corpus, or `all` |
| `--refresh` | Re-fetch posts already on disk |
| `--normalize-only` | Skip download; rebuild the corpus from the raw layer |
| `--delay`, `--retries`, `--backoff` | Politeness / robustness tuning |

### 4. Idempotency & resumability

Re-running is safe and cheap:

- Posts already on disk are **skipped** unless `--refresh` is passed.
- Failed posts are **not** written, so a plain re-run retries exactly those.
- Normalization is **decoupled** from download — re-run with `--normalize-only` to
  re-parse (e.g. after changing the HTML cleaning) without re-fetching anything.

---

## Where the data lands

```
data/
├── raw/healthcare_gov/
│   ├── collections/<type>.json  # raw per-type collection listings (articles, glossary, ...)
│   ├── posts/<slug>.json        # one raw content object per post
│   ├── _meta.json               # fetch provenance (timestamp, counts)
│   └── failures.json            # any posts that failed (only if there were failures)
└── processed/healthcare_gov/
    └── corpus.jsonl             # normalized {id, url, title, text, ...} — the RAG input
```

The `raw/` layer is the untouched download (kept so parsing can change without
re-fetching). `corpus.jsonl` is the normalized, HTML-stripped text that the Phase 0
ingestion pipeline chunks and embeds. `data/` is **committed to the repo** — the
HealthCare.gov content is freely reusable (see Licensing below), so the corpus is
vendored rather than git-ignored. Only the vector/DB stores it gets loaded into
(Chroma/LanceDB, DuckDB/SQLite) are ignored.

---

## Licensing

HealthCare.gov content is **freely reusable** — it is published specifically so that
"innovators, entrepreneurs, and partners can turn it into new products and services."
Unlike the Medicare Coverage Database LCDs/Articles (which embed AMA/ADA copyrighted
CPT/CDT codes), there is **no licensing caveat** here: it is safe to vendor and index.

## Reference

- HealthCare.gov for Developers: <https://www.healthcare.gov/developers/>
