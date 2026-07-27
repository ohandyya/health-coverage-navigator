# Medicare publications data

This document covers the **medicare.gov publications catalog** — the "Medicare & You
handbook + related CMS guides" source from [`plan.md`](plan.md). It is the Medicare half
of the Phase 0 RAG corpus; [`health_care_data.md`](health_care_data.md) covers the
ACA/Marketplace half (HealthCare.gov). Everything here is a U.S.-government work.

> **There is no API for this source.** Unlike HealthCare.gov, medicare.gov publishes no
> JSON endpoint for its publications — the catalog is a server-rendered HTML search page
> and the publications themselves are PDFs. Discovery therefore scrapes, and ingestion
> parses PDFs. No API key is needed for either.

---

## What data is available

83 English consumer publications, ~46 MB of PDF, **964 pages of text**. The anchor
document is the annual **Medicare & You** handbook (product `10050`, 128 pages), and the
rest are the topic guides that surround it:

| Category | Pages in corpus | Examples |
|---|---|---|
| Coverage and payment | 435 | Your Medicare Benefits, Medicare Coverage of Kidney Dialysis, SNF Care, Home Health, Hospice, Ambulance, DME |
| General information | 205 | **Medicare & You 2026**, Medicare: Getting Started, 2026 Medicare Costs |
| Health care choices | 118 | Choosing a Medigap Policy, Understanding Medicare Advantage Plans, Guide to Choosing a Hospital |
| Medicare prescription drug coverage | 92 | Your Guide to Medicare Prescription Drug Coverage, Part D Late Enrollment Penalty, Extra Help |
| Rights and protections | 77 | Medicare Appeals, Medicare Rights & Protections, Medicare Beneficiary Ombudsman |
| Staying healthy | 37 | Preventive Services, Diabetes Prevention Program |

This is exactly the "what does the rule/benefit say" lane from the plan's routing table —
Parts A/B/C/D mechanics, what's covered, appeals, and enrollment periods.

### Discovery: the paginated search page

```
https://www.medicare.gov/publications/search?keywords=&page=N     # 25 results/page, N = 0,1,2,…
```

Each result is an `<article class="publication-card">` carrying its metadata as data
attributes — `data-pub-num` (the product number), `data-lang-iso`, `data-category` — plus
a title, a one-line description, and a list of available formats. Pagination ends when a
page returns zero cards (currently page 4).

**Take the PDF from the card's `formats-list--primary` block.** That block is the
"Standard Print" edition; the `--secondary` "Get More Formats" block holds the large-print
(`-le-`), ePub, Mobi, audio, and braille alternates for the *same* publication. Selecting
structurally on the primary block is what keeps duplicates out of the corpus — much more
reliable than filtering filenames.

### The search pages are never saved to disk

Discovery parses each search page in memory and writes nothing. **Don't add a "save the
raw HTML for provenance" step** — it looks like the right thing and it isn't, for two
independent reasons:

1. **The pages carry third-party credentials.** medicare.gov's server-rendered HTML
   embeds an inline `drupalSettings` blob with a `medicare_email_signup.api_keys` object
   holding live GovDelivery **production and staging** API keys (English and Spanish),
   plus an Akamai bot-sensor script at a per-session obfuscated path, per-request Drupal
   form tokens, and a Google Search Console verification token. None of that may be
   re-published from a public repo — see the guardrail in [`CLAUDE.md`](../CLAUDE.md).
2. **They'd buy nothing.** Unlike the HealthCare.gov collection endpoints — which embed
   each post's full `content`, and so genuinely back up that corpus — these pages contain
   **no publication text at all**. They're a link index, and `catalog.json` already
   records every field the parser extracts from them, for all 83 publications.

If you need the markup to debug a parser break, re-run discovery: it's five requests.
When test tooling lands, capture one page as a `parse_cards` fixture — sanitized, and
under `tests/`, not `data/`.

> ### ⚠️ Two caveats that affect correctness
>
> **1. Publication URLs are unversioned.** `/publications/10050-medicare-and-you.pdf`
> always serves the *current* plan year — there is no `…-2026.pdf`. The year has to be
> read out of the content, which is what the `plan_year` field does (parsed from the
> catalog title, then the PDF's `/Subject`, `/Keywords`, `/Title`). It resolves for the
> year-stamped publications (`10050` → 2026, `11579` → 2026, `12229` → 2026) and is
> `null` for the evergreen ones. Per the plan's *"pin the plan year"* principle: when a
> retrieved chunk has a `plan_year`, it is load-bearing — a 2025 answer to a 2026
> question is wrong, not merely stale.
>
> **2. PDF bookmark outlines are inconsistent**, so `section` is best-effort. Two
> ~120-page booklets, both from CMS, both InDesign-generated:
>
> | | `10050` Medicare & You | `10116` Your Medicare Benefits |
> |---|---|---|
> | Root bookmark | `Medicare & You 2026` | `Structure Bookmarks` |
> | Entries | 419 (3.3/page) | 6,228 (51.9/page) |
> | Max depth | 3 | 8 |
> | Duplicate titles | 6% | 70% |
> | Usable? | ✅ real sections | ❌ tagged-content dump |
>
> The second is the PDF's accessibility structure tree exported as bookmarks — every
> paragraph and bullet, heavily duplicated. Using it would yield breadcrumbs like
> `• > • > The Waiver of Right to an ALJ Hearing form`. `build_section_map()` gates on
> those four signals and returns `None` when the outline looks synthetic, leaving
> `section: null`. **5 of 83 publications** currently yield usable sections (192 of 964
> records); most of the rest are short flyers with no bookmarks at all. Page-level
> citation always works, so nothing depends on the outline.

---

## How to download

### 1. Do you need an API key?

**No.** But medicare.gov **rejects requests without a real `User-Agent` (403)**, so the
descriptive UA the script sends is load-bearing rather than merely polite. It also spaces
out requests and backs off on `429`/`5xx`.

### 2. Install dependencies

`requests`, `beautifulsoup4`, and `pypdf` are declared in `pyproject.toml`:

```bash
uv sync
```

`pypdf` is pure-Python and BSD-licensed. It was chosen over `pdfplumber` (unnecessary —
these PDFs are single-column and extract cleanly) and `pymupdf` (AGPL, which we don't want
in a public repo).

### 3. Run the downloader

```bash
# Smoke test first — 3 publications end to end
uv run python scripts/download_medicare_pubs.py --limit 3

# Full download into ./data (~46 MB, a few minutes; idempotent, re-run to retry failures)
uv run python scripts/download_medicare_pubs.py

# Rebuild the corpus from the raw PDFs without re-downloading
uv run python scripts/download_medicare_pubs.py --normalize-only
```

| Flag | Purpose |
|---|---|
| `--out DIR` | Output root (default `./data`) |
| `--limit N` | Only fetch the first N discovered publications (smoke test) |
| `--max-pages N` | Search-page ceiling for discovery (default 20) |
| `--refresh` | Re-fetch PDFs already on disk |
| `--normalize-only` | Skip download; rebuild `corpus.jsonl` from the raw layer |
| `--delay`, `--retries`, `--backoff` | Politeness / robustness tuning |

### 4. Idempotency & resumability

- PDFs already on disk are **skipped** unless `--refresh` is passed. Catalog-derived
  metadata (title, category, description) is still refreshed on a skip, so a re-titled or
  re-categorized publication is picked up without re-downloading 46 MB.
- Failed downloads are **not** written, so a plain re-run retries exactly those.
- Normalization is **decoupled** — `--normalize-only` re-parses without re-fetching.

---

## Where the data lands

```
data/
├── raw/medicare_pubs/
│   ├── pdf/<filename>.pdf     # the untouched PDF downloads — GIT-IGNORED, see below
│   ├── catalog.json           # per-file manifest: url, sha256, bytes, Last-Modified
│   ├── _meta.json             # fetch provenance (timestamp, counts)
│   └── failures.json          # any publications that failed (only if there were failures)
└── processed/medicare_pubs/
    └── corpus.jsonl           # one record per page — the RAG input
```

**The raw PDFs are the one part of `data/` that is *not* committed.** ~46 MB of binaries
is not worth permanent git history when it is exactly reproducible: `catalog.json` records
the URL, `sha256`, byte count, and `Last-Modified` of every file, so re-running the script
rebuilds `raw/pdf/` identically. Both manifests and `corpus.jsonl` **are** committed. Those
per-file hashes also double as the change-detector for the Phase 5 "what changed this plan
year" monitor.

### `corpus.jsonl` record schema

One JSON object per page. `id` / `source` / `url` / `title` / `bite` / `text` intentionally
match the `healthcare_gov` field names so a later cross-source chunker can treat both
corpora uniformly.

| Field | Meaning |
|---|---|
| `id` | `<pub_id>_p<page>`, zero-padded (e.g. `10050_p031`). |
| `source` | Origin tag — `"medicare_pubs"`. |
| `pub_id` | CMS product number (e.g. `10050`). |
| `url` | Direct PDF URL — stable, but **unversioned** (see caveat 1). |
| `title` | Publication title from the catalog card (e.g. `Medicare & You 2026`). |
| `plan_year` | Parsed plan year, or `null` for evergreen publications (see caveat 1). |
| `lang` | Content language — always `en` (the non-English catalog is a separate listing we don't fetch). |
| `category` | CMS topic category (e.g. `Coverage and payment`). |
| `bite` | One-sentence catalog description — same role as HealthCare.gov's `bite`. |
| `page` | 1-based page number within the PDF — cite this. |
| `page_count` | Total pages in the publication. |
| `section` | Breadcrumb from the PDF outline (`Medicare & You 2026 > Section 2: Find out what Medicare covers > Part B-covered services > Acupuncture`), or `null` (see caveat 2). |
| `text` | The page's text — the RAG payload. Median ~1,760 characters. |

Pages that extract to nothing (covers, full-bleed artwork, blank separators) are dropped,
so a publication has slightly fewer records than `page_count`. Page numbers stay absolute,
so `10050_p031` is genuinely page 31 of the handbook.

**On text normalization:** pypdf preserves the PDF's *visual* line breaks, so a sentence
arrives as `…for chronic low\nback pain.` That silently breaks phrase matching for the
Phase 1-a lexical/BM25 retriever, so `clean_page_text()` unwraps continuation lines back
onto their parent, de-hyphenates across breaks, and drops bare page-number lines. The rule
is deliberately conservative — a line is only joined when the previous line ran to nearly
the full measure, didn't end a sentence, and the next line isn't a bullet — so headings and
list items keep their own lines.

---

## Licensing

Medicare.gov publications are **U.S.-government works in the public domain** — safe to
vendor and index. They are consumer-education booklets, not coverage policy, so unlike the
Medicare Coverage Database LCDs and Billing/Coding Articles they contain **no AMA/ADA
CPT/CDT code tables**. This was verified rather than assumed: the corpus has zero
occurrences of `CPT`, `CDT`, `HCPCS`, `©`, or "all rights reserved".

Re-run these before committing any change to this source (see the **Public-repo data
guardrail** in [`CLAUDE.md`](../CLAUDE.md)):

```bash
# Corpus content — proprietary code tables, copyright, PII.
C=data/processed/medicare_pubs/corpus.jsonl
grep -ncE '\b(CPT|CDT|HCPCS)\b' $C
grep -ncEi 'all rights reserved|American (Medical|Dental) Association' $C
grep -ncE '[0-9]{3}-[0-9]{2}-[0-9]{4}' $C                              # SSN-shaped

# Secrets, across every committed data file — expect no output at all.
# (--exclude='*.md' skips the READMEs, which discuss these terms in prose.)
grep -rlE 'api_keys|drupalSettings|BEGIN [A-Z ]*PRIVATE KEY' --exclude='*.md' data/
git ls-files data/ ':!*.md' | xargs grep -lohE '\b[A-Za-z0-9+/_-]{40,}\b' 2>/dev/null
```

The second block is the one that matters: this source's discovery pages carry live
third-party API keys, which is why they are parsed in memory and never written to disk
(see [The search pages are never saved to disk](#the-search-pages-are-never-saved-to-disk)).
If a future change starts persisting any HTML from medicare.gov, run these first.

## Reference

- Publications catalog: <https://www.medicare.gov/publications>
- Medicare & You: <https://www.medicare.gov/publications/10050-medicare-and-you.pdf>
