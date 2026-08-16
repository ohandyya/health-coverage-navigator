"""The committed sidecar that makes the git-ignored vector store trustworthy.

Same bargain as `chunks_meta.json`: the artifact is ignored, the manifest is committed, and
reproducibility is *checked* rather than asserted (`make embed-check`). One thing is different and
it is why this file matters more than its chunking counterpart — rebuilding chunks is free and
takes a second, while rebuilding vectors costs a paid call. So the manifest is not just a
reproducibility record, it is what lets the app **refuse to open a store built against different
chunks** instead of silently answering from stale vectors.

Counts and hashes only, deliberately **no prose**: explanatory text in a file under `data/` is what
tripped the licensing scanner's blocking `CDT` marker during the part_d_spuf work. The reasoning
lives in docs/lancedb.md, outside the scanned tree.
"""

import hashlib
import json
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from health_coverage_navigator.paths import VECTOR_META_PATH

TOOL = "health-coverage-navigator/0.1 (vectors)"

#: Bumped by hand for a change in how the store is *built* that the config values do not capture —
#: a different text being embedded, a different distance metric. Bumping invalidates every
#: `vectors_snapshot_id`, which is the point. Mirrors `chunking.params.CHUNKER_VERSION`.
EMBEDDER_VERSION = 1


class VectorManifest(BaseModel):
    """What `data/processed/vectors_meta.json` records."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    embedded_at: str
    tool: str
    embedder_version: int

    embedding_model: str
    dimensions: int
    distance: str
    """The metric the store is queried with. Recorded because a score is only comparable to another
    score measured the same way, and because switching it changes ranking without changing a single
    stored vector — the one kind of change a row count cannot detect."""

    row_count: int

    chunker_snapshot_id: dict[str, str]
    """Per-source `snapshot_id` from `chunks_meta.json`. **The load-bearing field.** A chunk id is
    only meaningful against the chunk set that produced it, and a vector store keyed by chunk ids
    from a different snapshot returns ids the in-memory corpus cannot resolve — which
    `AnswerDeps.remember` drops silently and the grounding validator then rejects two layers away
    from the cause. `VectorIndex.open` compares this and refuses rather than degrading."""

    snapshot_id: str
    """`sha256(sorted chunk snapshot ids + model + dimensions + distance + version)[:12]`, prefixed
    `vectors@`. What an eval run records so a score names the embeddings behind it."""

    empty_chunk_ids: list[str] = Field(default_factory=list)
    """Chunks skipped for having no embeddable text. Should always be empty — `Chunk._check_shape`
    forbids blank text — and is recorded anyway, because a silently shrinking corpus is exactly the
    kind of thing that should be visible in a diff."""


def snapshot_id(
    chunker_snapshots: dict[str, str],
    embedding_model: str,
    dimensions: int,
    distance: str,
) -> str:
    """A short, stable name for "these chunks, embedded this way".

    Excludes the row count and every hash of the output, so it can be computed *before* a build —
    which is what lets `--check` verify that the store on disk is the one the manifest describes,
    and what lets a caller name the snapshot it wants. Same design as
    `chunking.pipeline.snapshot_id`.
    """
    parts = json.dumps(
        {
            "chunks": dict(sorted(chunker_snapshots.items())),
            "model": embedding_model,
            "dimensions": dimensions,
            "distance": distance,
            "version": EMBEDDER_VERSION,
        },
        sort_keys=True,
    )
    return f"vectors@{hashlib.sha256(parts.encode('utf-8')).hexdigest()[:12]}"


def build_manifest(
    *,
    chunker_snapshots: dict[str, str],
    embedding_model: str,
    dimensions: int,
    distance: str,
    row_count: int,
    empty_chunk_ids: list[str],
) -> VectorManifest:
    return VectorManifest(
        embedded_at=datetime.now(UTC).isoformat(),
        tool=TOOL,
        embedder_version=EMBEDDER_VERSION,
        embedding_model=embedding_model,
        dimensions=dimensions,
        distance=distance,
        row_count=row_count,
        chunker_snapshot_id=dict(sorted(chunker_snapshots.items())),
        snapshot_id=snapshot_id(chunker_snapshots, embedding_model, dimensions, distance),
        empty_chunk_ids=sorted(empty_chunk_ids),
    )


def write_manifest(manifest: VectorManifest, path=None) -> None:
    """Write the sidecar.

    Unlike `chunks_meta.json` this is written on every successful build without an
    "unchanged except the timestamp" guard. The chunker needed one because it is re-run constantly
    while tuning parameters and a step that dirties the tree every time is one nobody re-runs; a
    build that costs a paid call is not run casually, and `--check` compares the *fields* rather
    than the file bytes, so a fresh timestamp is not a false positive.
    """
    target = VECTOR_META_PATH if path is None else path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")


def load_manifest(path=None) -> VectorManifest | None:
    """Read the sidecar, or `None` when it has never been written."""
    target = VECTOR_META_PATH if path is None else path
    if not target.is_file():
        return None
    return VectorManifest.model_validate_json(target.read_text(encoding="utf-8"))
