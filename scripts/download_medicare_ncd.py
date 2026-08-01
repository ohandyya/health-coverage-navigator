#!/usr/bin/env python3
"""Download the Medicare Coverage Database's National Coverage Determinations for RAG.

This is the "Medicare Coverage Database — NCDs only" source from docs/plan.md. An NCD is
CMS's nationwide decision on whether Medicare covers a service; it binds every MAC, which
makes it the *operative coverage rule* rather than a consumer explanation of one. That is
the source behind this project's canonical reference-lane question, "is this treatment
typically covered?"

  <out>/raw/medicare_ncd/report.json          the NCD listing report — the discovery index
  <out>/raw/medicare_ncd/ncd/<sect>_v<n>.json one untouched API response per NCD
  <out>/raw/medicare_ncd/_meta.json           fetch provenance (timestamp, counts)
  <out>/processed/medicare_ncd/corpus.jsonl   normalized one-record-per-NCD corpus

Unlike medicare_pubs, nothing here is git-ignored: 2.6 MB raw + 1.8 MB processed
(345 NCDs, ~950 KB of actual policy text), so the raw layer is committed whole.

WHY THE API AND NOT THE BULK ZIPs
---------------------------------
The MCD downloads page (cms.gov/medicare-coverage-database/downloads/downloads.aspx) 403s
non-browser clients and gates its ZIPs behind an in-browser license-acceptance click that
bundles National and Local coverage data together. The MCD **Coverage API** is keyless
JSON — and, decisively, CMS enforces our licensing boundary at the API layer:

  /v1/data/ncd/, /v1/reports/national-coverage-*   -> 200, no auth
  /v1/data/lcd/..., /v1/data/article/...           -> 401, AMA/ADA/AHA license token required

LCDs and Billing/Coding Articles embed AMA-copyrighted CPT and ADA-copyrighted CDT code
tables and may not enter this public repo (see the guardrail in CLAUDE.md). So "NCDs only"
is not a rule this script has to police by hand — it is exactly the set of endpoints that
answer without a license token. **Never call /v1/metadata/license-agreement/**: not holding
a token is the second line of defence, and a token would unlock precisely the data we are
forbidden to vendor.

That boundary is verified, not assumed. `scan_licensing()` re-checks the normalized corpus
on every run. Measured over all 345 NCDs: the `ama_statement` field is empty in every one,
and there is no ©, no "all rights reserved", no CDT, and no code table anywhere. What there
*is* — and what the scan reports rather than pretends away — is a handful of code mentions
in narrative prose ("changed CPT code", "add HCPCS code G0465"), nearly all of it inside
revision histories. Prose references in a US-government work are not a redistributed code
table, so they are fine; they are printed for human review, not treated as violations.

Two more things worth knowing before you trust this data:

  * `revision_history` is deliberately **kept out of `text`**. It is retrieval noise
    (transmittal numbers, change-request numbers, rescinded-and-replaced chains) and it is
    where nearly all the code-adjacent prose lives. It stays as its own record field.
  * NCDs are scoped by **effective date, not plan year** — there is no `plan_year` here.
    An NCD applies from its effective date until superseded, so date-pinning a coverage
    question means comparing against `effective_date`, not against a plan year. But 75 of
    the 345 are "longstanding" determinations with no posted effective date at all: the
    API returns a sentence where the date should be, so `effective_date` is null for them
    and `effective_date_note` carries the explanation. Any date filter must handle null.

Design goals (Phase 0 acceptance test):
  * Idempotent / resumable  - re-running skips NCDs already on disk (unless --refresh);
                              failed fetches simply aren't saved, so a re-run retries them.
  * Decoupled parse         - the normalized corpus.jsonl is rebuilt from the raw layer,
                              so you can re-parse (e.g. change cleaning) without re-fetching.
  * Polite                  - configurable delay + retry-with-backoff on 429/5xx.

No API key is required (CMS removed that requirement in February 2024).
See docs/medicare_ncd_data.md for the full data guide.

Usage (requests + beautifulsoup4 are declared in pyproject.toml, so just `uv sync`):
  uv sync
  uv run python scripts/download_medicare_ncd.py                  # full download into ./data
  uv run python scripts/download_medicare_ncd.py --limit 5        # smoke test (first 5 NCDs)
  uv run python scripts/download_medicare_ncd.py --refresh        # re-fetch everything
  uv run python scripts/download_medicare_ncd.py --normalize-only # rebuild corpus.jsonl from raw
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

API = "https://api.coverage.cms.gov/v1"
REPORT_URL = f"{API}/reports/national-coverage-ncd/"
NCD_URL = f"{API}/data/ncd/"
# The human-facing MCD page for an NCD — what a citation should link to. The report's own
# `url` field is an API path (/data/ncd?ncdid=...), not something a person can open.
VIEW_URL = "https://www.cms.gov/medicare-coverage-database/view/ncd.aspx?ncdid={id}&ncdver={ver}"
USER_AGENT = "health-coverage-navigator/0.1 (+open-source RAG corpus builder)"
SOURCE = "medicare_ncd"

# Sections of an NCD, in the order the MCD website presents them, mapped to the heading
# written into `text`. Empty sections are skipped; `revision_history` is deliberately
# absent (see the module docstring).
TEXT_SECTIONS = [
    ("benefit_category", "Benefit Category"),
    ("item_service_description", "Item/Service Description"),
    ("indications_limitations", "Indications and Limitations of Coverage"),
    ("reasons_for_denial", "Reasons for Denial"),
    ("other_text", "Other"),
    ("cross_reference", "Cross-reference"),
]

# --- licensing guardrail ---------------------------------------------------- #
# A hit on any of these would mean a genuine third-party license notice reached the
# corpus, which has never happened and would be a regression. Fail the run.
BLOCKING_MARKERS = {
    "copyright symbol": r"©",
    "all rights reserved": r"all rights reserved",
    "AMA copyright notice": r"American Medical Association.{0,40}(copyright|rights)",
    "ADA (dental)": r"American Dental Association",
    "CDT": r"\bCDT\b",
}
# These appear in NCD prose as ordinary narrative references, not as code tables. Report
# them with context so a human can eyeball each one; do not fail on them.
ADVISORY_MARKERS = {
    "CPT mentioned in prose": r"\bCPT\b",
    "HCPCS mentioned in prose": r"\bHCPCS\b",
    "HCPCS Level II-shaped token": r"\b[A-Z]\d{4}\b",
}


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return s


def fetch_json(
    session: requests.Session, url: str, params: dict | None, retries: int, backoff: float
) -> dict:
    """GET a URL and parse JSON, retrying on 429 / 5xx with exponential backoff.

    A 401 is never retried: it means we asked for license-gated data, which is a bug in
    the caller, not a transient failure.
    """
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = session.get(url, params=params, timeout=60)
            if resp.status_code == 401:
                raise RuntimeError(
                    f"401 for {url} — this endpoint needs an AMA/ADA/AHA license token, "
                    "which means it serves data blocked from this public repo. Do not add one."
                )
            if resp.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"{resp.status_code} for {url}")
            resp.raise_for_status()
            result = resp.json()
            if not isinstance(result, dict):
                raise RuntimeError(f"Expected JSON object at {url}, got {type(result)}")
            return result
        except RuntimeError:
            raise
        except (requests.RequestException, json.JSONDecodeError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(backoff * (2**attempt))
    assert last_err is not None
    raise last_err


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
def slugify(value: str) -> str:
    """Filesystem-safe slug. Dots survive — an NCD section number is `30.3`, not `30_3`."""
    return re.sub(r"[^a-zA-Z0-9._-]", "_", value) or "unknown"


def raw_filename(row: dict) -> str:
    """Raw-layer filename for one NCD, keyed on the number a citation actually names."""
    return f"{slugify(str(row['document_display_id']))}_v{row['document_version']}.json"


def discover(session: requests.Session, args: argparse.Namespace) -> list[dict]:
    """Fetch the NCD listing report — one row per NCD at its current version.

    The report returns every NCD in a single response today (345 rows, empty `next_token`),
    but the API documents a `next_token` cursor, so follow it rather than assuming one page.
    """
    rows: list[dict] = []
    token = ""
    for _ in range(args.max_pages):
        params = {"next_token": token} if token else None
        payload = fetch_json(session, REPORT_URL, params, args.retries, args.backoff)
        page = payload.get("data") or []
        rows.extend(page)
        print(f"  report page: {len(page):3d} NCDs (running total {len(rows)})")
        token = (payload.get("meta") or {}).get("next_token") or ""
        if not token or not page:
            break
        time.sleep(args.delay)
    else:
        print(
            f"  stopped at --max-pages={args.max_pages}; report may be truncated", file=sys.stderr
        )

    # Defensive: one row per document_id at its highest version, in citation order.
    latest: dict[str, dict] = {}
    for row in rows:
        key = str(row["document_id"])
        if key not in latest or int(row["document_version"]) > int(latest[key]["document_version"]):
            latest[key] = row
    return sorted(latest.values(), key=lambda r: str(r.get("document_display_id", "")))


# --------------------------------------------------------------------------- #
# Download
# --------------------------------------------------------------------------- #
def download(session: requests.Session, raw_dir: Path, args: argparse.Namespace) -> dict:
    ncd_dir = raw_dir / "ncd"
    ncd_dir.mkdir(parents=True, exist_ok=True)

    print("Discovering NCDs from the National Coverage NCD report:")
    rows = discover(session, args)
    print(f"\nDiscovered {len(rows)} NCDs.\n")

    # The report is the discovery record and the normalizer's work list — save it in full
    # even under --limit, so a smoke test can't quietly shrink a complete corpus. The
    # unfetched rows then show up as "missing" at normalize time, which is the truth.
    (raw_dir / "report.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    targets = rows[: args.limit] if args.limit else rows

    fetched = skipped = failed = 0
    failures: list[dict] = []
    for i, row in enumerate(targets, 1):
        target = ncd_dir / raw_filename(row)
        if target.exists() and not args.refresh:
            skipped += 1
            continue
        try:
            payload = fetch_json(
                session,
                NCD_URL,
                {"ncdid": row["document_id"], "ncdver": row["document_version"]},
                args.retries,
                args.backoff,
            )
            data = payload.get("data") or []
            if not data:
                raise RuntimeError("empty data array")
            target.write_text(json.dumps(data[0], indent=2, ensure_ascii=False), encoding="utf-8")
            fetched += 1
            print(
                f"  [{i}/{len(targets)}] fetched NCD {row['document_display_id']} — {row['title']}"
            )
            time.sleep(args.delay)
        except Exception as e:  # noqa: BLE001 - log & continue, re-run retries
            failed += 1
            failures.append({"document_display_id": row["document_display_id"], "error": str(e)})
            print(
                f"  [{i}/{len(targets)}] FAILED {row['document_display_id']}: {e}", file=sys.stderr
            )

    meta = {
        "fetched_at": datetime.now(UTC).isoformat(),
        "source_url": REPORT_URL,
        "discovered_count": len(rows),
        "fetched": fetched,
        "skipped_existing": skipped,
        "failed": failed,
        "tool": USER_AGENT,
        "license_note": (
            "NCDs only. LCD and Billing/Coding Article endpoints require an AMA/ADA/AHA "
            "license token and are blocked from this public repo; no token was requested."
        ),
    }
    (raw_dir / "_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    failures_path = raw_dir / "failures.json"
    if failures:
        failures_path.write_text(json.dumps(failures, indent=2), encoding="utf-8")
    elif failures_path.exists():
        failures_path.unlink()

    print(f"\nDownload: {fetched} fetched, {skipped} skipped, {failed} failed.")
    if failed:
        print("Re-run the script to retry failed NCDs (they were not saved).")
    return meta


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def field_to_text(value: Any) -> str:
    """Turn one API text field into clean, newline-separated plain text.

    The API escapes its HTML once (`&lt;p&gt;`, `&sol;` for a slash), so a single unescape
    pass yields real — if slightly malformed, e.g. `<p><strong>x</p></strong>` — markup,
    which BeautifulSoup is happy to repair.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    soup = BeautifulSoup(html.unescape(raw), "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    lines = [ln.strip() for ln in soup.get_text(separator="\n").splitlines()]
    return "\n".join(ln for ln in lines if ln)


DATE_RE = re.compile(r"\d{1,2}/\d{1,2}/\d{4}")


def clean_date(value: Any) -> str | None:
    """Return MM/DD/YYYY, or None for anything that is not actually a date.

    Dates arrive as MM/DD/YYYY, or the literal 'N/A' for an open-ended NCD — but 75 of
    the 345 NCDs put a *sentence* in `effective_date`: "This is a longstanding national
    coverage determination. The effective date of this version has not been posted."
    Passing that through would leave prose in a field every downstream date comparison
    assumes is a date. It becomes None here, and `date_note()` keeps the explanation.
    """
    text = str(value or "").strip()
    return text if DATE_RE.fullmatch(text) else None


def date_note(value: Any) -> str:
    """The API's prose explanation when a date field holds a sentence instead of a date."""
    text = str(value or "").strip()
    if not text or text.upper() == "N/A" or DATE_RE.fullmatch(text):
        return ""
    return re.sub(r"\s+", " ", text)


def build_text(rec: dict) -> str:
    """Compose the retrievable body: the policy sections, under `## ` headings.

    Headings are written even though the API delivers the sections separately, so the
    Phase 0 chunker can split on them and keep the section name as a citation label.
    """
    parts: list[str] = []
    for key, heading in TEXT_SECTIONS:
        body = field_to_text(rec.get(key))
        if body:
            parts.append(f"## {heading}\n{body}")
    return "\n\n".join(parts)


def normalize_record(row: dict, rec: dict) -> dict:
    """Flatten one report row + its detail response into a corpus record.

    `id`, `source`, `url`, `title`, `bite`, and `text` deliberately match the field names
    used by the healthcare_gov and medicare_pubs corpora, so a later cross-source chunker
    can treat all three uniformly.
    """
    section_number = str(row.get("document_display_id") or "")
    return {
        "id": f"ncd_{section_number}_v{row['document_version']}",
        "source": SOURCE,
        "url": VIEW_URL.format(id=row["document_id"], ver=row["document_version"]),
        "title": str(rec.get("title") or row.get("title") or "").strip(),
        # NCDs carry no editorial summary. The field is kept, and always empty, so every
        # corpus in data/processed/ has the same shape.
        "bite": "",
        "section_number": section_number,
        "chapter": str(row.get("chapter") or ""),
        "ncd_id": str(row["document_id"]),
        "ncd_version": int(row["document_version"]),
        "publication_number": str(rec.get("publication_number") or ""),
        "benefit_category": field_to_text(rec.get("benefit_category")),
        "effective_date": clean_date(rec.get("effective_date")),
        # Non-empty only when `effective_date` is null because CMS posted prose instead.
        "effective_date_note": date_note(rec.get("effective_date")),
        "effective_end_date": clean_date(rec.get("effective_end_date")),
        "implementation_date": clean_date(rec.get("implementation_date")),
        "last_updated": clean_date(row.get("last_updated")),
        "is_lab": str(row.get("is_lab") or "0") == "1",
        "transmittal_number": str(rec.get("transmittal_number") or ""),
        "transmittal_url": str(rec.get("transmittal_url") or ""),
        # Kept for provenance, kept OUT of `text` — see the module docstring.
        "revision_history": field_to_text(rec.get("revision_history")),
        "text": build_text(rec),
    }


def scan_licensing(records: list[dict]) -> int:
    """Re-verify the public-repo guardrail against the text we are about to commit.

    Returns the number of blocking hits. Advisory hits are printed, not counted — NCD
    prose legitimately *mentions* codes ("changed CPT code") without carrying a code table,
    and a scan that treated those as violations would just get switched off.
    """
    haystack = [(r["id"], f"{r['text']}\n{r['revision_history']}") for r in records]

    blocking = 0
    print("\nLicensing scan (public-repo guardrail):")
    for label, pattern in BLOCKING_MARKERS.items():
        hits = [(rid, m) for rid, txt in haystack for m in re.finditer(pattern, txt, re.I)]
        if hits:
            blocking += len(hits)
            print(f"  ERROR  {label}: {len(hits)} hit(s) — DO NOT COMMIT", file=sys.stderr)
            for rid, m in hits[:5]:
                print(f"           {rid}: ...{m.group(0)[:120]}...", file=sys.stderr)
        else:
            print(f"  ok     {label}: none")

    for label, pattern in ADVISORY_MARKERS.items():
        hits = [(rid, txt, m) for rid, txt in haystack for m in re.finditer(pattern, txt, re.I)]
        if not hits:
            print(f"  ok     {label}: none")
            continue
        print(f"  info   {label}: {len(hits)} occurrence(s) — narrative, review if surprising")
        for rid, txt, m in hits[:3]:
            start = max(0, m.start() - 70)
            context = re.sub(r"\s+", " ", txt[start : m.end() + 70])
            print(f"           {rid}: ...{context}...")
    return blocking


def normalize(raw_dir: Path, processed_dir: Path) -> tuple[int, int]:
    """Rebuild corpus.jsonl from the raw layer. Returns (records written, blocking hits)."""
    ncd_dir = raw_dir / "ncd"
    report_path = raw_dir / "report.json"
    if not ncd_dir.exists() or not report_path.exists():
        print("No raw NCDs found. Run a download first.", file=sys.stderr)
        return 0, 0

    rows = json.loads(report_path.read_text(encoding="utf-8"))
    processed_dir.mkdir(parents=True, exist_ok=True)
    out_path = processed_dir / "corpus.jsonl"

    records: list[dict] = []
    empty: list[str] = []
    missing: list[str] = []
    referenced: set[str] = set()
    for row in rows:
        filename = raw_filename(row)
        referenced.add(filename)
        path = ncd_dir / filename
        if not path.exists():
            missing.append(filename)
            continue
        rec = normalize_record(row, json.loads(path.read_text(encoding="utf-8")))
        if not rec["text"]:
            empty.append(rec["id"])
            continue
        records.append(rec)

    with out_path.open("w", encoding="utf-8") as out:
        for rec in records:
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"\nNormalize: wrote {len(records)} NCD records -> {out_path}")
    if missing:
        shown = ", ".join(missing[:5]) + (" ..." if len(missing) > 5 else "")
        print(
            f"  {len(missing)} NCD(s) in the report but not on disk — re-run without "
            f"--limit to fetch them: {shown}",
            file=sys.stderr,
        )
    if empty:
        print(f"  skipped {len(empty)} NCD(s) with no policy text: {', '.join(empty)}")
    undated = sum(1 for r in records if not r["effective_date"])
    if undated:
        print(
            f"  {undated} NCD(s) have no posted effective date (longstanding determinations); "
            "`effective_date` is null and `effective_date_note` says why"
        )

    # A revised NCD arrives under a new _v<n> filename, so the old one lingers unreferenced.
    # The corpus is driven by report.json and ignores them; say so rather than leaving a
    # superseded version sitting in a committed directory unexplained.
    stale = sorted(p.name for p in ncd_dir.glob("*.json") if p.name not in referenced)
    if stale:
        print(
            f"  superseded raw file(s) not in the current report ({len(stale)}): {', '.join(stale)}"
        )
        print("  they are excluded from corpus.jsonl; delete them if you don't want the history")

    return len(records), scan_licensing(records)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main() -> int:
    p = argparse.ArgumentParser(description="Download the Medicare Coverage Database NCDs for RAG.")
    p.add_argument("--out", type=Path, default=Path("data"), help="Output root (default: ./data)")
    p.add_argument("--refresh", action="store_true", help="Re-fetch NCDs already on disk")
    p.add_argument(
        "--normalize-only", action="store_true", help="Skip download; rebuild corpus.jsonl from raw"
    )
    p.add_argument("--limit", type=int, default=0, help="Only fetch the first N NCDs (smoke test)")
    p.add_argument(
        "--max-pages", type=int, default=20, help="Report-page ceiling for discovery (default: 20)"
    )
    p.add_argument(
        "--delay", type=float, default=0.2, help="Seconds between requests (default: 0.2)"
    )
    p.add_argument("--retries", type=int, default=3, help="Retries per request (default: 3)")
    p.add_argument("--backoff", type=float, default=1.0, help="Base backoff seconds (default: 1.0)")
    args = p.parse_args()

    raw_dir = args.out / "raw" / SOURCE
    processed_dir = args.out / "processed" / SOURCE

    if not args.normalize_only:
        session = make_session()
        download(session, raw_dir, args)

    _, blocking = normalize(raw_dir, processed_dir)
    if blocking:
        print(
            f"\n{blocking} blocking licensing hit(s) — do NOT commit data/ until resolved.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
