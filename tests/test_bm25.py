"""The BM25 ranking formula, over a synthetic corpus small enough to reason about by hand.

Deliberately *not* against the real chunks: `tests/test_corpus_index.py` does that. The point here
is that the formula behaves the way the formula is supposed to, which needs documents whose term
frequencies and lengths are known exactly. A retrieval test over 6,722 real chunks can only ever
check that a plausible answer came back.
"""

import math

import pytest

from health_coverage_navigator.agent.bm25 import STOPWORDS, Bm25Index, tokenize

# One clear match, one partial, one decoy sharing only a common word.
DOCS = [
    ("d1", "A deductible is the amount you pay before your plan starts to pay."),
    ("d2", "The out-of-pocket maximum caps what you pay for covered care."),
    ("d3", "Your monthly premium is what you pay to keep the plan active."),
    ("d4", "Deductible deductible deductible."),
]


@pytest.fixture(scope="module")
def index() -> Bm25Index:
    return Bm25Index.build(DOCS)


# ---- tokenizer ----


def test_tokenize_lowercases_and_splits_on_alphanumeric_runs():
    assert tokenize("Out-of-Pocket MAX 2026!") == ["out", "pocket", "max", "2026"]


def test_tokenize_drops_stopwords():
    assert "the" in STOPWORDS
    assert tokenize("the deductible") == ["deductible"]


def test_tokenize_keeps_domain_terms_that_look_like_stopwords():
    """`part` and `plan` are frequent but load-bearing — Medicare Part B is not a stopword."""
    assert tokenize("Medicare Part B plan") == ["medicare", "part", "b", "plan"]


def test_tokenize_keeps_negations():
    """An NCD's coverage rule can turn on a `not`; dropping it would make two opposite passages
    tokenize identically."""
    assert tokenize("is not covered") == ["not", "covered"]


def test_tokenize_of_pure_punctuation_is_empty():
    assert tokenize("?!  --- ") == []


# ---- index shape ----


def test_document_frequency_counts_documents_not_occurrences(index: Bm25Index):
    # d4 says "deductible" three times; df counts d1 and d4, not four occurrences.
    assert index.document_frequency("deductible") == 2


def test_idf_is_never_negative_for_a_very_common_term(index: Bm25Index):
    """The unsmoothed Okapi IDF goes negative above 50% document frequency, which would let a
    common word push a document *down* the ranking."""
    assert index.document_frequency("pay") * 2 > len(DOCS)
    assert index.idf("pay") >= 0.0


def test_idf_of_an_unknown_term_is_zero(index: Bm25Index):
    assert index.idf("formulary") == 0.0


def test_avgdl_is_over_tokenized_length(index: Bm25Index):
    expected = sum(len(tokenize(text)) for _, text in DOCS) / len(DOCS)
    assert index.avgdl == pytest.approx(expected)


def test_empty_index_does_not_divide_by_zero():
    empty = Bm25Index.build([])
    assert len(empty) == 0
    assert empty.search("deductible", k=5, k1=1.2, b=0.75) == []


def test_an_all_stopword_document_does_not_break_normalisation():
    index = Bm25Index.build([("stop", "the and or of"), ("real", "deductible")])
    assert index.search("deductible", k=5, k1=1.2, b=0.75) == [("real", pytest.approx(0.0, abs=10))]


# ---- ranking ----


def test_search_ranks_the_matching_document_first(index: Bm25Index):
    ranked = index.search("what is a deductible", k=3, k1=1.2, b=0.75)
    assert [doc_id for doc_id, _ in ranked][0] in {"d1", "d4"}
    assert "d3" not in {doc_id for doc_id, _ in ranked[:1]}


def test_search_respects_k(index: Bm25Index):
    assert len(index.search("pay", k=2, k1=1.2, b=0.75)) == 2


def test_search_returns_nothing_for_an_all_stopword_query(index: Bm25Index):
    """The honest answer, so the agent reformulates rather than reading an arbitrary fallback."""
    assert index.search("what is the", k=5, k1=1.2, b=0.75) == []


def test_search_returns_nothing_for_an_unknown_term(index: Bm25Index):
    assert index.search("acupuncture", k=5, k1=1.2, b=0.75) == []


def test_b_controls_length_normalisation(index: Bm25Index):
    """`b=0` disables it, so the short repetitive d4 wins on raw term frequency; `b=1` applies it
    in full, which is what docs/chunking.md §5 hands forward as the thing to measure — 505 of the
    2,256 NCD chunks are under 200 characters."""
    without = dict(index.search("deductible", k=4, k1=1.2, b=0.0))
    with_full = dict(index.search("deductible", k=4, k1=1.2, b=1.0))

    assert without["d4"] > without["d1"]
    # Normalisation penalises nothing at b=0 and penalises the long d1 at b=1, so the *gap* between
    # the short and long document can only widen.
    assert (with_full["d4"] - with_full["d1"]) > (without["d4"] - without["d1"])


def test_k1_saturates_term_frequency(index: Bm25Index):
    """d4 repeats the term three times. A low `k1` saturates almost immediately, so its advantage
    over a single mention shrinks."""
    low = dict(index.search("deductible", k=4, k1=0.1, b=0.0))
    high = dict(index.search("deductible", k=4, k1=5.0, b=0.0))
    assert (low["d4"] / low["d1"]) < (high["d4"] / high["d1"])


def test_a_repeated_query_term_weighs_more(index: Bm25Index):
    once = dict(index.search("deductible premium", k=4, k1=1.2, b=0.75))
    twice = dict(index.search("deductible deductible premium", k=4, k1=1.2, b=0.75))
    assert twice["d1"] > once["d1"]


def test_scores_are_finite_and_non_negative(index: Bm25Index):
    for _, score in index.search("deductible pay plan", k=4, k1=1.2, b=0.75):
        assert math.isfinite(score)
        assert score >= 0.0


# ---- determinism ----


def test_ties_break_on_id_not_insertion_order():
    """Two identical documents are indistinguishable to BM25. Letting corpus file order decide
    would make a re-chunk look like a retrieval regression."""
    forward = Bm25Index.build([("zeta", "deductible"), ("alpha", "deductible")])
    reverse = Bm25Index.build([("alpha", "deductible"), ("zeta", "deductible")])
    query = {"k": 2, "k1": 1.2, "b": 0.75}
    assert forward.search("deductible", **query) == reverse.search("deductible", **query)
    assert [i for i, _ in forward.search("deductible", **query)] == ["alpha", "zeta"]


def test_search_is_repeatable(index: Bm25Index):
    query = {"k": 4, "k1": 1.2, "b": 0.75}
    assert index.search("deductible pay", **query) == index.search("deductible pay", **query)


# ---- filtering ----


def test_keep_filters_before_the_top_k_cut(index: Bm25Index):
    """The reason `keep` exists rather than filtering the caller's result list: post-filtering a
    top-k would silently return fewer than k."""
    all_hits = index.search("pay", k=1, k1=1.2, b=0.75)
    assert all_hits[0][0] != "d3"

    # Restricted to d3 alone, d3 must be the hit — not an empty list left over from a top-1 that
    # was chosen before the filter applied.
    only_d3 = index.search("pay", k=1, k1=1.2, b=0.75, keep=frozenset({2}))
    assert [doc_id for doc_id, _ in only_d3] == ["d3"]


def test_keep_of_an_empty_set_returns_nothing(index: Bm25Index):
    assert index.search("pay", k=5, k1=1.2, b=0.75, keep=frozenset()) == []
