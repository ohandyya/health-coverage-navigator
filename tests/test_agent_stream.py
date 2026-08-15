"""The SSE event sequence the agent produces, and its relationship to the non-streaming answer.

The frontend was built at Phase 0 against a canned stream and has not changed. That only stays true
if the agent's stream has the same grammar the stub's did, so this file asserts the grammar rather
than the content: what order the events arrive in, that the `token` deltas reconstruct the answer,
and that `done` is authoritative.

The one property worth stating plainly: **`answer_question()` is `stream_answer()` drained**, so
the two cannot disagree. At Phase 0 that was a test comparing two independent code paths; a
nondeterministic model makes that unassertable by re-running, so it moved into the code, and what
is tested here is that the structure holds.
"""

import asyncio

import pytest

from health_coverage_navigator.agent.models import AgentAnswer
from health_coverage_navigator.agent.runtime import answer_question, build_agent, stream_answer
from health_coverage_navigator.api.models import (
    ChatRequest,
    CitationEvent,
    DoneEvent,
    StartEvent,
    StepEvent,
    TokenEvent,
)

#: Long enough that a 17-character streaming fragment cannot deliver it in one piece — which is the
#: only way `test_the_answer_streams_in_more_than_one_piece` can mean anything.
LONG_ANSWER = (
    "A deductible is the amount you pay for covered health care services before your insurance "
    "plan starts to pay. [c1] It resets each plan year, and some preventive services are covered "
    "before you meet it, so it is worth reading your plan's summary of benefits carefully. [c1]"
)


@pytest.fixture
def events(agent_kit) -> list:
    return agent_kit.stream(agent_kit.SEARCH, agent_kit.answer(answer=LONG_ANSWER))


# ---------------------------------------------------------------- the grammar ---------------


def test_the_stream_opens_with_start_and_closes_with_done(events: list):
    assert isinstance(events[0], StartEvent)
    assert isinstance(events[-1], DoneEvent)
    assert sum(isinstance(e, DoneEvent) for e in events) == 1


def test_ids_are_stable_from_start_to_done(events: list):
    """`useChat.ts` keys the in-flight message on `start`, then replaces it wholesale on `done`. A
    changed id mid-stream leaves the browser with two messages for one question."""
    start, done = events[0], events[-1]
    assert isinstance(start, StartEvent) and isinstance(done, DoneEvent)
    assert done.response.message_id == start.message_id
    assert done.response.conversation_id == start.conversation_id


def test_the_trace_arrives_before_the_answer(events: list):
    """The order the UI was built for: you want to see what it is doing while it thinks."""
    first_step = next(i for i, e in enumerate(events) if isinstance(e, StepEvent))
    first_token = next(i for i, e in enumerate(events) if isinstance(e, TokenEvent))
    assert first_step < first_token


def test_citations_arrive_before_done(events: list):
    citations = [i for i, e in enumerate(events) if isinstance(e, CitationEvent)]
    assert citations
    assert max(citations) < len(events) - 1


def test_token_deltas_reconstruct_the_answer_exactly(events: list):
    """The whole point of streaming the *structured* output: the incremental text the reader
    watches must end up identical to the authoritative one, or the answer visibly changes under
    them when `done` lands."""
    streamed = "".join(e.delta for e in events if isinstance(e, TokenEvent))
    done = events[-1]
    assert isinstance(done, DoneEvent)
    assert streamed == done.response.answer


def test_the_answer_streams_in_more_than_one_piece(events: list):
    """Guards the partial-JSON path specifically. If `_partial_answer` ever stopped parsing, the
    final flush in `stream_answer` would still deliver a correct answer in a single token event —
    every other test here would pass while streaming was silently dead."""
    tokens = [e for e in events if isinstance(e, TokenEvent)]
    assert len(tokens) > 3, "the answer arrived in one lump; partial-output parsing is not working"


def test_streamed_prefixes_only_ever_grow(events: list):
    """A delta that is not an append would make the browser render text the model never wrote,
    since `useChat.ts` concatenates without inspecting."""
    done = events[-1]
    assert isinstance(done, DoneEvent)
    seen = ""
    for event in events:
        if isinstance(event, TokenEvent):
            seen += event.delta
            assert done.response.answer.startswith(seen)


# ---------------------------------------------------------------- done is authoritative -----


def test_done_carries_everything_the_incremental_events_did(events: list):
    done = events[-1]
    assert isinstance(done, DoneEvent)
    assert done.response.citations == [e.citation for e in events if isinstance(e, CitationEvent)]
    assert done.response.trace == [e.step for e in events if isinstance(e, StepEvent)]


def test_answer_question_returns_the_done_payload(agent_kit):
    """The structural guarantee that replaced Phase 0's equality test."""
    answer = agent_kit.answer(answer=LONG_ANSWER)

    async def both():
        request = ChatRequest(message="what is a deductible?")
        events = [e async for e in stream_answer(request, agent_kit.index)]
        return events[-1], await answer_question(request, agent_kit.index)

    with build_agent().override(model=agent_kit.script(agent_kit.SEARCH, answer)):
        done, direct = asyncio.run(both())

    assert isinstance(done, DoneEvent)
    # Three kinds of field differ legitimately across two runs and nothing else may: ids are minted
    # per run, and durations are wall clock. Excluding them by name rather than reaching for a
    # blanket comparison is the point — a new field is then a test failure asking to be classified,
    # not something silently ignored.
    per_run = {"conversation_id", "message_id", "trace", "usage"}
    assert direct.model_dump(exclude=per_run) == done.response.model_dump(exclude=per_run)

    assert [(s.index, s.kind, s.tool, s.input) for s in direct.trace] == [
        (s.index, s.kind, s.tool, s.input) for s in done.response.trace
    ]
    assert direct.usage is not None and done.response.usage is not None
    assert direct.usage.model == done.response.usage.model
    assert direct.usage.total_tokens == done.response.usage.total_tokens


# ---------------------------------------------------------------- abstention ----------------


def test_an_abstention_still_streams_its_text_and_its_trace(agent_kit):
    """An abstention is an answer, not an empty response — and its trace is exactly what you would
    read to work out why it abstained."""
    events = agent_kit.stream(
        agent_kit.SEARCH,
        AgentAnswer(
            abstained=True,
            answer="I don't have that in my reference material, which covers HealthCare.gov "
            "consumer content, the Medicare publications, and Medicare NCDs.",
            citations=[],
        ),
    )
    done = events[-1]
    assert isinstance(done, DoneEvent)
    assert done.response.abstained is True
    assert "".join(e.delta for e in events if isinstance(e, TokenEvent)) == done.response.answer
    assert not any(isinstance(e, CitationEvent) for e in events)
    assert done.response.trace


def test_a_retried_answer_does_not_leave_a_stale_prefix(agent_kit):
    """The case the final flush exists for. A rejected draft streams tokens before the validator
    turns it down; the accepted answer may share no prefix with it, and the reader must end up with
    the accepted one rather than a splice of both."""
    rejected = agent_kit.answer(
        answer="Completely different rejected wording. [c1]",
        citations=[{"id": "c1", "chunk_id": "healthcare_gov:nope#000", "snippet": "x"}],
    )
    events = agent_kit.stream(agent_kit.SEARCH, rejected, agent_kit.answer(answer=LONG_ANSWER))

    done = events[-1]
    assert isinstance(done, DoneEvent)
    assert done.response.answer == LONG_ANSWER
    assert "".join(e.delta for e in events if isinstance(e, TokenEvent)).endswith(LONG_ANSWER)
