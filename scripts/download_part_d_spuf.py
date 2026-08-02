#!/usr/bin/env python3
"""Download the Medicare Part D quarterly formulary files (SPUF) for the structured plan corpus.

This is the "Medicare Part D formulary files (bulk drug coverage)" source from docs/plan.md —
the quarterly Prescription Drug Plan Formulary, Pharmacy Network, and Pricing Information Public
Use File that CMS builds from the Medicare Plan Finder. Where exchange_puf holds the ACA
marketplace's per-plan facts, this source holds Medicare Part D's: which drug (by NDC) a plan
covers, on what cost-share tier, and whether it carries prior authorization, step therapy, or a
quantity limit. That is Phase 3's structured-API lane and Phase 5's formulary/drug-cost lookup.

Seven files are fetched by default:
  basic-drugs-formulary       NDC-level coverage: tier + prior auth / step therapy / qty limit
  excluded-drugs-formulary    supplemental coverage of otherwise-excluded drugs (enhanced plans)
  indication-based-coverage   drugs covered only for a specific FDA-approved indication
  beneficiary-cost            per-plan cost sharing by tier / days supply / pharmacy type
  insulin-beneficiary-cost    the same, for insulin, which has its own capped cost share
  plan-information            contract/plan identity, premium, deductible, FORMULARY_ID join key
  geographic-locator          COUNTY_CODE / MA / PDP region lookup table

  <out>/raw/part_d_spuf/catalog.json                    per-member manifest — committed
  <out>/raw/part_d_spuf/_meta.json                      fetch provenance — committed
  <out>/raw/part_d_spuf/<quarter>/<stem>.txt            pipe-delimited extract — GIT-IGNORED
  <out>/processed/part_d_spuf/<quarter>/<stem>.parquet  the derived mirror — GIT-IGNORED
  <out>/processed/part_d_spuf/sample/<stem>_<q>.csv     a small inspectable slice — committed

WHY RANGE-FETCH, NOT WHOLE-ZIP
-------------------------------
The published quarterly zip is 2.49 GB, but it is a *container of 15 nested per-file zips*, and
the six-part pharmacy-network file is 2.29 GB of that — 92% of the download for a file nothing
before Phase 5 reads. The seven files above total 9.4 MB.

So instead of fetching the container, this script reads the zip's central directory over HTTP
(a suffix range request plus one ~2 KB read), then issues one range request per wanted member
and inflates it locally. data.cms.gov advertises `accept-ranges: bytes` and answers with 206 —
verified against the live host, not assumed. Every member is checked against the CRC32 recorded
in the central directory before it is written, which is what makes a partial fetch exactly as
trustworthy as a whole-file download: a truncated or corrupted range fails loudly.

A range request that comes back 200 instead of 206 means the server ignored the Range header
and is about to stream 2.49 GB. That is treated as a hard error, never as a fallback.

WHY THE PROCESSED LAYER IS A MIRROR, NOT A MODEL
--------------------------------------------------
Same bargain exchange_puf makes, for the same reason: these columns are publisher-formatted
text, not numbers. PREMIUM is "35.60", MA_REGION_CODE is " " (a single space) for standalone
PDPs, and the insulin file's copay columns hold " " and "0.00" in the same column to mean
different things. normalize() writes every column as VARCHAR (`all_varchar=true`) and changes
nothing else. Deciding which of the four preferred/non-preferred/mail cost columns is "the"
copay for a plan is a Phase 5 modeling decision that needs real query requirements.

The one transformation the mirror does apply is a character-set transcode: the source is
Latin-1 and Parquet/CSV are written UTF-8, so "Óptimo Plus (PPO)" is the same three words in
both, stored as different bytes. That is a re-encoding, not an edit — no character is dropped
or replaced. Values are otherwise preserved exactly, including leading and trailing spaces.

WHY MANIFEST-NOT-BLOB
----------------------
One quarter is ~86 MB of pipe-delimited text, 58 MB of it the basic-drugs formulary file's
1.12M rows. None of that belongs in git history. catalog.json instead records each member's
source URL, byte offset and length inside the container, CRC32, sha256, and row/column counts,
which is what makes data/raw/part_d_spuf/ exactly reproducible by re-running this script — the
same bargain data/raw/medicare_pubs/pdf/ and data/raw/exchange_puf/ already make.

Three caveats worth knowing before you trust this data:
  * STATE and COUNTY_CODE are populated only for Medicare Advantage rows. All standalone PDP
    rows (CONTRACT_ID starting with S) leave them blank and are located by PDP_REGION_CODE
    instead — so filtering this source by state silently drops every standalone drug plan.
  * Plans suppressed by CMS appear in plan-information with PLAN_SUPPRESSED_YN = "Y" and in no
    other file, so a formulary join will legitimately find nothing for them.
  * The files are Latin-1, not UTF-8, and nothing in CMS's record layout says so — see
    SOURCE_ENCODING. The one place it bites is Spanish plan names in plan-information.

The pharmacy-network file is deliberately not wired up: 2.29 GB across six parts that need
reassembly, and nothing before Phase 5's provider/pharmacy-network checks reads it. The pricing
file (191 MB) is defined but opt-in via `--file pricing`.

Design goals (Phase 0 acceptance test):
  * Idempotent / resumable  - re-running sends If-Modified-Since; a 304 with the extracts
                              already on disk skips the quarter entirely. A failed member simply
                              isn't written, so a re-run retries it.
  * Decoupled parse         - the Parquet mirror and CSV sample are rebuilt from the raw text,
                              so you can re-parse (e.g. change the sample anchor) without
                              re-fetching.
  * Polite                  - configurable delay + retry-with-backoff on 429/5xx.

No API key is required. cms.gov rejects requests without a browser-like User-Agent (403), same
as medicare.gov — see download_medicare_pubs.py.
See docs/part_d_spuf_data.md for the full data guide, licensing, and column reference.

Usage (requests + duckdb are declared in pyproject.toml, so just `uv sync`):
  uv sync
  uv run python scripts/download_part_d_spuf.py                              # newest quarter
  uv run python scripts/download_part_d_spuf.py --file geographic-locator    # smoke test, 30 KB
  uv run python scripts/download_part_d_spuf.py --quarter 2026Q1             # a past quarter
  uv run python scripts/download_part_d_spuf.py --refresh                    # ignore 304
  uv run python scripts/download_part_d_spuf.py --normalize-only             # rebuild from raw
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import struct
import sys
import time
import zipfile
import zlib
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import requests

CATALOG_URL = "https://data.cms.gov/data.json"
DATASET_TITLE = (
    "Quarterly Prescription Drug Plan Formulary, Pharmacy Network, and Pricing Information"
)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 "
    "health-coverage-navigator/0.1 (+open-source RAG corpus builder)"
)
SOURCE = "part_d_spuf"

# CLI slug -> (normalized member name inside the container zip, output stem).
# The member name is matched after normalization (see normalize_member_name) because CMS's
# names carry irregular internal spacing: "basic drugs formulary file  PPUF_2026Q2.zip" has two
# spaces before the quarter tag, and the indication file's inner .txt is Title Case while its
# zip is lowercase. Never match these on an exact string.
# "key" is how the file is sliced for the committed sample: most are keyed by CONTRACT_ID, the
# formulary is shared across plans and so is keyed by FORMULARY_ID, and the geographic locator
# is a pure lookup table with no plan key at all.
FILES: dict[str, dict[str, str]] = {
    "basic-drugs-formulary": {
        "member": "basic drugs formulary file",
        "stem": "basic_drugs_formulary",
        "key": "formulary",
    },
    "beneficiary-cost": {
        "member": "beneficiary cost file",
        "stem": "beneficiary_cost",
        "key": "contract",
    },
    "excluded-drugs-formulary": {
        "member": "excluded drugs formulary file",
        "stem": "excluded_drugs_formulary",
        "key": "contract",
    },
    "geographic-locator": {
        "member": "geographic locator file",
        "stem": "geographic_locator",
        "key": "geo",
    },
    "indication-based-coverage": {
        "member": "indication based coverage formulary file",
        "stem": "indication_based_coverage",
        "key": "contract",
    },
    "insulin-beneficiary-cost": {
        "member": "insulin beneficiary cost file",
        "stem": "insulin_beneficiary_cost",
        "key": "contract",
    },
    "plan-information": {
        "member": "plan information",
        "stem": "plan_information",
        "key": "contract",
    },
    "pricing": {
        "member": "pricing file",
        "stem": "pricing",
        "key": "contract",
    },
}
# Everything except pricing (191 MB) — see the module docstring for why pharmacy-network is
# not in FILES at all.
DEFAULT_FILES = sorted(set(FILES) - {"pricing"})

# DuckDB's CSV reader treats an empty field as NULL by default, which would silently collapse
# this corpus's two distinct "no value" spellings — an empty field and a single space — onto the
# same representation, contradicting the whole point of a lossless mirror. read_csv's nullstr
# option can't be turned off (an empty list is rejected), so it's pointed at a string that cannot
# occur in a CMS file, which is the same as disabling it. quote/escape are disabled too: these
# files are pipe-delimited with no quoting convention, so a bare '"' inside a plan name is data,
# not the start of a quoted field. encoding is latin-1 — see SOURCE_ENCODING.
CSV_READ_OPTS = (
    "all_varchar=true, header=true, sample_size=-1, delim='|', quote='', escape='', "
    "encoding='latin-1', nullstr=['\x01__NEVER_NULL__\x01']"
)

# CMS publishes these files as Latin-1, not UTF-8, and does not say so anywhere in the record
# layout. Only plan-information actually exercises it: 0xD3 and 0xE1 appear in the Spanish plan
# names "Óptimo Plus (PPO)", "Freedom Máximo (HMO-POS)", and "Community y Más (HMO C-SNP)",
# which is enough to make a strict UTF-8 read raise. The other six files are pure ASCII, and
# ASCII is a subset of Latin-1, so decoding all seven this way is exact rather than merely
# tolerant — no errors="replace", no mojibake, no silently dropped characters.
SOURCE_ENCODING = "latin-1"

# How much of the zip's tail to pull looking for the End Of Central Directory record. The EOCD
# is 22 bytes plus a comment of at most 65,535, so this is the whole worst case.
EOCD_PROBE_BYTES = 66_000

# --- licensing guardrail ---------------------------------------------------- #
# Re-verified on every normalize run against the committed sample slice — see
# scan_sample_licensing(). A hit here means the sample would carry a redistributed AMA/ADA code
# table (or PII) into the public repo; the run must fail rather than write it.
# Duplicated from download_exchange_puf.py / download_medicare_ncd.py rather than imported:
# scripts/ is not a package. Change one, change the others.
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
    """GET a URL, retrying on 429 / 5xx with exponential backoff.

    206 and 304 join 200 as non-raising statuses: this script's whole download path is range
    requests, and its idempotency check is a conditional GET. Neither is retried.
    """
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = session.get(url, headers=headers, timeout=120, stream=stream)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"{resp.status_code} for {url}")
            if resp.status_code not in (200, 206, 304):
                resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_err = e
            if attempt < retries:
                time.sleep(backoff * (2**attempt))
    assert last_err is not None
    raise last_err


def fetch_range(
    session: requests.Session,
    url: str,
    byte_range: str,
    args: argparse.Namespace,
    headers: dict[str, str] | None = None,
) -> requests.Response:
    """GET one byte range. A 200 here means the server ignored Range — that is fatal.

    Falling through to a 200 would stream the whole 2.49 GB container into memory, which is
    precisely what this script exists to avoid. stream=True keeps that from happening before
    the status is checked.
    """
    h = dict(headers or {})
    h["Range"] = f"bytes={byte_range}"
    resp = fetch(session, url, args.retries, args.backoff, headers=h, stream=True)
    if resp.status_code == 304:
        resp.close()
        return resp
    if resp.status_code != 206:
        resp.close()
        raise RuntimeError(
            f"expected 206 for Range {byte_range} on {url}, got {resp.status_code} — "
            "the server ignored the Range header; refusing to download the whole container"
        )
    body = resp.content
    time.sleep(args.delay)
    return_len = int(resp.headers.get("Content-Length", len(body)))
    if len(body) != return_len:
        raise RuntimeError(f"short read for Range {byte_range} on {url}")
    return resp


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
def quarter_key(temporal: str) -> str | None:
    """'2026-04-01/2026-06-30' -> '2026Q2'.

    The quarter is taken from the period's *start* month, which is what CMS names the file
    after (the 2026-04-01/2026-06-30 distribution's members are all tagged PPUF_2026Q2).
    """
    m = re.match(r"(\d{4})-(\d{2})-\d{2}/", temporal or "")
    if not m:
        return None
    year, month = m.group(1), int(m.group(2))
    return f"{year}Q{(month - 1) // 3 + 1}"


def discover(session: requests.Session, args: argparse.Namespace) -> dict[str, dict]:
    """Resolve quarter -> distribution from the CMS DCAT catalog.

    Unlike exchange_puf's predictable {BASE}/{year}/{slug}-puf.zip, the quarterly URL embeds a
    rotating UUID (.../2026-07/64c8d9e1-.../SPUF_2026_20260701.zip), so it has to be looked up.
    """
    resp = fetch(session, CATALOG_URL, args.retries, args.backoff)
    time.sleep(args.delay)
    payload = resp.json()
    datasets = payload.get("dataset", []) if isinstance(payload, dict) else []
    dataset = next((d for d in datasets if d.get("title") == DATASET_TITLE), None)
    if dataset is None:
        raise RuntimeError(f"dataset not found in {CATALOG_URL}: {DATASET_TITLE!r}")

    quarters: dict[str, dict] = {}
    for dist in dataset.get("distribution", []):
        url = dist.get("downloadURL") or ""
        key = quarter_key(dist.get("temporal", ""))
        if not url or not key or key in quarters:
            continue
        quarters[key] = {
            "quarter": key,
            "url": url,
            "temporal": dist.get("temporal", ""),
            "modified": dist.get("modified", ""),
        }
    if not quarters:
        raise RuntimeError(f"no dated distributions found for {DATASET_TITLE!r}")
    print(f"Discovered {len(quarters)} quarters, newest {max(quarters)}.")
    return quarters


# --------------------------------------------------------------------------- #
# Download: zip central directory over HTTP, then one range per wanted member
# --------------------------------------------------------------------------- #
def normalize_member_name(name: str) -> str:
    """Member filename -> a stable key, with the quarter tag and spacing noise removed."""
    stem = re.sub(r"\s+", " ", Path(name).stem).strip().lower()
    return re.sub(r"\s*ppuf[_\s]*\d{4}\s*q\s*\d\s*$", "", stem).strip()


def parse_zip64_extra(extra: bytes, usize: int, csize: int, lho: int) -> tuple[int, int, int]:
    """Replace any 0xFFFFFFFF-saturated value from the ZIP64 extended information field.

    The field carries only the values that were actually saturated, in a fixed order:
    uncompressed size, compressed size, local-header offset, disk number.
    """
    pos = 0
    while pos < len(extra) - 3:
        header_id, size = struct.unpack("<HH", extra[pos : pos + 4])
        if header_id == 0x0001:
            vals = extra[pos + 4 : pos + 4 + size]
            off = 0
            if usize == 0xFFFFFFFF:
                usize = struct.unpack("<Q", vals[off : off + 8])[0]
                off += 8
            if csize == 0xFFFFFFFF:
                csize = struct.unpack("<Q", vals[off : off + 8])[0]
                off += 8
            if lho == 0xFFFFFFFF:
                lho = struct.unpack("<Q", vals[off : off + 8])[0]
            break
        pos += 4 + size
    return usize, csize, lho


def read_central_directory(
    session: requests.Session,
    url: str,
    args: argparse.Namespace,
    headers: dict[str, str] | None = None,
) -> tuple[dict[str, dict], str] | None:
    """Read the container's central directory over HTTP. Returns (members, last_modified).

    Returns None when the conditional request came back 304 — the container is unchanged.
    """
    resp = fetch_range(session, url, f"-{EOCD_PROBE_BYTES}", args, headers=headers)
    if resp.status_code == 304:
        return None
    tail = resp.content
    last_modified = resp.headers.get("Last-Modified", "")

    idx = tail.rfind(b"PK\x05\x06")
    if idx < 0:
        raise RuntimeError(f"no end-of-central-directory record in the last bytes of {url}")
    count, cd_size, cd_offset = struct.unpack("<HII", tail[idx + 10 : idx + 20])

    if count == 0xFFFF or cd_size == 0xFFFFFFFF or cd_offset == 0xFFFFFFFF:
        # ZIP64. The locator sits just before the EOCD and points at the real record.
        loc = tail.rfind(b"PK\x06\x07")
        if loc < 0:
            raise RuntimeError(f"ZIP64 sentinel without a locator record in {url}")
        eocd64_offset = struct.unpack("<IIQI", tail[loc : loc + 20])[2]
        rec = fetch_range(session, url, f"{eocd64_offset}-{eocd64_offset + 55}", args).content
        count = struct.unpack("<Q", rec[32:40])[0]
        cd_size = struct.unpack("<Q", rec[40:48])[0]
        cd_offset = struct.unpack("<Q", rec[48:56])[0]

    cd = fetch_range(session, url, f"{cd_offset}-{cd_offset + cd_size - 1}", args).content
    members: dict[str, dict] = {}
    pos = 0
    while pos < len(cd) - 4 and cd[pos : pos + 4] == b"PK\x01\x02":
        method = struct.unpack("<H", cd[pos + 10 : pos + 12])[0]
        crc, csize, usize = struct.unpack("<III", cd[pos + 16 : pos + 28])
        name_len, extra_len, comment_len = struct.unpack("<HHH", cd[pos + 28 : pos + 34])
        lho = struct.unpack("<I", cd[pos + 42 : pos + 46])[0]
        name = cd[pos + 46 : pos + 46 + name_len].decode("utf-8", "replace")
        extra = cd[pos + 46 + name_len : pos + 46 + name_len + extra_len]
        if 0xFFFFFFFF in (usize, csize, lho):
            usize, csize, lho = parse_zip64_extra(extra, usize, csize, lho)
        members[normalize_member_name(name)] = {
            "name": name,
            "method": method,
            "crc": crc,
            "csize": csize,
            "usize": usize,
            "lho": lho,
        }
        pos += 46 + name_len + extra_len + comment_len

    if len(members) != count:
        raise RuntimeError(f"central directory declares {count} members, parsed {len(members)}")
    return members, last_modified


def fetch_member(
    session: requests.Session, url: str, member: dict, args: argparse.Namespace
) -> tuple[bytes, int]:
    """Range-fetch one member and inflate it. Returns (bytes, data offset in the container).

    The CRC32 assertion is the whole reason a partial fetch can be trusted: a truncated or
    mis-offset range produces garbage that fails here rather than being written to disk.
    """
    local = fetch_range(session, url, f"{member['lho']}-{member['lho'] + 29}", args).content
    if local[:4] != b"PK\x03\x04":
        raise RuntimeError(f"no local file header at offset {member['lho']} for {member['name']}")
    name_len, extra_len = struct.unpack("<HH", local[26:30])
    start = member["lho"] + 30 + name_len + extra_len

    blob = fetch_range(session, url, f"{start}-{start + member['csize'] - 1}", args).content
    if member["method"] == 0:
        data = blob
    elif member["method"] == 8:
        data = zlib.decompressobj(-zlib.MAX_WBITS).decompress(blob)
    else:
        raise RuntimeError(
            f"unsupported compression method {member['method']} for {member['name']}"
        )

    if len(data) != member["usize"]:
        raise RuntimeError(
            f"{member['name']}: inflated {len(data):,} bytes, expected {member['usize']:,}"
        )
    if zlib.crc32(data) & 0xFFFFFFFF != member["crc"]:
        raise RuntimeError(f"{member['name']}: CRC32 mismatch — the fetched range is not intact")
    return data, start


def extract_inner_text(data: bytes, member_name: str, target: Path) -> Path:
    """Each container member is itself a zip holding exactly one pipe-delimited .txt.

    Writes to a .part file and returns it *without* renaming — the caller renames only once the
    file has been measured, so a member that fails mid-way leaves nothing a later run trusts.
    """
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".txt")]
        if len(names) != 1:
            raise RuntimeError(f"expected exactly one .txt in {member_name}, found {names}")
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_suffix(".txt.part")
        with zf.open(names[0]) as src, part.open("wb") as dst:
            while chunk := src.read(1 << 20):
                dst.write(chunk)
    return part


def download_one(
    session: requests.Session,
    url: str,
    quarter: str,
    slug: str,
    members: dict[str, dict],
    last_modified: str,
    quarter_dir: Path,
    args: argparse.Namespace,
) -> dict:
    """Fetch + extract one (quarter, file). Returns its catalog entry."""
    key = FILES[slug]["member"]
    member = members.get(key)
    if member is None:
        raise RuntimeError(
            f"member {key!r} not in the {quarter} container; available: {sorted(members)}"
        )

    data, start = fetch_member(session, url, member, args)
    txt_path = quarter_dir / f"{FILES[slug]['stem']}.txt"
    part = extract_inner_text(data, member["name"], txt_path)

    digest = hashlib.sha256()
    with part.open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    with part.open("rb") as f:
        column_count = len(f.readline().decode(SOURCE_ENCODING).rstrip("\r\n").split("|"))
        row_count = sum(1 for _ in f)
    part.replace(txt_path)

    return {
        "quarter": quarter,
        "file": slug,
        "url": url,
        "member": member["name"],
        "txt_filename": txt_path.name,
        "last_modified": last_modified,
        "byte_offset": start,
        "byte_length": member["csize"],
        "crc32": f"{member['crc']:08x}",
        "txt_bytes": txt_path.stat().st_size,
        "txt_sha256": digest.hexdigest(),
        "row_count": row_count,
        "column_count": column_count,
        "fetched_at": datetime.now(UTC).isoformat(),
    }


def download(
    session: requests.Session,
    raw_dir: Path,
    args: argparse.Namespace,
    quarters: dict[str, dict],
) -> dict:
    catalog_path = raw_dir / "catalog.json"
    catalog: dict[str, dict] = {}
    if catalog_path.exists():
        catalog = {
            f"{e['quarter']}/{e['file']}": e
            for e in json.loads(catalog_path.read_text(encoding="utf-8"))
        }

    fetched = skipped = failed = 0
    failures: list[dict] = []
    transferred = 0
    for quarter in args.quarter:
        dist = quarters.get(quarter)
        if dist is None:
            failed += len(args.file)
            failures.append({"quarter": quarter, "error": "no distribution for this quarter"})
            print(f"  FAILED {quarter}: not published (have {sorted(quarters)})", file=sys.stderr)
            continue

        quarter_dir = raw_dir / quarter
        quarter_dir.mkdir(parents=True, exist_ok=True)
        wanted = [(slug, quarter_dir / f"{FILES[slug]['stem']}.txt") for slug in args.file]

        prior = next(
            (
                catalog[f"{quarter}/{slug}"].get("last_modified")
                for slug in args.file
                if catalog.get(f"{quarter}/{slug}", {}).get("last_modified")
            ),
            "",
        )
        headers = {"If-Modified-Since": prior} if prior and not args.refresh else {}

        try:
            result = read_central_directory(session, dist["url"], args, headers=headers)
        except Exception as e:  # noqa: BLE001 - log & continue, re-run retries
            failed += len(args.file)
            failures.append({"quarter": quarter, "error": str(e)})
            print(f"  FAILED {quarter}: {e}", file=sys.stderr)
            continue

        # A file counts as already held only if it is BOTH on disk AND in the catalog. Disk
        # alone is not enough: a member that failed after extraction but before it was measured
        # leaves a file behind that no catalog entry vouches for, and trusting that would let a
        # 304 silently strand it forever.
        held = [slug for slug, path in wanted if path.exists() and f"{quarter}/{slug}" in catalog]
        if result is None:
            if len(held) == len(wanted):
                skipped += len(args.file)
                print(f"  up to date (304): {quarter} ({len(args.file)} files)")
                continue
            # Unchanged upstream, but something is missing locally — re-read unconditionally
            # so the missing member can still be fetched.
            missing = [slug for slug, _ in wanted if slug not in held]
            print(
                f"  {quarter}: unchanged upstream, but {len(missing)} file(s) not held: {missing}"
            )
            result = read_central_directory(session, dist["url"], args)
            if result is None:
                raise RuntimeError(
                    f"unconditional central-directory read still returned 304 for {quarter}"
                )
        members, last_modified = result

        for i, (slug, _path) in enumerate(wanted, 1):
            try:
                entry = download_one(
                    session, dist["url"], quarter, slug, members, last_modified, quarter_dir, args
                )
                catalog[f"{quarter}/{slug}"] = entry
                fetched += 1
                transferred += entry["byte_length"]
                print(
                    f"  [{i}/{len(wanted)}] fetched {quarter}/{slug} "
                    f"({entry['row_count']:,} rows, {entry['byte_length']:,} bytes over the wire)"
                )
            except Exception as e:  # noqa: BLE001 - log & continue, re-run retries
                failed += 1
                failures.append({"quarter": quarter, "file": slug, "error": str(e)})
                print(f"  [{i}/{len(wanted)}] FAILED {quarter}/{slug}: {e}", file=sys.stderr)

    entries = sorted(catalog.values(), key=lambda e: (e["quarter"], e["file"]))
    raw_dir.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")

    meta = {
        "fetched_at": datetime.now(UTC).isoformat(),
        "source_url": CATALOG_URL,
        "dataset": DATASET_TITLE,
        "quarters": sorted(args.quarter),
        "files": sorted(args.file),
        "fetched": fetched,
        "skipped_existing": skipped,
        "failed": failed,
        "bytes_over_the_wire": transferred,
        "tool": USER_AGENT,
        # Deliberately describes the licensing position without naming the restricted code
        # sets: this string is written into a committed file under data/, where the scanner's
        # blocking markers apply to the text itself. Prose about the guardrail must not trip
        # the guardrail — see data/README.md. The named-code-set version lives in this
        # script's docstring and docs/part_d_spuf_data.md, which are outside data/.
        "license_note": (
            "Public domain (https://www.usa.gov/government-works per CMS's DCAT catalog; "
            "accessLevel public, no rights statement). Drugs are identified only by NDC and "
            "RxCUI, so no proprietary procedure or dental code tables are carried, and no "
            "file fetched here holds beneficiary-level data. The pharmacy-network file, the "
            "only one with a provider-shaped identifier, is not fetched."
        ),
    }
    (raw_dir / "_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    failures_path = raw_dir / "failures.json"
    if failures:
        failures_path.write_text(json.dumps(failures, indent=2), encoding="utf-8")
    elif failures_path.exists():
        failures_path.unlink()

    print(
        f"\nDownload: {fetched} fetched, {skipped} up to date, {failed} failed "
        f"({transferred:,} bytes over the wire)."
    )
    if failed:
        print("Re-run the script to retry failed files (they were not saved).")
    return meta


# --------------------------------------------------------------------------- #
# Normalize: raw pipe-delimited text -> Parquet mirror (+ committed sample slice)
# --------------------------------------------------------------------------- #
def sql_literal(path: Path) -> str:
    """A Path made safe to splice into a SQL string literal.

    DuckDB's COPY statement does not reliably bind two `?` placeholders together (the
    destination path ends up misparsed as a read pattern — reproduced against duckdb 1.5.5),
    so source and destination are spliced directly instead. Both are always paths this script
    itself constructed under --out, never user-supplied SQL, so escaping the one metacharacter
    that matters (a literal `'`) is enough.
    """
    return str(path).replace("'", "''")


def sql_in_list(values: Iterable[str]) -> str:
    """Values from the data itself, rendered as a SQL IN list. Empty matches nothing."""
    quoted = ", ".join("'" + v.replace("'", "''") + "'" for v in sorted(set(values)))
    return quoted or "NULL"


def build_parquet(con: duckdb.DuckDBPyConnection, txt_path: Path, parquet_path: Path) -> int:
    """Pipe-delimited text -> Parquet, every column VARCHAR. Returns the row count.

    all_varchar=True is load-bearing, not incidental — see the module docstring for why typing
    is deliberately deferred rather than guessed here.
    """
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    con.execute(
        f"COPY (SELECT * FROM read_csv('{sql_literal(txt_path)}', {CSV_READ_OPTS})) "
        f"TO '{sql_literal(parquet_path)}' (FORMAT parquet, COMPRESSION zstd)"
    )
    row = con.execute(
        f"SELECT COUNT(*) FROM read_parquet('{sql_literal(parquet_path)}')"
    ).fetchone()
    assert row is not None
    return int(row[0])


def resolve_anchors(
    con: duckdb.DuckDBPyConnection,
    plan_info: Path,
    contract_keyed: dict[str, Path],
    explicit: list[str] | None,
) -> list[str]:
    """Pick the contracts the committed sample is sliced to.

    exchange_puf's `WHERE StateCode = ?` does not transfer: STATE is populated only for Medicare
    Advantage rows, every standalone PDP row leaves it blank (they are region-coded), and Alaska
    — that source's default — has zero rows here. So the anchor is a CONTRACT_ID instead.

    Two steps, because one rule alone does not produce a usable fixture:

      seed    - the smallest standalone-PDP (S) and local-MA (H) contract, so the fixture
                exercises both plan shapes. Plan-information row count is the size proxy.
      top-up  - any fetched file the seed would leave *empty* contributes its own smallest
                contract. Without this the fixture silently ships 0-row files: the
                indication-based-coverage file names only three contracts in the entire
                country, so no seed will ever hit it by chance, and excluded-drugs covers
                270 of 691. A 0-row fixture cannot test a join.

    The default is computed rather than hardcoded because a specific contract can stop being
    offered between quarters; the *rule* is what is stable. The chosen values are printed and
    recorded in _meta.json so the committed sample stays auditable.
    """
    if explicit:
        return sorted(set(explicit))

    counts: dict[str, int] = dict(
        con.execute(
            f"SELECT CONTRACT_ID, COUNT(*) "
            f"FROM read_csv('{sql_literal(plan_info)}', {CSV_READ_OPTS}) GROUP BY 1"
        ).fetchall()
    )

    def smallest(candidates: Iterable[str]) -> str | None:
        pool = sorted((counts[c], c) for c in candidates if c in counts)
        return pool[0][1] if pool else None

    anchors: list[str] = []
    for kind in ("S", "H"):
        pick = smallest(c for c in counts if c.startswith(kind))
        if pick:
            anchors.append(pick)

    for slug, path in sorted(contract_keyed.items()):
        present = {
            r[0]
            for r in con.execute(
                f"SELECT DISTINCT CONTRACT_ID FROM read_csv('{sql_literal(path)}', {CSV_READ_OPTS})"
            ).fetchall()
        }
        if present & set(anchors):
            continue
        pick = smallest(present)
        if pick:
            print(f"    + {pick} so {slug} is not an empty fixture")
            anchors.append(pick)

    return sorted(set(anchors))


def sample_predicate(slug: str, anchors: list[str], context: dict[str, set[str]]) -> str:
    """The WHERE clause that slices one file down to the anchor contracts.

    Sliced by FILES[slug]["key"]: most files carry CONTRACT_ID, the formulary is keyed by
    FORMULARY_ID (many plans share one formulary), and the geographic locator is a pure lookup
    table sliced by what the anchor plans actually reference — so nothing is committed whole.
    """
    key = FILES[slug]["key"]
    if key == "formulary":
        return f"FORMULARY_ID IN ({sql_in_list(context['formulary_ids'])})"
    if key == "geo":
        return (
            f"COUNTY_CODE IN ({sql_in_list(context['counties'])}) "
            f"OR PDP_REGION_CODE IN ({sql_in_list(context['pdp_regions'])})"
        )
    return f"CONTRACT_ID IN ({sql_in_list(anchors)})"


def build_sample(
    con: duckdb.DuckDBPyConnection, txt_path: Path, sample_path: Path, predicate: str
) -> int:
    """Write one file's anchor slice as comma-CSV (the raw files are pipe-delimited; DuckDB
    quotes the commas that appear in values such as 'CHA HMO, INC.').

    Regenerated on every normalize run rather than hand-curated, so a licensing regression in a
    future quarter is caught by scan_sample_licensing() instead of silently persisting in a
    stale, never-rechecked fixture.
    """
    sample_path.parent.mkdir(parents=True, exist_ok=True)
    con.execute(
        f"COPY (SELECT * FROM read_csv('{sql_literal(txt_path)}', {CSV_READ_OPTS}) "
        f"WHERE {predicate}) TO '{sql_literal(sample_path)}' (FORMAT csv, HEADER true)"
    )
    row = con.execute(
        f"SELECT COUNT(*) FROM read_csv('{sql_literal(sample_path)}', "
        f"all_varchar=true, header=true, sample_size=-1)"
    ).fetchone()
    assert row is not None
    return int(row[0])


def scan_sample_licensing(sample_paths: list[Path]) -> int:
    """Re-verify the public-repo guardrail against the sample slice we are about to commit.

    Mirrors scan_sample_licensing() in download_exchange_puf.py. The full files are never
    committed (see the module docstring), but the sample slice is, so it gets the same
    treatment: blocking markers fail the run, advisory markers are reported for human review.
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


def record_anchors(raw_dir: Path, anchors: dict[str, list[str]]) -> None:
    """Fold the resolved sample anchors into _meta.json.

    normalize() rather than download() owns this because --normalize-only must still leave an
    accurate record of which contracts the committed sample was sliced to.
    """
    meta_path = raw_dir / "_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    meta["sample_anchor_rule"] = (
        "smallest standalone-PDP (S) and local-MA (H) contract by plan-information row count, "
        "ties broken by CONTRACT_ID; override with --sample-contract"
    )
    meta["sample_contracts"] = anchors
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def normalize(raw_dir: Path, processed_dir: Path, args: argparse.Namespace) -> tuple[int, int]:
    """Rebuild the Parquet mirror + sample slice from raw/. Returns (files written, blocking)."""
    catalog_path = raw_dir / "catalog.json"
    if not catalog_path.exists():
        print("No raw catalog found. Run a download first.", file=sys.stderr)
        return 0, 0
    catalog = {
        f"{e['quarter']}/{e['file']}": e
        for e in json.loads(catalog_path.read_text(encoding="utf-8"))
    }

    written = 0
    sample_paths: list[Path] = []
    anchors_by_quarter: dict[str, list[str]] = {}
    con = duckdb.connect()
    try:
        for quarter in args.quarter:
            available = [slug for slug in args.file if f"{quarter}/{slug}" in catalog]
            for slug in args.file:
                if slug not in available:
                    print(
                        f"  no catalog entry for {quarter}/{slug} — run a download first",
                        file=sys.stderr,
                    )

            # The sample anchors come from plan-information, so everything else's slice depends
            # on it having been fetched.
            anchors: list[str] = []
            context: dict[str, set[str]] = {
                "formulary_ids": set(),
                "counties": set(),
                "pdp_regions": set(),
            }
            plan_info = None
            if "plan-information" in available:
                entry = catalog[f"{quarter}/plan-information"]
                candidate = raw_dir / quarter / entry["txt_filename"]
                if candidate.exists():
                    plan_info = candidate
            if plan_info is not None:
                contract_keyed = {
                    slug: raw_dir / quarter / catalog[f"{quarter}/{slug}"]["txt_filename"]
                    for slug in available
                    if FILES[slug]["key"] == "contract" and slug != "plan-information"
                }
                contract_keyed = {s: p for s, p in contract_keyed.items() if p.exists()}
                anchors = resolve_anchors(con, plan_info, contract_keyed, args.sample_contract)
                anchors_by_quarter[quarter] = anchors
                rows = con.execute(
                    f"SELECT FORMULARY_ID, COUNTY_CODE, PDP_REGION_CODE "
                    f"FROM read_csv('{sql_literal(plan_info)}', {CSV_READ_OPTS}) "
                    f"WHERE CONTRACT_ID IN ({sql_in_list(anchors)})"
                ).fetchall()
                for formulary_id, county, pdp_region in rows:
                    for bucket, value in (
                        ("formulary_ids", formulary_id),
                        ("counties", county),
                        ("pdp_regions", pdp_region),
                    ):
                        if (value or "").strip():
                            context[bucket].add(value)
                print(
                    f"  {quarter}: sample anchored to {', '.join(anchors)} "
                    f"({len(context['formulary_ids'])} formulary id(s))"
                )
            else:
                print(
                    f"  {quarter}: plan-information not available — writing the mirror but no "
                    "sample (the sample anchor is derived from it)",
                    file=sys.stderr,
                )

            for slug in available:
                entry = catalog[f"{quarter}/{slug}"]
                txt_path = raw_dir / quarter / entry["txt_filename"]
                if not txt_path.exists():
                    print(f"  missing (re-run download): {txt_path}", file=sys.stderr)
                    continue

                stem = FILES[slug]["stem"]
                parquet_path = processed_dir / quarter / f"{stem}.parquet"
                rows_written = build_parquet(con, txt_path, parquet_path)
                print(f"  {quarter}/{slug}: wrote {rows_written:,} rows -> {parquet_path}")
                written += 1

                if plan_info is None:
                    continue
                sample_path = processed_dir / "sample" / f"{stem}_{quarter}.csv"
                sample_rows = build_sample(
                    con, txt_path, sample_path, sample_predicate(slug, anchors, context)
                )
                print(f"    sample: {sample_rows:,} rows -> {sample_path}")
                sample_paths.append(sample_path)
    finally:
        con.close()

    if anchors_by_quarter:
        record_anchors(raw_dir, anchors_by_quarter)
    blocking = scan_sample_licensing(sample_paths) if sample_paths else 0
    return written, blocking


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def quarters_from_catalog(raw_dir: Path) -> list[str]:
    catalog_path = raw_dir / "catalog.json"
    if not catalog_path.exists():
        return []
    entries = json.loads(catalog_path.read_text(encoding="utf-8"))
    return sorted({e["quarter"] for e in entries})


def main() -> int:
    p = argparse.ArgumentParser(
        description="Download the Medicare Part D quarterly formulary files (SPUF)."
    )
    p.add_argument("--out", type=Path, default=Path("data"), help="Output root (default: ./data)")
    p.add_argument(
        "--quarter",
        action="append",
        dest="quarter",
        help="Quarter to fetch, e.g. 2026Q2; repeatable (default: the newest published)",
    )
    p.add_argument(
        "--file",
        choices=sorted(FILES),
        action="append",
        dest="file",
        help="SPUF file to fetch; repeatable (default: all but pricing)",
    )
    p.add_argument("--refresh", action="store_true", help="Ignore Last-Modified, force re-fetch")
    p.add_argument(
        "--normalize-only",
        action="store_true",
        help="Skip download; rebuild Parquet + sample from raw",
    )
    p.add_argument(
        "--sample-contract",
        action="append",
        dest="sample_contract",
        help="CONTRACT_ID to slice for the committed sample; repeatable "
        "(default: smallest PDP + smallest MA contract)",
    )
    p.add_argument(
        "--delay", type=float, default=0.5, help="Seconds between requests (default: 0.5)"
    )
    p.add_argument("--retries", type=int, default=3, help="Retries per request (default: 3)")
    p.add_argument("--backoff", type=float, default=1.0, help="Base backoff seconds (default: 1.0)")
    args = p.parse_args()

    args.file = sorted(set(args.file or DEFAULT_FILES))
    args.quarter = sorted({q.upper() for q in args.quarter}) if args.quarter else []

    raw_dir = args.out / "raw" / SOURCE
    processed_dir = args.out / "processed" / SOURCE

    if not args.normalize_only:
        session = make_session()
        quarters = discover(session, args)
        if not args.quarter:
            args.quarter = [max(quarters)]
        download(session, raw_dir, args, quarters)

    if not args.quarter:
        args.quarter = quarters_from_catalog(raw_dir)
        if not args.quarter:
            print("No raw catalog found. Run a download first.", file=sys.stderr)
            return 0

    _, blocking = normalize(raw_dir, processed_dir, args)
    if blocking:
        print(
            f"\n{blocking} blocking licensing hit(s) in the sample slice — do NOT commit "
            f"data/processed/{SOURCE}/sample/ until resolved.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
