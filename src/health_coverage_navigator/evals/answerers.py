"""The things `run_gold_set` can be pointed at.

`evals/runner.py` takes the answerer as a parameter — that seam was cut at Phase 0 specifically so
Phase 1a could swap one function and leave the scoring, the storage format, the HTTP route and the
dashboard untouched. This module is what fills it.

| answerer | label | costs | measures |
|---|---|---|---|
| `stub_answerer` | `stub` | nothing | the harness itself |
| `bm25_answerer` | `bm25` | nothing | **retrieval alone**, with no model in the loop |
| `agent_answerer` | `agent` | a model call per question | the whole phase |

The middle row is the one worth explaining. It answers nothing — it retrieves and stops — but it
runs through the *same* scorer, the same run file, and the same dashboard, so `recall@5` from a
retrieval-only run is directly comparable to `recall@5` from an agent run. That comparison is the
phase's most useful single number: it separates "the retriever cannot find it" from "the agent did
not look properly", which are different bugs with different fixes, and it costs nothing to run.

Each entry is a **builder** returning the answerer, not the answerer itself, so per-run setup (the
~200 ms index build) is paid once rather than once per question — and so a test can hand in a small
hand-built index instead of reaching through a cached global.

This module must stay importable without `pydantic_ai`, a model, or an API key: `bm25_answerer`
has to work on a machine that has never set `OPENAI_API_KEY`. That is why the agent import is
inside its builder.
"""

import asyncio
from collections.abc import Callable

from health_coverage_navigator.agent.index import CorpusIndex
from health_coverage_navigator.api.models import ChatRequest, ChatResponse, Citation, TraceStep
from health_coverage_navigator.config import get_config
from health_coverage_navigator.evals.models import GoldQuestion

#: What every answerer looks like from the runner's side. Defined here rather than in `runner.py`
#: so `runner` can import it alongside the builders without the two modules importing each other.
AnswerFn = Callable[[GoldQuestion], ChatResponse]

#: What a retrieval-only run puts in the `answer` field. Not a real answer, and it says so — the
#: same reasoning as `HealthResponse.stub`: output that is not an answer must never be mistakable
#: for one, including by whoever opens the run file six months from now.
NO_ANSWER = (
    "_Retrieval-only run: these are the chunks lexical search returned for the question. "
    "No model was called and no answer was synthesized._"
)


def stub_answerer() -> AnswerFn:
    """The Phase 0 answerer: the same canned responses the chat endpoint serves.

    Kept reachable on purpose. It is the baseline every real number is read against — Phase 0
    measured recall@5 at 0.033 against it — and it is what the API's stub mode still serves.
    """
    from health_coverage_navigator.api.stub import stub_answer

    def answer(question: GoldQuestion) -> ChatResponse:
        return stub_answer(ChatRequest(message=question.question, plan_year=question.plan_year))

    return answer


def bm25_answerer(index: CorpusIndex) -> AnswerFn:
    """Retrieval with no model: the top-k chunks, wrapped in the response shape."""

    def answer(question: GoldQuestion) -> ChatResponse:
        retrieval = get_config().retrieval
        hits = index.search(
            question.question,
            retrieval.top_k,
            k1=retrieval.bm25_k1,
            b=retrieval.bm25_b,
        )

        citations: list[Citation] = []
        for position, hit in enumerate(hits, start=1):
            chunk = index.chunk(hit.chunk_id)
            if chunk is None:  # pragma: no cover - hits come from this index by construction
                continue
            citations.append(
                Citation(
                    id=f"c{position}",
                    source_type="reference",
                    title=hit.label,
                    url=chunk.url or None,
                    doc_id=chunk.doc_id,
                    chunk_id=chunk.id,
                    snippet=chunk.text,
                    score=hit.score,
                )
            )

        return ChatResponse(
            conversation_id="conv_bm25",
            message_id=f"msg_bm25_{question.id}",
            # Never true. A bare retriever has no notion of "the corpus does not cover this" — it
            # always returns its top k, however weak. Scoring the abstention slice against this
            # answerer would measure nothing, which is why the CLI runs it over `in_corpus()` only.
            abstained=False,
            answer=NO_ANSWER,
            claims=[],
            citations=citations,
            trace=[
                TraceStep(
                    index=0,
                    kind="tool_call",
                    tool="search_corpus",
                    input={"query": question.question, "k": retrieval.top_k},
                    summary=f"search_corpus(query={question.question!r}, k={retrieval.top_k})",
                ),
                TraceStep(
                    index=1,
                    kind="tool_result",
                    tool="search_corpus",
                    summary=f"{len(citations)} chunks returned",
                ),
            ],
        )

    return answer


def agent_answerer(index: CorpusIndex) -> AnswerFn:
    """The real answerer: the Phase 1a agent, one run per question.

    `asyncio.run` per question rather than one loop over the whole set. `run_gold_set` is
    deliberately synchronous (docs/progress.md — a plain callback, so the HTTP route can buffer
    progress events for replay), the gold set is 35 questions, and loop setup is noise next to a
    model call. Running them concurrently would also deliver progress events out of order, which
    the dashboard renders as a run that jumps around.
    """
    from health_coverage_navigator.agent.runtime import answer_question

    def answer(question: GoldQuestion) -> ChatResponse:
        return asyncio.run(
            answer_question(
                ChatRequest(message=question.question, plan_year=question.plan_year),
                index,
            )
        )

    return answer
