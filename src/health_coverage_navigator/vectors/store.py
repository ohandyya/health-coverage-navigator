"""`VectorIndex` — semantic retrieval over the chunked corpus, and the build that populates it.

The read side mirrors `agent/bm25.py` on purpose: `search()` returns `(chunk_id, score)` pairs,
highest first, exactly like `Bm25Index.search`. That is not cosmetic. `agent/tools.py` maps those
ids back through `CorpusIndex._by_id` to build `ChunkHit`s, which is what puts a vector hit into
`AnswerDeps.seen_chunks` and therefore what makes it citable — one code shape for both retrieval
paths, so a citation cannot be grounded differently depending on which tool found it.

Four decisions worth knowing before changing anything here.

**The table stores no text.** Columns are `chunk_id`, `doc_id`, `source`, `vector` — nothing else.
docs/lancedb.md §2's sketch stores the chunk text alongside the vector; we deliberately do not. The
text already has exactly one source of truth (`chunks.jsonl`, loaded into `CorpusIndex`), and
copying it into a second store is how two copies drift — after which a citation's snippet could be
validated against text the corpus no longer contains. It also keeps the store to ~41 MB.

**No ANN index. Exhaustive search, always.** At 6,722 rows a flat scan is sub-millisecond, so an
IVF/HNSW index would buy nothing measurable and cost the one property this store exists for: an
approximate index makes recall *approximate*, and the whole argument for the `vector` eval runner
is that it is deterministic where an agent run is not. A recall number that wobbles because of the
index is a recall number that cannot settle Phase 1b's question. Revisit above ~100k rows.

**Cosine, and scores are similarities.** LanceDB returns `_distance` (lower is better); BM25 scores
are higher-is-better, and both reach the model on one `ChunkHit.score` field. So distance is
converted to `1 - distance` here, at the boundary, rather than leaving two opposite conventions
loose in the codebase. They are still not comparable *to each other* — that is a prompt problem,
handled in the tool docstrings.

**Opening validates the snapshot.** A chunk id is only meaningful against the chunk set that made
it. A store built before a re-chunk returns ids `CorpusIndex` cannot resolve, and the failure that
produces is genuinely awful: `AnswerDeps.remember` drops them silently, the grounding validator then
rejects every citation of them, the retry budget burns, and the run dies as
`UnexpectedModelBehavior` two layers from the cause. `open()` compares manifests up front and
refuses.
"""

from collections.abc import Iterable, Sequence
from pathlib import Path

import lancedb
import pyarrow as pa
from lancedb.table import AsyncTable

from health_coverage_navigator.chunking.models import Chunk
from health_coverage_navigator.corpus import CorpusName, chunker_snapshots
from health_coverage_navigator.paths import VECTOR_STORE_DIR
from health_coverage_navigator.vectors.embedder import Embedder, embed_one
from health_coverage_navigator.vectors.manifest import (
    VectorManifest,
    build_manifest,
    load_manifest,
    write_manifest,
)

#: One table, spanning all three text corpora. Deliberately not one table per source — the same
#: reasoning `Bm25Index` uses for a single index: a query ranks against one space, and per-source
#: tables would make a cross-corpus question into a merge of incomparable score lists.
TABLE_NAME = "corpus"

#: Cosine, matching how text embeddings are conventionally compared. OpenAI's vectors arrive
#: unit-normalised, so this ranks identically to L2 — stated explicitly anyway, because it is
#: recorded in the manifest and a silent change to it would move every ranking without changing a
#: single stored vector.
DISTANCE = "cosine"


class VectorsNotBuiltError(RuntimeError):
    """The vector store has never been built here.

    A real, expected state rather than a bug — the store is git-ignored and building it costs a
    paid embedding call, so a fresh clone has none. Mirrors `ChunksNotBuiltError`, and is handled
    the same way: reported where a caller can act on it, never degraded around.
    """


class VectorsStaleError(RuntimeError):
    """The store on disk was built against different chunks, a different model, or a different
    metric than the ones now configured.

    Raised rather than tolerated because the alternative is silent wrongness. Unlike a missing
    store — which is visibly unbuilt — a stale one answers every query, plausibly, from the wrong
    passages.
    """


def _schema(dimensions: int) -> pa.Schema:
    """The table's Arrow schema. Fixed-size vector width is part of it, so a wrong-width row is a
    write error rather than a store that ranks nonsense."""
    return pa.schema(
        [
            pa.field("chunk_id", pa.string()),
            pa.field("doc_id", pa.string()),
            pa.field("source", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), dimensions)),
        ]
    )


class VectorIndex:
    """An open LanceDB table plus the embedder that turns a query into a vector."""

    __slots__ = ("_embedder", "_manifest", "_table")

    def __init__(self, table: AsyncTable, embedder: Embedder, manifest: VectorManifest) -> None:
        self._table = table
        self._embedder = embedder
        self._manifest = manifest

    @property
    def manifest(self) -> VectorManifest:
        return self._manifest

    @property
    def snapshot_id(self) -> str:
        """What an eval run records so a score names the embeddings behind it."""
        return self._manifest.snapshot_id

    @classmethod
    async def open(
        cls,
        embedder: Embedder,
        *,
        store_dir: Path | None = None,
        meta_path: Path | None = None,
        expect_chunks: dict[str, str] | None = None,
        embedding_model: str | None = None,
    ) -> "VectorIndex":
        """Open the store, refusing one that does not match the corpus and model in play.

        `expect_chunks` defaults to the live `chunker_snapshots()` and `embedding_model` to the
        configured one; both are parameters so a test can drive the staleness check without
        rewriting committed manifests.
        """
        directory = VECTOR_STORE_DIR if store_dir is None else store_dir
        manifest = load_manifest(meta_path)
        if manifest is None or not directory.is_dir():
            raise VectorsNotBuiltError(
                f"no vector store at {directory} — it is git-ignored, so a fresh clone has none. "
                f"Run `make embed` to build it (about $0.04 and under two minutes)."
            )

        expected = chunker_snapshots() if expect_chunks is None else expect_chunks
        if manifest.chunker_snapshot_id != expected:
            raise VectorsStaleError(
                f"the vector store was built against chunks {manifest.chunker_snapshot_id} but the "
                f"corpus is now {expected}. Every stored chunk id refers to the old chunking, so "
                f"citations would resolve against passages that no longer exist. Run `make embed`."
            )

        if embedding_model is None:
            from health_coverage_navigator.config import get_config

            embedding_model = get_config().vectors.embedding_model
        if manifest.embedding_model != embedding_model:
            raise VectorsStaleError(
                f"the vector store was built with {manifest.embedding_model} but config.yaml now "
                f"says {embedding_model}. Documents and queries must share one embedding space. "
                f"Run `make embed`."
            )

        db = await lancedb.connect_async(directory)
        table = await db.open_table(TABLE_NAME)
        return cls(table, embedder, manifest)

    async def search(
        self,
        query: str,
        k: int,
        *,
        source: CorpusName | None = None,
    ) -> list[tuple[str, float]]:
        """Rank chunks by embedding similarity. Returns `(chunk_id, similarity)`, highest first.

        The same return shape as `Bm25Index.search`, so `agent/tools.py` resolves both through
        `CorpusIndex` identically. Similarity is `1 - cosine_distance`: higher is better, matching
        BM25's direction, though the two scales remain incomparable to each other.

        `source` is applied by LanceDB as a **pre-filter** (its default; `postfilter()` is opt-in),
        which matters for the same reason `Bm25Index.search`'s `keep` does — a post-filter would
        take the global top-k and *then* discard, silently returning fewer than `k`.
        """
        if k <= 0:
            return []
        vector = await embed_one(self._embedder, query)

        builder = (await self._table.search(vector)).distance_type(DISTANCE)
        if source is not None:
            # A LanceDB SQL predicate. `source` is a `CorpusName` Literal, not free text, so there
            # is no injection surface here — but the quoting is written out rather than formatted
            # from an unchecked string so it stays that way if the type ever widens.
            builder = builder.where(f"source = '{source}'")
        rows = await builder.select(["chunk_id", "_distance"]).limit(k).to_list()

        return [(row["chunk_id"], 1.0 - float(row["_distance"])) for row in rows]


def embeddable(chunks: Iterable[Chunk]) -> tuple[list[Chunk], list[str]]:
    """Split chunks into those with embeddable text and the ids of those without.

    Should never find any — `Chunk._check_shape` forbids blank text — but the embeddings endpoint
    400s on an empty string, and a paid build is the wrong place to discover an edge case. The
    skipped ids go into the manifest so a corpus that silently shrank is visible in a diff.
    """
    keep: list[Chunk] = []
    empty: list[str] = []
    for chunk in chunks:
        if chunk.retrieval_text.strip():
            keep.append(chunk)
        else:
            empty.append(chunk.id)
    return keep, empty


async def build_store(
    chunks: Sequence[Chunk],
    embedder: Embedder,
    *,
    dimensions: int,
    embedding_model: str,
    batch_size: int,
    store_dir: Path | None = None,
    meta_path: Path | None = None,
    on_batch=None,
) -> VectorManifest:
    """Embed every chunk and write the table, replacing whatever was there.

    **Embeds `retrieval_text`, stores the chunk id.** docs/chunking.md §6 settles the first half:
    the context header (`NCD 30.3 > Acupuncture > Indications...`) is real retrieval signal but is
    not text from the source document. Phase 1a indexes it for BM25 for exactly that reason, and
    the same argument applies to an embedding. It must never reach the model as quotable text,
    which here is free — the table stores no text at all, so `agent/tools.py` can only ever surface
    `Chunk.text` from the corpus.

    `mode="overwrite"` rather than an incremental upsert: the store is a pure function of the
    chunks and the model, it rebuilds in under two minutes, and a partial store is the failure mode
    the snapshot check exists to prevent. Not worth an incremental path to save ninety seconds.
    """
    directory = VECTOR_STORE_DIR if store_dir is None else store_dir
    keep, empty_ids = embeddable(chunks)

    rows: list[dict[str, object]] = []
    for start in range(0, len(keep), batch_size):
        batch = keep[start : start + batch_size]
        vectors = await embedder([c.retrieval_text for c in batch])
        if len(vectors) != len(batch):  # pragma: no cover - Embedder contract violation
            raise ValueError(f"embedder returned {len(vectors)} vectors for {len(batch)} chunks")
        rows.extend(
            {"chunk_id": c.id, "doc_id": c.doc_id, "source": c.source, "vector": v}
            for c, v in zip(batch, vectors, strict=True)
        )
        if on_batch is not None:
            on_batch(min(start + batch_size, len(keep)), len(keep))

    directory.mkdir(parents=True, exist_ok=True)
    db = await lancedb.connect_async(directory)
    await db.create_table(TABLE_NAME, data=rows, schema=_schema(dimensions), mode="overwrite")

    manifest = build_manifest(
        chunker_snapshots=chunker_snapshots(),
        embedding_model=embedding_model,
        dimensions=dimensions,
        distance=DISTANCE,
        row_count=len(rows),
        empty_chunk_ids=empty_ids,
    )
    write_manifest(manifest, meta_path)
    return manifest


async def store_row_count(store_dir: Path | None = None) -> int | None:
    """Rows actually in the table, or `None` when there is no store.

    What `make embed-check` compares against the manifest — the cheap half of verification, since
    it reads a count rather than re-embedding 6,722 chunks to compare vectors.
    """
    directory = VECTOR_STORE_DIR if store_dir is None else store_dir
    if not directory.is_dir():
        return None
    db = await lancedb.connect_async(directory)
    # `.tables`, not the response object: `list_tables()` returns a paginated `ListTablesResponse`,
    # and a membership test against it silently means something other than "is this table here".
    if TABLE_NAME not in (await db.list_tables()).tables:
        return None
    table = await db.open_table(TABLE_NAME)
    return await table.count_rows()
