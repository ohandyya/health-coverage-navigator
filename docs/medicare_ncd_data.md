# Medicare Coverage Database (NCD) data

This document covers the **Medicare Coverage Database (MCD)** — specifically its **National
Coverage Determinations**, the third bulk RAG source in Phase 0. It explains what an NCD is,
why the download goes through the Coverage API rather than the bulk ZIPs, how the licensing
boundary is enforced, and how the data lands on disk.

> **This is the repo's one partly-clean source.** The MCD holds National coverage data (NCDs
> — cleared for this public repo) right next to Local coverage data (LCDs and Billing/Coding
> Articles — **blocked**, because they embed AMA-copyrighted CPT and ADA-copyrighted CDT code
> tables). "Vendor only the cleared subset" is not an abstract rule here; it is the whole
> design of the fetcher. See the **Public-repo data guardrail** in [`CLAUDE.md`](../CLAUDE.md).

---

## What data is available

An **NCD** is CMS's nationwide decision on whether Medicare covers a particular item or
service. It binds every MAC in the country, which makes it the *operative coverage rule*
rather than a consumer explanation of one — the difference between
[`medicare_pubs`](medicare_pubs_data.md) ("Medicare covers acupuncture for chronic low back
pain") and this source (the determination that says so, with its effective date, statutory
benefit category, and the transmittal that implemented it).

The corpus is **345 NCDs**, ~950 KB of policy text. Median 1,540 characters, max ~19,700 —
short documents, roughly one article each. They are organized into 29 chapters by body system
or service type:

| Chapter | NCDs | Subject |
| --- | --- | --- |
| 20 | 49 | Cardiovascular |
| 190 | 33 | Pathology and laboratory |
| 220 | 30 | Radiology / diagnostic imaging |
| 110 | 23 | Drugs and biologicals |
| 160 | 23 | Nervous system and psychiatry |
| 230 | 19 | Genitourinary |
| 210 | 16 | Preventive and screening services |
| … | | 22 more chapters |

23 of the 345 are flagged `is_lab` — determinations covering laboratory tests.

Each NCD carries: a statutory **benefit category**, an item/service description, the
**indications and limitations of coverage** (the operative part — what is nationally covered,
nationally non-covered, or left to MAC discretion), an effective date, and a revision history
back to the transmittals that changed it.

---

## Why the Coverage API and not the bulk ZIPs

[`plan.md`](plan.md) points at the MCD **Downloads** page, which offers weekly-refreshed ZIPs
of the full NCD, LCD, and Article datasets. We do not use it. Two reasons:

1. **It is not machine-fetchable.** `cms.gov/medicare-coverage-database/downloads/downloads.aspx`
   returns **403** to non-browser clients, and the ZIPs sit behind an in-browser
   license-acceptance click.
2. **It bundles what we must keep separate.** That single license click covers the AMA/ADA/AHA
   terms for the Local coverage data in the same place as the National data. Accepting a CPT
   license in order to download NCDs is exactly the confusion this repo's guardrail exists to
   prevent.

The **MCD Coverage API** (`https://api.coverage.cms.gov/v1/`) is the better route on both
counts: keyless since February 2024, plain JSON, and generously rate-limited.

### The API enforces our licensing boundary for us

This is the load-bearing property of this source. CMS gates its endpoints along precisely the
line the guardrail draws:

| Endpoint family | Auth required | Verified |
| --- | --- | --- |
| `/v1/reports/national-coverage-ncd/` | none | `200` keyless |
| `/v1/data/ncd/` | none | `200` keyless |
| `/v1/data/lcd/…` | AMA/ADA/AHA license-agreement bearer token | `401` keyless |
| `/v1/data/article/…` | AMA/ADA/AHA license-agreement bearer token | `401` keyless |

Every LCD and Article endpoint — including the ones that serve the CPT/HCPCS code tables,
ICD-10 covered/non-covered lists, and SAD exclusion tables — answers `401` without a token.
Every National coverage endpoint answers without one.

So **"NCDs only" is not a rule the script polices by hand. It is the set of endpoints that
answer without a license token.**

> ### ⚠️ Never call `/v1/metadata/license-agreement/`
> That endpoint issues the bearer token, valid one hour, that unlocks the LCD and Article
> data. Not holding a token is this fetcher's second line of defence: even a coding mistake
> that pointed at an LCD endpoint would get a `401` rather than data we may not vendor.
> `fetch_json()` treats a `401` as a fatal error and never retries it, precisely so that
> failure is loud. If you ever find yourself wanting a token, the answer is that the data
> behind it does not belong in this repo.

### What we deliberately do not fetch

The API serves several other National-coverage document types with no license token — NCAs
(National Coverage Analyses and their decision memos), CALs, MEDCAC meeting materials,
Technology Assessments, and Medicare Coverage Documents. They are license-clean and could be
added later as a separate source key. They are out of scope today because they are *process*
documents — the evidence review behind a decision — rather than the coverage rule itself, and
Phase 0 wants the rule.

---

## How to download

### 1. Do you need an API key?

**No.** CMS removed the Coverage API's key requirement in February 2024. No registration, no
`.env` entry. (Contrast the **Marketplace API**, which does need a key and belongs to Phase 3.)

### 2. Install dependencies

`requests` and `beautifulsoup4` are already declared in `pyproject.toml`:

```bash
uv sync
```

### 3. Run the downloader

```bash
# Smoke test first — 3 NCDs end to end
uv run python scripts/download_medicare_ncd.py --limit 3

# Full download into ./data (345 NCDs, ~2 minutes)
uv run python scripts/download_medicare_ncd.py

# Rebuild the corpus from the raw layer without re-downloading
uv run python scripts/download_medicare_ncd.py --normalize-only
```

| Flag | Purpose |
| --- | --- |
| `--out DIR` | Output root (default `./data`) |
| `--limit N` | Only fetch the first N NCDs (smoke test) |
| `--max-pages N` | Report-page ceiling for discovery (default 20) |
| `--refresh` | Re-fetch NCDs already on disk |
| `--normalize-only` | Skip download; rebuild `corpus.jsonl` from the raw layer |
| `--delay`, `--retries`, `--backoff` | Politeness / robustness tuning |

The script **exits non-zero** if its licensing scan finds a blocking marker — see
[Licensing](#licensing).

### 4. Idempotency & resumability

Discovery fetches the NCD listing report, saves it to `raw/medicare_ncd/report.json`, then
fetches each NCD it does not already have. Re-running skips everything on disk (`345 skipped,
0 fetched`); a failed fetch is simply not written, so a re-run retries it. `report.json` is
always saved **in full**, even under `--limit`, so a smoke test cannot quietly shrink a
complete corpus — the unfetched NCDs are reported as missing at normalize time instead.

Because a revised NCD arrives under a new version number, it lands in a new
`<section>_v<n>.json` file rather than overwriting the old one. Normalization is driven by
`report.json`, so superseded files are excluded from the corpus; the script names them so you
can delete them if you do not want the history.

---

## Where the data lands

```
data/raw/medicare_ncd/
├── report.json              # the National Coverage NCD report — the discovery index
├── ncd/<section>_v<n>.json  # one untouched API response per NCD, e.g. 30.3_v2.json
└── _meta.json               # fetch provenance: timestamp, counts, tool, license note

data/processed/medicare_ncd/
└── corpus.jsonl             # one normalized record per NCD — the RAG input
```

Raw files are named by **section number** (`30.3`), not by the API's internal document ID,
because the section number is what a citation names — "NCD 30.3" is how CMS, MACs, and
clinicians refer to the acupuncture determination.

**Nothing here is git-ignored.** The entire source is 2.6 MB raw + 1.8 MB processed, so unlike
the `medicare_pubs` PDFs there is no manifest-not-blob tradeoff to make: the raw layer is
committed whole.

### `corpus.jsonl` record schema

One JSON object per **NCD**. `id`, `source`, `url`, `title`, `bite`, and `text` deliberately
match the `healthcare_gov` and `medicare_pubs` field names so a later cross-source chunker can
treat all three corpora uniformly.

| Field | Meaning |
| --- | --- |
| `id` | `ncd_<section>_v<version>` (e.g. `ncd_30.3_v2`). |
| `source` | Origin tag — `"medicare_ncd"`. |
| `url` | The human-facing MCD page for this NCD. **Not** the API path the report returns in its own `url` field, which a person cannot open. |
| `title` | NCD title (e.g. `Acupuncture`). |
| `bite` | **Always empty.** NCDs carry no editorial summary; the field exists so every corpus has the same shape. |
| `section_number` | The citable NCD number (e.g. `30.3`). |
| `chapter` | Chapter number (e.g. `30`). |
| `ncd_id`, `ncd_version` | The API's identity for this document — what you need to re-fetch it. |
| `publication_number` | `100-3` — the Medicare NCD Manual. |
| `benefit_category` | The statutory benefit category or categories the item falls under. |
| `effective_date` | `MM/DD/YYYY`, or `null` — see the caveat below. |
| `effective_date_note` | Non-empty only when `effective_date` is `null`; carries CMS's explanation verbatim. |
| `effective_end_date` | End date, or `null` (currently `null` for all 345 — no NCD in the current set has been end-dated). |
| `implementation_date` | When MACs had to have the change in place. |
| `last_updated` | Last update to this version, from the report. |
| `is_lab` | Boolean — does this determination cover a laboratory test. |
| `transmittal_number`, `transmittal_url` | The CMS change instruction that implemented this version, and a link to its PDF. |
| `revision_history` | Change history, HTML-stripped. **Deliberately excluded from `text`** — see below. |
| `text` | The policy body — the RAG payload. |

### How `text` is composed

`text` concatenates the policy sections in the MCD's own order, each under a `## ` heading:

```
## Benefit Category
## Item/Service Description
## Indications and Limitations of Coverage
## Reasons for Denial          (present only when populated — rare)
## Other                        (present only when populated — rare)
## Cross-reference
```

Empty sections are omitted. The headings are written even though the API delivers each section
as a separate field, so the Phase 0 chunker can split on them and keep the section name as a
citation label ("NCD 30.3, *Indications and Limitations of Coverage*").

**`revision_history` is not in `text`, on purpose.** It is retrieval noise — transmittal
numbers, change-request numbers, rescinded-and-replaced chains — that would match lexically
against date and number queries without answering anything a user asked. It stays as its own
record field, so nothing is lost. A side effect is that it keeps most code-adjacent prose out
of the retrievable body (see [Licensing](#licensing)), but that is a consequence, not the
reason.

### Two caveats worth knowing before you trust this data

**1. NCDs are scoped by effective date, not by plan year — and 75 of them have no date at
all.** There is no `plan_year` field here, and there should not be: an NCD applies from its
effective date until superseded, cutting across plan years. But 75 of the 345 are
*longstanding* determinations predating CMS's posting practice, where the API returns a
sentence where the date should be:

> This is a longstanding national coverage determination. The effective date of this version
> has not been posted.

Leaving that in the field would put prose in something every downstream date comparison
assumes is a date. So `effective_date` is `null` for those 75 and `effective_date_note` carries
the sentence. **Any date filter over this corpus must handle `null`** — and `null` means "in
force, date unknown", not "not in force".

**2. The API's HTML is escaped once, and slightly malformed.** Text fields arrive as
`&lt;p&gt;&lt;strong&gt;A. General&lt;&sol;p&gt;&lt;&sol;strong&gt;` — HTML-escaped, with
`&sol;` for a slash, and with mis-nested tags underneath. A single `html.unescape()` yields
real markup, which BeautifulSoup repairs. Do not add a second unescape pass; it is a no-op
here and would corrupt any literal `&amp;` in the policy text.

---

## Licensing

**NCDs are cleared for this public repo.** They are U.S.-government works, and unlike LCDs and
Billing/Coding Articles they carry no AMA/ADA code tables. The API's own auth boundary
([above](#the-api-enforces-our-licensing-boundary-for-us)) is the primary evidence: NCDs are
served without a license agreement precisely because there is nothing licensed in them.

That was verified against the full corpus, not assumed. `scan_licensing()` runs at the end of
every download and re-checks the text that is about to be committed:

- **Blocking markers — zero hits, and any hit fails the run:** `©`, "all rights reserved", an
  AMA copyright notice, "American Dental Association", `CDT`. The API's `ama_statement` field
  is empty in all 345 NCDs.
- **Advisory markers — reported, not treated as violations:** `CPT` (22 occurrences), `HCPCS`
  (14), and HCPCS-Level-II-shaped tokens (10).

**Be precise about what those advisory hits are**, because it would be easy to over- or
under-react. They are *narrative mentions of codes*, overwhelmingly inside revision histories:

> 04/1992 — Corrected CPT and ICD-9-CM codes. Effective date 11/18/1991. (TN 58)

> …to add HCPCS code G0465 to the instructions…

A handful of individual HCPCS Level II identifiers do appear this way (`G0465`, `G0460`,
`G0255`, `C1824`, `C1898`, `K1030`, `C9076`). What does **not** appear anywhere is a code
*table* — a list of codes with descriptors, which is the copyrightable thing and the reason
LCDs are blocked. Prose references to a code inside a U.S.-government policy document are not
a redistribution of the AMA's code set.

> **Do not copy `medicare_pubs`'s claim here.** That source's guide states its corpus has *zero*
> occurrences of `CPT`/`HCPCS`, which is true there and false here. A scan that reported zero
> for this source would be wrong, and a scan that failed the build on these narrative mentions
> would just get switched off. It reports them instead, with context, for human review.

Re-run these before committing any change to this source:

```bash
C=data/processed/medicare_ncd/corpus.jsonl

# The script's own scan is the real check — it exits non-zero on a blocking marker.
uv run python scripts/download_medicare_ncd.py --normalize-only

# Blocking markers — expect 0 for both.
grep -ncEi 'all rights reserved|American Dental Association|©' $C
grep -ncE '\bCDT\b' $C

# Advisory — grep -c counts records, not occurrences: expect 22 records
# (36 total occurrences). Investigate a jump, not a nonzero.
grep -ncE '\b(CPT|HCPCS)\b' $C

# Nothing from the license-gated endpoints should ever be on disk.
grep -rl 'license-agreement\|/data/lcd\|/data/article' data/raw/medicare_ncd/ || echo "clean"
```

---

## Reference

- MCD Coverage API docs: <https://api.coverage.cms.gov/docs/>
- OpenAPI spec: <https://api.coverage.cms.gov/docs/v1/coverage-api.json>
- Medicare Coverage Database: <https://www.cms.gov/medicare-coverage-database/>
- NCD/LCD distinction: <https://www.cms.gov/medicare/coverage/determination-process>
- Example NCD (Acupuncture, 30.3):
  <https://www.cms.gov/medicare-coverage-database/view/ncd.aspx?ncdid=11&ncdver=2>
