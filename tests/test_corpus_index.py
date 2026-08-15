"""`CorpusIndex` against the real corpus — the four search primitives the agent's tools wrap.

Chunks are built **in memory** rather than read from `data/processed/*/chunks.jsonl`, following
`tests/test_chunks.py`: that file is git-ignored, so reading it would make the suite fail on a
fresh clone, and a test against a committed artifact passes happily when the artifact is stale.
`CorpusIndex.load()`'s own failure path is tested separately, with the paths redirected.

No `pydantic_ai` import anywhere in this file, and that is the point of `index.py` being a layer
below `tools.py`: retrieval is testable with no model, no key, and no network.
"""

import pytest

from health_coverage_navigator.agent.bm25 import Bm25Index
from health_coverage_navigator.agent.index import (
    MAX_HITS,
    MAX_PATTERN_CHARS,
    ChunksNotBuiltError,
    CorpusIndex,
)
from health_coverage_navigator.chunking import Chunk, build_chunks
from health_coverage_navigator.config import get_config
from health_coverage_navigator.corpus import CORPUS_NAMES
from health_coverage_navigator.evals.loader import load_gold_set
from health_coverage_navigator.evals.models import GoldSet


@pytest.fixture(scope="module")
def all_chunks() -> list[Chunk]:
    return [c for name in CORPUS_NAMES for c in build_chunks(name).chunks]


@pytest.fixture(scope="module")
def index(all_chunks: list[Chunk]) -> CorpusIndex:
    return CorpusIndex(all_chunks)


@pytest.fixture(scope="module")
def gold() -> GoldSet:
    return load_gold_set()


def _search(index: CorpusIndex, query: str, k: int = 5, **kwargs):
    retrieval = get_config().retrieval
    return index.search(query, k, k1=retrieval.bm25_k1, b=retrieval.bm25_b, **kwargs)


# ------------------------------------------------------------ construction ------------------


def test_index_covers_every_chunk(index: CorpusIndex, all_chunks: list[Chunk]):
    assert len(index) == len(all_chunks) == len(index.bm25)


def test_index_is_built_over_retrieval_text_not_bare_text(index: CorpusIndex):
    """docs/chunking.md §6: the context header is legitimate lexical signal. An NCD section body
    of `Physicians' Services` is only findable by its NCD number because the header carries it."""
    ncd = next(c for c in index.chunks if c.source == "medicare_ncd" and c.section_number)
    assert ncd.section_number is not None
    assert ncd.section_number not in ncd.text
    assert ncd.section_number in ncd.retrieval_text


def test_load_names_the_missing_corpus_and_the_fix(monkeypatch, tmp_path):
    """A missing `chunks.jsonl` is an expected state on a fresh clone, so the error has to be
    actionable rather than a bare `FileNotFoundError` from deep inside `ChunkSet.load`."""
    monkeypatch.setattr(
        "health_coverage_navigator.agent.index.chunks_path",
        lambda source: tmp_path / source / "chunks.jsonl",
    )
    with pytest.raises(ChunksNotBuiltError) as excinfo:
        CorpusIndex.load()
    message = str(excinfo.value)
    assert "make chunk" in message
    for name in CORPUS_NAMES:
        assert name in message


# ------------------------------------------------------------ list_documents ----------------


def test_list_documents_counts_match_the_corpus(index: CorpusIndex, all_chunks: list[Chunk]):
    overview = index.list_documents()
    assert overview.total_chunks == len(all_chunks)
    assert overview.total_documents == len({c.doc_id for c in all_chunks})
    assert set(overview.documents_by_source) == set(CORPUS_NAMES)


def test_list_documents_filters_by_source(index: CorpusIndex):
    overview = index.list_documents(source="medicare_ncd")
    assert set(overview.documents_by_source) == {"medicare_ncd"}
    assert all(d.source == "medicare_ncd" for d in overview.documents)


def test_list_documents_reports_that_it_truncated(index: CorpusIndex):
    """2,056 documents cannot be listed, and a sample that does not say it is a sample would let
    the agent answer 'what do you cover' from 20 titles."""
    overview = index.list_documents(limit=5)
    assert len(overview.documents) == 5
    assert overview.truncated is True
    assert overview.total_documents > 5


def test_list_documents_chunk_counts_are_per_document(index: CorpusIndex):
    overview = index.list_documents(source="medicare_ncd", limit=10_000)
    by_doc = {d.doc_id: d.chunks for d in overview.documents}
    expected = {}
    for chunk in index.chunks:
        if chunk.source == "medicare_ncd":
            expected[chunk.doc_id] = expected.get(chunk.doc_id, 0) + 1
    assert by_doc == expected


# ------------------------------------------------------------ grep_corpus -------------------


def test_grep_finds_an_exact_phrase(index: CorpusIndex):
    hits = index.grep(r"out-of-pocket limit")
    assert hits
    assert all("out-of-pocket limit" in h.text.lower() for h in hits)


def test_grep_matches_text_not_the_synthesised_header(index: CorpusIndex):
    """A header hit would report a match that is not in the source document — the citation would
    quote something the reader cannot find."""
    hits = index.grep(r"healthcare\.gov/", source="healthcare_gov")
    for hit in hits:
        assert "healthcare.gov/" in hit.text.lower()


def test_grep_respects_its_limit_and_the_global_cap(index: CorpusIndex):
    assert len(index.grep(r"\bcoverage\b", limit=3)) == 3
    assert len(index.grep(r"\bcoverage\b", limit=1000)) == MAX_HITS


def test_grep_case_sensitivity_is_switchable(index: CorpusIndex):
    assert index.grep("DEDUCTIBLE", ignore_case=True)
    assert not index.grep("DEDUCTIBLEEE", ignore_case=False)


def test_grep_rejects_an_overlong_pattern(index: CorpusIndex):
    with pytest.raises(ValueError, match="search_corpus"):
        index.grep("a" * (MAX_PATTERN_CHARS + 1))


def test_grep_raises_on_a_malformed_pattern(index: CorpusIndex):
    """Surfaced as `re.error` so the tool wrapper can turn it into a retry the model can act on,
    rather than a 500 the user sees."""
    import re

    with pytest.raises(re.error):
        index.grep("(unclosed")


# ------------------------------------------------------------ search_corpus -----------------


def test_search_returns_scored_hits_in_descending_order(index: CorpusIndex):
    hits = _search(index, "what is a deductible")
    assert hits
    scores = [h.score for h in hits]
    assert all(s is not None for s in scores)
    assert scores == sorted(scores, reverse=True)  # type: ignore[type-var]


def test_search_hits_carry_citable_provenance(index: CorpusIndex):
    for hit in _search(index, "skilled nursing facility coinsurance"):
        assert index.chunk(hit.chunk_id) is not None
        assert hit.label
        assert hit.text == index.chunk(hit.chunk_id).text  # type: ignore[union-attr]


def test_search_filters_by_source_before_the_top_k_cut(index: CorpusIndex):
    """Post-filtering a top-k would silently return fewer than k — the reason `keep` is threaded
    all the way down into `Bm25Index.search`."""
    hits = _search(index, "coverage", k=5, source="medicare_ncd")
    assert len(hits) == 5
    assert all(h.source == "medicare_ncd" for h in hits)


def test_search_caps_k(index: CorpusIndex):
    assert len(_search(index, "coverage", k=1000)) == MAX_HITS


def test_search_of_an_all_stopword_query_is_empty(index: CorpusIndex):
    assert _search(index, "what is the") == []


def test_search_is_deterministic(index: CorpusIndex):
    first = _search(index, "medicare part b premium")
    second = _search(index, "medicare part b premium")
    assert [h.chunk_id for h in first] == [h.chunk_id for h in second]


def test_every_gold_target_is_findable_by_its_own_wording(index: CorpusIndex, gold: GoldSet):
    """Searching a gold `expected_snippet` must surface its own document.

    This is the invariant that says the index is *correct*: every gold target is present, indexed,
    and reachable. It is phrasing-independent by construction — the query is verbatim source text —
    so it isolates a genuine indexing regression from the separate, much softer question of how
    well BM25 handles a consumer's paraphrase.

    That softer question belongs to `make eval-retrieval`, not here. Measured today, feeding the
    raw gold *questions* scores recall@5 = 0.567, and the misses are all vocabulary gaps ("what
    exactly is a deductible?" is dominated by the rare word *exactly*, not by *deductible*).
    Reformulating a failed query is the agent's job and is exactly why Phase 1a hands it a toolset
    instead of one `retrieve(query)` call.
    """
    missed = []
    for question in gold.in_corpus():
        assert question.expected_snippet is not None
        hits = _search(index, question.expected_snippet, k=get_config().retrieval.top_k)
        retrieved = {index.chunk(h.chunk_id).doc_id for h in hits}  # type: ignore[union-attr]
        if not retrieved & set(question.expected_doc_ids):
            missed.append(question.id)

    assert not missed, f"gold targets not retrievable by their own source text: {missed}"


def test_raw_question_recall_does_not_regress(index: CorpusIndex, gold: GoldSet):
    """A floor, not a target — deliberately well below the 0.567 measured today.

    A tiny 30-question set cannot distinguish 0.53 from 0.57, so asserting the measured value would
    turn ordinary noise into a failing build. What this catches is the change that halves it.
    """
    top_k = get_config().retrieval.top_k
    in_corpus = gold.in_corpus()
    hits = 0
    for question in in_corpus:
        retrieved = {
            index.chunk(h.chunk_id).doc_id  # type: ignore[union-attr]
            for h in _search(index, question.question, k=top_k)
        }
        hits += bool(retrieved & set(question.expected_doc_ids))

    assert hits / len(in_corpus) >= 0.40


def test_the_context_header_earns_its_place(index: CorpusIndex, gold: GoldSet):
    """docs/chunking.md §6 asserts the header is legitimate lexical signal. Measured, it is:
    recall@5 over the raw gold questions is 0.567 with it and 0.500 without, and MRR is higher at
    every `k1`/`b` cell swept. This pins that finding so a future "simplify: index `text`" change
    has to argue with a number.
    """
    bare = Bm25Index.build((c.id, c.text) for c in index.chunks)
    retrieval = get_config().retrieval
    top_k = retrieval.top_k
    by_id = {c.id: c for c in index.chunks}

    def recall(search) -> float:
        found = 0
        for question in gold.in_corpus():
            docs = {by_id[chunk_id].doc_id for chunk_id, _ in search(question.question)}
            found += bool(docs & set(question.expected_doc_ids))
        return found / len(gold.in_corpus())

    with_header = recall(
        lambda q: index.bm25.search(q, k=top_k, k1=retrieval.bm25_k1, b=retrieval.bm25_b)
    )
    without_header = recall(
        lambda q: bare.search(q, k=top_k, k1=retrieval.bm25_k1, b=retrieval.bm25_b)
    )
    assert with_header > without_header


# ------------------------------------------------------------ get_chunk ---------------------


def test_get_chunk_returns_neighbours_in_reading_order(index: CorpusIndex):
    multi = next(
        c for c in index.chunks if c.ordinal == 1 and len(index.list_documents().documents) > 0
    )
    window = index.get_chunk(multi.id, before=1, after=1)
    ordinals = [index.chunk(h.chunk_id).ordinal for h in window]  # type: ignore[union-attr]
    assert ordinals == sorted(ordinals)
    assert multi.ordinal in ordinals


def test_get_chunk_never_crosses_a_document_boundary(index: CorpusIndex):
    target = next(c for c in index.chunks if c.ordinal == 0)
    window = index.get_chunk(target.id, before=5, after=5)
    assert {index.chunk(h.chunk_id).doc_id for h in window} == {target.doc_id}  # type: ignore[union-attr]


def test_get_chunk_clamps_at_the_start_of_a_document(index: CorpusIndex):
    target = next(c for c in index.chunks if c.ordinal == 0)
    window = index.get_chunk(target.id, before=3, after=0)
    assert [h.chunk_id for h in window] == [target.id]


def test_get_chunk_of_an_unknown_id_is_empty_not_an_error(index: CorpusIndex):
    """A mistyped id is the likely cause, and an empty result is a clearer signal to the agent
    than an exception it cannot see the type of."""
    assert index.get_chunk("healthcare_gov:not-a-real-doc#000") == []


def test_get_chunk_hits_carry_no_score(index: CorpusIndex):
    """`score` means lexical relevance to a query. `get_chunk` has no query, so inventing one
    would make the number meaningless where the UI renders it."""
    target = next(c for c in index.chunks if c.ordinal == 0)
    assert all(h.score is None for h in index.get_chunk(target.id))
