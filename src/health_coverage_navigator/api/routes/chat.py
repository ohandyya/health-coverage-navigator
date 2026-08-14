"""Ask a question — non-streaming and streaming.

Both paths produce the *same* `ChatResponse` for a given run, and at Phase 1a that is guaranteed
structurally rather than asserted: `agent/runtime.py`'s `answer_question()` is `stream_answer()`
drained to its `done` event. At Phase 0 a test compared two independent code paths for equality;
a nondeterministic model makes that unassertable by re-running, so the property moved into the
code instead of being checked after the fact.

Three ways this module can answer, in the order it tries them:

1. **stub** — `create_app(stub=True)`. The Phase 0 canned answers, still what the contract tests
   assert against and what an offline demo runs on.
2. **no corpus** — `chunks.jsonl` was never built here. A 503 that names `make chunk`. Deliberately
   not a fallback to (1): canned output must never be mistakable for a real answer, which is the
   whole reason `HealthResponse.stub` is a boolean.
3. **the agent** — the real path.

Streaming was built at Phase 0 rather than here, which turned out to be the right order: the SSE
grammar, the parser and its tests were all proven before there was an agent to blame.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from starlette.responses import StreamingResponse

from health_coverage_navigator.agent.runtime import answer_question, stream_answer
from health_coverage_navigator.api.deps import AppContext, get_context
from health_coverage_navigator.api.models import (
    ChatRequest,
    ChatResponse,
    CitationEvent,
    DoneEvent,
    ErrorEvent,
    ErrorResponse,
    StartEvent,
    StepEvent,
    StreamEventEnvelope,
    TokenEvent,
    sse_frame,
)
from health_coverage_navigator.api.stub import stub_answer

router = APIRouter()

#: Without a delay the whole stub answer arrives in one TCP write and the streaming UI is never
#: actually exercised — it renders identically to the non-streaming path and any bug in the
#: incremental rendering stays invisible. Applies to the stub only; the agent is genuinely slow.
STUB_TOKEN_DELAY_S = 0.02
STUB_STEP_DELAY_S = 0.12

NO_CORPUS = (
    "The reference corpus has not been built on this machine. `chunks.jsonl` is git-ignored, so a "
    "fresh clone has none — run `make chunk` and restart the server."
)


class EventStreamResponse(StreamingResponse):
    """A `StreamingResponse` with `text/event-stream` declared at the class level.

    That is what makes FastAPI file the route's `responses=` schema under `text/event-stream`
    rather than `application/json`: with a bare `StreamingResponse` the class `media_type` is
    `None` and the schema lands in the wrong content type. A small difference between a correct
    OpenAPI document and a plausible one.
    """

    media_type = "text/event-stream"


@router.post(
    "/chat",
    response_model=ChatResponse,
    responses={503: {"model": ErrorResponse}},
    summary="Ask a question (non-streaming)",
)
async def post_chat(
    request: ChatRequest, ctx: Annotated[AppContext, Depends(get_context)]
) -> ChatResponse:
    if ctx.stub:
        return stub_answer(request)
    if ctx.index is None:
        raise HTTPException(status_code=503, detail=NO_CORPUS)
    return await answer_question(request, ctx.index)


async def _stub_events(request: ChatRequest) -> AsyncIterator[str]:
    """The canned answer, delivered as SSE frames.

    Order matters and mirrors what the real agent produces: the trace fills in first (you want to
    see *what it is doing* while it thinks), then the answer streams, then citations land as they
    are used, then `done` carries the authoritative object.
    """
    response = stub_answer(request)

    yield sse_frame(
        StartEvent(conversation_id=response.conversation_id, message_id=response.message_id)
    )
    for step in response.trace:
        await asyncio.sleep(STUB_STEP_DELAY_S)
        yield sse_frame(StepEvent(step=step))

    for word in response.answer.split(" "):
        await asyncio.sleep(STUB_TOKEN_DELAY_S)
        yield sse_frame(TokenEvent(delta=word + " "))

    for citation in response.citations:
        yield sse_frame(CitationEvent(citation=citation))

    yield sse_frame(DoneEvent(response=response))


async def _agent_events(request: ChatRequest, ctx: AppContext) -> AsyncIterator[str]:
    """The real agent, with every failure delivered as an `error` frame rather than a broken stream.

    A `StreamingResponse` has already sent its 200 and its headers by the time the first tool runs,
    so an exception after that point cannot become an HTTP status — it truncates the body, and the
    browser reports a network error for what was really a rate limit or a step-limit trip. The
    contract has an `ErrorEvent` for exactly this, and `useChat.ts` already renders it.
    """
    assert ctx.index is not None  # guarded by the route
    try:
        async for event in stream_answer(request, ctx.index):
            yield sse_frame(event)
    except asyncio.CancelledError:  # pragma: no cover - client hung up
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced to the client, never swallowed
        yield sse_frame(ErrorEvent(code="internal", message=f"{type(exc).__name__}: {exc}"))


@router.post(
    "/chat/stream",
    response_class=EventStreamResponse,
    responses={
        200: {
            "model": StreamEventEnvelope,
            "description": (
                "Server-Sent Events. Each frame's `data:` is one `StreamEvent`, discriminated by "
                "its `type` field. The `done` event carries the complete `ChatResponse` and is "
                "authoritative — clients replace their incrementally-built state with it."
            ),
        },
        503: {"model": ErrorResponse},
    },
    summary="Ask a question (SSE stream)",
)
async def post_chat_stream(
    request: ChatRequest, ctx: Annotated[AppContext, Depends(get_context)]
) -> EventStreamResponse:
    if not ctx.stub and ctx.index is None:
        # Raised before the response starts, so this one *can* be a status code. Once the stream is
        # open, `_agent_events` has to use an error frame instead.
        raise HTTPException(status_code=503, detail=NO_CORPUS)

    events = _stub_events(request) if ctx.stub else _agent_events(request, ctx)
    return EventStreamResponse(
        events,
        headers={
            "Cache-Control": "no-cache",
            # Tells any buffering proxy (and Vite's dev proxy) to pass frames through as they are
            # written. Without it the "stream" arrives all at once and looks like a slow request.
            "X-Accel-Buffering": "no",
        },
    )
