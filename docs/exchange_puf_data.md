# Health Insurance Exchange Public Use Files (Exchange PUFs) data

This document covers the **Health Insurance Exchange Public Use Files**, the fourth bulk source
in Phase 0 and the first that is **structured plan data rather than a RAG text corpus**. It
explains what the three vendored tables contain, why the processed layer is a lossless mirror
rather than an app-ready model, how the data lands on disk, and what is and is not cleared for
this public repo.

> **This is the first source where `processed/` does not mean "app-ready."** Every "numeric"
> column in these files — deductibles, max-out-of-pocket, actuarial value, even yes/no flags —
> is publisher-formatted text (`'$450 '`, `'Not Applicable'`, `'70.88%'`). The Parquet mirror
> under `processed/exchange_puf/` preserves that exactly; it does not parse it. See
> [Why the processed layer is a mirror, not a model](#why-the-processed-layer-is-a-mirror-not-a-model).

---

## What data is available

CMS's Center for Consumer Information & Insurance Oversight (CCIIO) publishes the Exchange PUFs
annually, covering every Qualified Health Plan (QHP) and Stand-alone Dental Plan (SADP) sold on
the federally-facilitated Marketplace (FFM) — the "structured plan corpus" `docs/plan.md`
names. Where `healthcare_gov`, `medicare_pubs`, and `medicare_ncd` all *explain* coverage rules
in prose, this source *is* the per-plan facts: what a specific plan's deductible is, whether it
covers a given benefit, and which counties it's sold in.

CMS publishes nine PUF tables per plan year (2014–2026); this downloader fetches three:

| Table | Rows (PY2026) | Columns | What it answers |
| --- | --- | --- | --- |
| **Plan Attributes** | 22,059 | 151 | Per-plan-variant deductibles, MOOP, metal level, HSA eligibility, formulary ID, service area ID |
| **Benefits & Cost Sharing** | 1,457,952 | 24 | Per-plan, per-benefit cost sharing — is *this* benefit covered, and what's the copay/coinsurance |
| **Service Area** | 8,820 | 14 | Which counties/ZIPs a plan's `ServiceAreaId` covers |

**Service Area is included even though `docs/plan.md` names only the first two.** Without it
there is no way to answer the plan's own example question — *"find plans in ZIP 30076"* — since
Plan Attributes carries a `ServiceAreaId` but not the ZIP-to-area mapping itself. It is also
tiny (44 KB zipped), so including it is nearly free.

**Not fetched:** Rate PUF (premiums — Phase 5 territory), Network PUF, Business Rules PUF,
Plan ID Crosswalk PUF, Machine Readable URL PUF, Transparency in Coverage PUF, and Quality PUF
(available 2017+ only). Add a table by extending the `TABLES` dict in
`scripts/download_exchange_puf.py`; the URL pattern (below) is uniform across all nine.

Rows are keyed by **`StandardComponentId`** (14-character HIOS Plan ID) in Plan Attributes and
**`PlanId`** (a 16-character Standard Component ID + CSR-variant suffix, e.g. `12345AK1234000-01`)
in Benefits & Cost Sharing — see [`docs/glossary.md`](glossary.md) for what each identifier
means and how they relate.

---

## Why the processed layer is a mirror, not a model

[`data/README.md`](../data/README.md) defines `processed/<source>/` as *"the cleaned artifact
the application actually consumes."* For the three text corpora that's literally true —
`corpus.jsonl` is ready to chunk and embed. **It is not true here, and the docs say so rather
than let this source quietly stretch the definition.**

Every column that looks numeric is publisher-formatted text:

```
CopayInnTier1                'Not Applicable'  'No Charge'  '$0.00'  'No Charge after deductible'  ''
MEHBInnTier1IndividualMOOP   '$450 '           'Not Applicable'      ''
IssuerActuarialValue         '70.88%'          '100.00%'             ''
IsHSAEligible                'Yes'             'No'                  ''
```

Note the dollar sign, the **trailing space**, and the two distinct spellings of "no value" —
an empty field and the literal text `'Not Applicable'` — which mean different things (the
column doesn't apply to this row's benefit design, versus a value simply wasn't provided) but
are easy to collapse into one by accident. `normalize()` writes every column as `VARCHAR`
(`all_varchar=True` in DuckDB's `read_csv`) and changes nothing else — no trimming, no type
coercion, and, less obviously, **no NULLing**: DuckDB's CSV reader treats an empty field as
`NULL` by default, which would silently merge that empty-field case with "field truly absent"
and erase the distinction from `'Not Applicable'` besides. `CSV_READ_OPTS` in
`scripts/download_exchange_puf.py` overrides `nullstr` to a value that can never occur in a
CMS PUF, so an empty CSV field lands in the mirror as an empty string, not `NULL` — verified:
`MEHBInnTier1IndividualMOOP` has 20,670 empty-string rows and zero `NULL`s. That's deliberate:

1. **Type inference would silently guess wrong.** Left to infer types, DuckDB (or pandas, or
   anything else) will pick a type per file per run, and `'No Charge after deductible'` next
   to `'$40.00'` in the same column makes that guess unstable across states and plan years.
   Guessing once and getting it wrong is worse than not guessing.
2. **Picking the right column is a modeling decision, not a parsing one.** Plan Attributes has
   **36 max-out-of-pocket columns** — `MEHB`/`DEHB`/`TEHB` (medical / dental / combined) ×
   `InnTier1`/`InnTier2`/`OutOfNet`/`CombInnOon` × `Individual`/`FamilyPerPerson`/`FamilyPerGroup`
   — because plans vary in which tier structure and which benefit categories they combine.
   `MEHBInnTier1IndividualMOOP` alone is empty for 20,670 of 22,059 rows; most plans carry their
   real MOOP under `TEHB*` instead. Choosing which column (or fallback chain) represents "the"
   MOOP for a plan needs real query requirements — Phase 3/5's job, not this ingestion step's.

So the Parquet under `processed/exchange_puf/<year>/` is a **faithful, lossless, queryable
mirror** of the CMS CSVs — every column `VARCHAR`, values preserved byte-for-byte, joinable and
filterable via DuckDB's `read_parquet()`, but not yet the thing an application reads a number
out of. The typed layer belongs on top of it later, as a view or a second table, once there's a
real question driving which of the 36 MOOP columns matters.

This distinction is also what keeps the CMS Disclaimer-User Agreement's one real obligation
honest: *"the user may not present or otherwise reference data that have been altered in any
way as CMS data."* Calling this mirror "the CMS Plan Attributes PUF" would be wrong even though
it is unaltered — it's a derived artifact in a different file format. It's documented as such.

---

## How to download

### 1. Do you need an API key?

**No.** These are static annual file downloads, no auth, no registration.

### 2. Install dependencies

`requests` and `duckdb` are already declared in `pyproject.toml`:

```bash
uv sync
```

### 3. Run the downloader

```bash
# Smoke test first — Service Area alone is 44 KB
uv run python scripts/download_exchange_puf.py --table service-area

# Full default run — 2026, all three tables (~13.5 MB zipped, ~410 MB CSV, ~2.2 MB Parquet)
uv run python scripts/download_exchange_puf.py

# A second plan year
uv run python scripts/download_exchange_puf.py --year 2025 --year 2026

# Rebuild the Parquet mirror + sample from raw, no network
uv run python scripts/download_exchange_puf.py --normalize-only
```

| Flag | Purpose |
| --- | --- |
| `--out DIR` | Output root (default `./data`) |
| `--year YEAR` | Plan year to fetch; **repeatable** (default: `2026`) |
| `--table NAME` | One of `benefits-and-cost-sharing` / `plan-attributes` / `service-area`; repeatable (default: all three) |
| `--refresh` | Ignore the recorded ETag/Last-Modified and force a re-download |
| `--normalize-only` | Skip download; rebuild the Parquet mirror + sample from `raw/` |
| `--sample-state CODE` | State to slice for the committed sample (default `AK`) |
| `--delay`, `--retries`, `--backoff` | Politeness / robustness tuning |

The script **exits non-zero** if the licensing scan on the committed sample slice finds a
blocking marker — see [Licensing](#licensing).

### 4. Idempotency & resumability

Unlike the other three sources (which compare a freshly-downloaded sha256 against the catalog),
this one gets a real conditional GET: `download.cms.gov` is Akamai-backed and honors
`If-None-Match` / `If-Modified-Since` built from the `etag` / `last_modified` recorded in
`catalog.json`. A re-run against an unchanged file gets **HTTP 304** and downloads nothing:

```
[1/3] up to date (304): 2026/benefits-and-cost-sharing
[2/3] up to date (304): 2026/plan-attributes
[3/3] up to date (304): 2026/service-area
```

`--refresh` skips sending the conditional headers and forces a fresh download. The zip is
streamed to a `.part` file and renamed only on success, so an interrupted download can't leave
a truncated zip that a later run trusts as complete.

---

## Where the data lands

```
data/raw/exchange_puf/
├── catalog.json                          # per-(year, table) manifest — COMMITTED
├── _meta.json                            # fetch provenance — COMMITTED
└── <year>/
    ├── <slug>-puf.zip                    # untouched download — GIT-IGNORED
    └── <Member_Name>_PUF.csv             # extracted CSV, fetch cache — GIT-IGNORED

data/processed/exchange_puf/
├── <year>/
│   ├── plan_attributes.parquet           # GIT-IGNORED — the mirror
│   ├── benefits_cost_sharing.parquet     # GIT-IGNORED
│   └── service_area.parquet              # GIT-IGNORED
└── sample/
    ├── plan_attributes_AK_2026.csv       # COMMITTED — 98 rows, one state
    ├── benefits_cost_sharing_AK_2026.csv # COMMITTED — 4,752 rows
    └── service_area_AK_2026.csv          # COMMITTED — 43 rows
```

**Why manifest-not-blob.** Plan year 2026 alone: Benefits & Cost Sharing is a **375 MB CSV**
(1,457,952 rows — the file CMS itself notes exceeds Excel's row limit); Plan Attributes is
32 MB (22,059 × 151). None of that belongs in git history. `catalog.json` records, per
`(year, table)`: `url`, `etag`, `last_modified`, `zip_bytes`, `csv_bytes`, `row_count`,
`column_count`, `fetched_at` — enough to reproduce `data/raw/exchange_puf/` exactly by
re-running the script, the same manifest-not-blob bargain
[`data/raw/medicare_pubs/pdf/`](../data/README.md) makes for its ~46 MB of PDFs.

### The committed sample

`processed/exchange_puf/sample/` is a full-column slice of each table filtered to one state
(Alaska by default — the smallest: 4,752 / 98 / 43 rows across the three tables, ~1.4 MB
total). It exists so the repo has something inspectable without a 400 MB download, and so
Phase 3's tests/evals have a small, real fixture per `docs/plan.md`'s "fixtures over live
calls" convention. It is **regenerated on every `normalize()` run**, not hand-curated, and
**re-scanned for licensing markers before it's trusted** (see [Licensing](#licensing)) — so a
future plan year that happens to introduce a code table into Alaska's data fails the run
loudly instead of silently landing in a stale, never-rechecked fixture.

---

## Licensing

**Public domain.** Per the [data.gov catalog entry](https://catalog.data.gov/dataset/plan-attributes-puf-py2026),
licensed under <https://www.usa.gov/publicdomain/label/1.0/>. The CMS *Disclaimer-User
Agreement* (`exchange-pufs-disclaimagree-py26.pdf`) imposes **no redistribution restriction** —
its only real obligations are the "don't present altered data as CMS data" clause addressed
above, and a recommended citation format:

> Centers for Medicare & Medicaid Services. (2025). *2026 Health Insurance Exchange Public Use
> Files (\<file name\>)* [Data file and code book]. Retrieved from
> <http://www.cms.gov/CCIIO/Resources/Data-Resources/marketplace-puf.html>

**One wrinkle, worth naming precisely.** The full PUFs (never committed — see
[manifest-not-blob](#where-the-data-lands)) contain code references inside **issuer-authored
free text**, not a code table: 249 lines in Benefits & Cost Sharing mention `CPT` with actual
code numbers —

> "…rhinoplasty; blepharoplasty services identified by CPT codes 15820, 15821, 15822, 15823;
> brow ptosis identified by CPT code 67900…"

— and CDT dental codes paired with descriptors ("D0120 (Periodic oral evaluation - established
patient)"). This is an issuer describing *their own plan's* exclusions in free text, the same
kind of narrative reference `medicare_ncd_data.md` documents for NCD revision histories — not a
redistributed AMA/ADA code table. It never reaches the repo regardless, because the full files
are git-ignored; the question that matters is whether the **committed sample** carries any of
it.

**It doesn't — verified, not assumed.** `scan_sample_licensing()` runs at the end of every
`normalize()` call and checks the sample slice about to be committed:

- **Blocking markers — zero hits on the AK/2026 sample, and any hit fails the run:** `©`, "all
  rights reserved", an AMA copyright notice, "American Dental Association", `CPT`, `CDT`,
  `HCPCS`, and CDT-shaped dental-code tokens (`\bD\d{4}\b`).
- **Advisory markers — reported, not treated as violations:** email-shaped strings. The AK
  sample has **33**, all one value — an issuer's InstaMed payment-portal account identifier,
  formatted like an address (`ISSUER.NAME` at the `INSTAMED` payment-processor domain),
  embedded in Plan Attributes' `URLForEnrollmentPayment` column. It's a business account ID,
  not a person, and is allowlisted by its literal value in `scripts/sensitive_baseline.toml`.

If a different `--sample-state` or a future plan year's Alaska data ever trips a blocking
marker, the run fails with `return 1` rather than silently writing code-bearing text into the
public repo.

Re-run before committing any change to this source:

```bash
# The script's own scan is the real check — it exits non-zero on a blocking marker.
uv run python scripts/download_exchange_puf.py --normalize-only

# Whole-repo guardrail (secrets / PII / licensing) — should stay clean.
make scan
```

---

## Reference

- Exchange PUF landing page: <https://www.cms.gov/marketplace/resources/data/public-use-files>
- Download index (zip pattern `https://download.cms.gov/marketplace-puf/<year>/<slug>-puf.zip`):
  <https://download.cms.gov/marketplace-puf/2026/plan-attributes-puf.zip>
- Data dictionaries (per table, `https://www.cms.gov/files/document/<name>-datadictionary-py<yy>.pdf`):
  [Plan Attributes](https://www.cms.gov/files/document/planattributes-datadictionary-py26.pdf) ·
  [Benefits & Cost Sharing](https://www.cms.gov/files/document/benefitscostsharing-datadictionary-py26.pdf) ·
  [Service Area](https://www.cms.gov/files/document/servicearea-datadictionary-py26.pdf)
- Disclaimer-User Agreement: <https://www.cms.gov/files/document/exchange-pufs-disclaimagree-py26.pdf-0>
- General Information Factsheet: <https://www.cms.gov/files/document/exchange-pufs-geninfofacts-py26.pdf-0>
- data.gov catalog entry: <https://catalog.data.gov/dataset/plan-attributes-puf-py2026>
