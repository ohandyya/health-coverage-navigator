#!/usr/bin/env python3
"""Download the HealthCare.gov Content API into a local corpus for RAG.

The HealthCare.gov Content API exposes three layers:
  - Index:       https://www.healthcare.gov/api/index.json   (glossary terms ONLY)
  - Collections: https://www.healthcare.gov/api/<type>.json  (full posts grouped by type)
  - Objects:     https://www.healthcare.gov/<post>.json       (full content per post)

This script is collection-driven. The index endpoint is mislabeled: despite the docs
calling it a "site-wide index of all posts," /api/index.json lists only glossary terms,
so driving discovery from it silently drops every article and state page. Instead we
enumerate posts from the per-type collection endpoints (articles, glossary, states;
blog/topics are empty and questions 404s), then fetch each post's individual object for
its richest metadata (bite, excerpt, topics, state, ...). Layers written to disk:

  <out>/raw/healthcare_gov/collections/<type>.json  raw per-type collection listings
  <out>/raw/healthcare_gov/posts/<slug>.json         raw content object per post
  <out>/raw/healthcare_gov/_meta.json                fetch provenance (timestamp, counts)
  <out>/processed/healthcare_gov/corpus.jsonl        normalized {id,url,title,text,...}

Design goals (Phase 0 acceptance test):
  * Idempotent / resumable  - re-running skips posts already on disk (unless --refresh);
                              failed posts simply aren't saved, so a re-run retries them.
  * Decoupled parse         - the normalized corpus.jsonl is rebuilt from the raw layer,
                              so you can re-parse (e.g. change cleaning) without re-fetching.
  * Polite                  - configurable delay + retry-with-backoff on 429/5xx.

No API key is required: the Content API is open, keyless, and CORS-enabled. The one
courtesy is to be polite (descriptive User-Agent, request delay, backoff on 429/5xx),
which this script does by default. See docs/health_care_data.md for the full data guide.

Usage (requests + beautifulsoup4 are declared in pyproject.toml, so just `uv sync`):
  uv sync
  uv run python scripts/download_healthcare_gov.py                  # full download into ./data
  uv run python scripts/download_healthcare_gov.py --limit 20       # smoke test (first 20 posts)
  uv run python scripts/download_healthcare_gov.py --refresh        # re-fetch everything
  uv run python scripts/download_healthcare_gov.py --normalize-only # rebuild corpus.jsonl from raw
  uv run python scripts/download_healthcare_gov.py --lang en        # keep only English in corpus
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

BASE = "https://www.healthcare.gov"
# Discovery is driven from these per-type collection listings (the /api/index.json
# endpoint is glossary-only). Empty types (blog, topics) and missing ones
# (questions -> 404) are skipped gracefully at runtime.
COLLECTION_TYPES = ["articles", "blog", "questions", "glossary", "states", "topics"]
USER_AGENT = "health-coverage-navigator/0.1 (+open-source RAG corpus builder)"


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return s


def fetch_json(session: requests.Session, url: str, retries: int, backoff: float) -> dict:  # pyright: ignore[reportReturnType]
    """GET a URL and parse JSON, retrying on 429 / 5xx with exponential backoff."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"{resp.status_code} for {url}")
            resp.raise_for_status()
            result = resp.json()  # may raise JSONDecodeError

            if not isinstance(result, dict):
                raise RuntimeError(f"Expected JSON object at {url}, got {type(result)}")

            return result
        except (requests.RequestException, json.JSONDecodeError, RuntimeError) as e:
            last_err = e
            if attempt < retries:
                sleep = backoff * (2**attempt)
                time.sleep(sleep)
            else:
                raise last_err from e


# --------------------------------------------------------------------------- #
# URL / slug helpers
# --------------------------------------------------------------------------- #
def to_post_json_url(url: str) -> str:
    """Turn an index entry's HTML url into its .json content-object url.

    Per the API docs, the object url is the post url with its trailing slash
    replaced by '.json' (e.g. /accessibility -> /accessibility.json).
    """
    full = url if url.startswith("http") else BASE + "/" + url.lstrip("/")
    if full.endswith(".json"):
        return full
    if full.endswith("/"):
        return full[:-1] + ".json"
    return full + ".json"


def slugify(url: str) -> str:
    """Filesystem-safe slug from a post url path (unique per post)."""
    path = urlparse(url).path.strip("/")
    path = re.sub(r"\.json$", "", path)
    if not path:
        path = "index"
    return re.sub(r"[^a-zA-Z0-9._-]", "_", path)


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def html_to_text(html: str) -> str:
    """Strip HTML to clean, newline-separated text suitable for chunking."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def normalize_record(obj: dict) -> dict:
    """Flatten a raw content object into one clean record for the corpus."""
    url = obj.get("url", "")
    return {
        "id": slugify(url),
        "source": "healthcare_gov",
        "url": url,
        "title": obj.get("title", ""),
        "lang": obj.get("lang", ""),
        "date": obj.get("date", ""),
        "categories": obj.get("categories", []),
        "tags": obj.get("tags", []),
        "topics": obj.get("topics", []),
        "bite": obj.get("bite", ""),  # short summary, present on individual objects
        "text": html_to_text(obj.get("content", "")),
    }


# --------------------------------------------------------------------------- #
# Pipeline steps
# --------------------------------------------------------------------------- #
def enumerate_posts(session, raw_dir: Path, args) -> list[str]:
    """Discover every post URL from the per-type collection endpoints.

    The site-wide index only lists glossary terms, so we drive discovery from the
    collection listings instead. Each raw collection is saved for provenance. Empty
    collections (blog, topics) and missing ones (questions -> 404) are skipped with a
    warning rather than aborting the run.
    """
    collections_dir = raw_dir / "collections"
    collections_dir.mkdir(parents=True, exist_ok=True)

    seen: dict[str, str] = {}  # normalized url -> original page url (dedupe, keep first)
    for ctype in COLLECTION_TYPES:
        url = f"{BASE}/api/{ctype}.json"
        try:
            coll = fetch_json(session, url, args.retries, args.backoff)
        except Exception as e:  # noqa: BLE001 - a dead/empty collection shouldn't kill the run
            print(f"  collection {ctype:9s}: skipped ({e})", file=sys.stderr)
            continue
        (collections_dir / f"{ctype}.json").write_text(json.dumps(coll, indent=2), encoding="utf-8")
        entries = coll.get(ctype, [])
        new = 0
        for entry in entries:
            page_url = entry.get("url")
            if not page_url:
                continue
            key = "/" + page_url.strip("/")  # normalize slash variants across endpoints
            if key not in seen:
                seen[key] = page_url
                new += 1
        print(f"  collection {ctype:9s}: {len(entries):4d} posts ({new} new)")
        time.sleep(args.delay)

    return list(seen.values())


def download(session, raw_dir: Path, args) -> dict:
    posts_dir = raw_dir / "posts"
    posts_dir.mkdir(parents=True, exist_ok=True)

    print("Discovering posts from collection endpoints:")
    page_urls = enumerate_posts(session, raw_dir, args)
    if args.limit:
        page_urls = page_urls[: args.limit]
    print(f"\nDiscovered {len(page_urls)} unique posts.\n")

    fetched = skipped = failed = 0
    failures = []
    for i, page_url in enumerate(page_urls, 1):
        slug = slugify(page_url)
        target = posts_dir / f"{slug}.json"

        if target.exists() and not args.refresh:
            skipped += 1
            continue

        obj_url = to_post_json_url(page_url)
        try:
            obj = fetch_json(session, obj_url, args.retries, args.backoff)
            target.write_text(json.dumps(obj, indent=2), encoding="utf-8")
            fetched += 1
            print(f"  [{i}/{len(page_urls)}] fetched {slug}")
            time.sleep(args.delay)
        except Exception as e:  # noqa: BLE001 - log & continue, re-run retries
            failed += 1
            failures.append({"url": obj_url, "error": str(e)})
            print(f"  [{i}/{len(page_urls)}] FAILED {slug}: {e}", file=sys.stderr)

    meta = {
        "fetched_at": datetime.now(UTC).isoformat(),
        "discovered_count": len(page_urls),
        "fetched": fetched,
        "skipped_existing": skipped,
        "failed": failed,
        "tool": USER_AGENT,
    }
    (raw_dir / "_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    if failures:
        (raw_dir / "failures.json").write_text(json.dumps(failures, indent=2), encoding="utf-8")

    print(f"\nDownload: {fetched} fetched, {skipped} skipped, {failed} failed.")
    if failed:
        print("Re-run the script to retry failed posts (they were not saved).")
    return meta


def normalize(raw_dir: Path, processed_dir: Path, langs: set[str] | None) -> int:
    posts_dir = raw_dir / "posts"
    if not posts_dir.exists():
        print("No raw posts found. Run a download first.", file=sys.stderr)
        return 0

    processed_dir.mkdir(parents=True, exist_ok=True)
    out_path = processed_dir / "corpus.jsonl"

    written = 0
    with out_path.open("w", encoding="utf-8") as out:
        for post_file in sorted(posts_dir.glob("*.json")):
            obj = json.loads(post_file.read_text(encoding="utf-8"))
            rec = normalize_record(obj)
            if langs and rec["lang"] and rec["lang"] not in langs:
                continue
            if not rec["text"]:  # skip empty / non-article posts
                continue
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            written += 1

    print(f"Normalize: wrote {written} records -> {out_path}")
    return written


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main() -> int:
    p = argparse.ArgumentParser(description="Download HealthCare.gov Content API for RAG.")
    p.add_argument("--out", type=Path, default=Path("data"), help="Output root (default: ./data)")
    p.add_argument("--refresh", action="store_true", help="Re-fetch posts already on disk")
    p.add_argument(
        "--normalize-only", action="store_true", help="Skip download; rebuild corpus.jsonl from raw"
    )
    p.add_argument(
        "--limit", type=int, default=0, help="Only fetch the first N discovered posts (smoke test)"
    )
    p.add_argument(
        "--lang", default="all", help="Comma-separated langs to keep in corpus.jsonl, or 'all'"
    )
    p.add_argument(
        "--delay", type=float, default=0.25, help="Seconds between requests (default: 0.25)"
    )
    p.add_argument("--retries", type=int, default=3, help="Retries per request (default: 3)")
    p.add_argument("--backoff", type=float, default=1.0, help="Base backoff seconds (default: 1.0)")
    args = p.parse_args()

    raw_dir = args.out / "raw" / "healthcare_gov"
    processed_dir = args.out / "processed" / "healthcare_gov"
    langs = (
        None
        if args.lang.strip().lower() == "all"
        else {lang.strip() for lang in args.lang.split(",")}
    )

    if not args.normalize_only:
        session = make_session()
        download(session, raw_dir, args)

    normalize(raw_dir, processed_dir, langs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
