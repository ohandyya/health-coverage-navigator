"""CLI for the chunking step.

A package module rather than a `scripts/` entry, because Phase 1a's retriever imports this code
and `scripts/` is not a package — but it follows the `scripts/` CLI conventions (kebab-case long
flags, `(default: X)` help, `main() -> int`, progress on stdout and errors on stderr) so it feels
like the rest of the repo's data tooling.

Usage:
  uv run python -m health_coverage_navigator.chunking            # chunk all three corpora
  uv run python -m health_coverage_navigator.chunking --source medicare_ncd
  uv run python -m health_coverage_navigator.chunking --stats    # report only, write nothing
  uv run python -m health_coverage_navigator.chunking --check    # verify against the manifests
"""

import argparse
import json
import sys
from typing import cast

from health_coverage_navigator.chunking.pipeline import (
    _summarize,
    build_chunks,
    build_manifest,
    write_chunks,
)
from health_coverage_navigator.config import ChunkParams, get_config
from health_coverage_navigator.corpus import CORPUS_NAMES, CorpusName, chunks_meta_path


def main() -> int:
    p = argparse.ArgumentParser(description="Chunk the text corpora for retrieval.")
    p.add_argument(
        "--source",
        choices=CORPUS_NAMES,
        action="append",
        help="Corpus to chunk; repeatable (default: all three)",
    )
    p.add_argument(
        "--stats", action="store_true", help="Report the chunk distribution and write nothing"
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="Rebuild in memory and fail if it disagrees with the committed manifest",
    )
    p.add_argument(
        "--max-chars", type=int, default=None, help="Chunk size budget (default: from config.yaml)"
    )
    p.add_argument(
        "--overlap-chars",
        type=int,
        default=None,
        help="Overlap between chunks (default: from config.yaml)",
    )
    args = p.parse_args()

    sources = cast(list[CorpusName], args.source or list(CORPUS_NAMES))
    overrides = {
        k: v
        for k, v in (("max_chars", args.max_chars), ("overlap_chars", args.overlap_chars))
        if v is not None
    }
    # Re-validated through the model rather than `model_copy(update=...)`, which skips validation —
    # a `--max-chars 0` from the command line must fail the same way it would in config.yaml.
    params = ChunkParams(**(get_config().chunking.model_dump() | overrides))

    results = []
    for i, source in enumerate(sources, 1):
        result = build_chunks(source, params)
        results.append(result)
        print(f"  [{i}/{len(sources)}] {source}: {len(result.chunks)} chunks")
        if result.skipped_ids:
            print(f"         skipped {len(result.skipped_ids)} sub-threshold docs:")
            print(f"         {', '.join(result.skipped_ids)}")

    print()
    print(_summarize(results))

    if args.stats:
        return 0
    if args.check:
        return _check(results)

    print()
    for result in results:
        manifest = write_chunks(result)
        print(f"Wrote {manifest['output']['path']} ({manifest['snapshot_id']})")
    return 0


def _check(results: list) -> int:
    """Compare a fresh in-memory build against each committed manifest."""
    drift = 0
    print()
    for result in results:
        meta_path = chunks_meta_path(result.source)
        if not meta_path.exists():
            print(f"  MISSING {meta_path.name} for {result.source}", file=sys.stderr)
            drift += 1
            continue
        committed = json.loads(meta_path.read_text(encoding="utf-8"))
        fresh = build_manifest(result)
        for field in ("snapshot_id",):
            if committed.get(field) != fresh[field]:
                print(
                    f"  DRIFT {result.source}: {field} {committed.get(field)} != {fresh[field]}",
                    file=sys.stderr,
                )
                drift += 1
        if committed.get("output", {}).get("sha256") != fresh["output"]["sha256"]:
            print(f"  DRIFT {result.source}: output sha256 differs", file=sys.stderr)
            drift += 1
        if not drift:
            print(f"  ok     {result.source} matches {committed['snapshot_id']}")

    if drift:
        print("\nChunks do not match the committed manifests. Run `make chunk`.", file=sys.stderr)
        return 1
    print("\nCheck: all sources match their committed manifests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
