"""The things `run_gold_set` can be pointed at.

`evals/runner.py` takes the answerer as a parameter — that seam was cut at Phase 0 specifically so
Phase 1a could swap one function and leave the scoring, the storage format, the HTTP route and the
dashboard untouched. This module is what fills it.

| answerer | label | costs | measures |
|---|---|---|---|
| `stub_answerer` | `stub` | nothing | the harness itself |
| `bm25_answerer` | `bm25` | nothing | **lexical retrieval alone**, no model in the loop |
| `vector_answerer` | `vector` | ~30 embedding calls | **semantic retrieval alone**, likewise |
| `agent_answerer` | `agent` | a model call per question | the whole phase |

The two middle rows are the ones worth explaining. They answer nothing — they retrieve and stop —
but they run through the *same* scorer, the same run file, and the same dashboard, so their
`recall@5` is directly comparable both to each other and to an agent run. Two different comparisons
come out of that, and they are the reason these exist:

* `bm25` vs `agent` separates "the retriever cannot find it" from "the agent did not look
  properly" — different bugs with different fixes.
* `bm25` vs `vector` is **the Phase 1b decision**, and it is made here rather than between two agent
  runs because these two are deterministic. Seven agent runs at fixed config span recall@5
  0.600-0.867 (docs/progress.md), so an agent A/B cannot resolve the effect being looked for; a
  pair of runs with no model in them can.

Each entry is a **builder** returning the answerer, not the answerer itself, so per-run setup (the
~200 ms index build) is paid once rather than once per question — and so a test can hand in a small
hand-built index instead of reaching through a cached global.

This module must stay importable without `pydantic_ai`, a model, or an API key: `bm25_answerer`
has to work on a machine that has never set `OPENAI_API_KEY`. That is why the agent import is
inside its builder.
"""

from collections.abc import Awaitable, Callable

from health_coverage_navigator.agent.index import CorpusIndex
from health_coverage_navigator.agent.models import ChunkHit
from health_coverage_navigator.api.models import ChatRequest, ChatResponse, Citation, TraceStep
from health_coverage_navigator.config import Toolset, get_config
from health_coverage_navigator.evals.models import GoldQuestion
from health_coverage_navigator.live.marketplace import MarketplaceClient
from health_coverage_navigator.live.nppes import NppesClient
from health_coverage_navigator.live.openfda import OpenFdaClient
from health_coverage_navigator.structured.store import StructuredStore
from health_coverage_navigator.vectors.store import VectorIndex
from health_coverage_navigator.web.client import WebSearchClient

#: What every answerer looks like from the runner's side. Defined here rather than in `runner.py`
#: so `runner` can import it alongside the builders without the two modules importing each other.
#:
#: **Awaitable, because the one answerer that matters is.** `answer_question` is async all the way
#: down, and the alternative — a synchronous seam with `asyncio.run` inside it — forces the runner
#: to reach for threads to get any concurrency, and makes an async grader impossible (`asyncio.run`
#: raises inside a running loop, which is what `judge_grader` would hit). The two free answerers
#: below never await anything; that is a small cost paid once, against a runner that expresses
#: "three questions at a time" as a semaphore instead of a thread pool.
AnswerFn = Callable[[GoldQuestion], Awaitable[ChatResponse]]

#: What a retrieval-only run puts in the `answer` field. Not a real answer, and it says so — the
#: same reasoning as `HealthResponse.stub`: output that is not an answer must never be mistakable
#: for one, including by whoever opens the run file six months from now.
NO_ANSWER = (
    "_Retrieval-only run: these are the chunks {retriever} returned for the question. "
    "No model was called and no answer was synthesized._"
)


def _retrieval_response(
    question: GoldQuestion,
    hits: list[ChunkHit],
    index: CorpusIndex,
    *,
    runner: str,
    tool: str,
    tool_input: dict[str, object],
    retriever: str,
) -> ChatResponse:
    """Wrap a ranked hit list in the response shape, for an answerer with no model behind it.

    Shared by `bm25_answerer` and `vector_answerer` rather than written twice. The two differ only
    in *which* primitive produced the hits and what the synthesized trace step is called — and
    keeping the rest identical is the point, because the comparison between their `recall@5` values
    is only meaningful if nothing else about how they are scored differs.

    Citations are rebuilt from the real `Chunk`, exactly as `runtime._citations` does, so a
    retrieval run and an agent run put the same fields in front of the scorer.
    """
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
        conversation_id=f"conv_{runner}",
        message_id=f"msg_{runner}_{question.id}",
        # Never true. A bare retriever has no notion of "the corpus does not cover this" — it
        # always returns its top k, however weak. Scoring the abstention slice against this
        # answerer would measure nothing, which is why the CLI runs it over `in_corpus()` only.
        abstained=False,
        answer=NO_ANSWER.format(retriever=retriever),
        claims=[],
        citations=citations,
        trace=[
            TraceStep(
                index=0,
                kind="tool_call",
                tool=tool,
                input=tool_input,
                summary=f"{tool}({', '.join(f'{k}={v!r}' for k, v in tool_input.items())})",
            ),
            TraceStep(
                index=1,
                kind="tool_result",
                tool=tool,
                summary=f"{len(citations)} chunks returned",
            ),
        ],
    )


def stub_answerer() -> AnswerFn:
    """The Phase 0 answerer: the same canned responses the chat endpoint serves.

    Kept reachable on purpose. It is the baseline every real number is read against — Phase 0
    measured recall@5 at 0.033 against it — and it is what the API's stub mode still serves.
    """
    from health_coverage_navigator.api.stub import stub_answer

    # `async` without an `await`: this answerer is pure CPU and has nothing to wait for. It is a
    # coroutine only because `AnswerFn` is, and the seam is worth more than the honesty of two
    # signatures — see the note on `AnswerFn`.
    async def answer(question: GoldQuestion) -> ChatResponse:
        return stub_answer(ChatRequest(message=question.question, plan_year=question.plan_year))

    return answer


def bm25_answerer(index: CorpusIndex) -> AnswerFn:
    """Retrieval with no model: the top-k chunks, wrapped in the response shape.

    Synchronous work behind an async signature, like `stub_answerer`. BM25 over 6,722 chunks is
    ~3 ms and blocks the loop for that long; at 30 questions that is not worth a thread hop.
    """

    async def answer(question: GoldQuestion) -> ChatResponse:
        retrieval = get_config().retrieval
        hits = index.search(
            question.question,
            retrieval.top_k,
            k1=retrieval.bm25_k1,
            b=retrieval.bm25_b,
        )
        return _retrieval_response(
            question,
            hits,
            index,
            runner="bm25",
            tool="search_corpus",
            tool_input={"query": question.question, "k": retrieval.top_k},
            retriever="lexical search",
        )

    return answer


def vector_answerer(index: CorpusIndex, vectors: VectorIndex) -> AnswerFn:
    """Semantic retrieval with no model: the top-k nearest chunks, wrapped in the response shape.

    **The number Phase 1b is actually decided on.** Not free — it embeds each question — but at
    ~30 short queries that is a fraction of a cent, and unlike an agent run it is *deterministic*:
    the same question produces the same vector and the same neighbours every time. That matters
    more than the cost. docs/progress.md records seven agent runs at fixed config spanning recall@5
    0.600-0.867, so an agent A/B cannot resolve a difference smaller than about 0.2 — which is
    larger than the effect being looked for. This runner has no such spread, and its `recall@5`
    sits directly beside `bm25`'s 0.567 under the same scorer.

    Genuinely awaits, unlike the two free answerers: the embedding call is a real network round
    trip, so the runner's `--concurrency` buys real overlap here.
    """

    async def answer(question: GoldQuestion) -> ChatResponse:
        top_k = get_config().vectors.top_k
        ranked = await vectors.search(question.question, top_k)
        # Resolved through the same `CorpusIndex` the agent's tool uses, for the same reason: a
        # chunk id the corpus cannot resolve is not citable, and it must not become a scored hit.
        hits = [
            ChunkHit.of(chunk, score)
            for chunk_id, score in ranked
            if (chunk := index.chunk(chunk_id)) is not None
        ]
        return _retrieval_response(
            question,
            hits,
            index,
            runner="vector",
            tool="vector_search",
            tool_input={"query": question.question, "k": top_k},
            retriever="semantic search",
        )

    return answer


def agent_answerer(
    index: CorpusIndex,
    vectors: VectorIndex | None = None,
    toolset: Toolset | None = None,
    structured: StructuredStore | None = None,
    web: WebSearchClient | None = None,
    openfda: OpenFdaClient | None = None,
    nppes: NppesClient | None = None,
    marketplace: MarketplaceClient | None = None,
) -> AnswerFn:
    """The real answerer: the agent, one run per question.

    The only answerer that genuinely awaits, and the reason the whole seam is awaitable. It hands
    the agent's coroutine straight to the runner's event loop, so `run_gold_set` can hold several
    questions open at once with a semaphore rather than a thread pool, and so a grader may be async
    too — `judge_grader` needs that, because `asyncio.run` cannot be called from inside a running
    loop.

    Concurrency reorders *progress events*, which is the objection recorded here originally, so it
    stays opt-in: the dashboard leaves `max_concurrency` at 1, the CLI's `--concurrency` turns it
    up. Scores are unaffected either way — questions are graded independently and `gather` returns
    them in gold-set order.

    `toolset` is Phase 1b's eval axis: `None` means "whatever `config.yaml` says", which is what the
    dashboard and `make eval` use, while `--toolset` names one explicitly. It is the *only* thing
    that differs between the lexical-only, vector-only and both-tools runs — one runner with a flag
    rather than three code paths (docs/plan.md §1b).

    `structured` is Phase 1-c's, and it is a second axis rather than a fourth toolset because it
    selects a different lane rather than a different way of searching one. Passing `None` runs the
    agent with no relational tools at all, which is what `--no-structured` measures: whether the
    extra lane costs anything on the questions that were already answerable.

    `web` is Phase 2's third axis, and it is the same shape as `structured` for the same reason.
    `--no-web` measures the same thing one lane later.

    The three live clients are Phase 3's axis, and they are **three parameters rather than one**
    because they are independently absent: openFDA and NPPES are keyless, the Marketplace needs a
    credential, and a run with two of the three is a real configuration rather than a broken one.
    `--no-live` withholds all three, which is what §16b measures — whether six more tools cost
    anything on the questions that were already answerable.
    """
    from health_coverage_navigator.agent.runtime import answer_question

    async def answer(question: GoldQuestion) -> ChatResponse:
        return await answer_question(
            ChatRequest(message=question.question, plan_year=question.plan_year),
            index,
            toolset=toolset,
            vectors=vectors,
            structured=structured,
            web=web,
            openfda=openfda,
            nppes=nppes,
            marketplace=marketplace,
        )

    return answer
