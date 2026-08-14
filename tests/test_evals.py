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

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from health_coverage_navigator.api.models import ChatResponse, Citation, EvalQuestionResult
from health_coverage_navigator.evals.answerers import NO_ANSWER, bm25_answerer, stub_answerer
from health_coverage_navigator.evals.grading import groundedness_grader, key_fact_coverage_grader
from health_coverage_navigator.evals.judge import (
    FactVerdict,
    JudgeVerdict,
    build_judge,
    judge_grader,
)
from health_coverage_navigator.evals.models import GoldQuestion, GoldSet
from health_coverage_navigator.evals.runner import aggregate, run_gold_set


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


def test_the_bm25_answerer_cites_real_chunks(agent_kit):
    response = bm25_answerer(agent_kit.index)(_question())

    assert response.citations
    for citation in response.citations:
        assert agent_kit.index.chunk(citation.chunk_id) is not None
        assert citation.source_type == "reference"
        assert citation.score is not None


def test_the_bm25_answerer_says_it_did_not_answer(agent_kit):
    """Same reasoning as `HealthResponse.stub`: output that is not an answer must never be
    mistakable for one, including by whoever opens the run file six months from now."""
    response = bm25_answerer(agent_kit.index)(_question())
    assert response.answer == NO_ANSWER
    assert "No model was called" in response.answer


def test_the_bm25_answerer_never_abstains(agent_kit):
    """A bare retriever has no notion of "the corpus does not cover this" — it always returns its
    top k, however weak. That is why the CLI runs it over `in_corpus()` only."""
    response = bm25_answerer(agent_kit.index)(_question(question="unrelated nonsense"))
    assert response.abstained is False


def test_the_bm25_answerer_scores_through_the_normal_runner(agent_kit):
    """The whole reason retrieval-only reuses the answerer seam: recall@5 from a retrieval run and
    recall@5 from an agent run come out of the same scorer and are directly comparable."""
    gold = GoldSet(questions=[_question(expected_doc_ids=["glossary_deductible"])])
    run = run_gold_set(bm25_answerer(agent_kit.index), gold, runner="bm25")

    assert run.runner == "bm25"
    assert run.model is None, "no model was involved; the record must not imply one"
    assert run.config_fingerprint
    assert "recall@5" in run.metrics


# ---------------------------------------------------------------- groundedness --------------


def test_groundedness_is_one_when_every_quotation_is_verbatim(agent_kit):
    chunk = agent_kit.index.chunk(agent_kit.DEDUCTIBLE_ID)
    grade = groundedness_grader(agent_kit.index)
    metrics = grade(
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


def test_groundedness_catches_words_put_in_a_real_source_s_mouth(agent_kit):
    """The failure that reads as *more* trustworthy than a fabricated citation: a real document,
    quoted as saying something it does not say."""
    grade = groundedness_grader(agent_kit.index)
    metrics = grade(
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


def test_citation_resolution_catches_a_fabricated_source(agent_kit):
    grade = groundedness_grader(agent_kit.index)
    metrics = grade(
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


def test_groundedness_tolerates_reflowed_whitespace(agent_kit):
    """The corpora wrap mid-sentence. A byte-exact comparison would report genuine quotations as
    ungrounded, which would make the metric useless exactly where it matters."""
    grade = groundedness_grader(agent_kit.index)
    metrics = grade(
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


def test_an_uncited_response_reports_no_groundedness_at_all(agent_kit):
    """`{}` and `0.0` are different claims. An abstention that cited nothing has no groundedness to
    measure, and scoring it zero would drag the corpus-wide average down with a number about
    nothing."""
    grade = groundedness_grader(agent_kit.index)
    assert grade(_question(expected_abstain=True, **_ABSTAIN), _response([], "No sources.")) == {}


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


def test_key_fact_coverage_is_lexical_and_says_so():
    """It scores an answer and its exact negation identically, which is precisely why it is named
    *coverage* and why `--judge` exists."""
    grade = key_fact_coverage_grader()
    question = _question(answer_key_facts=["the amount paid before the plan starts paying"])

    positive = grade(question, _response([], "The amount paid before the plan starts paying."))
    negated = grade(question, _response([], "Not the amount paid before the plan starts paying."))
    assert positive == negated == {"key_fact_coverage": 1.0}


def test_key_fact_coverage_is_skipped_for_abstentions():
    grade = key_fact_coverage_grader()
    assert grade(_question(expected_abstain=True, **_ABSTAIN), _response([], "x")) == {}


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


def test_a_failing_grader_does_not_abort_the_run(agent_kit):
    """A judge that times out on question 12 must not throw away the other 34 measurements."""

    def explode(question, response):
        raise RuntimeError("judge unavailable")

    gold = GoldSet(questions=[_question(), _question(id="q2")])
    run = run_gold_set(bm25_answerer(agent_kit.index), gold, runner="bm25", graders=[explode])

    assert run.n_questions == 2
    assert all("judge unavailable" in (r.error or "") for r in run.results)


def test_the_run_record_pins_what_it_was_measured_under(agent_kit):
    """`config.yaml`'s model is a floating alias, so the fingerprint says which alias was
    configured and `model` says what it resolved to."""
    gold = GoldSet(questions=[_question()])
    run = run_gold_set(
        bm25_answerer(agent_kit.index), gold, runner="agent", model="openai:some-model"
    )
    assert run.model == "openai:some-model"
    assert len(run.config_fingerprint or "") == 64


# ---------------------------------------------------------------- the judge -----------------


def _verdict_model(verdict: JudgeVerdict) -> FunctionModel:
    def respond(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart("final_result", verdict.model_dump())])

    return FunctionModel(respond)


def test_the_judge_scores_the_fraction_of_key_facts_supported():
    question = _question(answer_key_facts=["fact one", "fact two", "fact three"])
    verdict = JudgeVerdict(
        facts=[
            FactVerdict(fact="fact one", supported=True),
            FactVerdict(fact="fact two", supported=True),
            FactVerdict(fact="fact three", supported=False, note="not stated"),
        ]
    )
    with build_judge().override(model=_verdict_model(verdict)):
        metrics = judge_grader()(question, _response([], "Some answer."))

    assert metrics["answer_correctness"] == pytest.approx(2 / 3)
    assert metrics["contradiction"] == 0.0


def test_the_judge_cannot_score_above_one():
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
        metrics = judge_grader()(question, _response([], "Some answer."))
    assert metrics["answer_correctness"] == 1.0


def test_the_judge_reports_a_contradiction_separately():
    """A contradiction is a worse failure than an omission, so it gets its own number rather than
    being folded into the coverage fraction."""
    question = _question(answer_key_facts=["fact one"])
    verdict = JudgeVerdict(
        facts=[FactVerdict(fact="fact one", supported=True)],
        contradicts=True,
        contradiction="says the opposite later",
    )
    with build_judge().override(model=_verdict_model(verdict)):
        metrics = judge_grader()(question, _response([], "Some answer."))
    assert metrics["answer_correctness"] == 1.0
    assert metrics["contradiction"] == 1.0


def test_the_judge_is_never_called_for_an_abstention():
    """Whether an abstention was correct is already settled deterministically by
    `abstention_accuracy`; paying for an opinion on it would be buying an answer twice."""
    called = False

    def respond(messages, info: AgentInfo) -> ModelResponse:
        nonlocal called
        called = True
        return ModelResponse(parts=[ToolCallPart("final_result", JudgeVerdict().model_dump())])

    with build_judge().override(model=FunctionModel(respond)):
        question = _question(expected_abstain=True, **_ABSTAIN)
        assert judge_grader()(question, _response([], "No sources.")) == {}
    assert called is False


def test_a_false_abstention_scores_zero_without_a_model_call():
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
        metrics = judge_grader()(_question(), abstained)

    assert metrics == {"answer_correctness": 0.0, "contradiction": 0.0}
    assert called is False


# ---------------------------------------------------------------- the stub baseline ---------


def test_the_stub_answerer_still_works():
    """Kept reachable on purpose: Phase 0 measured recall@5 at 0.033 against it, and that is the
    number every real score is read against."""
    response = stub_answerer()(_question())
    assert response.citations
