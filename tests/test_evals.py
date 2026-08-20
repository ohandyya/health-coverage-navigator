"""The Phase 1a eval slice: the retrieval-only answerer, the graders, and the judge.

Phase 0 measured retrieval. Phase 1a adds two things to grade — **groundedness** (is every
quotation real?) and **answer correctness** (does the answer say what a correct answer says?) — and
the interesting property of the design is that the first is deterministic and free while only the
second needs a model. These tests hold that line: everything here runs with no key and no network,
including the judge, which is driven by a scripted model.

`evals/runner.py`'s own scoring (`recall@5`, `mrr`, `abstention_accuracy`) is Phase 0's and is
exercised through `tests/test_api.py`'s eval-run test; what is new here is the grader plumbing
around it.
"""

import asyncio

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from health_coverage_navigator.api.models import ChatResponse, Citation, EvalQuestionResult
from health_coverage_navigator.evals.answerers import (
    NO_ANSWER,
    AnswerFn,
    bm25_answerer,
    stub_answerer,
    vector_answerer,
)
from health_coverage_navigator.evals.grading import (
    groundedness_grader,
    key_fact_coverage_grader,
    routing_grader,
)
from health_coverage_navigator.evals.judge import (
    FactVerdict,
    JudgeVerdict,
    build_judge,
    judge_grader,
)
from health_coverage_navigator.evals.models import GoldQuestion, GoldSet
from health_coverage_navigator.evals.runner import aggregate, run_gold_set, score_question


def _question(**overrides) -> GoldQuestion:
    payload = {
        "id": "q1",
        "question": "What is a deductible?",
        "difficulty": "easy",
        "corpus": "healthcare_gov",
        "expected_source_type": "reference",
        "expected_doc_ids": ["glossary_deductible"],
        "expected_snippet": "before your insurance plan starts to pay",
        "expected_answer": "What you pay before the plan starts to pay.",
        "answer_key_facts": ["the amount paid before the plan starts paying"],
    }
    payload.update(overrides)
    return GoldQuestion.model_validate(payload)


def _response(citations: list[Citation], answer: str = "An answer. [c1]") -> ChatResponse:
    return ChatResponse(
        conversation_id="c",
        message_id="m",
        abstained=False,
        answer=answer,
        claims=[],
        citations=citations,
        trace=[],
    )


# ---------------------------------------------------------------- retrieval-only ------------


async def test_the_bm25_answerer_cites_real_chunks(agent_kit):
    response = await bm25_answerer(agent_kit.index)(_question())

    assert response.citations
    for citation in response.citations:
        assert agent_kit.index.chunk(citation.chunk_id) is not None
        assert citation.source_type == "reference"
        assert citation.score is not None


async def test_the_bm25_answerer_says_it_did_not_answer(agent_kit):
    """Same reasoning as `HealthResponse.stub`: output that is not an answer must never be
    mistakable for one, including by whoever opens the run file six months from now."""
    response = await bm25_answerer(agent_kit.index)(_question())
    assert response.answer == NO_ANSWER.format(retriever="lexical search")
    assert "No model was called" in response.answer


async def test_the_bm25_answerer_never_abstains(agent_kit):
    """A bare retriever has no notion of "the corpus does not cover this" — it always returns its
    top k, however weak. That is why the CLI runs it over `in_corpus()` only."""
    response = await bm25_answerer(agent_kit.index)(_question(question="unrelated nonsense"))
    assert response.abstained is False


async def test_the_bm25_answerer_scores_through_the_normal_runner(agent_kit):
    """The whole reason retrieval-only reuses the answerer seam: recall@5 from a retrieval run and
    recall@5 from an agent run come out of the same scorer and are directly comparable."""
    gold = GoldSet(questions=[_question(expected_doc_ids=["glossary_deductible"])])
    run = await run_gold_set(bm25_answerer(agent_kit.index), gold, runner="bm25")

    assert run.runner == "bm25"
    assert run.model is None, "no model was involved; the record must not imply one"
    assert run.config_fingerprint
    assert "recall@5" in run.metrics


# ---------------------------------------------------------------- the vector runner ----------
#
# Phase 1b's headline number comes from here, so what these check is that it lands in the record
# comparably to the bm25 one — not that semantic search retrieves well, which is a question about
# a real model and belongs to `make eval-retrieval-vector`.


async def test_the_vector_answerer_cites_real_chunks(agent_kit):
    """Every citation must resolve to a chunk in the corpus. A vector runner whose ids did not
    resolve would score zero on every question while looking like a retrieval failure."""
    answer = vector_answerer(agent_kit.index, agent_kit.vectors)
    chunk = agent_kit.index.chunk(agent_kit.DEDUCTIBLE_ID)
    assert chunk is not None
    response = await answer(_question(question=chunk.retrieval_text))

    assert response.citations
    for citation in response.citations:
        assert agent_kit.index.chunk(citation.chunk_id) is not None
        assert citation.source_type == "reference"


async def test_the_vector_answerer_says_it_did_not_answer(agent_kit):
    """Same reasoning as the bm25 one and as `HealthResponse.stub`: output that is not an answer
    must never be mistakable for one — and it must say *which* retriever produced it, since two
    retrieval-only runners now write into the same run directory."""
    response = await vector_answerer(agent_kit.index, agent_kit.vectors)(_question())
    assert response.answer == NO_ANSWER.format(retriever="semantic search")
    assert "No model was called" in response.answer


async def test_the_vector_answerer_never_abstains(agent_kit):
    """A bare retriever always returns its top k, however weak, which is why the CLI runs it over
    `in_corpus()` only."""
    response = await vector_answerer(agent_kit.index, agent_kit.vectors)(
        _question(question="unrelated nonsense")
    )
    assert response.abstained is False


async def test_the_vector_runner_scores_through_the_normal_runner(agent_kit):
    """The point of the whole exercise: `recall@5` from a vector run comes out of the same scorer
    as `recall@5` from a bm25 run, so the two are directly comparable."""
    gold = GoldSet(questions=[_question(expected_doc_ids=["glossary_deductible"])])
    run = await run_gold_set(
        vector_answerer(agent_kit.index, agent_kit.vectors),
        gold,
        runner="vector",
        vectors_snapshot_id=agent_kit.vectors.snapshot_id,
    )

    assert run.runner == "vector"
    assert run.model is None, "no model was involved; the record must not imply one"
    assert run.toolset is None, "there is no agent here, so there was no toolset to choose"
    assert run.vectors_snapshot_id == agent_kit.vectors.snapshot_id
    assert "recall@5" in run.metrics


async def test_the_run_record_pins_the_toolset_and_the_embeddings(agent_kit):
    """Phase 1b's comparison is between runs that differ *only* in `toolset`. A run that does not
    name it, or does not name the embeddings behind it, cannot take part in that comparison —
    which is the same argument `config_fingerprint` and `chunker_snapshot_id` already won."""
    gold = GoldSet(questions=[_question()])
    run = await run_gold_set(
        bm25_answerer(agent_kit.index),
        gold,
        runner="agent",
        model="openai:test",
        toolset="both",
        vectors_snapshot_id="vectors@abc123",
    )

    assert run.toolset == "both"
    assert run.vectors_snapshot_id == "vectors@abc123"
    assert run.chunker_snapshot_id, "the chunk pin must survive alongside the new ones"
    assert run.config_fingerprint


# ---------------------------------------------------------------- groundedness --------------


async def test_groundedness_is_one_when_every_quotation_is_verbatim(agent_kit):
    chunk = agent_kit.index.chunk(agent_kit.DEDUCTIBLE_ID)
    grade = groundedness_grader(agent_kit.index)
    metrics = await grade(
        _question(),
        _response(
            [
                Citation(
                    id="c1",
                    source_type="reference",
                    title="t",
                    chunk_id=chunk.id,
                    snippet="before your insurance plan starts to pay",
                )
            ]
        ),
    )
    assert metrics == {"citation_resolution": 1.0, "groundedness": 1.0}


async def test_groundedness_catches_words_put_in_a_real_source_s_mouth(agent_kit):
    """The failure that reads as *more* trustworthy than a fabricated citation: a real document,
    quoted as saying something it does not say."""
    grade = groundedness_grader(agent_kit.index)
    metrics = await grade(
        _question(),
        _response(
            [
                Citation(
                    id="c1",
                    source_type="reference",
                    title="t",
                    chunk_id=agent_kit.DEDUCTIBLE_ID,
                    snippet="coverage kicks in once you spend enough",
                )
            ]
        ),
    )
    assert metrics["citation_resolution"] == 1.0
    assert metrics["groundedness"] == 0.0


async def test_citation_resolution_catches_a_fabricated_source(agent_kit):
    grade = groundedness_grader(agent_kit.index)
    metrics = await grade(
        _question(),
        _response(
            [
                Citation(
                    id="c1",
                    source_type="reference",
                    title="t",
                    chunk_id="healthcare_gov:invented#000",
                    snippet="anything",
                )
            ]
        ),
    )
    assert metrics == {"citation_resolution": 0.0, "groundedness": 0.0}


async def test_groundedness_tolerates_reflowed_whitespace(agent_kit):
    """The corpora wrap mid-sentence. A byte-exact comparison would report genuine quotations as
    ungrounded, which would make the metric useless exactly where it matters."""
    grade = groundedness_grader(agent_kit.index)
    metrics = await grade(
        _question(),
        _response(
            [
                Citation(
                    id="c1",
                    source_type="reference",
                    title="t",
                    chunk_id=agent_kit.DEDUCTIBLE_ID,
                    snippet="before your\n  insurance plan   starts to pay",
                )
            ]
        ),
    )
    assert metrics["groundedness"] == 1.0


async def test_an_uncited_response_reports_no_groundedness_at_all(agent_kit):
    """`{}` and `0.0` are different claims. An abstention that cited nothing has no groundedness to
    measure, and scoring it zero would drag the corpus-wide average down with a number about
    nothing."""
    grade = groundedness_grader(agent_kit.index)
    assert (
        await grade(_question(expected_abstain=True, **_ABSTAIN), _response([], "No sources."))
        == {}
    )


#: The shape `GoldQuestion` requires of an abstention: everything else must be null or empty.
_ABSTAIN = {
    "difficulty": None,
    "corpus": None,
    "expected_source_type": None,
    "expected_doc_ids": [],
    "expected_snippet": None,
    "expected_answer": None,
    "answer_key_facts": [],
}


# ---------------------------------------------------------------- key-fact coverage ---------


async def test_key_fact_coverage_is_lexical_and_says_so():
    """It scores an answer and its exact negation identically, which is precisely why it is named
    *coverage* and why `--judge` exists."""
    grade = key_fact_coverage_grader()
    question = _question(answer_key_facts=["the amount paid before the plan starts paying"])

    positive = await grade(
        question, _response([], "The amount paid before the plan starts paying.")
    )
    negated = await grade(
        question, _response([], "Not the amount paid before the plan starts paying.")
    )
    assert positive == negated == {"key_fact_coverage": 1.0}


async def test_key_fact_coverage_is_skipped_for_abstentions():
    grade = key_fact_coverage_grader()
    assert await grade(_question(expected_abstain=True, **_ABSTAIN), _response([], "x")) == {}


# ---------------------------------------------------------------- aggregation ---------------


def test_aggregate_averages_any_metric_a_grader_reports():
    """The generic path is what lets a new grader reach the dashboard without touching the runner,
    the API, or the storage format — `EvalRunSummary.metrics` is free-form by design."""
    results = [
        EvalQuestionResult(question_id="a", passed=True, metrics={"groundedness": 1.0}),
        EvalQuestionResult(question_id="b", passed=True, metrics={"groundedness": 0.5}),
    ]
    assert aggregate(results)["groundedness"] == pytest.approx(0.75)


def test_aggregate_averages_over_reporters_not_over_the_whole_set():
    """A grader returns `{}` for a question it does not apply to. Counting those as zero would
    report a number about nothing as if it were a failure."""
    results = [
        EvalQuestionResult(question_id="a", passed=True, metrics={"groundedness": 1.0}),
        EvalQuestionResult(question_id="b", passed=True, metrics={}),
    ]
    assert aggregate(results)["groundedness"] == pytest.approx(1.0)


def test_aggregate_hides_the_runner_s_own_bookkeeping():
    """`reciprocal_rank` is per-question input to `mrr`; surfacing it too would put the same number
    on the dashboard twice under two names."""
    results = [EvalQuestionResult(question_id="a", passed=True, metrics={"reciprocal_rank": 1.0})]
    assert "reciprocal_rank" not in aggregate(results)


async def test_a_failing_grader_does_not_abort_the_run(agent_kit):
    """A judge that times out on question 12 must not throw away the other 34 measurements."""

    async def explode(question, response):
        raise RuntimeError("judge unavailable")

    gold = GoldSet(questions=[_question(), _question(id="q2")])
    run = await run_gold_set(bm25_answerer(agent_kit.index), gold, runner="bm25", graders=[explode])

    assert run.n_questions == 2
    assert all("judge unavailable" in (r.error or "") for r in run.results)


async def test_the_run_record_pins_what_it_was_measured_under(agent_kit):
    """`config.yaml`'s model is a floating alias, so the fingerprint says which alias was
    configured and `model` says what it resolved to."""
    gold = GoldSet(questions=[_question()])
    run = await run_gold_set(
        bm25_answerer(agent_kit.index), gold, runner="agent", model="openai:some-model"
    )
    assert run.model == "openai:some-model"
    assert len(run.config_fingerprint or "") == 64


# ---------------------------------------------------------------- the judge -----------------


def _verdict_model(verdict: JudgeVerdict) -> FunctionModel:
    def respond(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart("final_result", verdict.model_dump())])

    return FunctionModel(respond)


async def test_the_judge_scores_the_fraction_of_key_facts_supported():
    question = _question(answer_key_facts=["fact one", "fact two", "fact three"])
    verdict = JudgeVerdict(
        facts=[
            FactVerdict(fact="fact one", supported=True),
            FactVerdict(fact="fact two", supported=True),
            FactVerdict(fact="fact three", supported=False, note="not stated"),
        ]
    )
    with build_judge().override(model=_verdict_model(verdict)):
        metrics = await judge_grader()(question, _response([], "Some answer."))

    assert metrics["answer_correctness"] == pytest.approx(2 / 3)
    assert metrics["contradiction"] == 0.0


async def test_the_judge_cannot_score_above_one():
    """Scored over the gold set's count, not the judge's: a judge that returns four verdicts for a
    three-item checklist must not be able to invent credit."""
    question = _question(answer_key_facts=["fact one"])
    verdict = JudgeVerdict(
        facts=[
            FactVerdict(fact="fact one", supported=True),
            FactVerdict(fact="invented", supported=True),
        ]
    )
    with build_judge().override(model=_verdict_model(verdict)):
        metrics = await judge_grader()(question, _response([], "Some answer."))
    assert metrics["answer_correctness"] == 1.0


async def test_the_judge_reports_a_contradiction_separately():
    """A contradiction is a worse failure than an omission, so it gets its own number rather than
    being folded into the coverage fraction."""
    question = _question(answer_key_facts=["fact one"])
    verdict = JudgeVerdict(
        facts=[FactVerdict(fact="fact one", supported=True)],
        contradicts=True,
        contradiction="says the opposite later",
    )
    with build_judge().override(model=_verdict_model(verdict)):
        metrics = await judge_grader()(question, _response([], "Some answer."))
    assert metrics["answer_correctness"] == 1.0
    assert metrics["contradiction"] == 1.0


async def test_the_judge_is_never_called_for_an_abstention():
    """Whether an abstention was correct is already settled deterministically by
    `abstention_accuracy`; paying for an opinion on it would be buying an answer twice."""
    called = False

    def respond(messages, info: AgentInfo) -> ModelResponse:
        nonlocal called
        called = True
        return ModelResponse(parts=[ToolCallPart("final_result", JudgeVerdict().model_dump())])

    with build_judge().override(model=FunctionModel(respond)):
        question = _question(expected_abstain=True, **_ABSTAIN)
        assert await judge_grader()(question, _response([], "No sources.")) == {}
    assert called is False


async def test_a_false_abstention_scores_zero_without_a_model_call():
    """An abstention on an answerable question covers nothing, by definition — cheaper and less
    arguable than asking a judge to read "I don't have that" against a checklist."""
    called = False

    def respond(messages, info: AgentInfo) -> ModelResponse:
        nonlocal called
        called = True
        return ModelResponse(parts=[ToolCallPart("final_result", JudgeVerdict().model_dump())])

    abstained = _response([], "I don't have that in my reference material.")
    abstained.abstained = True

    with build_judge().override(model=FunctionModel(respond)):
        metrics = await judge_grader()(_question(), abstained)

    assert metrics == {"answer_correctness": 0.0, "contradiction": 0.0}
    assert called is False


# ---------------------------------------------------------------- the stub baseline ---------


async def test_the_stub_answerer_still_works():
    """Kept reachable on purpose: Phase 0 measured recall@5 at 0.033 against it, and that is the
    number every real score is read against."""
    response = await stub_answerer()(_question())
    assert response.citations


# ---------------------------------------------------------------- concurrency ---------------
#
# `run_gold_set` runs questions through an `asyncio.Semaphore` and one `gather`, with no separate
# sequential branch. That collapses two code paths into one, but only because of two properties
# that are easy to state and easy to break silently — a reordered run record would not fail
# anything else in this file. Both are asserted against an answerer that finishes in deliberately
# the wrong order.


def _out_of_order_answerer(order: list[str]) -> AnswerFn:
    """An answerer whose questions finish in reverse: `q0` sleeps longest, `q9` returns first.

    Sleeps are real awaits, so with any concurrency above 1 the event loop genuinely interleaves
    them. `order` records completion order, which is what distinguishes "results were sorted
    afterwards" from "results were never reordered".
    """

    async def answer(question: GoldQuestion) -> ChatResponse:
        await asyncio.sleep((10 - int(question.id[1:])) * 0.01)
        order.append(question.id)
        return _response([])

    return answer


async def test_results_stay_in_gold_order_however_they_complete():
    """`gather` returns in submission order regardless of completion order, which is what keeps a
    run record identical at any concurrency and therefore diffable against another run."""
    completion: list[str] = []
    gold = GoldSet(questions=[_question(id=f"q{i}") for i in range(10)])

    run = await run_gold_set(
        _out_of_order_answerer(completion), gold, runner="bm25", max_concurrency=5
    )

    assert [r.question_id for r in run.results] == [f"q{i}" for i in range(10)]
    assert completion != [f"q{i}" for i in range(10)], (
        "the answerer was supposed to finish out of order; this test proves nothing if it did not"
    )


async def test_concurrency_one_still_reports_progress_in_gold_order():
    """The property the dashboard depends on, and the reason it pins `max_concurrency=1`.

    A semaphore with one slot hands it to waiters FIFO, and `gather` schedules tasks in submission
    order, so `max_concurrency=1` is genuinely sequential rather than merely bounded.
    """
    completion: list[str] = []
    reported: list[str] = []
    gold = GoldSet(questions=[_question(id=f"q{i}") for i in range(6)])

    await run_gold_set(
        _out_of_order_answerer(completion),
        gold,
        runner="bm25",
        max_concurrency=1,
        on_progress=lambda result: reported.append(result.question_id),
    )

    assert reported == [f"q{i}" for i in range(6)]
    assert completion == reported


# ---------------------------------------------------------------- the relational lane --------
#
# Phase 1-c adds a third question shape and a second thing to measure. Both are graded through the
# same runner and the same scorer, which is what keeps a structured number comparable to a
# reference one — and what makes the split in `aggregate()` load-bearing rather than cosmetic.


def _structured_question(**overrides) -> GoldQuestion:
    payload = {
        "id": "str-1",
        "question": "What is the deductible on plan X for 2026?",
        "difficulty": "medium",
        "expected_source_type": "structured_api",
        "plan_year": 2026,
        "expected_table": "exchange_puf.plan_attributes",
        "expected_cells": ["$4,500 "],
        "answer_key_facts": ["the deductible is $4,500"],
    }
    payload.update(overrides)
    return GoldQuestion.model_validate(payload)


def await_grade(grader, question, response) -> dict[str, float]:
    """Run one grader from a synchronous test. `asyncio.run` at a test boundary is the sanctioned
    use — the graders are async because the LLM judge must be."""
    return asyncio.run(grader(question, response))


def _row_citation(snippet: str, id: str = "c1") -> Citation:
    return Citation(
        id=id, source_type="structured_api", title="Plan Attributes 2026", snippet=snippet
    )


#: One row citation carrying the value the gold question expects, verbatim.
_CITED_CELL = "TEHBDedInnTier1Individual: $4,500 "


def test_a_structured_question_passes_only_on_the_exact_cell() -> None:
    """Exact match, not overlap: in this lane an approximate figure is a wrong figure, and the
    trailing space is part of what the source published."""
    question = _structured_question()
    exact = score_question(question, _response([_row_citation(_CITED_CELL)]))
    assert exact.passed and exact.metrics["structured_exact_match"] == 1.0

    tidied = score_question(question, _response([_row_citation(_CITED_CELL.rstrip())]))
    assert not tidied.passed and tidied.metrics["structured_exact_match"] == 0.0


def test_a_structured_question_fails_when_the_agent_abstains() -> None:
    """Abstaining on an answerable plan question is the failure this phase most plausibly adds —
    the tables are right there, and a model that does not think to look reads as cautious."""
    response = _response([_row_citation(_CITED_CELL)]).model_copy(update={"abstained": True})
    assert not score_question(_structured_question(), response).passed


def test_structured_questions_stay_out_of_the_recall_denominator() -> None:
    """The split that keeps a capability from reading as a regression.

    A structured question has no expected doc ids, so leaving it among the retrieval-scored
    questions would drop recall@5 by construction — a headline metric moving because the question
    *set* changed rather than because the agent did.
    """
    reference = score_question(
        _question(),
        _response(
            [
                Citation(
                    id="c1",
                    source_type="reference",
                    title="t",
                    doc_id="glossary_deductible",
                    snippet="s",
                )
            ]
        ),
    )
    structured = score_question(_structured_question(), _response([_row_citation(_CITED_CELL)]))

    metrics = aggregate([reference, structured])
    assert metrics["recall@5"] == 1.0, "the one retrieval question was retrieved"
    assert metrics["structured_exact_match"] == 1.0


async def test_routing_is_graded_on_the_lane_the_citations_came_from() -> None:
    """Answer correctness and routing correctness come apart exactly here: a fluent passage about
    deductibles in general is the *wrong lane* for "what is this plan's deductible", and only this
    metric says so."""
    grade = routing_grader()
    question = _structured_question()

    right = await grade(question, _response([_row_citation("x: 1")]))
    assert right == {"routing_correct": 1.0}

    wrong = await grade(
        question,
        _response([Citation(id="c1", source_type="reference", title="t", snippet="a passage")]),
    )
    assert wrong == {"routing_correct": 0.0}


async def test_routing_does_not_grade_an_abstention() -> None:
    """Declining is a different judgement, already measured by `abstention_accuracy`. Grading it
    here would let a run that abstains on everything score perfectly on routing."""
    grade = routing_grader()
    response = _response([], answer="Not in my data.").model_copy(update={"abstained": True})
    assert await grade(_structured_question(), response) == {}

    abstention = GoldQuestion(id="abs-1", question="who takes my insurance?", expected_abstain=True)
    assert await grade(abstention, response) == {}, "an abstention question has no lane to grade"


def test_a_row_citation_is_not_counted_as_a_fabricated_source(agent_kit) -> None:
    """The bug the first structured eval run found, as a test.

    `groundedness_grader` resolved every citation through the corpus index, so a row citation —
    which has no chunk id by design — scored as unresolvable. That reported the relational lane
    working correctly as a fabrication (0.889 where the guardrail guarantees 1.000), and a metric
    that punishes a capability for existing reads exactly like a real regression.
    """
    grade = groundedness_grader(agent_kit.index)
    chunk = agent_kit.index.chunk(agent_kit.DEDUCTIBLE_ID)
    assert chunk is not None

    mixed = _response(
        [
            Citation(
                id="c1",
                source_type="reference",
                title="t",
                chunk_id=chunk.id,
                snippet="before your insurance plan starts to pay",
            ),
            _row_citation(_CITED_CELL, id="c2"),
        ],
        answer="Prose. [c1] And a row. [c2]",
    )
    assert await_grade(grade, _question(), mixed) == {
        "citation_resolution": 1.0,
        "groundedness": 1.0,
    }


def test_an_answer_made_only_of_rows_reports_no_groundedness(agent_kit) -> None:
    """`{}` means "this question does not have that metric", which is not the same as zero.

    A row's byte-exact check happens in the output validator against the rows that run recorded,
    and no grader can see those afterwards. Reporting a number here anyway would be inventing an
    independent check that is really the same one, weaker.
    """
    rows_only = _response([_row_citation(_CITED_CELL)])
    metrics = await_grade(groundedness_grader(agent_kit.index), _structured_question(), rows_only)
    assert metrics == {"citation_resolution": 1.0}


def test_an_empty_row_citation_still_counts_as_unresolved(agent_kit) -> None:
    """Rows are exempt from the *verbatim* check, not from having to carry evidence."""
    empty = _response([_row_citation("   ")])
    metrics = await_grade(groundedness_grader(agent_kit.index), _structured_question(), empty)
    assert metrics == {"citation_resolution": 0.0}


def test_an_errored_question_still_counts_against_its_lane() -> None:
    """A run that lost questions must never score better than one that answered them all.

    `aggregate()` picks each lane's denominator by `expected_source_type`, so an errored result that
    dropped that field would leave the denominator entirely rather than counting as the miss it is.
    Measured on Phase 2's first eval before the fix: three questions died to
    `Exceeded maximum output retries` and recall@5 was reported as **0.741 over 27** instead of
    **0.667 over 30** — the score went *up* because the run went worse.
    """
    from health_coverage_navigator.evals.runner import aggregate

    answered = [
        EvalQuestionResult(
            question_id=f"ref-{i}",
            passed=True,
            expected_source_type="reference",
            rank=1,
            metrics={"reciprocal_rank": 1.0},
        )
        for i in range(2)
    ]
    errored = EvalQuestionResult(
        question_id="ref-2",
        passed=False,
        expected_source_type="reference",
        error="UnexpectedModelBehavior: Exceeded maximum output retries (2)",
    )

    assert aggregate(answered)["recall@5"] == 1.0
    assert aggregate([*answered, errored])["recall@5"] == pytest.approx(2 / 3), (
        "an errored question must stay in its lane's denominator"
    )
