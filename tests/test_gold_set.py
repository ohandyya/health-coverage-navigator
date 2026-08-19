"""Corpus-dependent correctness checks for the Phase 0 gold evaluation set.

The structural invariants that hold independent of the corpus (abstention shape, required
fields) are enforced by pydantic validators in health_coverage_navigator.evals.models and don't
need re-testing here. This file is what makes it impossible to commit a gold question whose
`expected_doc_ids` or `expected_snippet` has drifted from the actual corpus.
"""

import asyncio
import collections

import pytest

from health_coverage_navigator.corpus import CORPUS_NAMES, load_corpus
from health_coverage_navigator.evals.loader import load_gold_set
from health_coverage_navigator.evals.models import GoldSet
from health_coverage_navigator.structured.catalog import StructuredNotBuiltError
from health_coverage_navigator.structured.store import StructuredStore

CORPORA = CORPUS_NAMES


def _normalize(text: str) -> str:
    return " ".join(text.split())


@pytest.fixture(scope="session")
def gold() -> GoldSet:
    return load_gold_set()


@pytest.fixture(scope="session")
def corpus_docs() -> dict[str, dict[str, dict]]:
    """corpus name -> {doc id -> record} for all three text corpora."""
    return {name: {rec["id"]: rec for rec in load_corpus(name)} for name in CORPUS_NAMES}


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


def test_six_abstentions(gold: GoldSet):
    assert len(gold.abstentions()) == 6


def test_four_structured_questions(gold: GoldSet):
    """Phase 1-c's slice. Deliberately outside `in_corpus()` — see `GoldSet.in_corpus`."""
    structured = gold.structured()
    assert len(structured) == 4
    assert all(q.expected_table and q.expected_cells for q in structured)
    assert not any(q.corpus or q.expected_doc_ids or q.expected_snippet for q in structured)
    assert all(q.plan_year is not None for q in structured), (
        "a per-year table cannot be queried by a question that does not pin a year"
    )


def test_structured_cells_are_values_the_mirror_actually_holds(gold: GoldSet):
    """The assertion that keeps `expected_cells` honest.

    They are generated by `evals/structured_gold.py` rather than typed, and this re-checks each one
    against the live mirror: a trailing space lost to an editor would otherwise leave the eval
    grading the agent against a value CMS never published. It checks the value exists *somewhere in
    the named table* rather than in one identified row — the failure being guarded against is a
    mangled literal, and pinning the row would mean storing the generating query too.

    **Skipped when the mirror is not built.** It is git-ignored, so `make check-all` has to pass on
    a fresh clone; that is the same bargain `test_chunks.py` makes with `chunks.jsonl`.
    """
    try:
        store = StructuredStore.open()
    except StructuredNotBuiltError:
        pytest.skip("plan-data mirror not built here — run `make puf`")

    try:
        for question in gold.structured():
            table = question.expected_table or ""
            described = asyncio.run(store.describe(table, plan_year=question.plan_year, limit=500))
            columns = [column.name for column in described.columns]
            for value in question.expected_cells:
                literal = value.replace("'", "''")
                predicate = " OR ".join(f"\"{column}\" = '{literal}'" for column in columns)
                found = asyncio.run(
                    store.query(
                        f"SELECT COUNT(*) AS n FROM {described.view} WHERE {predicate}",
                        plan_year=question.plan_year,
                    )
                )
                assert found.rows[0].cells["n"] != "0", (
                    f"{question.id}: {value!r} is not a value in {described.view}. Regenerate it "
                    f"with `python -m health_coverage_navigator.evals.structured_gold` rather than "
                    f"editing it by hand."
                )
    finally:
        store.close()


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
