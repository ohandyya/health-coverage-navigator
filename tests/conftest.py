"""Suite-wide guarantees, and the shared kit for driving the agent without a model provider.

**No test may reach a model provider.** `pydantic_ai.models.ALLOW_MODEL_REQUESTS = False` makes any
real request raise instead of going out, so `make check-all` cannot spend money, cannot need an
`OPENAI_API_KEY`, and cannot fail because a provider is having a bad afternoon. Tests that need a
model use `FunctionModel` through `agent.override(model=...)`, which the flag does not affect.

**That flag is not enough on its own since Phase 1b.** It guards PydanticAI's model requests, and
an embedding call goes out through the OpenAI SDK directly — `openai_embedder` would happily reach
the network with the developer's own key while every test appeared to pass. `_no_live_embeddings`
below closes that, so the invariant is "no test reaches a *provider*", not just "no test reaches a
model".

Everything else here exists because `tests/` is not a package, so `test_agent_stream.py` cannot
import a helper from `test_agent.py`. Fixtures are pytest's answer to that, and the shared piece —
a scripted model — is genuinely shared rather than incidentally duplicated: both files need a model
that does exactly what the test says and nothing else.

The repo's other fixtures (`client`, `gold`, `chunks`) stay in the modules that use them. Hoisting
those here would make each test file's dependencies invisible from the file itself, which is worth
avoiding for a fixture with exactly one consumer.
"""

import asyncio
import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
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
from health_coverage_navigator.config import Toolset
from health_coverage_navigator.vectors.embedder import Embedder
from health_coverage_navigator.vectors.store import VectorIndex, build_store


@pytest.fixture(autouse=True)
def _no_live_model_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    import pydantic_ai.models

    monkeypatch.setattr(pydantic_ai.models, "ALLOW_MODEL_REQUESTS", False)


@pytest.fixture(autouse=True)
def _no_live_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The embedding half of the no-provider invariant.

    `ALLOW_MODEL_REQUESTS` is PydanticAI's switch and has no bearing on a direct
    `AsyncOpenAI().embeddings.create()`, which is exactly what `openai_embedder` does. Without this
    the suite would need a key, would spend money, and — worst — a test that accidentally used the
    real embedder would *pass*, quietly, at whatever the developer's rate limit allowed.

    Patched at the factory rather than at the SDK, so the failure names the seam a test should have
    used instead of surfacing as an authentication error from somewhere in `openai`.
    """
    import health_coverage_navigator.vectors.embedder as embedder_module

    def refuse(*_args: Any, **_kwargs: Any):
        raise AssertionError(
            "a test tried to build the live OpenAI embedder. Use the `fake_embedder` fixture; "
            "the suite must not reach a provider."
        )

    monkeypatch.setattr(embedder_module, "openai_embedder", refuse)


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


#: Width of the fake embedding space. Small on purpose — nothing here measures retrieval quality,
#: and 1,536 floats per chunk in a fixture is noise in a failure message.
FAKE_DIM = 16

#: The embedding model name the fixture store records. Not a real model, and it says so, so a
#: manifest that somehow reached a real store would be rejected by `VectorIndex.open` rather than
#: quietly used.
FAKE_MODEL = "fake-embedding-model"


def fake_embed(texts: Sequence[str]) -> list[list[float]]:
    """A deterministic hash-based embedding: same text, same unit vector, every time.

    **Not semantic, and deliberately not pretending to be.** Two paraphrases get unrelated vectors,
    so this can never stand in for a judgement about whether vector search *works* — that is what
    `make eval-retrieval-vector` measures, against the real corpus and a real model. What it does
    give is the two properties the plumbing tests need: an exact-text query lands its own chunk at
    similarity 1.0, and the ordering is stable across runs.
    """
    out: list[list[float]] = []
    for text in texts:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = [(digest[i % len(digest)] / 255.0) - 0.5 for i in range(FAKE_DIM)]
        norm = math.sqrt(sum(x * x for x in raw)) or 1.0
        out.append([x / norm for x in raw])
    return out


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


#: A `vector_search` call that actually retrieves the deductible chunk under `fake_embed`.
#: The query is that chunk's exact `retrieval_text` — with a hash embedder a paraphrase retrieves
#: nothing (see `fake_embed`), so anything else would be testing the fixture rather than the tool.
VECTOR_SEARCH = (
    "vector_search",
    {
        "query": make_chunk("glossary_deductible", "Deductible", DEDUCTIBLE_TEXT).retrieval_text,
        "k": 5,
    },
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
    vectors: VectorIndex

    script = staticmethod(scripted_model)
    answer = staticmethod(grounded_answer)
    chunk = staticmethod(make_chunk)
    embed = staticmethod(fake_embed)

    SEARCH = SEARCH
    VECTOR_SEARCH = VECTOR_SEARCH
    DEDUCTIBLE_ID = DEDUCTIBLE_ID
    PREMIUM_ID = PREMIUM_ID
    DEDUCTIBLE_TEXT = DEDUCTIBLE_TEXT
    PREMIUM_TEXT = PREMIUM_TEXT

    def run(
        self,
        *turns: Any,
        message: str = "what is a deductible?",
        toolset: Toolset | None = None,
    ) -> ChatResponse:
        """One full agent run against the scripted turns."""
        with build_agent(toolset=toolset).override(model=scripted_model(*turns)):
            return asyncio.run(
                answer_question(
                    ChatRequest(message=message),
                    self.index,
                    toolset=toolset,
                    vectors=self.vectors,
                )
            )

    def stream(
        self,
        *turns: Any,
        message: str = "what is a deductible?",
        toolset: Toolset | None = None,
    ) -> list[StreamEvent]:
        """Every SSE event one run emits, in order."""

        async def drain() -> list[StreamEvent]:
            request = ChatRequest(message=message)
            return [
                event
                async for event in stream_answer(
                    request, self.index, toolset=toolset, vectors=self.vectors
                )
            ]

        with build_agent(toolset=toolset).override(model=scripted_model(*turns)):
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


@dataclass(frozen=True, slots=True)
class VectorKit:
    """Everything `test_vectors.py` needs to build and open a store offline, in one fixture.

    Same reason `AgentKit` is one object: `tests/` is not a package, so a test module can reach all
    of this through a single parameter but cannot import any of it by name from here.
    """

    DIM = FAKE_DIM
    MODEL = FAKE_MODEL

    embed = staticmethod(fake_embed)

    @staticmethod
    async def embedder(texts: Sequence[str]) -> list[list[float]]:
        """`fake_embed` behind the async `Embedder` signature."""
        return fake_embed(texts)

    @staticmethod
    async def build(chunks: Sequence[Chunk], root: Path) -> VectorIndex:
        return await build_fake_store(chunks, root)

    @staticmethod
    async def open(root: Path, **overrides: Any) -> VectorIndex:
        """Reopen a store `build` wrote, with the staleness inputs overridable."""
        return await VectorIndex.open(
            VectorKit.embedder,
            **{
                "store_dir": root / "lancedb",
                "meta_path": root / "vectors_meta.json",
                "embedding_model": FAKE_MODEL,
                **overrides,
            },
        )


@pytest.fixture
def vector_kit() -> VectorKit:
    return VectorKit()


@pytest.fixture
def fake_embedder() -> Embedder:
    """`fake_embed` behind the async `Embedder` signature."""
    return VectorKit.embedder


async def build_fake_store(chunks: Sequence[Chunk], root: Path) -> VectorIndex:
    """A real LanceDB store over `chunks`, embedded with the hash embedder.

    Real rather than a stub object, because the things most likely to break are LanceDB's own —
    the fixed-width Arrow schema, the pre-filter, the `_distance` column — and a hand-written fake
    `VectorIndex` would only assert that our mock behaves like our mock. It costs milliseconds,
    needs no network, and writes only under `tmp_path`.

    The snapshot check passes rather than being bypassed: `build_store` records the live committed
    `chunker_snapshots()`, and the reopen compares against those same ids. So this exercises the
    agreeing path; `tests/test_vectors.py` drives the disagreeing one directly.
    """

    async def embedder(texts: Sequence[str]) -> list[list[float]]:
        return fake_embed(texts)

    store_dir, meta_path = root / "lancedb", root / "vectors_meta.json"
    await build_store(
        chunks,
        embedder,
        dimensions=FAKE_DIM,
        embedding_model=FAKE_MODEL,
        batch_size=2,
        store_dir=store_dir,
        meta_path=meta_path,
    )
    return await VectorIndex.open(
        embedder,
        store_dir=store_dir,
        meta_path=meta_path,
        embedding_model=FAKE_MODEL,
    )


@pytest.fixture(scope="session")
def agent_kit(tmp_path_factory: pytest.TempPathFactory) -> AgentKit:
    """Two hand-written chunks, not the real 6,722, plus a vector store over the same two.

    These tests are about what happens to a citation, not about whether retrieval ranks well;
    `tests/test_corpus_index.py` and `make eval-retrieval-vector` cover that against the real
    corpus. A two-document index also makes "the model cited something it never retrieved" easy to
    set up, which is the single most important thing the guardrail does.

    The vector store is built from the *same* two chunks as the BM25 index on purpose: the
    grounding path resolves every hit through `CorpusIndex`, so a store over different chunks would
    make every vector citation silently uncitable — the exact failure `VectorIndex.open`'s staleness
    check exists to prevent in production.
    """
    chunks = [
        make_chunk("glossary_deductible", "Deductible", DEDUCTIBLE_TEXT),
        make_chunk("glossary_premium", "Premium", PREMIUM_TEXT),
    ]
    root = tmp_path_factory.mktemp("agent_kit")
    return AgentKit(
        index=CorpusIndex(chunks),
        vectors=asyncio.run(build_fake_store(chunks, root)),
    )
