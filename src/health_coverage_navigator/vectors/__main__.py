"""CLI for the embedding step — `make embed` and `make embed-check`.

A package module rather than a `scripts/` entry, for the same reason the chunking CLI is one: the
agent imports this code and `scripts/` is not a package. It follows the `scripts/` conventions
anyway (kebab-case long flags, `(default: X)` help, `main() -> int`, progress on stdout and errors
on stderr) so it reads like the rest of the repo's data tooling.

**The cost line prints before the first call**, copied from `evals/runner.py`: this is the only
build step in the repo that spends money, and a command that starts spending before it says how
much is one you cannot interrupt in time.

Usage:
  uv run python -m health_coverage_navigator.vectors            # build (skips if up to date)
  uv run python -m health_coverage_navigator.vectors --refresh  # rebuild even if up to date
  uv run python -m health_coverage_navigator.vectors --check    # verify, embed nothing
  uv run python -m health_coverage_navigator.vectors --dry-run  # report the cost, embed nothing
"""

import argparse
import asyncio
import sys

from health_coverage_navigator.chunking.models import ChunkSet
from health_coverage_navigator.config import get_config
from health_coverage_navigator.corpus import CORPUS_NAMES, chunker_snapshots, chunks_path
from health_coverage_navigator.vectors.embedder import openai_embedder
from health_coverage_navigator.vectors.manifest import load_manifest, snapshot_id
from health_coverage_navigator.vectors.store import DISTANCE, build_store, store_row_count

#: Chars per token, matching `Chunk.approx_tokens`. Only ever used for the cost estimate printed
#: before a build — deliberately not stored anywhere, since a token count baked into a committed
#: artifact is invalidated by any model change (docs/chunking.md §3).
CHARS_PER_TOKEN = 4

#: USD per million input tokens for the default embedding model, for the pre-flight estimate only.
#: Approximate by design: it exists to answer "is this cents or dollars" before a paid call, and a
#: stale price still answers that correctly.
USD_PER_MTOK = 0.02


def _load_chunks():
    """Every chunk across the three text corpora, or an error naming `make chunk`."""
    missing = [s for s in CORPUS_NAMES if not chunks_path(s).is_file()]
    if missing:
        print(
            f"no chunks for {', '.join(missing)} — chunks.jsonl is git-ignored, so a fresh clone "
            f"has none. Run `make chunk` first.",
            file=sys.stderr,
        )
        return None
    chunks = []
    for source in CORPUS_NAMES:
        chunks.extend(ChunkSet.load(source).chunks)
    return chunks


async def run(args: argparse.Namespace) -> int:
    config = get_config().vectors
    expected = chunker_snapshots()
    wanted = snapshot_id(expected, config.embedding_model, config.dimensions, DISTANCE)
    manifest = load_manifest()

    if args.check:
        return await _check(wanted, manifest)

    chunks = _load_chunks()
    if chunks is None:
        return 1

    # Idempotent by default: the store is a pure function of the chunk snapshots, the model and the
    # metric, all of which `snapshot_id` covers. Re-embedding an up-to-date store is pure cost, so
    # it takes an explicit `--refresh` — the same shape as the downloaders' skip-unless-refresh.
    if manifest is not None and manifest.snapshot_id == wanted and not args.refresh:
        rows = await store_row_count()
        if rows == manifest.row_count:
            print(f"Vector store is up to date ({manifest.snapshot_id}, {rows:,} rows).")
            print("Nothing to do. Use --refresh to rebuild anyway.")
            return 0

    total_chars = sum(len(c.retrieval_text) for c in chunks)
    tokens = total_chars // CHARS_PER_TOKEN
    batches = (len(chunks) + config.batch_size - 1) // config.batch_size
    print(
        f"{len(chunks):,} chunks · {tokens:,} tokens · {batches} requests · "
        f"~${tokens / 1_000_000 * USD_PER_MTOK:.2f}",
        file=sys.stderr,
    )
    print(f"  model  {config.embedding_model} ({config.dimensions}d, {DISTANCE})", file=sys.stderr)
    print(f"  target {wanted}", file=sys.stderr)
    if args.dry_run:
        print("\n--dry-run: nothing embedded.", file=sys.stderr)
        return 0

    def report(done: int, total: int) -> None:
        print(f"  embedded {done:,}/{total:,}", file=sys.stderr, flush=True)

    written = await build_store(
        chunks,
        openai_embedder(config.embedding_model, config.dimensions),
        dimensions=config.dimensions,
        embedding_model=config.embedding_model,
        batch_size=config.batch_size,
        on_batch=report,
    )
    print(f"\nWrote {written.row_count:,} vectors ({written.snapshot_id}).")
    if written.empty_chunk_ids:
        print(f"Skipped {len(written.empty_chunk_ids)} chunk(s) with no embeddable text.")
    return 0


async def _check(wanted: str, manifest) -> int:
    """Verify the store against the committed manifest without embedding anything.

    Compares the snapshot id (which covers the chunks, the model, the dimensionality and the
    metric) and the row count. It deliberately does **not** re-embed to compare vectors: that would
    cost as much as a rebuild, and the snapshot id already changes for every input that could
    change a vector.
    """
    if manifest is None:
        print("No vectors_meta.json. Run `make embed`.", file=sys.stderr)
        return 1
    if manifest.snapshot_id != wanted:
        print(
            f"DRIFT: manifest is {manifest.snapshot_id} but the current corpus and config want "
            f"{wanted}. Run `make embed`.",
            file=sys.stderr,
        )
        return 1

    rows = await store_row_count()
    if rows is None:
        print(
            f"Manifest {manifest.snapshot_id} is current, but no vector store exists on disk "
            f"(it is git-ignored). Run `make embed`.",
            file=sys.stderr,
        )
        return 1
    if rows != manifest.row_count:
        print(
            f"DRIFT: the store holds {rows:,} rows but the manifest says {manifest.row_count:,}. "
            f"Run `make embed`.",
            file=sys.stderr,
        )
        return 1

    print(f"Check: vector store matches the committed manifest ({manifest.snapshot_id}).")
    print(f"  {rows:,} rows · {manifest.embedding_model} ({manifest.dimensions}d)")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Embed the chunked corpus into the LanceDB store.")
    p.add_argument(
        "--check",
        action="store_true",
        help="Verify the store against the committed manifest and embed nothing",
    )
    p.add_argument(
        "--refresh", action="store_true", help="Rebuild even when the store is already up to date"
    )
    p.add_argument("--dry-run", action="store_true", help="Report the size and cost, then stop")
    return asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
