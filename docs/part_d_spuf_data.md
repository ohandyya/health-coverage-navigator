# Medicare Part D quarterly formulary files (SPUF) data

This document covers the **Quarterly Prescription Drug Plan Formulary, Pharmacy Network, and
Pricing Information Public Use File** — the fifth bulk source in this repo and the second that
is structured plan data rather than a RAG text corpus. Where [`exchange_puf`](exchange_puf_data.md)
holds the ACA marketplace's per-plan facts, this source holds Medicare Part D's: which drug a
plan covers, on what tier, and with what utilization-management strings attached.

> **The published file is 2.49 GB, and this downloader transfers 9.4 MB of it.** The zip is a
> container of 15 nested per-file zips, and the six-part pharmacy-network file is 92% of the
> weight. Rather than download the container, the script reads its central directory over HTTP
> and range-fetches only the members it wants. See
> [Why range-fetch](#why-range-fetch-and-why-that-is-safe).

---

## What data is available

CMS rebuilds this file every quarter from the Medicare Plan Finder, covering every Part D plan —
standalone **PDP**s and Part D benefits bundled into **Medicare Advantage** — except employer,
PACE, and demonstration plans. Ten files ship in the container; this downloader fetches seven:

| File | Rows (2026Q2) | Cols | What it answers |
| --- | --- | --- | --- |
| **Basic Drugs Formulary** | 1,124,586 | 12 | Is this NDC covered, on what tier, with prior auth / step therapy / quantity limit |
| **Plan Information** | 112,294 | 14 | Contract/plan identity, premium, deductible, and the `FORMULARY_ID` join key |
| **Beneficiary Cost** | 172,642 | 24 | Cost sharing by tier × days supply × preferred/non-preferred/mail pharmacy |
| **Insulin Beneficiary Cost** | 43,057 | 13 | The same for insulin, which has its own capped cost share |
| **Excluded Drugs Formulary** | 13,717 | 10 | Otherwise-excluded drugs an enhanced plan covers as a supplemental benefit |
| **Geographic Locator** | 3,279 | 7 | `COUNTY_CODE` → county / MA region / PDP region lookup |
| **Indication-Based Coverage** | 397 | 4 | Drugs covered only for a specific FDA-approved indication |

**Not fetched by default:**

- **Pharmacy Network** — 2.29 GB across six parts that need reassembly. Plan-to-pharmacy
  mapping with dispensing fees. Nothing before Phase 5's provider/pharmacy-network checks reads
  it, so it is not wired up at all.
- **Pricing** (191 MB) — per-plan average monthly unit cost per NDC. Defined and opt-in via
  `--file pricing`; add it when the drug-cost work actually starts.

The join that makes this source useful runs `plan_information.FORMULARY_ID` →
`basic_drugs_formulary.FORMULARY_ID`. Note that a formulary is shared across many plans (328
distinct formularies back 691 contracts), so the formulary file is keyed by `FORMULARY_ID`, not
by plan.

### Three caveats that affect correctness

> **`STATE` is only populated for Medicare Advantage rows.** All standalone PDP rows leave
> `STATE` and `COUNTY_CODE` blank and are located by `PDP_REGION_CODE` instead — PDPs are sold
> by region, not by county. **Filtering this source by state silently drops every standalone
> drug plan**, which is exactly the plan type a "what does my Part D plan cover" question is
> usually about. Alaska, `exchange_puf`'s default sample state, has **zero** rows here.
>
> **Suppressed plans appear in one file only.** Plans CMS suppressed for the reporting period
> carry `PLAN_SUPPRESSED_YN = "Y"` in Plan Information and appear in no other file, so a
> formulary join legitimately returns nothing for them. That is missing data, not a broken join.
>
> **The files are Latin-1, not UTF-8**, and nothing in CMS's record layout says so. Only Plan
> Information exercises it, via three Spanish plan names — `Óptimo Plus (PPO)`,
> `Freedom Máximo (HMO-POS)`, `Community y Más (HMO C-SNP)` — but that is enough to make a
> strict UTF-8 read raise. The other six files are pure ASCII, and ASCII is a subset of
> Latin-1, so the script decodes all seven as Latin-1: exact, not merely tolerant.

---

## Why range-fetch, and why that is safe

The quarterly zip is **2,489,876,562 bytes**. Its 15 members are themselves zips:

```
  409,818,543  pharmacy networks file  PPUF_2026Q2 part 1.zip   ┐
  409,257,507  pharmacy networks file  PPUF_2026Q2 part 3.zip   │ 2.29 GB — not fetched
  ...          (parts 2, 4, 5, 6)                               ┘
  219,755,746  pricing file PPUF_2026Q2.zip                       191 MB — opt-in
    8,570,454  basic drugs formulary file  PPUF_2026Q2.zip      ┐
      649,777  beneficiary cost file  PPUF_2026Q2.zip           │ 9.4 MB — the default set
      430,958  plan information  PPUF_2026Q2.zip                │
      ...      (insulin, excluded, geographic, indication)      ┘
```

Downloading 2.49 GB to read 9.4 MB of it is the kind of cost that gets a pipeline run once and
then quietly abandoned. So `download_part_d_spuf.py` does this instead:

1. `Range: bytes=-66000` → locate the End Of Central Directory record (falling through to the
   ZIP64 locator, which this archive needs).
2. Range-read the central directory (1,865 bytes) → every member's compression method,
   compressed size, CRC32, and local-header offset.
3. Per wanted member: read its local header, then its compressed byte span, and inflate.

**The integrity guarantee is the CRC32 check**, not trust in the range mechanics. Every member
is verified against the checksum the central directory records before it is written to disk, so
a truncated, mis-offset, or corrupted range fails loudly instead of landing as plausible-looking
garbage. A range request answered with **200 instead of 206** — meaning the server ignored
`Range` and is about to stream 2.49 GB — is treated as a hard error, never as a silent fallback.

This was verified against the live host, not assumed: `data.cms.gov` advertises
`accept-ranges: bytes`, answers ranges with 206, and honors `If-Modified-Since` with a real 304.
It sends `Last-Modified` but **no ETag**, which is why idempotency here keys on the former alone.

---

## Why the processed layer is a mirror, not a model

Same bargain [`exchange_puf`](exchange_puf_data.md#why-the-processed-layer-is-a-mirror-not-a-model)
makes, for the same reason: the columns are publisher-formatted text, not numbers.

```
PREMIUM                 '35.60'   '0.00'    ''
MA_REGION_CODE          ' '                            <- a single space, for every PDP row
copay_amt_pref_insln    ' '       '0.00'    '10.00'    <- blank and zero mean different things
TIER_LEVEL_VALUE        '1'       '3'       '5'
```

`normalize()` writes every column as `VARCHAR` and changes nothing else — no trimming, no type
coercion, and no NULLing (DuckDB's reader would turn an empty field into `NULL` by default,
erasing the distinction from a single space). Choosing which of the four
preferred / non-preferred / mail-preferred / mail-non-preferred cost columns is "the" copay for a
plan is a Phase 5 modeling decision that needs real query requirements, not a guess made here.

**The one transformation applied is a character-set transcode**: the source is Latin-1 and
Parquet/CSV are written UTF-8, so `Óptimo Plus (PPO)` is the same three words in both, stored as
different bytes. That is a re-encoding, not an edit — no character is dropped or replaced.

---

## How to download

### 1. Do you need an API key?

**No.** No key, no registration, no click-through agreement. `cms.gov` does reject requests
without a browser-like User-Agent (403), same as `medicare.gov` — the script sends one.

### 2. Install dependencies

`requests` and `duckdb` are already declared in `pyproject.toml`; the zip parsing is stdlib:

```bash
uv sync
```

### 3. Run the downloader

```bash
# Smoke test first — the geographic locator alone is 30 KB
uv run python scripts/download_part_d_spuf.py --file geographic-locator

# Full default run — newest quarter, seven files (~9.4 MB over the wire, ~86 MB of text)
uv run python scripts/download_part_d_spuf.py

# A past quarter (30 are published, back to 2018)
uv run python scripts/download_part_d_spuf.py --quarter 2026Q1

# Rebuild the Parquet mirror + sample from raw, no network
uv run python scripts/download_part_d_spuf.py --normalize-only
```

| Flag | Purpose |
| --- | --- |
| `--out DIR` | Output root (default `./data`) |
| `--quarter Q` | Quarter to fetch, e.g. `2026Q2`; **repeatable** (default: the newest published) |
| `--file NAME` | One of the eight file slugs; repeatable (default: all but `pricing`) |
| `--refresh` | Ignore the recorded `Last-Modified` and force a re-fetch |
| `--normalize-only` | Skip download; rebuild the Parquet mirror + sample from `raw/` |
| `--sample-contract ID` | `CONTRACT_ID` to anchor the committed sample; repeatable |
| `--delay`, `--retries`, `--backoff` | Politeness / robustness tuning |

The script **exits non-zero** if the licensing scan on the committed sample finds a blocking
marker — see [Licensing](#licensing).

### 4. Idempotency & resumability

The `Last-Modified` recorded in `catalog.json` is replayed as `If-Modified-Since` on the very
first range request of a quarter, so an unchanged container costs one request and no body:

```
Discovered 30 quarters, newest 2026Q2.
  up to date (304): 2026Q2 (7 files)

Download: 0 fetched, 7 up to date, 0 failed (0 bytes over the wire).
```

A file counts as *held* only if it is both on disk **and** in `catalog.json`. Disk alone is not
enough — a member that failed after extraction but before it was measured would leave a file
behind that nothing vouches for, and a 304 would then strand it forever. When a 304 arrives but
something is not held, the script re-reads the directory unconditionally and fetches what is
missing:

```
  2026Q2: unchanged upstream, but 1 file(s) not held: ['plan-information']
```

Each member is extracted to a `.part` file, measured, and renamed only on success, so an
interrupted run cannot leave a truncated extract that a later run trusts.

---

## Where the data lands

```
data/raw/part_d_spuf/
├── catalog.json                              # per-member manifest — COMMITTED
├── _meta.json                                # fetch provenance + sample anchors — COMMITTED
└── <quarter>/
    └── <stem>.txt                            # pipe-delimited extract — GIT-IGNORED

data/processed/part_d_spuf/
├── <quarter>/
│   ├── basic_drugs_formulary.parquet         # GIT-IGNORED — the mirror
│   ├── plan_information.parquet              # GIT-IGNORED
│   └── ... (5 more)
└── sample/
    ├── basic_drugs_formulary_2026Q2.csv      # COMMITTED — 12,869 rows
    ├── plan_information_2026Q2.csv           # COMMITTED — 34 rows
    └── ... (5 more, 844 KB total)
```

**Why manifest-not-blob.** One quarter is ~86 MB of pipe-delimited text, 58 MB of it the
formulary file's 1.12M rows. `catalog.json` records, per `(quarter, file)`: `url`, `member`,
`last_modified`, `byte_offset`, `byte_length`, `crc32`, `txt_bytes`, `txt_sha256`, `row_count`,
and `column_count` — enough to reproduce `data/raw/part_d_spuf/` exactly by re-running the
script. The byte offset and CRC32 are what make that reproducibility checkable rather than
merely claimed.

### The committed sample

`processed/part_d_spuf/sample/` is a full-column slice of each file, anchored to a handful of
contracts. `exchange_puf`'s "filter to one state" rule does not transfer here — `STATE` is blank
for every PDP and Alaska has no rows at all — so the anchor is `CONTRACT_ID`, chosen in two
steps:

- **seed** — the smallest standalone-PDP (`S`) and local-MA (`H`) contract by plan-information
  row count, so the fixture exercises both plan shapes. Committing only one would mean the
  region-coded PDP rows or the county-coded MA rows never appear.
- **top-up** — any fetched file the seed would leave *empty* contributes its own smallest
  contract. Without this the fixture ships 0-row files: indication-based coverage names only
  **three contracts in the entire country**, so no seed will ever hit it by chance, and
  excluded-drugs covers 270 of 691. A 0-row fixture cannot test a join.

For 2026Q2 that resolves to `S5743`, `H1671`, `H4057`, `H5050` — 844 KB total. The rule, not the
contracts, is what is stable: a specific contract can stop being offered between quarters, so
the anchors are computed each run and recorded in `_meta.json` under `sample_contracts`.

The sample is **regenerated on every `normalize()` run**, never hand-curated, and re-scanned for
licensing markers before it is trusted — so a future quarter that introduces something
problematic fails the run loudly instead of persisting in a stale, never-rechecked fixture.

---

## Licensing

**Public domain, cleared for this public repo.** CMS's own DCAT catalog
(`https://data.cms.gov/data.json`) records this dataset as:

| Field | Value |
| --- | --- |
| `license` | `https://www.usa.gov/government-works` |
| `accessRights` / `accessLevel` | `public` |
| `rights` | *(none)* |

No registration, no API key, no click-through data-use agreement. The "Terms and Conditions for
Use" and "Agreement for Use" documents referenced from the older CMS *files-for-order* page are
a leftover from when these were order-by-request files; they cover data accuracy and delivery
timeframes, not redistribution. Since January 2021 the files are free downloads.

**Why this clears the guardrail in [`CLAUDE.md`](../CLAUDE.md), unlike the MCD:**

1. **No proprietary code tables.** Drugs are identified by **NDC** (FDA, public) and **RxCUI**
   (NLM RxNorm). There is no CPT, CDT, HCPCS Level II, or ICD content anywhere in the container —
   this is drug coverage, not procedure coding, so the AMA/ADA licensing line is never
   approached. Contrast `medicare_ncd`, where only the NCD subset is safe.
2. **No PII/PHI.** Every row is plan-level or product-level. There is no beneficiary-level data.
   The one identifier-shaped column in the whole container is `PHARMACY_NUMBER` in the
   pharmacy-network file — a 12-digit pharmacy identifier for a business, not a person — and
   that file is not fetched.

**One scanner interaction worth knowing.** A Medicare `CONTRACT_ID` is a letter followed by four
digits (`H1671`, `S5743`), which is *exactly* the shape of an HCPCS Level II code — and `H`, `R`,
and `S` are all real HCPCS Level II letters. The two are indistinguishable by shape, so
`scripts/scan_sensitive.py`'s `licensing:hcpcs-shaped` detector fires on all 1,099 of them. That
is **not** a regex bug and the pattern is deliberately left alone: tightening it globally would
hide real `G0465`-style tokens in the other corpora. What disambiguates is context — these files
carry no procedure codes at all — so it is handled by a narrow, path-scoped allowlist entry in
`scripts/sensitive_baseline.toml`. Any *other* letter appearing in these files still fires,
which is the drift signal worth keeping.

Re-run before committing any change to this source:

```bash
# The script's own scan is the real check — it exits non-zero on a blocking marker.
uv run python scripts/download_part_d_spuf.py --normalize-only

# Whole-repo guardrail (secrets / PII / licensing) — should stay clean.
make scan
```

---

## Reference

- Dataset landing page:
  <https://data.cms.gov/provider-summary-by-type-of-service/medicare-part-d-prescribers/quarterly-prescription-drug-plan-formulary-pharmacy-network-and-pricing-information>
- Machine-readable catalog (where the rotating download URL is discovered):
  <https://data.cms.gov/data.json>
- Record layout (field definitions and value codes):
  <https://data.cms.gov/sites/default/files/2025-10/83da019f-fa24-483e-87de-cc089780a6a5/SPUFRecordLayout-2026.pdf>
- Methodology / file inventory:
  <https://data.cms.gov/sites/default/files/2023-10/98c7b019-7e9c-4c6d-a77c-f49f6c5b87e6/Methodology-SPUF-2024.pdf>
- Monthly variant (formulary + pharmacy network, no pricing):
  <https://data.cms.gov/provider-summary-by-type-of-service/medicare-part-d-prescribers/monthly-prescription-drug-plan-formulary-and-pharmacy-network-information>
- data.gov catalog entry:
  <https://catalog.data.gov/dataset/quarterly-prescription-drug-plan-formulary-pharmacy-network-and-pricing-information>
