"""Suite-wide guarantees, and the shared kit for driving the agent without a model provider.

**No test may reach a model provider.** `pydantic_ai.models.ALLOW_MODEL_REQUESTS = False` makes any
real request raise instead of going out, so `make check-all` cannot spend money, cannot need an
`OPENAI_API_KEY`, and cannot fail because a provider is having a bad afternoon. Tests that need a
model use `FunctionModel` through `agent.override(model=...)`, which the flag does not affect.

Everything else here exists because `tests/` is not a package, so `test_agent_stream.py` cannot
import a helper from `test_agent.py`. Fixtures are pytest's answer to that, and the shared piece —
a scripted model — is genuinely shared rather than incidentally duplicated: both files need a model
that does exactly what the test says and nothing else.

The repo's other fixtures (`client`, `gold`, `chunks`) stay in the modules that use them. Hoisting
those here would make each test file's dependencies invisible from the file itself, which is worth
avoiding for a fixture with exactly one consumer.
"""

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic_ai import UnexpectedModelBehavior, capture_run_messages
from pydantic_ai.messages import ModelMessage, ModelResponse, RetryPromptPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from health_coverage_navigator.agent.index import CorpusIndex
from health_coverage_navigator.agent.models import AgentAnswer
from health_coverage_navigator.agent.runtime import answer_question, build_agent, stream_answer
from health_coverage_navigator.api.models import ChatRequest, ChatResponse, StreamEvent
from health_coverage_navigator.chunking.models import Chunk


@pytest.fixture(autouse=True)
def _no_live_model_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    import pydantic_ai.models

    monkeypatch.setattr(pydantic_ai.models, "ALLOW_MODEL_REQUESTS", False)


# ---------------------------------------------------------------- the agent kit -------------

DEDUCTIBLE_TEXT = (
    "The amount you pay for covered health care services before your insurance plan starts "
    "to pay. After you meet it, you usually pay a copayment or coinsurance."
)
PREMIUM_TEXT = "The amount you pay for your health insurance every month."

DEDUCTIBLE_ID = "healthcare_gov:glossary_deductible#000"
PREMIUM_ID = "healthcare_gov:glossary_premium#000"

#: The tool call every script opens with. `search_corpus` is what the prompt tells the model to
#: reach for first, so the scripts start where a real run does.
SEARCH = ("search_corpus", {"query": "deductible", "k": 5})

#: How finely a scripted stream chops its JSON. Deliberately small and aligned to nothing, so
#: fragments split mid-key, mid-string and mid-escape — the same instinct as `stream.test.ts`
#: feeding the SSE parser a frame split across a chunk boundary. The bug lives at the seam.
CHUNK_CHARS = 17


def make_chunk(doc_id: str, title: str, text: str) -> Chunk:
    return Chunk(
        id=f"healthcare_gov:{doc_id}#000",
        doc_id=doc_id,
        source="healthcare_gov",
        ordinal=0,
        char_start=0,
        char_end=len(text),
        text=text,
        n_chars=len(text),
        title=title,
        url=f"/glossary/{title.lower()}",
    )


def _turn(turns: tuple, messages: list[ModelMessage]) -> tuple[str, dict]:
    """Which scripted turn this model request is, as `(tool_name, arguments)`.

    Counting `ModelResponse`s rather than tracking state keeps the model a pure function of the
    conversation, which is what makes a retry test work: the validator's complaint arrives as a new
    message, so the next call naturally reads the next turn. The last turn repeats forever, which
    is what lets a test show the model making the *same* mistake until the run fails loudly.
    """
    position = min(sum(isinstance(m, ModelResponse) for m in messages), len(turns) - 1)
    turn = turns[position]
    if isinstance(turn, AgentAnswer):
        return "final_result", turn.model_dump()
    return turn


def scripted_model(*turns: Any) -> FunctionModel:
    """A model that replays a fixed sequence of responses, one per model request.

    Each turn is either a `(tool_name, args)` pair — a tool call — or an `AgentAnswer`, which
    becomes the final result.

    **Both a `function` and a `stream_function` are supplied**, because `agent/runtime.py` only
    ever streams — `answer_question()` is `stream_answer()` drained — and a `FunctionModel` with no
    `stream_function` asserts on a streamed request. The streaming half emits the arguments as
    small JSON fragments rather than in one piece, which is not incidental: that is the shape a
    real provider sends, and it is what exercises `runtime._partial_answer`'s partial-JSON parsing
    and the token diffing built on it. A single-chunk script would leave the whole streaming path
    untested while appearing to pass.
    """

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        name, args = _turn(turns, messages)
        return ModelResponse(parts=[ToolCallPart(name, args)])

    async def stream(messages: list[ModelMessage], info: AgentInfo):
        name, args = _turn(turns, messages)
        body = json.dumps(args)
        yield {0: DeltaToolCall(name=name, json_args="")}
        for start in range(0, len(body), CHUNK_CHARS):
            yield {0: DeltaToolCall(json_args=body[start : start + CHUNK_CHARS])}

    return FunctionModel(respond, stream_function=stream)


def grounded_answer(**overrides: Any) -> AgentAnswer:
    """A correct answer over the two-chunk fixture corpus: cited, verbatim, marker resolved.

    Every guardrail test is this with one thing broken, which keeps what is under test visible in
    the override rather than buried in a wall of literals.
    """
    payload: dict[str, Any] = {
        "abstained": False,
        "answer": "A deductible is what you pay before your plan starts to pay. [c1]",
        "citations": [
            {
                "id": "c1",
                "chunk_id": DEDUCTIBLE_ID,
                "snippet": "before your insurance plan starts to pay",
            }
        ],
    }
    payload.update(overrides)
    return AgentAnswer.model_validate(payload)


@dataclass(frozen=True, slots=True)
class AgentKit:
    """Everything a test needs to run the agent offline, in one fixture.

    One object rather than half a dozen fixtures because `tests/` is not a package: a test module
    can reach all of this through a single parameter, but cannot import any of it by name from
    here. That constraint is also why `run` and `expect_rejection` live on the kit — a test module
    would otherwise need to import `AgentKit` just to annotate its own helper.
    """

    index: CorpusIndex

    script = staticmethod(scripted_model)
    answer = staticmethod(grounded_answer)
    chunk = staticmethod(make_chunk)

    SEARCH = SEARCH
    DEDUCTIBLE_ID = DEDUCTIBLE_ID
    PREMIUM_ID = PREMIUM_ID
    DEDUCTIBLE_TEXT = DEDUCTIBLE_TEXT
    PREMIUM_TEXT = PREMIUM_TEXT

    def run(self, *turns: Any, message: str = "what is a deductible?") -> ChatResponse:
        """One full agent run against the scripted turns."""
        with build_agent().override(model=scripted_model(*turns)):
            return asyncio.run(answer_question(ChatRequest(message=message), self.index))

    def stream(self, *turns: Any, message: str = "what is a deductible?") -> list[StreamEvent]:
        """Every SSE event one run emits, in order."""

        async def drain() -> list[StreamEvent]:
            request = ChatRequest(message=message)
            return [event async for event in stream_answer(request, self.index)]

        with build_agent().override(model=scripted_model(*turns)):
            return asyncio.run(drain())

    def expect_rejection(self, *turns: Any) -> str:
        """Run a script that never satisfies the validator, and return its last complaint.

        Exhausting the retry budget raises `UnexpectedModelBehavior`, which is the right outcome —
        a model that will not ground its answer must fail loudly rather than serve an ungrounded
        one. The *message* is returned so the caller can check it, because a retry the model cannot
        act on is a retry wasted.
        """
        with (
            capture_run_messages() as messages,
            build_agent().override(model=scripted_model(*turns)),
            pytest.raises(UnexpectedModelBehavior),
        ):
            asyncio.run(answer_question(ChatRequest(message="q"), self.index))

        retries = [
            part.content
            for message in messages
            for part in getattr(message, "parts", [])
            if isinstance(part, RetryPromptPart) and isinstance(part.content, str)
        ]
        assert retries, "the validator rejected the answer without telling the model why"
        return retries[-1]


@pytest.fixture(scope="session")
def agent_kit() -> AgentKit:
    """Two hand-written chunks, not the real 6,722.

    These tests are about what happens to a citation, not about whether BM25 ranks well;
    `tests/test_corpus_index.py` covers the latter against the real corpus. A two-document index
    also makes "the model cited something it never retrieved" easy to set up, which is the single
    most important thing the guardrail does.
    """
    return AgentKit(
        index=CorpusIndex(
            [
                make_chunk("glossary_deductible", "Deductible", DEDUCTIBLE_TEXT),
                make_chunk("glossary_premium", "Premium", PREMIUM_TEXT),
            ]
        )
    )
