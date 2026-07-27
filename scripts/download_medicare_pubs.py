#!/usr/bin/env python3
"""Download the medicare.gov publications catalog into a local corpus for RAG.

This is the "Medicare & You handbook + related CMS guides" source from docs/plan.md:
the official consumer-facing CMS publications (Medicare & You, Your Medicare Benefits,
Medicare Appeals, Choosing a Medigap Policy, the Part D drug-coverage guide, the
hospice / SNF / home-health coverage guides, ...). All are U.S.-government works.

There is **no API** for this catalog. Unlike HealthCare.gov, medicare.gov publishes no
JSON endpoint, so discovery scrapes the server-rendered search page:

  https://www.medicare.gov/publications/search?keywords=&page=N   (25 results/page)

Each result is an <article class="publication-card"> carrying the metadata we want as
data attributes (`data-pub-num`, `data-lang-iso`, `data-category`) plus a format list.
We take the PDF in the card's `formats-list--primary` block — the "Standard Print"
edition — which structurally excludes the large-print, epub, mobi, audio, and braille
alternates for the same publication. Layers written to disk:

  <out>/raw/medicare_pubs/search/page-<N>.html  raw discovery pages (provenance)
  <out>/raw/medicare_pubs/pdf/<filename>.pdf    the untouched PDF downloads
  <out>/raw/medicare_pubs/catalog.json          per-file manifest (url, sha256, ...)
  <out>/raw/medicare_pubs/_meta.json            fetch provenance (timestamp, counts)
  <out>/processed/medicare_pubs/corpus.jsonl    normalized one-record-per-page corpus

Note the PDFs under raw/pdf/ are **git-ignored** — ~44 MB of binaries is not worth
permanent git history. catalog.json records each file's url + sha256 + Last-Modified,
so a plain re-run reproduces the raw layer exactly. Everything else here is committed.

Two caveats worth knowing before you trust this data:

  * The publication URLs are **unversioned** — /publications/10050-medicare-and-you.pdf
    always serves the current plan year. The year therefore has to be read out of the
    content (catalog title / PDF metadata), which is what `plan_year` is for. Pin it in
    every query; mixing plan years is the classic correctness bug in this domain.
  * PDF bookmark outlines are **inconsistent**. Medicare & You (10050) has a real,
    richly nested outline we turn into section breadcrumbs. Medicare Appeals (11525)
    instead has an auto-generated "Structure Bookmarks" tree full of duplicated
    paragraph fragments. `build_section_map` sanity-gates the outline and returns None
    when it looks synthetic, leaving `section: null` — page-level citation still works.

Design goals (Phase 0 acceptance test):
  * Idempotent / resumable  - re-running skips PDFs already on disk (unless --refresh);
                              failed PDFs simply aren't saved, so a re-run retries them.
  * Decoupled parse         - the normalized corpus.jsonl is rebuilt from the raw layer,
                              so you can re-parse (e.g. change cleaning) without re-fetching.
  * Polite                  - configurable delay + retry-with-backoff on 429/5xx.

No API key is required. medicare.gov does reject requests without a real User-Agent
(403), so the descriptive UA below is load-bearing, not just courtesy.
See docs/medicare_pubs_data.md for the full data guide.

Usage (requests + beautifulsoup4 + pypdf are declared in pyproject.toml, so just `uv sync`):
  uv sync
  uv run python scripts/download_medicare_pubs.py                  # full download into ./data
  uv run python scripts/download_medicare_pubs.py --limit 3        # smoke test (first 3 pubs)
  uv run python scripts/download_medicare_pubs.py --refresh        # re-fetch everything
  uv run python scripts/download_medicare_pubs.py --normalize-only # rebuild corpus.jsonl from raw
"""

from __future__ import annotations

import argparse
import hashlib
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
from pypdf import PdfReader

BASE = "https://www.medicare.gov"
SEARCH_URL = BASE + "/publications/search?keywords=&page={page}"
USER_AGENT = "health-coverage-navigator/0.1 (+open-source RAG corpus builder)"
SOURCE = "medicare_pubs"

# Outline sanity gates - see build_section_map(). Real CMS bookmark trees are shallow and
# mostly-unique; the auto-generated "Structure Bookmarks" ones are deep and repetitive.
# Measured on the two ends of the spectrum, both ~120-page booklets:
#   10050 (real outline):  419 entries,  3.3/page, depth 3, 6% duplicate titles
#   10116 (structure map): 6228 entries, 51.9/page, depth 8, 70% duplicate titles
# The thresholds sit in the gap between those, well clear of both.
OUTLINE_ROOT_DENYLIST = {"structure bookmarks"}
OUTLINE_MAX_ENTRIES_PER_PAGE = 8.0
OUTLINE_MAX_DUPLICATE_RATIO = 0.6
OUTLINE_MAX_DEPTH = 6


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def fetch(session: requests.Session, url: str, retries: int, backoff: float) -> requests.Response:
    """GET a URL, retrying on 429 / 5xx with exponential backoff."""
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = session.get(url, timeout=60)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"{resp.status_code} for {url}")
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_err = e
            if attempt < retries:
                time.sleep(backoff * (2**attempt))
    assert last_err is not None
    raise last_err


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
def sanitize_search_html(page_html: str) -> str:
    """Strip scripts, styles, and per-request form tokens from a search page.

    This repo is public, and the raw search pages are committed as discovery
    provenance — but medicare.gov's server-rendered HTML embeds third-party
    credentials that must not be re-published from here. Concretely, its inline
    drupalSettings blob carries a `medicare_email_signup.api_keys` object with live
    GovDelivery **prod and stage** API keys, and the page ends with an Akamai bot-sensor
    script at a per-session obfuscated path. Neither has anything to do with the
    publication catalog.

    Everything discovery needs lives in the server-rendered
    <article class="publication-card"> markup, so dropping every <script>/<style> and
    blanking Drupal's per-request form tokens costs us nothing. The sanitized document
    is what gets both parsed and written to disk, so the committed provenance layer is
    exactly the input the parser saw.
    """
    soup = BeautifulSoup(page_html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    for field in soup.select('input[name="form_build_id"], input[name="form_token"]'):
        field["value"] = ""
        # data-drupal-selector mirrors the same token, so blank it too.
        field.attrs.pop("data-drupal-selector", None)
    # Google Search Console site-verification token — public by design, but it is still a
    # verification token and it is meaningless outside medicare.gov's own domain.
    for meta in soup.select('meta[name="google"]'):
        meta.decompose()
    return str(soup)


def parse_cards(page_html: str) -> list[dict[str, Any]]:
    """Extract one record per publication card that offers a standard-print PDF.

    The card's `formats-list--primary` block holds the standard-print edition. Taking
    the PDF from there (rather than any PDF anchor in the card) is what excludes the
    large-print `-le-` alternates, which live in the `--secondary` "More Formats" block.
    """
    soup = BeautifulSoup(page_html, "html.parser")
    pubs: list[dict[str, Any]] = []

    for card in soup.select("article.publication-card"):
        primary = card.select_one(".publication-card__formats-list--primary")
        if primary is None:
            continue
        link = primary.select_one('a[data-pub-download-format="pdf"][href]')
        if link is None:
            continue  # order-only publication, nothing to download

        href = str(link["href"])
        if not href.endswith(".pdf"):
            continue
        # Belt and braces: the primary block should never hold a large-print edition.
        if "-le-" in Path(href).name.lower():
            continue

        title_el = card.select_one(".publication-card__title")
        desc_el = card.select_one(".publication-card__description p")
        filename = Path(href).name
        pub_num = card.get("data-pub-num") or ""
        pubs.append(
            {
                "pub_id": str(pub_num) or re.sub(r"\D.*$", "", filename) or filename,
                "filename": filename,
                "url": BASE + href if href.startswith("/") else href,
                "catalog_title": title_el.get_text(strip=True) if title_el else "",
                "description": desc_el.get_text(strip=True) if desc_el else "",
                # The data-category attribute is double-escaped by the site
                # ("General&amp;#x20;information"), so it needs a second unescape pass.
                "category": html.unescape(str(card.get("data-category") or "")),
                "lang": str(card.get("data-lang-iso") or "en"),
            }
        )
    return pubs


def discover(session: requests.Session, raw_dir: Path, args: argparse.Namespace) -> list[dict]:
    """Walk the paginated publications search until a page yields no publications."""
    search_dir = raw_dir / "search"
    search_dir.mkdir(parents=True, exist_ok=True)

    seen: dict[str, dict] = {}  # url -> pub record (dedupe, keep first)
    for page in range(args.max_pages):
        url = SEARCH_URL.format(page=page)
        resp = fetch(session, url, args.retries, args.backoff)
        # Sanitize before both writing and parsing - the committed provenance layer must
        # not carry medicare.gov's embedded third-party API keys. See sanitize_search_html.
        page_html = sanitize_search_html(resp.text)
        (search_dir / f"page-{page}.html").write_text(page_html, encoding="utf-8")

        pubs = parse_cards(page_html)
        new = 0
        for pub in pubs:
            if pub["url"] not in seen:
                seen[pub["url"]] = pub
                new += 1
        print(f"  search page {page}: {len(pubs):3d} publications ({new} new)")
        if not pubs:
            break
        time.sleep(args.delay)
    else:
        print(
            f"  stopped at --max-pages={args.max_pages}; catalog may be truncated",
            file=sys.stderr,
        )

    return list(seen.values())


# --------------------------------------------------------------------------- #
# Download
# --------------------------------------------------------------------------- #
def download(session: requests.Session, raw_dir: Path, args: argparse.Namespace) -> dict:
    pdf_dir = raw_dir / "pdf"
    pdf_dir.mkdir(parents=True, exist_ok=True)

    print("Discovering publications from the search pages:")
    pubs = discover(session, raw_dir, args)
    if args.limit:
        pubs = pubs[: args.limit]
    print(f"\nDiscovered {len(pubs)} publications with a standard-print PDF.\n")

    # Keep prior manifest entries so a partial/limited run doesn't drop known provenance.
    catalog_path = raw_dir / "catalog.json"
    catalog: dict[str, dict] = {}
    if catalog_path.exists():
        catalog = {e["url"]: e for e in json.loads(catalog_path.read_text(encoding="utf-8"))}

    fetched = skipped = failed = 0
    failures: list[dict] = []
    for i, pub in enumerate(pubs, 1):
        target = pdf_dir / pub["filename"]

        if target.exists() and not args.refresh and pub["url"] in catalog:
            # Refresh the catalog-derived metadata (title, category, description) even
            # when the PDF itself is skipped — CMS re-titles and re-categorizes
            # publications, and those fields are free. Only the fetch-derived fields
            # (bytes, sha256, last_modified, fetched_at) require a download.
            catalog[pub["url"]].update(pub)
            skipped += 1
            continue

        try:
            resp = fetch(session, pub["url"], args.retries, args.backoff)
            target.write_bytes(resp.content)
            catalog[pub["url"]] = {
                **pub,
                "bytes": len(resp.content),
                "sha256": hashlib.sha256(resp.content).hexdigest(),
                "last_modified": resp.headers.get("Last-Modified", ""),
                "fetched_at": datetime.now(UTC).isoformat(),
            }
            fetched += 1
            print(f"  [{i}/{len(pubs)}] fetched {pub['filename']} ({len(resp.content):,} bytes)")
            time.sleep(args.delay)
        except Exception as e:  # noqa: BLE001 - log & continue, re-run retries
            failed += 1
            failures.append({"url": pub["url"], "error": str(e)})
            print(f"  [{i}/{len(pubs)}] FAILED {pub['filename']}: {e}", file=sys.stderr)

    entries = sorted(catalog.values(), key=lambda e: (e["pub_id"], e["filename"]))
    catalog_path.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")

    meta = {
        "fetched_at": datetime.now(UTC).isoformat(),
        "source_url": SEARCH_URL.format(page=0),
        "discovered_count": len(pubs),
        "fetched": fetched,
        "skipped_existing": skipped,
        "failed": failed,
        "catalog_size": len(entries),
        "tool": USER_AGENT,
    }
    (raw_dir / "_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    if failures:
        (raw_dir / "failures.json").write_text(json.dumps(failures, indent=2), encoding="utf-8")

    print(f"\nDownload: {fetched} fetched, {skipped} skipped, {failed} failed.")
    if failed:
        print("Re-run the script to retry failed publications (they were not saved).")
    return meta


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def build_section_map(reader: PdfReader) -> dict[int, str] | None:
    """Map 0-based page index -> section breadcrumb, from the PDF's bookmark outline.

    Returns None when the outline fails a sanity check. Many CMS PDFs ship an
    auto-generated "Structure Bookmarks" tree that mirrors the tagged-content structure
    (every paragraph, every bullet) rather than real sections; using it would produce
    breadcrumbs like "• > • > The Waiver of Right to an ALJ Hearing form". Callers treat
    a None result as "no section metadata for this publication".
    """
    try:
        outline = reader.outline
    except Exception:  # noqa: BLE001 - a malformed outline shouldn't kill the whole pub
        return None
    if not outline:
        return None

    entries: list[tuple[int, int, str]] = []  # (depth, page_index, title)
    max_depth = 0

    def walk(items: list, depth: int) -> None:
        nonlocal max_depth
        max_depth = max(max_depth, depth)
        for item in items:
            if isinstance(item, list):
                walk(item, depth + 1)
                continue
            title = re.sub(r"\s+", " ", str(getattr(item, "title", "") or "")).strip()
            if not title:
                continue
            try:
                page_index = reader.get_destination_page_number(item)
            except Exception:  # noqa: BLE001 - unresolvable destination, skip this entry
                continue
            if page_index is None:
                continue
            entries.append((depth, page_index, title))

    walk(outline, 0)
    if not entries:
        return None

    # --- sanity gates -------------------------------------------------------
    root_titles = {t.lower() for d, _, t in entries if d == 0}
    if root_titles & OUTLINE_ROOT_DENYLIST:
        return None
    if max_depth > OUTLINE_MAX_DEPTH:
        return None
    page_count = len(reader.pages)
    if page_count and len(entries) > OUTLINE_MAX_ENTRIES_PER_PAGE * page_count:
        return None
    titles = [t for _, _, t in entries]
    if 1 - len(set(titles)) / len(titles) > OUTLINE_MAX_DUPLICATE_RATIO:
        return None

    # --- breadcrumbs --------------------------------------------------------
    # Entries are in document order; keep a stack of the enclosing titles by depth.
    section_map: dict[int, str] = {}
    stack: list[str] = []
    for depth, page_index, title in entries:
        del stack[depth:]
        stack.extend([""] * (depth - len(stack)))
        stack.append(title)
        crumb = " > ".join(c for c in stack if c)
        # First bookmark landing on a page wins; later ones are sub-headings of it.
        section_map.setdefault(page_index, crumb)

    # Carry the last-seen section forward across pages with no bookmark of their own.
    current = ""
    for page_index in range(page_count):
        if page_index in section_map:
            current = section_map[page_index]
        elif current:
            section_map[page_index] = current
    return section_map or None


BULLET_START = re.compile(r"^(?:[•▪◦*\-–—]|\d+[.)]\s|\(\d+\))")
# A wrapped body line runs nearly to the full measure; headings and list labels are short.
# Sampled across these publications, body lines land at 60-80 chars, so 55 is a safe floor.
REFLOW_MIN_LINE_LEN = 55


def clean_page_text(text: str) -> str:
    """Normalize extracted page text: drop bare page numbers, unwrap hard line breaks.

    pypdf preserves the PDF's visual line breaks, so a sentence arrives as
    "... for chronic low\nback pain." That breaks phrase matching for the Phase 1-a
    lexical/BM25 retriever, so continuation lines are joined back onto their parent.
    The rule is deliberately conservative — only join when the previous line ran to
    nearly the full measure, doesn't end a sentence, and the next line isn't a bullet —
    so headings and list items keep their own lines.
    """
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.fullmatch(r"C?\d{1,4}", line):  # bare page number ("31", "C3")
            continue

        if lines:
            prev = lines[-1]
            if prev.endswith("-") and not prev.endswith((" -", "--")):
                lines[-1] = prev[:-1] + line  # de-hyphenate across the break
                continue
            if (
                len(prev) >= REFLOW_MIN_LINE_LEN
                and not prev.endswith((".", "!", "?", ":", ";"))
                and not BULLET_START.match(line)
            ):
                lines[-1] = prev + " " + line
                continue
        lines.append(line)
    return "\n".join(lines)


def find_plan_year(*candidates: str | None) -> int | None:
    """First plausible 4-digit plan year found across the candidate strings."""
    for candidate in candidates:
        if not candidate:
            continue
        match = re.search(r"\b(20\d{2})\b", candidate)
        if match:
            return int(match.group(1))
    return None


def normalize_pdf(pdf_path: Path, entry: dict) -> list[dict]:
    """Turn one PDF into a list of per-page corpus records."""
    reader = PdfReader(pdf_path)
    meta = reader.metadata or {}
    pdf_title = str(meta.get("/Title") or "").strip()
    subject = str(meta.get("/Subject") or "").strip()
    keywords = str(meta.get("/Keywords") or "").strip()

    title = entry.get("catalog_title") or pdf_title or pdf_path.stem
    plan_year = find_plan_year(entry.get("catalog_title"), subject, keywords, pdf_title)
    section_map = build_section_map(reader)
    page_count = len(reader.pages)

    records: list[dict] = []
    for index, page in enumerate(reader.pages):
        try:
            text = clean_page_text(page.extract_text() or "")
        except Exception as e:  # noqa: BLE001 - one bad page shouldn't drop the publication
            print(f"    page {index + 1} of {pdf_path.name}: extract failed ({e})", file=sys.stderr)
            continue
        if not text:
            continue  # cover art, full-bleed images, blank separators
        records.append(
            {
                "id": f"{entry['pub_id']}_p{index + 1:03d}",
                "source": SOURCE,
                "pub_id": entry["pub_id"],
                "url": entry["url"],
                "title": title,
                "plan_year": plan_year,
                "lang": entry.get("lang") or "en",
                "category": entry.get("category", ""),
                "bite": entry.get("description", ""),
                "page": index + 1,
                "page_count": page_count,
                "section": (section_map or {}).get(index),
                "text": text,
            }
        )
    return records


def normalize(raw_dir: Path, processed_dir: Path) -> int:
    pdf_dir = raw_dir / "pdf"
    catalog_path = raw_dir / "catalog.json"
    if not pdf_dir.exists() or not catalog_path.exists():
        print("No raw PDFs found. Run a download first.", file=sys.stderr)
        return 0

    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    processed_dir.mkdir(parents=True, exist_ok=True)
    out_path = processed_dir / "corpus.jsonl"

    written = 0
    pubs = 0
    no_section = []
    with out_path.open("w", encoding="utf-8") as out:
        for entry in sorted(catalog, key=lambda e: (e["pub_id"], e["filename"])):
            pdf_path = pdf_dir / entry["filename"]
            if not pdf_path.exists():
                print(f"  missing (re-run download): {entry['filename']}", file=sys.stderr)
                continue
            try:
                records = normalize_pdf(pdf_path, entry)
            except Exception as e:  # noqa: BLE001 - skip unparseable PDFs, keep the rest
                print(f"  FAILED to parse {entry['filename']}: {e}", file=sys.stderr)
                continue
            if records and all(rec["section"] is None for rec in records):
                no_section.append(entry["pub_id"])
            for rec in records:
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            written += len(records)
            pubs += 1

    print(f"Normalize: wrote {written} page records from {pubs} publications -> {out_path}")
    if no_section:
        print(f"  no usable bookmark outline ({len(no_section)}): {', '.join(sorted(no_section))}")
    return written


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main() -> int:
    p = argparse.ArgumentParser(description="Download the medicare.gov publications for RAG.")
    p.add_argument("--out", type=Path, default=Path("data"), help="Output root (default: ./data)")
    p.add_argument("--refresh", action="store_true", help="Re-fetch PDFs already on disk")
    p.add_argument(
        "--normalize-only", action="store_true", help="Skip download; rebuild corpus.jsonl from raw"
    )
    p.add_argument(
        "--limit", type=int, default=0, help="Only fetch the first N publications (smoke test)"
    )
    p.add_argument(
        "--max-pages", type=int, default=20, help="Search-page ceiling for discovery (default: 20)"
    )
    p.add_argument(
        "--delay", type=float, default=0.5, help="Seconds between requests (default: 0.5)"
    )
    p.add_argument("--retries", type=int, default=3, help="Retries per request (default: 3)")
    p.add_argument("--backoff", type=float, default=1.0, help="Base backoff seconds (default: 1.0)")
    args = p.parse_args()

    raw_dir = args.out / "raw" / SOURCE
    processed_dir = args.out / "processed" / SOURCE

    if not args.normalize_only:
        session = make_session()
        download(session, raw_dir, args)

    normalize(raw_dir, processed_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
