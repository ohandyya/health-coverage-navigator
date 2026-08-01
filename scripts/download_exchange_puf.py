#!/usr/bin/env python3
"""Download the Health Insurance Exchange Public Use Files (Exchange PUFs) for the
structured plan corpus.

This is the "Health Insurance Exchange Public Use Files" source from docs/plan.md — the bulk
CSV/ZIP dumps behind the ACA marketplace, published annually by CMS's Center for Consumer
Information & Insurance Oversight (CCIIO). Where the other three corpora in this repo *explain*
coverage, the PUFs *are* the plan data: the per-plan facts (deductibles, MOOP, cost sharing,
service area) that back "is drug X covered under plan Y" and "find plans in ZIP 30076" —
Phase 3's structured-API lane and Phase 5's plan comparison.

Three tables are fetched by default:
  benefits-and-cost-sharing   per-plan, per-benefit cost sharing (copay/coinsurance/covered)
  plan-attributes             per-plan deductibles, MOOP, HSA eligibility, metal level, ...
  service-area                which counties/ZIPs each plan's ServiceAreaId covers

Service Area is included even though docs/plan.md names only the first two: without it there
is no way to answer the plan's own example question, "find plans in ZIP 30076" — Plan
Attributes carries a ServiceAreaId but not the ZIP-to-area mapping itself. It is also tiny
(~44 KB zipped), so the inclusion is nearly free.

  <out>/raw/exchange_puf/<year>/<slug>-puf.zip        the untouched download — GIT-IGNORED
  <out>/raw/exchange_puf/<year>/<Member>_PUF.csv      extracted CSV, fetch cache — GIT-IGNORED
  <out>/raw/exchange_puf/catalog.json                 per-file manifest — committed
  <out>/raw/exchange_puf/_meta.json                   fetch provenance — committed
  <out>/processed/exchange_puf/<year>/<table>.parquet the derived mirror — GIT-IGNORED
  <out>/processed/exchange_puf/sample/<table>_<ST>.csv a small inspectable slice — committed

WHY THE PROCESSED LAYER IS A MIRROR, NOT A MODEL
--------------------------------------------------
Every "numeric" PUF column is actually publisher-formatted text: `CopayInnTier1` holds values
like "Not Applicable", "No Charge", "$0.00", and "No Charge after deductible"; the 36
MEHB/DEHB/TEHB max-out-of-pocket columns hold "$450 " (note the trailing space) or empty; the
issuer actuarial value column holds "70.88%". normalize() writes every column as VARCHAR
(`all_varchar=True` — see build_parquet()) and changes nothing else about the values, on
purpose: guessing types now would silently coerce those sentinels differently across states
and plan years, and choosing *which* of the 36 MOOP columns is "the" MOOP for a plan is a
Phase 5 modeling decision that needs real query requirements, not something to invent here.
So processed/exchange_puf/ is a faithful, lossless, queryable columnar mirror of the CSVs —
not the "cleaned artifact the application actually consumes" that data/README.md promises for
the other three sources. See that file's Layout section for the amended definition, and
docs/exchange_puf_data.md for the full column inventory.

WHY MANIFEST-NOT-BLOB
----------------------
Plan year 2026 alone: Benefits & Cost Sharing is a 375 MB CSV (1,457,952 rows — this is the
file CMS itself says exceeds Excel's row limit), Plan Attributes is 32 MB (22,059 rows x 151
columns). None of that belongs in git history. catalog.json instead records each file's url,
ETag, Last-Modified, sha256, and row/column counts, which is what makes data/raw/exchange_puf/
exactly reproducible by re-running this script — the same manifest-not-blob bargain
data/raw/medicare_pubs/pdf/ makes, which data/README.md already names this source as the next
instance of.

CONDITIONAL DOWNLOAD
---------------------
download.cms.gov is Akamai-backed and answers conditional GETs: sending the ETag/Last-Modified
recorded in catalog.json gets a 304 with no body when the file hasn't changed (verified against
the live host — this is not assumed). That makes a re-run a real "is there anything new"
check, not just a re-download-then-hash-compare — stronger than the sha256-after-the-fact
idempotency the other download scripts use, because it costs nothing when nothing changed.

Design goals (Phase 0 acceptance test):
  * Idempotent / resumable  - re-running sends If-None-Match/If-Modified-Since; a 304 skips
                              the download and re-extract entirely.
  * Decoupled parse         - the Parquet mirror and CSV sample are rebuilt from the raw CSVs,
                              so you can re-parse (e.g. add a table) without re-fetching.
  * Polite                  - configurable delay + retry-with-backoff on 429/5xx.

No API key is required. cms.gov / download.cms.gov reject requests without a browser-like
User-Agent (403), same as medicare.gov — see download_medicare_pubs.py.
See docs/exchange_puf_data.md for the full data guide, licensing, and column reference.

Usage (requests + duckdb are declared in pyproject.toml, so just `uv sync`):
  uv sync
  uv run python scripts/download_exchange_puf.py                        # 2026, all 3 tables
  uv run python scripts/download_exchange_puf.py --table service-area   # smoke test, one table
  uv run python scripts/download_exchange_puf.py --year 2025 --year 2026
  uv run python scripts/download_exchange_puf.py --refresh              # ignore ETag, re-fetch
  uv run python scripts/download_exchange_puf.py --normalize-only       # rebuild from raw only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import requests

DOWNLOAD_BASE = "https://download.cms.gov/marketplace-puf"
DICTIONARY_BASE = "https://www.cms.gov/files/document"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 "
    "health-coverage-navigator/0.1 (+open-source RAG corpus builder)"
)
SOURCE = "exchange_puf"

# table slug -> (Parquet/output stem, data-dictionary PDF stem on cms.gov)
TABLES: dict[str, dict[str, str]] = {
    "benefits-and-cost-sharing": {
        "stem": "benefits_cost_sharing",
        "dictionary": "benefitscostsharing-datadictionary-py{yy}.pdf",
    },
    "plan-attributes": {
        "stem": "plan_attributes",
        "dictionary": "planattributes-datadictionary-py{yy}.pdf",
    },
    "service-area": {
        "stem": "service_area",
        "dictionary": "servicearea-datadictionary-py{yy}.pdf",
    },
}
DEFAULT_YEARS = [2026]

# DuckDB's CSV reader treats an empty field as NULL by default, which would silently
# collapse this corpus's two distinct "no value" spellings — an empty field and the literal
# text "Not Applicable" — onto the same representation, contradicting the whole point of a
# lossless mirror. read_csv's nullstr option can't be turned off (an empty list is rejected),
# so it's pointed at a string that cannot occur in a CMS PUF, which is the same as disabling
# it: no CSV field is ever equal to this sentinel, so nothing is ever nulled.
CSV_READ_OPTS = "all_varchar=true, header=true, sample_size=-1, nullstr=['\x01__NEVER_NULL__\x01']"

# --- licensing guardrail ---------------------------------------------------- #
# Re-verified on every normalize run against the committed sample slice — see
# scan_sample_licensing(). A hit here means the sample would carry a redistributed
# AMA/ADA code table (or PII) into the public repo; the run must fail rather than write it.
BLOCKING_MARKERS = {
    "copyright symbol": r"©",
    "all rights reserved": r"all rights reserved",
    "AMA copyright notice": r"American Medical Association",
    "ADA (dental)": r"American Dental Association",
    "CPT": r"\bCPT\b",
    "CDT": r"\bCDT\b",
    "HCPCS": r"\bHCPCS\b",
    "CDT-shaped dental code": r"\bD\d{4}\b",
}
ADVISORY_MARKERS = {
    "email-shaped string": r"[\w.+-]+@[\w-]+\.[a-z]{2,}",
}


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def fetch(
    session: requests.Session,
    url: str,
    retries: int,
    backoff: float,
    headers: dict[str, str] | None = None,
    stream: bool = False,
) -> requests.Response:
    """GET a URL, retrying on 429 / 5xx with exponential backoff. Never retries on 304."""
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = session.get(url, headers=headers, timeout=120, stream=stream)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"{resp.status_code} for {url}")
            if resp.status_code not in (200, 304):
                resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_err = e
            if attempt < retries:
                time.sleep(backoff * (2**attempt))
    assert last_err is not None
    raise last_err


# --------------------------------------------------------------------------- #
# Download
# --------------------------------------------------------------------------- #
def zip_url(year: int, slug: str) -> str:
    return f"{DOWNLOAD_BASE}/{year}/{slug}-puf.zip"


def dictionary_url(year: int, slug: str) -> str:
    yy = str(year)[-2:]
    return f"{DICTIONARY_BASE}/{TABLES[slug]['dictionary'].format(yy=yy)}"


def download_one(
    session: requests.Session,
    year_dir: Path,
    year: int,
    slug: str,
    catalog: dict[str, dict],
    args: argparse.Namespace,
) -> tuple[str, dict | None]:
    """Conditionally fetch + extract one (year, slug). Returns (status, catalog_entry)."""
    key = f"{year}/{slug}"
    url = zip_url(year, slug)
    prior = catalog.get(key)

    headers: dict[str, str] = {}
    if prior and not args.refresh:
        if prior.get("etag"):
            headers["If-None-Match"] = prior["etag"]
        if prior.get("last_modified"):
            headers["If-Modified-Since"] = prior["last_modified"]

    zip_path = year_dir / f"{slug}-puf.zip"
    resp = fetch(session, url, args.retries, args.backoff, headers=headers, stream=True)

    if resp.status_code == 304 and zip_path.exists():
        return "skipped", prior

    # A 200 with conditional headers set (server ignored them, or this is the first fetch)
    # still means "save it". Stream to a .part file so an interrupted run can't leave a
    # truncated zip that a later run trusts.
    part_path = zip_path.with_suffix(".zip.part")
    total = 0
    with part_path.open("wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 20):
            f.write(chunk)
            total += len(chunk)
    part_path.replace(zip_path)

    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise RuntimeError(f"expected exactly one CSV in {zip_path.name}, found {names}")
        member = names[0]
        zf.extract(member, year_dir)
        csv_path = year_dir / member
        if csv_path.name != member:  # zip stored a nested path; flatten it
            csv_path = year_dir / Path(member).name
            (year_dir / member).replace(csv_path)

    csv_bytes = csv_path.stat().st_size
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        row_count = sum(1 for _ in f) - 1  # minus header
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        column_count = len(f.readline().split(","))

    entry = {
        "year": year,
        "table": slug,
        "url": url,
        "csv_filename": csv_path.name,
        "etag": resp.headers.get("ETag", ""),
        "last_modified": resp.headers.get("Last-Modified", ""),
        "zip_bytes": total,
        "csv_bytes": csv_bytes,
        "row_count": row_count,
        "column_count": column_count,
        "fetched_at": datetime.now(UTC).isoformat(),
    }
    return "fetched", entry


def download(session: requests.Session, raw_dir: Path, args: argparse.Namespace) -> dict:
    catalog_path = raw_dir / "catalog.json"
    catalog: dict[str, dict] = {}
    if catalog_path.exists():
        catalog = {
            f"{e['year']}/{e['table']}": e
            for e in json.loads(catalog_path.read_text(encoding="utf-8"))
        }

    fetched = skipped = failed = 0
    failures: list[dict] = []
    jobs = [(year, slug) for year in args.year for slug in args.table]
    for i, (year, slug) in enumerate(jobs, 1):
        year_dir = raw_dir / str(year)
        year_dir.mkdir(parents=True, exist_ok=True)
        try:
            status, entry = download_one(session, year_dir, year, slug, catalog, args)
            if entry is not None:
                catalog[f"{year}/{slug}"] = entry
            if status == "fetched" and entry is not None:
                fetched += 1
                print(
                    f"  [{i}/{len(jobs)}] fetched {year}/{slug} "
                    f"({entry['row_count']:,} rows, {entry['csv_bytes']:,} bytes)"
                )
            else:
                skipped += 1
                print(f"  [{i}/{len(jobs)}] up to date (304): {year}/{slug}")
            time.sleep(args.delay)
        except Exception as e:  # noqa: BLE001 - log & continue, re-run retries
            failed += 1
            failures.append({"year": year, "table": slug, "error": str(e)})
            print(f"  [{i}/{len(jobs)}] FAILED {year}/{slug}: {e}", file=sys.stderr)

    entries = sorted(catalog.values(), key=lambda e: (e["year"], e["table"]))
    catalog_path.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")

    meta = {
        "fetched_at": datetime.now(UTC).isoformat(),
        "source_url": DOWNLOAD_BASE,
        "years": sorted(args.year),
        "tables": sorted(args.table),
        "fetched": fetched,
        "skipped_existing": skipped,
        "failed": failed,
        "tool": USER_AGENT,
        "license_note": (
            "Public domain (CMS Exchange PUF Disclaimer-User Agreement). No redistribution "
            "restriction. The agreement requires that altered/derived data not be presented "
            "as CMS data — the Parquet mirror under processed/ is documented as derived, not "
            "as the CMS file itself."
        ),
    }
    (raw_dir / "_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    failures_path = raw_dir / "failures.json"
    if failures:
        failures_path.write_text(json.dumps(failures, indent=2), encoding="utf-8")
    elif failures_path.exists():
        failures_path.unlink()

    print(f"\nDownload: {fetched} fetched, {skipped} up to date, {failed} failed.")
    if failed:
        print("Re-run the script to retry failed tables.")
    return meta


# --------------------------------------------------------------------------- #
# Normalize: raw CSV -> Parquet mirror (+ committed sample slice)
# --------------------------------------------------------------------------- #
def sql_literal(path: Path) -> str:
    """A Path made safe to splice into a SQL string literal.

    DuckDB's COPY statement does not reliably bind two `?` placeholders together (the
    destination path ends up misparsed as a read pattern — reproduced against duckdb 1.5.5),
    so source and destination are spliced directly instead. Both are always paths this script
    itself constructed under --out, never user-supplied SQL, so escaping the one
    metacharacter that matters (a literal `'`) is enough.
    """
    return str(path).replace("'", "''")


def build_parquet(csv_path: Path, parquet_path: Path) -> int:
    """CSV -> Parquet, every column VARCHAR. Returns the row count.

    all_varchar=True is load-bearing, not incidental: every "numeric" PUF column is
    publisher-formatted text ("$450 ", "Not Applicable", "70.88%", "No Charge after
    deductible"), and letting DuckDB infer types would silently coerce those sentinels
    differently across states and years. See the module docstring for why typing is
    deliberately deferred to Phase 5 rather than guessed here. CSV_READ_OPTS's nullstr
    override matters just as much: DuckDB's reader treats an empty CSV field as NULL by
    default, which is not what "lossless" means — the mirror should hold exactly what CMS
    published, an empty string, not SQL's separate concept of "no value at all." See
    CSV_READ_OPTS for how that default is turned off.
    """
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(
        f"COPY (SELECT * FROM read_csv('{sql_literal(csv_path)}', {CSV_READ_OPTS})) "
        f"TO '{sql_literal(parquet_path)}' (FORMAT parquet, COMPRESSION zstd)"
    )
    row = con.execute(
        f"SELECT COUNT(*) FROM read_parquet('{sql_literal(parquet_path)}')"
    ).fetchone()
    con.close()
    assert row is not None
    return int(row[0])


def scan_sample_licensing(sample_paths: list[Path]) -> int:
    """Re-verify the public-repo guardrail against the sample slice we are about to commit.

    Mirrors scan_licensing() in download_medicare_ncd.py. The full PUFs are never committed
    (see the module docstring), but the sample slice is, so it gets the same treatment:
    blocking markers fail the run, advisory markers are reported for human review.
    """
    print("\nLicensing scan (public-repo guardrail) on the committed sample:")
    blocking = 0
    haystack = [(p.name, p.read_text(encoding="utf-8", errors="replace")) for p in sample_paths]

    for label, pattern in BLOCKING_MARKERS.items():
        hits = [(name, m) for name, txt in haystack for m in re.finditer(pattern, txt, re.I)]
        if hits:
            blocking += len(hits)
            print(f"  ERROR  {label}: {len(hits)} hit(s) — DO NOT COMMIT", file=sys.stderr)
            for name, m in hits[:5]:
                print(f"           {name}: ...{m.group(0)[:120]}...", file=sys.stderr)
        else:
            print(f"  ok     {label}: none")

    for label, pattern in ADVISORY_MARKERS.items():
        hits = [(name, txt, m) for name, txt in haystack for m in re.finditer(pattern, txt, re.I)]
        if not hits:
            print(f"  ok     {label}: none")
            continue
        print(f"  info   {label}: {len(hits)} occurrence(s) — review if surprising")
        for name, txt, m in hits[:5]:
            start = max(0, m.start() - 60)
            context = re.sub(r"\s+", " ", txt[start : m.end() + 60])
            print(f"           {name}: ...{context}...")
    return blocking


def build_sample(csv_path: Path, sample_path: Path, state: str) -> int:
    """Filter one table's CSV to a single state, written as CSV (columns 1:1 with the CMS
    data dictionary). Regenerated on every normalize run rather than hand-curated, so a
    licensing regression in a new plan year is caught by scan_sample_licensing() instead of
    silently persisting in a stale, never-rechecked fixture.
    """
    sample_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(
        f"COPY (SELECT * FROM read_csv('{sql_literal(csv_path)}', {CSV_READ_OPTS}) "
        f"WHERE StateCode = ?) TO '{sql_literal(sample_path)}' (FORMAT csv, HEADER true)",
        [state],
    )
    row = con.execute(
        f"SELECT COUNT(*) FROM read_csv('{sql_literal(sample_path)}', {CSV_READ_OPTS})"
    ).fetchone()
    con.close()
    assert row is not None
    return int(row[0])


def normalize(raw_dir: Path, processed_dir: Path, args: argparse.Namespace) -> tuple[int, int]:
    """Rebuild the Parquet mirror + sample slice from raw/. Returns (tables written, blocking)."""
    catalog_path = raw_dir / "catalog.json"
    if not catalog_path.exists():
        print("No raw catalog found. Run a download first.", file=sys.stderr)
        return 0, 0
    catalog = {
        f"{e['year']}/{e['table']}": e for e in json.loads(catalog_path.read_text(encoding="utf-8"))
    }

    written = 0
    sample_paths: list[Path] = []
    for year in args.year:
        for slug in args.table:
            entry = catalog.get(f"{year}/{slug}")
            if entry is None:
                print(
                    f"  no catalog entry for {year}/{slug} — run a download first",
                    file=sys.stderr,
                )
                continue
            csv_path = raw_dir / str(year) / entry["csv_filename"]
            if not csv_path.exists():
                print(f"  missing (re-run download): {csv_path}", file=sys.stderr)
                continue

            stem = TABLES[slug]["stem"]
            parquet_path = processed_dir / str(year) / f"{stem}.parquet"
            rows = build_parquet(csv_path, parquet_path)
            print(f"  {year}/{slug}: wrote {rows:,} rows -> {parquet_path}")
            written += 1

            sample_path = processed_dir / "sample" / f"{stem}_{args.sample_state}_{year}.csv"
            sample_rows = build_sample(csv_path, sample_path, args.sample_state)
            print(f"    sample ({args.sample_state}): {sample_rows:,} rows -> {sample_path}")
            sample_paths.append(sample_path)

    blocking = scan_sample_licensing(sample_paths) if sample_paths else 0
    return written, blocking


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main() -> int:
    p = argparse.ArgumentParser(
        description="Download the Exchange PUFs for the structured plan corpus."
    )
    p.add_argument("--out", type=Path, default=Path("data"), help="Output root (default: ./data)")
    p.add_argument(
        "--year",
        type=int,
        action="append",
        dest="year",
        help="Plan year to fetch; repeatable (default: 2026)",
    )
    p.add_argument(
        "--table",
        choices=sorted(TABLES),
        action="append",
        dest="table",
        help="PUF table to fetch; repeatable (default: all three)",
    )
    p.add_argument(
        "--refresh", action="store_true", help="Ignore ETag/Last-Modified, force re-fetch"
    )
    p.add_argument(
        "--normalize-only",
        action="store_true",
        help="Skip download; rebuild Parquet + sample from raw",
    )
    p.add_argument(
        "--sample-state",
        default="AK",
        help="State to slice for the committed sample (default: AK)",
    )
    p.add_argument(
        "--delay", type=float, default=0.5, help="Seconds between requests (default: 0.5)"
    )
    p.add_argument("--retries", type=int, default=3, help="Retries per request (default: 3)")
    p.add_argument("--backoff", type=float, default=1.0, help="Base backoff seconds (default: 1.0)")
    args = p.parse_args()

    args.year = sorted(set(args.year or DEFAULT_YEARS))
    args.table = sorted(set(args.table or TABLES.keys()))

    raw_dir = args.out / "raw" / SOURCE
    processed_dir = args.out / "processed" / SOURCE

    if not args.normalize_only:
        session = make_session()
        download(session, raw_dir, args)

    _, blocking = normalize(raw_dir, processed_dir, args)
    if blocking:
        print(
            f"\n{blocking} blocking licensing hit(s) in the sample slice — do NOT commit "
            "data/processed/exchange_puf/sample/ until resolved.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
