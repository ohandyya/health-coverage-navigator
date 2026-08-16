"""The vector store: building it, querying it, and refusing a stale one.

Driven with `conftest.fake_embed`, a hash-based embedder — so **nothing here measures retrieval
quality**, and it would be a mistake to add a test that tried. Whether semantic search beats
lexical is a question about a real model over the real corpus, and `make eval-retrieval-vector`
answers it against the gold set. What these tests cover is the plumbing that would make such a
measurement meaningless if it were wrong: chunk ids that resolve, filters applied before the top-k
cut, and a store that refuses to answer once it no longer matches the corpus.

No `pydantic_ai` import anywhere in this file, matching `test_corpus_index.py`. `vectors/` sits
below the agent for the same reason `index.py` does, and a test that dragged the agent in would
quietly undo that.
"""

from pathlib import Path

import pytest

from health_coverage_navigator.agent.index import CorpusIndex
from health_coverage_navigator.chunking.models import Chunk
from health_coverage_navigator.corpus import CorpusName, chunker_snapshots
from health_coverage_navigator.vectors.embedder import EmbeddingError, embed_one
from health_coverage_navigator.vectors.manifest import (
    EMBEDDER_VERSION,
    load_manifest,
    snapshot_id,
    write_manifest,
)
from health_coverage_navigator.vectors.store import (
    DISTANCE,
    VectorIndex,
    VectorsNotBuiltError,
    VectorsStaleError,
    embeddable,
    store_row_count,
)


def _chunk(source: CorpusName, doc_id: str, text: str, **extra: object) -> Chunk:
    """One chunk. `extra` carries the per-source fields `Chunk._check_shape` requires."""
    return Chunk(
        id=f"{source}:{doc_id}#000",
        doc_id=doc_id,
        source=source,
        ordinal=0,
        char_start=0,
        char_end=len(text),
        text=text,
        n_chars=len(text),
        title=doc_id.replace("_", " ").title(),
        url=f"/{doc_id}",
        **extra,  # type: ignore[arg-type]
    )


@pytest.fixture
def corpus() -> list[Chunk]:
    """One chunk per source, so the source filter has something to discriminate."""
    return [
        _chunk("healthcare_gov", "glossary_deductible", "What you pay before your plan pays."),
        _chunk(
            "medicare_ncd",
            "ncd_30_3",
            "Acupuncture for chronic low back pain.",
            section_number="30.3",
        ),
        _chunk(
            "medicare_pubs",
            "medicare_and_you",
            "Part B covers doctor visits.",
            page=12,
            plan_year=2026,
        ),
    ]


@pytest.fixture
async def store(vector_kit, corpus: list[Chunk], tmp_path: Path) -> VectorIndex:
    return await vector_kit.build(corpus, tmp_path)


# ---------------------------------------------------------------- building -------------------


async def test_the_store_holds_one_row_per_chunk(store: VectorIndex, corpus: list[Chunk]):
    assert store.manifest.row_count == len(corpus)


async def test_the_manifest_records_what_the_score_was_measured_under(
    vector_kit, store: VectorIndex
):
    """Every field here answers "would a number from this store be comparable to a number from
    that one" — which is the whole reason Phase 1b records a `vectors_snapshot_id` on a run."""
    manifest = store.manifest
    assert manifest.embedding_model == vector_kit.MODEL
    assert manifest.dimensions == vector_kit.DIM
    assert manifest.distance == DISTANCE
    assert manifest.embedder_version == EMBEDDER_VERSION
    assert manifest.chunker_snapshot_id == chunker_snapshots()
    assert manifest.snapshot_id.startswith("vectors@")


async def test_the_snapshot_id_changes_with_every_input_that_changes_a_vector():
    """A snapshot id that missed one of these would let two incomparable runs claim the same
    provenance, which is worse than having no id at all."""
    chunks = {"healthcare_gov": "healthcare_gov@aaaa"}
    original = snapshot_id(chunks, "m", 8, "cosine")

    assert snapshot_id(chunks, "other-model", 8, "cosine") != original
    assert snapshot_id(chunks, "m", 16, "cosine") != original
    assert snapshot_id(chunks, "m", 8, "l2") != original
    assert snapshot_id({"healthcare_gov": "healthcare_gov@bbbb"}, "m", 8, "cosine") != original

    assert snapshot_id(chunks, "m", 8, "cosine") == original


async def test_the_snapshot_id_does_not_depend_on_dict_ordering():
    """Same rule `ChunkParams.fingerprint` follows: dict iteration order must never reach a
    committed artifact."""
    a = snapshot_id({"a": "1", "b": "2"}, "m", 8, "cosine")
    b = snapshot_id({"b": "2", "a": "1"}, "m", 8, "cosine")
    assert a == b


async def test_the_manifest_round_trips(store: VectorIndex, tmp_path: Path):
    reloaded = load_manifest(tmp_path / "vectors_meta.json")
    assert reloaded == store.manifest


async def test_rebuilding_replaces_rather_than_appends(
    vector_kit, corpus: list[Chunk], tmp_path: Path
):
    """`mode="overwrite"`. Appending would double every chunk and leave two vectors competing for
    one id, which the search would resolve arbitrarily."""
    await vector_kit.build(corpus, tmp_path)
    await vector_kit.build(corpus, tmp_path)
    assert await store_row_count(tmp_path / "lancedb") == len(corpus)


def test_a_blank_chunk_is_skipped_rather_than_sent_to_the_provider():
    """The embeddings endpoint 400s on an empty string, and a paid build is the wrong place to
    discover that. `Chunk` forbids blank text, so this should never fire — which is exactly why it
    is worth a test rather than a comment."""
    good = _chunk("healthcare_gov", "a", "real text")
    keep, empty = embeddable([good])
    assert keep == [good]
    assert empty == []


# ---------------------------------------------------------------- querying -------------------


async def test_an_exact_query_returns_its_own_chunk_first(store: VectorIndex, corpus: list[Chunk]):
    """`retrieval_text` in, similarity 1.0 out — the store embeds the same string it was built
    from. Also pins that the score is a *similarity*, not the distance LanceDB returns."""
    hits = await store.search(corpus[0].retrieval_text, 3)
    assert hits[0][0] == corpus[0].id
    assert hits[0][1] == pytest.approx(1.0, abs=1e-4)


async def test_the_store_embeds_retrieval_text_not_bare_text(
    store: VectorIndex, corpus: list[Chunk]
):
    """docs/chunking.md §6: the context header is retrieval signal but is not text from the source
    document. Indexing bare `text` would drop that signal; surfacing the header as quotable text
    would break grounding. The store does the first half — the second is free, since it stores no
    text at all."""
    header_query = await store.search(corpus[0].retrieval_text, 1)
    bare_query = await store.search(corpus[0].text, 1)
    assert header_query[0][1] == pytest.approx(1.0, abs=1e-4)
    # A hash embedder: if the store had indexed bare `text`, *this* would be the exact match.
    assert bare_query[0][1] < 0.999


async def test_scores_come_back_in_descending_order(store: VectorIndex, corpus: list[Chunk]):
    hits = await store.search(corpus[1].retrieval_text, 3)
    assert [s for _, s in hits] == sorted((s for _, s in hits), reverse=True)


async def test_every_hit_resolves_in_the_corpus_index(store: VectorIndex, corpus: list[Chunk]):
    """**The load-bearing property of this file.** A hit whose id `CorpusIndex` cannot resolve is
    dropped by `AnswerDeps.remember`, so the grounding validator then rejects every citation of it
    — a failure that surfaces two layers from its cause. Everything else here protects this."""
    index = CorpusIndex(corpus)
    hits = await store.search(corpus[0].retrieval_text, 3)
    assert hits
    for chunk_id, _ in hits:
        assert index.chunk(chunk_id) is not None, chunk_id


async def test_the_source_filter_applies_before_the_top_k_cut(
    store: VectorIndex, corpus: list[Chunk]
):
    """Same property `Bm25Index`'s `keep` has. A post-filter would take the global top-k and *then*
    discard, so a `k=1` search restricted to a source could come back empty while that source had
    a perfectly good match."""
    hits = await store.search(corpus[0].retrieval_text, 1, source="medicare_ncd")
    assert len(hits) == 1
    assert hits[0][0] == corpus[1].id


async def test_k_is_respected_and_zero_returns_nothing(store: VectorIndex, corpus: list[Chunk]):
    assert len(await store.search(corpus[0].retrieval_text, 2)) == 2
    assert await store.search(corpus[0].retrieval_text, 0) == []
    assert await store.search(corpus[0].retrieval_text, -1) == []


async def test_the_same_query_ranks_the_same_way_twice(store: VectorIndex, corpus: list[Chunk]):
    """The property the whole `vector` eval runner rests on. If this ever fails, that runner stops
    being the deterministic instrument Phase 1b chose it for."""
    once = await store.search(corpus[2].retrieval_text, 3)
    twice = await store.search(corpus[2].retrieval_text, 3)
    assert once == twice


# ---------------------------------------------------------------- refusing -------------------


async def test_a_missing_store_names_the_command_that_builds_it(vector_kit, tmp_path: Path):
    with pytest.raises(VectorsNotBuiltError, match="make embed"):
        await vector_kit.open(tmp_path / "nothing-here")


async def test_a_store_built_against_different_chunks_is_refused(
    vector_kit, corpus: list[Chunk], tmp_path: Path
):
    """The failure this check exists to prevent is the nastiest one available: every chunk id in a
    store built before a re-chunk points at a passage that no longer exists, so citations resolve
    against the wrong text — or not at all — and nothing says so."""
    await vector_kit.build(corpus, tmp_path)

    with pytest.raises(VectorsStaleError, match="make embed"):
        await vector_kit.open(
            tmp_path, expect_chunks={"healthcare_gov": "healthcare_gov@0000deadbeef"}
        )


async def test_a_store_built_with_a_different_model_is_refused(
    vector_kit, corpus: list[Chunk], tmp_path: Path
):
    """Documents and queries must share one embedding space; comparing across two is not a
    degraded search, it is a meaningless one."""
    await vector_kit.build(corpus, tmp_path)

    with pytest.raises(VectorsStaleError, match="embedding space"):
        await vector_kit.open(tmp_path, embedding_model="some-other-model")


async def test_a_manifest_without_its_store_is_not_built_rather_than_stale(
    vector_kit, corpus: list[Chunk], tmp_path: Path
):
    """The realistic fresh-clone shape once the manifest is committed and the store is not: the
    sidecar is present and current, and there is simply nothing on disk to open."""
    store = await vector_kit.build(corpus, tmp_path)
    write_manifest(store.manifest, tmp_path / "orphan.json")

    with pytest.raises(VectorsNotBuiltError):
        await VectorIndex.open(
            vector_kit.embedder,
            store_dir=tmp_path / "never-built",
            meta_path=tmp_path / "orphan.json",
            embedding_model=vector_kit.MODEL,
        )


# ---------------------------------------------------------------- the embedder seam ----------


async def test_embed_one_unwraps_the_batch_contract(vector_kit, fake_embedder):
    vector = await embed_one(fake_embedder, "hello")
    assert len(vector) == vector_kit.DIM


async def test_an_embedder_that_returns_the_wrong_count_is_an_error():
    """Silent misalignment is the failure being prevented: every vector attached to the wrong
    chunk is not an exception anywhere, just a store that returns the wrong passages forever."""

    async def broken(texts):
        return []

    with pytest.raises(EmbeddingError):
        await embed_one(broken, "hello")
