"""One real question, one real model call, against the live provider.

**This is a liveness check, not a correctness check, and the distinction is the reason it is not a
pytest test.** `make check-all` asserts that *our* code is right and is guaranteed never to reach a
provider (`tests/conftest.py` sets `ALLOW_MODEL_REQUESTS = False` suite-wide). This asks a different
question — is the wiring to OpenAI intact, does a real model's output still parse, do tools still
reach the API — whose answer can change for reasons outside this repo. A failing test means your
commit is wrong; a failing smoke check might mean the provider changed something. Folding the two
into one command trains you to shrug at red.

It sits outside `check-all` for the same reason `eval`, `scan` and `chunk` do: it costs money and
needs the network.

**Why `scripts/` and not `tests/` or `src/`.** Not `src/`, because that is application code and
this is a check. Not `tests/`, because that directory carries exactly one invariant stated in bold
— *no test may reach a model provider* — and the one file that deliberately does would undercut it
for anyone reading, whether or not pytest collects it (it would not; `smoke.py` is not
`test_*.py`). What is left is `scripts/`, which already holds `scan_sensitive.py`: the same animal,
a standalone verification gate with its own Make target, deliberately outside the fast inner-loop
gate. `scripts` is in pyright's `include` so this file is typechecked like the rest — a check that
only runs when you spend money must not be able to rot silently between runs.

**What it covers that nothing else does.** `make eval` already exercises the agent live, 35 times,
and grades it — but through `answer_question()`, and the streaming half of `stream_answer()` has
exactly one property no offline test can assert: `runtime._partial_answer()` parses the output as a
**real provider fragments it**. `tests/conftest.py` simulates that with a 17-character split aligned
to nothing, which is a decent guess and nothing more. If OpenAI changes how `/v1/responses` chops
output deltas, every test stays green, the final flush in `stream_answer` still delivers a correct
answer in one lump, and token-by-token streaming is silently dead. `answer_streams_incrementally`
below is the check that exists for that, and it is the reason this file drives the streaming path
rather than the simpler one.

Three rungs, no overlap:

| | costs | asserts |
|---|---|---|
| `make check-all` | nothing, offline | the code is correct |
| `make smoke` | one model call | the live path works at all |
| `make eval` | 35 model calls | the answers are any good |

Nothing here asserts answer *content*. The model is nondeterministic and the point is the plumbing;
what a good answer looks like is `make eval`'s question. The trace and the answer are printed in
full precisely because a human reading one real run catches what no assertion was written for.
"""

import argparse
import asyncio
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass

from health_coverage_navigator.agent.index import ChunksNotBuiltError, CorpusIndex, get_corpus_index
from health_coverage_navigator.agent.runtime import stream_answer
from health_coverage_navigator.agent.tools import needs_vectors
from health_coverage_navigator.api.models import (
    ChatRequest,
    DoneEvent,
    StartEvent,
    StepEvent,
    StreamEvent,
    TokenEvent,
)
from health_coverage_navigator.config import get_config
from health_coverage_navigator.vectors import embedder as embedder_module
from health_coverage_navigator.vectors.store import VectorIndex

#: In the corpus, and the question docs/agent.md §5 uses as its worked example of the agent
#: reformulating a query BM25 handles badly on its own. A run that answers this one has exercised
#: retrieval, tool choice, grounding and streaming together.
DEFAULT_QUESTION = "What exactly is a deductible?"

#: `abs-01` from the gold set. Provider-directory questions are the clearest out-of-corpus case
#: there is — no amount of searching finds them — so this is the abstention path's smoke question.
ABSTENTION_QUESTION = "Which dermatologists near ZIP 30076 accept Aetna?"


def _normalize(text: str) -> str:
    """The same whitespace-collapsing comparison the output validator and the graders use."""
    return " ".join(text.split()).lower()


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool
    detail: str


def _checks(
    events: Sequence[StreamEvent],
    index: CorpusIndex,
    *,
    expect_abstention: bool,
) -> list[Check]:
    """Everything asserted about one live run, as a checklist rather than a traceback.

    A checklist because several of these can fail independently and the first failure is rarely the
    interesting one — "streaming came in one lump *and* no tool was called" is a different diagnosis
    from either alone.
    """
    checks: list[Check] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(Check(name, ok, detail))

    starts = [e for e in events if isinstance(e, StartEvent)]
    dones = [e for e in events if isinstance(e, DoneEvent)]
    steps = [e.step for e in events if isinstance(e, StepEvent)]
    tokens = [e for e in events if isinstance(e, TokenEvent)]

    check(
        "stream_grammar",
        bool(events)
        and isinstance(events[0], StartEvent)
        and len(dones) == 1
        and isinstance(events[-1], DoneEvent),
        f"{len(events)} events, {len(dones)} done",
    )
    if not dones:
        check("run_completed", False, "the stream ended without a done event")
        return checks

    done = dones[-1]
    response = done.response

    check(
        "ids_stable",
        bool(starts)
        and response.message_id == starts[0].message_id
        and response.conversation_id == starts[0].conversation_id,
        "start and done agree",
    )

    # The check that would have caught the `OpenAIChatModel` 400 on the first run rather than in a
    # browser: that error rejected every tool call outright, and an agent whose tools never fire
    # still produces a plausible-looking answer.
    tool_calls = [s for s in steps if s.tool]
    tools_used = ", ".join(sorted({s.tool for s in tool_calls if s.tool}))
    check(
        "tools_reached_the_provider",
        bool(tool_calls),
        f"{len(tool_calls)} tool step(s): {tools_used}"
        if tool_calls
        else "no tool ran — the model answered from nothing",
    )

    # **The live-only check.** Offline tests can only assert this against a fake provider's
    # fragmentation. Exactly one token event means `_partial_answer` parsed nothing and the final
    # flush delivered the whole answer at once — correct output, dead streaming, invisible in the UI
    # except as a long pause followed by a wall of text.
    check(
        "answer_streams_incrementally",
        len(tokens) > 1,
        f"{len(tokens)} token event(s)"
        + ("" if len(tokens) > 1 else " — partial-JSON parsing is not working"),
    )

    streamed = "".join(e.delta for e in tokens)
    check(
        "streamed_text_matches_done",
        streamed == response.answer,
        "identical"
        if streamed == response.answer
        else f"streamed {len(streamed)} chars, done carries {len(response.answer)}",
    )

    check(
        "abstained" if expect_abstention else "answered",
        response.abstained is expect_abstention,
        f"abstained={response.abstained}",
    )

    # `Citation.chunk_id` is optional in the contract because Phase 2's web lane will have URLs and
    # no chunk. At Phase 1a every citation comes from the corpus, so a missing one is as much a
    # failure as a fabricated one and is counted the same way.
    #
    # Vacuously true for an abstention with no citations, which is the correct outcome there — what
    # this guards against is an abstention that invents sources anyway.
    unresolved = [
        c.chunk_id
        for c in response.citations
        if c.chunk_id is None or index.chunk(c.chunk_id) is None
    ]
    check(
        "citations_resolve",
        not unresolved,
        f"{len(response.citations)} citation(s)"
        if not unresolved
        else f"unresolvable chunk_id(s): {unresolved}",
    )

    misquoted = [
        c.id
        for c in response.citations
        if c.chunk_id is not None
        and (chunk := index.chunk(c.chunk_id)) is not None
        and _normalize(c.snippet) not in _normalize(chunk.text)
    ]
    check(
        "snippets_verbatim",
        not misquoted,
        "every quotation is in its chunk" if not misquoted else f"paraphrased: {misquoted}",
    )

    if not expect_abstention:
        # Redundant with the output validator by design: it rejects an uncited, non-abstained answer
        # with a `ModelRetry` before the response is ever built, so a failure here means the
        # guardrail itself stopped working — which is exactly the thing worth checking on the live
        # path rather than assuming.
        check(
            "answer_is_cited",
            bool(response.citations),
            f"{len(response.citations)} citation(s)"
            if response.citations
            else "uncited — the output validator should have rejected this",
        )

    return checks


def _report(events: Sequence[StreamEvent], checks: Sequence[Check]) -> None:
    """Print the run for a human, then the checklist.

    The trace and the answer come first and in full. Assertions catch what someone thought to write
    down; reading one real answer is what catches the rest.
    """
    done = next((e for e in reversed(events) if isinstance(e, DoneEvent)), None)

    print("\n─── trace " + "─" * 60)
    for event in events:
        if isinstance(event, StepEvent):
            step = event.step
            duration = f"  {step.duration_ms} ms" if step.duration_ms is not None else ""
            print(f"  [{step.index}] {step.kind:<12} {step.summary}{duration}")
            if step.input:
                print(f"       {step.input}")

    if done is not None:
        response = done.response
        print("\n─── answer " + "─" * 59)
        print(f"  abstained={response.abstained}")
        print("\n".join(f"  {line}" for line in response.answer.splitlines()))

        if response.citations:
            print("\n─── citations " + "─" * 56)
            for citation in response.citations:
                print(f"  [{citation.id}] {citation.title}  ({citation.chunk_id})")
                print(f"       {citation.snippet[:150]}")

        if response.usage:
            usage = response.usage
            print("\n─── usage " + "─" * 60)
            print(
                f"  model={usage.model}  tokens={usage.total_tokens}  latency={usage.latency_ms} ms"
            )

    print("\n─── checks " + "─" * 59)
    for entry in checks:
        mark = "PASS" if entry.ok else "FAIL"
        print(f"  {mark}  {entry.name:<30} {entry.detail}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Drive one real question through the live agent and check the streaming path. "
            "Costs one model call and needs OPENAI_API_KEY."
        )
    )
    parser.add_argument(
        "--question",
        help=f"the question to ask. Default: {DEFAULT_QUESTION!r}",
    )
    parser.add_argument(
        "--abstain",
        action="store_true",
        help=(
            "smoke the abstention path instead: ask an out-of-corpus question and require the "
            "agent to decline rather than answer"
        ),
    )
    parser.add_argument("--model", help="override config.yaml's agent.model for this run")
    parser.add_argument(
        "--toolset",
        choices=("lexical", "vector", "both"),
        help=(
            "override config.yaml's agent.toolset. Worth knowing why this exists: under `both` the "
            "agent is told to reach for search_corpus first on a defined term, and it does — so a "
            "default smoke run never exercises vector_search, and the semantic path could break "
            "without any check here noticing. `--toolset vector` is how that path gets smoked. "
            "Default: from config.yaml."
        ),
    )
    args = parser.parse_args()

    question = args.question or (ABSTENTION_QUESTION if args.abstain else DEFAULT_QUESTION)

    try:
        index = get_corpus_index()
    except ChunksNotBuiltError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1

    toolset = args.toolset or get_config().agent.toolset
    print(f"Q: {question}")
    print(f"   ({len(index)} chunks indexed, toolset={toolset}; this makes a real model call)")

    async def drain() -> list[StreamEvent]:
        # The store is opened here rather than at import so a `--toolset lexical` smoke run still
        # works on a machine where `make embed` has never been run.
        vectors = None
        if needs_vectors(toolset):
            config = get_config().vectors
            vectors = await VectorIndex.open(
                embedder_module.openai_embedder(config.embedding_model, config.dimensions)
            )
        request = ChatRequest(message=question)
        return [
            event
            async for event in stream_answer(
                request, index, model=args.model, toolset=toolset, vectors=vectors
            )
        ]

    started = time.perf_counter()
    try:
        events = asyncio.run(drain())
    except Exception as exc:  # noqa: BLE001 - the failure *is* the result here
        # A live run fails for reasons a traceback states badly: an expired key, a model the
        # provider retired, a rejected tool call. The type and message are the diagnosis.
        print(f"\nFAIL  the run raised {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    elapsed = time.perf_counter() - started

    checks = _checks(events, index, expect_abstention=args.abstain)
    _report(events, checks)

    failed = [c for c in checks if not c.ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed in {elapsed:.1f}s")
    if failed:
        print("FAILED: " + ", ".join(c.name for c in failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
