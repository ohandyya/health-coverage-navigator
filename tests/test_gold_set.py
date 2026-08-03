"""Corpus-dependent correctness checks for the Phase 0 gold evaluation set.

The structural invariants that hold independent of the corpus (abstention shape, required
fields) are enforced by pydantic validators in health_coverage_navigator.evals.models and don't
need re-testing here. This file is what makes it impossible to commit a gold question whose
`expected_doc_ids` or `expected_snippet` has drifted from the actual corpus.
"""

import collections
import json

import pytest

from health_coverage_navigator.evals.loader import load_gold_set
from health_coverage_navigator.evals.models import GoldSet
from health_coverage_navigator.paths import PROCESSED_DIR

CORPORA = ("healthcare_gov", "medicare_ncd", "medicare_pubs")


def _normalize(text: str) -> str:
    return " ".join(text.split())


@pytest.fixture(scope="session")
def gold() -> GoldSet:
    return load_gold_set()


@pytest.fixture(scope="session")
def corpus_docs() -> dict[str, dict[str, dict]]:
    """corpus name -> {doc id -> record} for all three text corpora."""
    docs: dict[str, dict[str, dict]] = {}
    for name in CORPORA:
        path = PROCESSED_DIR / name / "corpus.jsonl"
        with open(path, encoding="utf-8") as f:
            docs[name] = {}
            for line in f:
                rec = json.loads(line)
                docs[name][rec["id"]] = rec
    return docs


# ---------------------------------------------------------------- 1. parses -----------------


def test_ids_are_unique(gold: GoldSet):
    ids = [q.id for q in gold.questions]
    dupes = [i for i, n in collections.Counter(ids).items() if n > 1]
    assert not dupes, f"duplicate question ids: {dupes}"


def test_id_prefix_matches_corpus(gold: GoldSet):
    prefix_for_corpus = {"healthcare_gov": "hcg-", "medicare_ncd": "ncd-", "medicare_pubs": "pub-"}
    for q in gold.in_corpus():
        assert q.corpus is not None
        expected_prefix = prefix_for_corpus[q.corpus]
        assert q.id.startswith(expected_prefix), (
            f"{q.id}: expected id prefix {expected_prefix!r} for corpus {q.corpus!r}"
        )
    for q in gold.abstentions():
        assert q.id.startswith("abs-"), f"{q.id}: abstention ids must start with 'abs-'"


# ------------------------------------------------------------ 2. docs exist -----------------


def test_expected_doc_ids_exist(gold: GoldSet, corpus_docs: dict[str, dict[str, dict]]):
    for q in gold.in_corpus():
        assert q.corpus is not None
        available = corpus_docs[q.corpus]
        for doc_id in q.expected_doc_ids:
            assert doc_id in available, (
                f"{q.id}: expected_doc_id {doc_id!r} not found in {q.corpus}/corpus.jsonl"
            )


# --------------------------------------------------------- 3. snippets verbatim -------------


def test_expected_snippet_is_verbatim(gold: GoldSet, corpus_docs: dict[str, dict[str, dict]]):
    for q in gold.in_corpus():
        assert q.corpus is not None
        assert q.expected_snippet is not None
        primary_doc_id = q.expected_doc_ids[0]
        doc = corpus_docs[q.corpus][primary_doc_id]
        needle = _normalize(q.expected_snippet)
        haystack = _normalize(doc["text"])
        assert needle in haystack, (
            f"{q.id}: expected_snippet not found verbatim (whitespace-normalized) in "
            f"{primary_doc_id}'s text"
        )


# --------------------------------------------------------- 4. no retired targets ------------


def test_no_retired_ncd_targets(gold: GoldSet, corpus_docs: dict[str, dict[str, dict]]):
    ncd_docs = corpus_docs["medicare_ncd"]
    for q in gold.by_corpus("medicare_ncd"):
        for doc_id in q.expected_doc_ids:
            title = ncd_docs[doc_id]["title"]
            assert "RETIRED" not in title.upper(), (
                f"{q.id}: expected_doc_id {doc_id!r} ({title!r}) is a RETIRED NCD"
            )


# ------------------------------------------------------------- 5. distribution --------------


def test_ten_questions_per_corpus(gold: GoldSet):
    for corpus in CORPORA:
        assert len(gold.by_corpus(corpus)) == 10, f"expected 10 questions for {corpus}"


def test_difficulty_distribution(gold: GoldSet):
    counts = collections.Counter(q.difficulty for q in gold.in_corpus())
    assert counts == {"easy": 8, "medium": 14, "hard": 8}, f"got {dict(counts)}"


def test_five_abstentions(gold: GoldSet):
    assert len(gold.abstentions()) == 5


def test_glossary_docs_capped(gold: GoldSet):
    glossary_hits = [
        q
        for q in gold.by_corpus("healthcare_gov")
        for doc_id in q.expected_doc_ids
        if doc_id.startswith("glossary_")
    ]
    assert len(glossary_hits) <= 3, "at most 3 healthcare_gov questions may target glossary docs"


def test_medicare_pubs_source_diversity(gold: GoldSet, corpus_docs: dict[str, dict[str, dict]]):
    pub_docs = corpus_docs["medicare_pubs"]
    pub_ids = {
        pub_docs[doc_id]["pub_id"]
        for q in gold.by_corpus("medicare_pubs")
        for doc_id in q.expected_doc_ids
    }
    assert len(pub_ids) >= 7, f"expected >=7 distinct pub_ids, got {len(pub_ids)}: {pub_ids}"


def test_medicare_ncd_chapter_diversity(gold: GoldSet, corpus_docs: dict[str, dict[str, dict]]):
    ncd_docs = corpus_docs["medicare_ncd"]
    chapters = {
        ncd_docs[doc_id]["chapter"]
        for q in gold.by_corpus("medicare_ncd")
        for doc_id in q.expected_doc_ids
    }
    assert len(chapters) == 10, (
        f"expected 10 distinct NCD chapters, got {len(chapters)}: {chapters}"
    )


def test_volatility_capped(gold: GoldSet):
    n_volatile = sum(1 for q in gold.in_corpus() if q.volatile)
    assert n_volatile <= 6, f"expected at most 6 volatile questions, got {n_volatile}"


# --------------------------------------------------------------- 6. abstentions --------------


def test_abstentions_have_no_expected_doc(gold: GoldSet):
    for q in gold.abstentions():
        assert q.expected_doc_ids == []
        assert q.expected_source_type is None
        assert q.corpus is None
        assert q.difficulty is None


def test_non_abstentions_have_expected_doc(gold: GoldSet):
    for q in gold.in_corpus():
        assert q.expected_doc_ids, f"{q.id}: non-abstention with no expected_doc_ids"


# ----------------------------------------------------------- 7. answers gradeable -----------


def test_answers_are_gradeable(gold: GoldSet):
    for q in gold.in_corpus():
        assert q.expected_answer, f"{q.id}: missing expected_answer"
        assert q.answer_key_facts, f"{q.id}: missing answer_key_facts"
