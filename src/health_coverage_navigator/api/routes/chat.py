"""Ask a question — non-streaming and streaming.

Both paths return the *same* `ChatResponse` for the same input; the stream just delivers it in
pieces and then hands over the whole thing in its `done` event. That identity is asserted by a
test, because two code paths producing subtly different answers is the failure this design is
most exposed to.

Streaming is built at Phase 0 rather than waiting for the real agent, which is a deliberate
departure from docs/frontend_plan.md §4.4's schedule on three grounds: it is the only non-artificial
place to hang `StreamEventEnvelope` so the SSE union reaches `openapi.json` (§4.5's second gotcha);
the plan itself calls the stream parser "the one place a subtle bug hides", and debugging it
alongside a new agent in Phase 1a is the worse order; and it is the same canned response either
way, so it costs a generator rather than a design.
"""

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter
from starlette.responses import StreamingResponse

from health_coverage_navigator.api.models import (
    ChatRequest,
    ChatResponse,
    CitationEvent,
    DoneEvent,
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
#: incremental rendering stays invisible until a real, slow agent shows up.
STUB_TOKEN_DELAY_S = 0.02
STUB_STEP_DELAY_S = 0.12


class EventStreamResponse(StreamingResponse):
    """A `StreamingResponse` with `text/event-stream` declared at the class level.

    That is what makes FastAPI file the route's `responses=` schema under `text/event-stream`
    rather than `application/json`: with a bare `StreamingResponse` the class `media_type` is
    `None` and the schema lands in the wrong content type. A small difference between a correct
    OpenAPI document and a plausible one.
    """

    media_type = "text/event-stream"


@router.post("/chat", response_model=ChatResponse, summary="Ask a question (non-streaming)")
def post_chat(request: ChatRequest) -> ChatResponse:
    # Sync `def` (so Starlette runs it in the threadpool) because `stub_answer` is pure CPU and
    # instant. Becomes `async def` in Phase 1a, when there is real I/O behind it.
    return stub_answer(request)


async def _stub_events(request: ChatRequest) -> AsyncIterator[str]:
    """The canned answer, delivered as SSE frames.

    Order matters and mirrors what a real agent produces: the trace fills in first (you want to
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
        }
    },
    summary="Ask a question (SSE stream)",
)
async def post_chat_stream(request: ChatRequest) -> EventStreamResponse:
    return EventStreamResponse(
        _stub_events(request),
        headers={
            "Cache-Control": "no-cache",
            # Tells any buffering proxy (and Vite's dev proxy) to pass frames through as they are
            # written. Without it the "stream" arrives all at once and looks like a slow request.
            "X-Accel-Buffering": "no",
        },
    )
