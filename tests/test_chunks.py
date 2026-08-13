"""Corpus-dependent correctness checks for the chunked corpus.

The structural invariants that hold for any single chunk (id format, span arithmetic, per-source
fields) are enforced by the pydantic validator in health_coverage_navigator.chunking.models and
don't need re-testing here. This file tests the things only the *whole corpus* can answer: that no
content was lost or duplicated across chunk boundaries, that the overlap guarantee actually holds,
and — the one that makes a parameter change safe — that every gold `expected_snippet` still lands
inside a single chunk.

Chunks are built **in memory** rather than read from `data/processed/<source>/chunks.jsonl`, which
is git-ignored. That is deliberate and is stronger than testing the file: a test against a
committed artifact passes happily when the artifact is stale relative to the code that produced
it. `test_chunks_match_committed_manifest` closes the loop by checking the on-disk manifest against
a fresh build.
"""

import collections
import json
import re

import pytest

from health_coverage_navigator.chunking import Chunk, build_chunks
from health_coverage_navigator.chunking.params import DEFAULT_PARAMS
from health_coverage_navigator.chunking.pipeline import build_manifest
from health_coverage_navigator.corpus import CORPUS_NAMES, chunks_meta_path, load_corpus
from health_coverage_navigator.evals.loader import load_gold_set
from health_coverage_navigator.evals.models import GoldSet

#: Only whitespace and an NCD heading line may ever fall between two consecutive chunks of a doc.
BETWEEN_CHUNKS = re.compile(r"\A\s*(?:## [^\n]*\n)?\s*\Z")


def _normalize(text: str) -> str:
    return " ".join(text.split())


@pytest.fixture(scope="session")
def gold() -> GoldSet:
    return load_gold_set()


@pytest.fixture(scope="session")
def corpus_docs() -> dict[str, dict[str, dict]]:
    """corpus name -> {doc id -> record} for all three text corpora."""
    return {name: {rec["id"]: rec for rec in load_corpus(name)} for name in CORPUS_NAMES}


@pytest.fixture(scope="session")
def chunks() -> dict[str, list[Chunk]]:
    """corpus name -> chunks, built in process from the committed corpus."""
    return {name: build_chunks(name).chunks for name in CORPUS_NAMES}


@pytest.fixture(scope="session")
def all_chunks(chunks: dict[str, list[Chunk]]) -> list[Chunk]:
    return [c for name in CORPUS_NAMES for c in chunks[name]]


# ------------------------------------------------------------ 1. identity -------------------


def test_chunk_ids_are_unique(all_chunks: list[Chunk]):
    dupes = [i for i, n in collections.Counter(c.id for c in all_chunks).items() if n > 1]
    assert not dupes, f"duplicate chunk ids: {dupes[:5]}"


def test_chunk_id_round_trips_to_parent(all_chunks: list[Chunk]):
    for c in all_chunks:
        source, rest = c.id.split(":", 1)
        doc_id, ordinal = rest.rsplit("#", 1)
        assert (source, doc_id, int(ordinal)) == (c.source, c.doc_id, c.ordinal), (
            f"{c.id!r} does not parse back to its own fields"
        )


def test_every_parent_doc_exists(chunks: dict[str, list[Chunk]], corpus_docs):
    for name in CORPUS_NAMES:
        for c in chunks[name]:
            assert c.doc_id in corpus_docs[name], (
                f"{c.id}: parent {c.doc_id!r} is not in {name}/corpus.jsonl"
            )


def test_corpus_doc_ids_are_unique(corpus_docs):
    """Gold labels and chunk parentage both key on doc id, so a collision is unrecoverable.

    Regression guard: healthcare_gov carried 56 duplicate ids until the Spanish state pages'
    self-reported `url`/`lang` stopped being trusted (see download_healthcare_gov.normalize_record).
    """
    for name in CORPUS_NAMES:
        docs = load_corpus(name)
        assert len(docs) == len(corpus_docs[name]), f"{name}: corpus.jsonl has duplicate ids"


# ------------------------------------------------------ 2. verbatim slice -------------------


def test_chunk_text_is_verbatim_slice(chunks: dict[str, list[Chunk]], corpus_docs):
    for name in CORPUS_NAMES:
        for c in chunks[name]:
            parent = corpus_docs[name][c.doc_id]["text"]
            assert parent[c.char_start : c.char_end] == c.text, (
                f"{c.id}: text is not the parent's [{c.char_start}:{c.char_end}] slice"
            )


def test_no_content_is_lost(chunks: dict[str, list[Chunk]], corpus_docs):
    """Walking a doc's chunks in order, nothing but whitespace/headings falls between them."""
    for name in CORPUS_NAMES:
        by_doc: dict[str, list[Chunk]] = collections.defaultdict(list)
        for c in chunks[name]:
            by_doc[c.doc_id].append(c)

        for doc_id, doc_chunks in by_doc.items():
            parent = corpus_docs[name][doc_id]["text"]
            assert BETWEEN_CHUNKS.match(parent[: doc_chunks[0].char_start]), (
                f"{doc_id}: content dropped before the first chunk"
            )
            assert BETWEEN_CHUNKS.match(parent[doc_chunks[-1].char_end :]), (
                f"{doc_id}: content dropped after the last chunk"
            )
            for prev, nxt in zip(doc_chunks, doc_chunks[1:], strict=False):
                if nxt.char_start > prev.char_end:
                    gap = parent[prev.char_end : nxt.char_start]
                    assert BETWEEN_CHUNKS.match(gap), (
                        f"{nxt.id}: {len(gap)} chars dropped between chunks: {gap[:80]!r}"
                    )


def test_ordinals_are_dense_and_ordered(chunks: dict[str, list[Chunk]]):
    for name in CORPUS_NAMES:
        by_doc: dict[str, list[Chunk]] = collections.defaultdict(list)
        for c in chunks[name]:
            by_doc[c.doc_id].append(c)
        for doc_id, doc_chunks in by_doc.items():
            assert [c.ordinal for c in doc_chunks] == list(range(len(doc_chunks))), (
                f"{doc_id}: ordinals are not 0..n-1 in order"
            )
            starts = [c.char_start for c in doc_chunks]
            assert starts == sorted(starts), f"{doc_id}: chunks are not in document order"


# --------------------------------------------------------- 3. size/overlap ------------------


def test_chunk_size_budget(all_chunks: list[Chunk]):
    ceiling = DEFAULT_PARAMS.max_chars + DEFAULT_PARAMS.overlap_chars
    for c in all_chunks:
        assert c.n_chars <= ceiling, f"{c.id}: {c.n_chars} chars exceeds the {ceiling} ceiling"
        assert c.text.strip(), f"{c.id}: blank text"


def test_overlap_guarantee(chunks: dict[str, list[Chunk]]):
    """Consecutive chunks of the same section overlap by at least `overlap_chars`.

    This is what makes single-chunk containment of a <=320-char passage a guarantee rather than
    luck, and it is the property a "snap to the nearest boundary" refactor would silently break.
    """
    for name in CORPUS_NAMES:
        by_doc: dict[str, list[Chunk]] = collections.defaultdict(list)
        for c in chunks[name]:
            by_doc[c.doc_id].append(c)
        for doc_chunks in by_doc.values():
            for prev, nxt in zip(doc_chunks, doc_chunks[1:], strict=False):
                if prev.heading != nxt.heading or nxt.char_start >= prev.char_end:
                    continue  # different section: no overlap is expected across a heading
                overlap = prev.char_end - nxt.char_start
                assert overlap >= DEFAULT_PARAMS.overlap_chars, (
                    f"{nxt.id}: only {overlap} chars of overlap with {prev.id}"
                )


def test_every_non_trivial_doc_yields_a_chunk(chunks: dict[str, list[Chunk]], corpus_docs):
    for name in CORPUS_NAMES:
        chunked = {c.doc_id for c in chunks[name]}
        skipped = set(build_chunks(name).skipped_ids)
        for doc_id, rec in corpus_docs[name].items():
            long_enough = len(_normalize(rec["text"])) >= DEFAULT_PARAMS.min_doc_chars
            if long_enough:
                assert doc_id in chunked, f"{doc_id}: above the size floor but produced no chunk"
            else:
                assert doc_id in skipped and doc_id not in chunked, (
                    f"{doc_id}: below the size floor but was not reported as skipped"
                )


# ------------------------------------------------------------- 4. gold set ------------------


def test_gold_snippets_survive_chunking(gold: GoldSet, chunks: dict[str, list[Chunk]]):
    """Every gold snippet is wholly inside ONE chunk of one of its expected docs.

    A snippet split across two chunks would make its question unanswerable by any single
    retrieval, so this converts a chunking-parameter regression into a build failure rather than a
    mysterious drop in eval scores.
    """
    for q in gold.in_corpus():
        assert q.corpus is not None
        assert q.expected_snippet is not None
        wanted = set(q.expected_doc_ids)
        snippet = _normalize(q.expected_snippet)
        hits = [c for c in chunks[q.corpus] if c.doc_id in wanted and snippet in _normalize(c.text)]
        assert hits, (
            f"{q.id}: snippet {snippet[:60]!r}... is not inside any single chunk of "
            f"{sorted(wanted)}"
        )


def test_gold_snippet_chunk_maps_back_to_doc(gold: GoldSet, chunks: dict[str, list[Chunk]]):
    """The chunk->doc mapping the eval runner uses for recall@k actually resolves."""
    for q in gold.in_corpus():
        assert q.corpus is not None
        assert q.expected_snippet is not None
        snippet = _normalize(q.expected_snippet)
        for c in chunks[q.corpus]:
            if snippet in _normalize(c.text) and c.doc_id in set(q.expected_doc_ids):
                assert c.id.startswith(f"{c.source}:{c.doc_id}#"), (
                    f"{q.id}: chunk {c.id} does not carry its parent doc id"
                )
                break


# ---------------------------------------------------------- 5. provenance -------------------


def test_citation_label_is_populated(chunks: dict[str, list[Chunk]]):
    for c in chunks["medicare_ncd"]:
        assert c.section_number and f"NCD {c.section_number}" in c.citation_label, (
            f"{c.id}: NCD citation label lost its section number"
        )
    for c in chunks["medicare_pubs"]:
        assert f"p. {c.page}" in c.citation_label, f"{c.id}: pubs citation label lost its page"
    for c in chunks["healthcare_gov"]:
        assert c.url in c.citation_label, f"{c.id}: healthcare_gov citation label lost its url"


def test_context_header_precedes_chunk_text(all_chunks: list[Chunk]):
    for c in all_chunks:
        assert c.retrieval_text.endswith(c.text), f"{c.id}: retrieval_text does not end in text"
        assert c.retrieval_text.startswith(c.context_header), (
            f"{c.id}: retrieval_text does not start with the context header"
        )


def test_pubs_heading_coverage(chunks: dict[str, list[Chunk]]):
    """A ratchet on the running-header heuristic.

    `section` alone labels only 192 of 964 pages, and some of those are degenerate ("Section 5:").
    Promoting the printed page banner lifts coverage well past that; a regex change that silently
    killed the detector would show up here rather than as quietly worse retrieval.
    """
    pages = {c.doc_id for c in chunks["medicare_pubs"]}
    with_heading = {c.doc_id for c in chunks["medicare_pubs"] if c.heading}
    assert len(with_heading) >= 600, (
        f"only {len(with_heading)} of {len(pages)} pages carry a heading (expected >= 600)"
    )


# ------------------------------------------------------ 6. reproducibility ------------------


def test_chunking_is_deterministic():
    for name in CORPUS_NAMES:
        first = build_chunks(name).serialize()
        second = build_chunks(name).serialize()
        assert first == second, f"{name}: two builds of the same corpus differ"


def test_chunks_match_committed_manifest():
    """The committed manifest must describe the chunks this code produces right now.

    This is what earns `chunks.jsonl` its place in .gitignore: the artifact is absent from git but
    its reproducibility is checked, not merely asserted.
    """
    for name in CORPUS_NAMES:
        meta_path = chunks_meta_path(name)
        assert meta_path.exists(), f"{name}: chunks_meta.json is missing — run `make chunk`"
        committed = json.loads(meta_path.read_text(encoding="utf-8"))
        fresh = build_manifest(build_chunks(name))
        assert committed["snapshot_id"] == fresh["snapshot_id"], (
            f"{name}: snapshot_id drift — corpus or params changed without re-running `make chunk`"
        )
        assert committed["output"]["sha256"] == fresh["output"]["sha256"], (
            f"{name}: chunk output differs from the committed manifest — run `make chunk`"
        )
