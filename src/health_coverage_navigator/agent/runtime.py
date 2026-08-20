"""The agent itself, and the mapping from what it produces to what the contract serves.

Three jobs, in order of how much they matter:

**1. The grounding guardrail is code, not prose.** `_validate_grounding` runs on every candidate
answer and raises `ModelRetry` when a citation names a chunk no tool returned, quotes words that
are not in that chunk, or leaves a `[cN]` marker pointing at nothing. The model gets told what it
got wrong and tries again. The prompt asks for grounded answers; this is what makes them grounded.

**2. Citations are built from the corpus, never from the model.** All the model contributes is
*which* chunk and *which words* — both checked. Title, URL, doc id, source type and score are read
off the real `Chunk`. There is no path by which an invented title reaches the browser.

**3. `answer_question()` is `stream_answer()` drained to the end.** One code path, two shapes. At
Phase 0 a test asserted the streaming and non-streaming endpoints returned identical objects; a
nondeterministic model makes that unassertable by re-running, so the property is now structural
instead — the non-streaming answer *is* the streaming one's `done` payload.

The output type the model sees (`AgentAnswer`) is deliberately not the wire type (`ChatResponse`).
See `agent/models.py` for why.
"""

import time
import uuid
from collections.abc import AsyncIterator
from functools import lru_cache

from pydantic_ai import (
    Agent,
    AgentRunResultEvent,
    ModelRetry,
    PartDeltaEvent,
    PartStartEvent,
    RunContext,
    RunUsage,
    TextPartDelta,
    ToolCallPartDelta,
    UsageLimits,
)
from pydantic_core import from_json

from health_coverage_navigator.agent.deps import AnswerDeps
from health_coverage_navigator.agent.index import CorpusIndex
from health_coverage_navigator.agent.models import MARKER_RE, AgentAnswer, AgentCitation
from health_coverage_navigator.agent.prompt import system_prompt
from health_coverage_navigator.agent.tools import select_tools
from health_coverage_navigator.api.models import (
    AnswerClaim,
    ChatRequest,
    ChatResponse,
    Citation,
    CitationEvent,
    DoneEvent,
    StartEvent,
    StepEvent,
    StreamEvent,
    TokenEvent,
    TraceStep,
    Usage,
)
from health_coverage_navigator.config import Toolset, get_config
from health_coverage_navigator.settings import get_secrets
from health_coverage_navigator.structured.catalog import source_label, source_url
from health_coverage_navigator.structured.models import Row
from health_coverage_navigator.structured.store import StructuredStore
from health_coverage_navigator.vectors.store import VectorIndex
from health_coverage_navigator.web.client import WebSearchClient
from health_coverage_navigator.web.models import WebResult


def _normalize(text: str) -> str:
    """Collapse whitespace for a quotation check that survives the corpus's mid-sentence wrapping.

    The same normalisation `tests/test_gold_set.py` applies to `expected_snippet`. A byte-exact
    test would reject quotations that are genuinely verbatim, which would send the model into a
    retry loop it cannot win.
    """
    return " ".join(text.split()).lower()


# ---------------------------------------------------------------- the agent ------------------


def _resolve_model(model: str) -> object:
    """Turn `config.yaml`'s `provider:model` string into a model object, with the credential and
    the retry budget attached.

    **`Agent("openai:...")` does not see `Secrets`** — docs/progress.md records this as a dead end
    worth not repeating. PydanticAI reads `OPENAI_API_KEY` from the process environment and
    `uv run` does not load `.env`, so the obvious wiring raises `UserError` even though the settings
    object holds the key. Passing the provider explicitly is also the better shape: the credential
    flows through one audited call instead of ambient global state.

    Anything that is not an `openai:` string is handed to PydanticAI as-is, which is what lets a
    test pass `TestModel`/`FunctionModel` through the same door.
    """
    if not model.startswith("openai:"):
        return model

    from openai import AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIResponsesModel
    from pydantic_ai.providers.openai import OpenAIProvider

    # The client is built by hand rather than letting the provider make a default one, for one
    # reason: `max_retries`. The SDK's default of 2 is not enough under `make eval --concurrency`,
    # where a tokens-per-minute limit produces a burst of 429s that each ask to be retried in under
    # three seconds. Those are transient by definition, and a transient failure that lands in an
    # eval run as a failed question moves the headline score — a worse outcome than waiting.
    client = AsyncOpenAI(
        api_key=get_secrets().openai_api_key.get_secret_value(),
        max_retries=get_config().agent.request_retries,
    )

    # **`OpenAIResponsesModel`, not `OpenAIChatModel`.** This has to match what a bare `openai:`
    # string would infer on its own (`infer_model` maps the plain `openai` provider prefix to
    # `OpenAIResponsesModel`; only `openai-chat:` gets `OpenAIChatModel`). Nothing in this function
    # is here to pick a different API surface — it exists for the credential and the retry budget,
    # and the model class must simply track inference.
    #
    # Getting it wrong is a live 400, not a lint warning: the first draft built `OpenAIChatModel`
    # on an unchecked assumption, and `gpt-5.6-luna` rejects tool calls on that endpoint outright —
    # *"Function tools with reasoning_effort are not supported ... in /v1/chat/completions. To use
    # function tools, use /v1/responses"*. An agent with no tools is not this project.
    return OpenAIResponsesModel(
        model.removeprefix("openai:"), provider=OpenAIProvider(openai_client=client)
    )


def build_agent(
    model: str | None = None,
    toolset: Toolset | None = None,
    structured: bool | None = None,
    web: bool | None = None,
) -> Agent[AnswerDeps, AgentAnswer]:
    """The one agent. Every later phase registers more tools here rather than building another.

    Resolves **every** default *before* the cache lookup, which is load-bearing rather than tidy:
    `lru_cache` keys on the call's arguments, so `build_agent()` and `build_agent(None)` are
    different keys and would hand back two different `Agent` objects. Tests substitute a model with
    `agent.override(...)` on the instance they hold, so a second instance means the override
    silently does not apply and the run goes to the real provider — which is exactly how this was
    found. Phase 1b added a second defaulted argument and took the bug again; Phase 1-c added a
    third and took it a second time. **Phase 2 adds a fourth.** Every one of them is resolved here,
    and `tests/conftest.py`'s `AgentKit._agent` derives the key the same way `stream_answer` does.
    """
    config = get_config().agent
    return _build_agent(
        model or config.model,
        toolset or config.toolset,
        config.structured_tools if structured is None else structured,
        config.web_tools if web is None else web,
    )


#: Three toolsets x two lane booleans x two lane booleans x a handful of models. Sized so a
#: `make eval` sweep across configurations does not evict the agent it is about to reuse.
@lru_cache(maxsize=48)
def _build_agent(
    model: str, toolset: Toolset, structured: bool, web: bool
) -> Agent[AnswerDeps, AgentAnswer]:
    """Cached because construction resolves the credential and builds every tool schema.

    Every axis is part of the key rather than read inside, because each changes the registered tools
    *and* the instructions — two agents that differ in what they can do must not share one cached
    object. Phase 1-c made that sharper than Phase 1b did (a structured agent is told plan-specific
    questions are answerable, a reference-only one is told they are not) and Phase 2 sharper still:
    a web-enabled agent is told current-events questions are answerable, and every other
    configuration is told to abstain on them.
    """
    config = get_config().agent
    agent = Agent(
        _resolve_model(model),  # type: ignore[arg-type]
        deps_type=AnswerDeps,
        output_type=AgentAnswer,
        instructions=system_prompt(toolset, structured, web),
        tools=select_tools(toolset, structured, web),
        # Retries are the grounding guardrail's budget: a rejected answer is re-attempted with the
        # validator's complaint attached. Two is enough for the realistic failures (a mistyped
        # chunk id, a paraphrased quotation) and short of enough to burn a run on a model that has
        # decided to cite something it never read.
        retries=config.retries,
    )
    agent.output_validator(_validate_grounding)
    return agent


def _validate_grounding(ctx: RunContext[AnswerDeps], answer: AgentAnswer) -> AgentAnswer:
    """Refuse an answer whose provenance does not hold up, and say exactly why.

    Every check here has a matching one in `ChatResponse._check_provenance`, which raises a 500.
    Catching them one layer earlier turns a served error into a retry the model can act on. The two
    that have no contract equivalent — an unseen chunk id, a non-verbatim snippet — are the
    grounding rule itself, and they are the reason this function exists rather than leaving the
    contract validator to do it.
    """
    if answer.abstained:
        # An abstention with citations is not a contradiction — a Phase 2 web answer could
        # legitimately abstain while pointing somewhere — but at Phase 1a it means the model found
        # sources and then declined to use them, which is the false-abstention failure the eval
        # set measures separately. Let it through; the citations still have to be real.
        pass
    elif not answer.citations:
        raise ModelRetry(
            "An answer with no citations is not allowed. Either cite the passages you used, or "
            "set abstained=true and explain that the reference material does not cover this."
        )

    ids = [c.id for c in answer.citations]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ModelRetry(f"Citation ids must be unique; {duplicates} appear more than once.")

    for citation in answer.citations:
        if citation.is_row:
            _validate_row_citation(ctx, citation)
            continue
        if citation.is_web:
            _validate_web_citation(ctx, citation)
            continue

        chunk = ctx.deps.seen_chunks.get(citation.chunk_id or "")
        if chunk is None:
            known = sorted(ctx.deps.seen_chunks)[:10]
            raise ModelRetry(
                f"Citation {citation.id} names chunk_id {citation.chunk_id!r}, which no tool "
                f"returned in this conversation. You may only cite passages you retrieved. "
                f"Chunks you have seen: {known or 'none — search first'}."
            )
        if _normalize(citation.snippet or "") not in _normalize(chunk.text):
            raise ModelRetry(
                f"Citation {citation.id}'s snippet is not present in chunk "
                f"{citation.chunk_id!r}. Copy the supporting words exactly from that passage's "
                f"text rather than paraphrasing or summarising them."
            )

    known_ids = set(ids)
    orphans = sorted(set(MARKER_RE.findall(answer.answer)) - known_ids)
    if orphans:
        raise ModelRetry(
            f"The answer contains marker(s) {orphans} with no matching citation. Every [cN] in "
            f"the text must correspond to a citation with id 'cN'."
        )
    return answer


def _validate_row_citation(ctx: RunContext[AnswerDeps], citation: AgentCitation) -> None:
    """The relational lane's half of the grounding guardrail.

    Same rule as a passage citation, one lane over: cite only what a tool returned, and quote it
    exactly. What changes is what "exactly" means. A passage quotation is normalised for whitespace
    because the corpora wrap mid-sentence, so a byte-exact test would reject genuinely verbatim
    quotations. **A cell has no wrapping to survive**, and its whitespace is data — `'$4,500 '`
    carries a trailing space that distinguishes the published value from a tidied one — so this
    comparison is byte-exact, and deliberately stricter than the chunk path.
    """
    row = ctx.deps.seen_rows.get(citation.row_id or "")
    if row is None:
        known = sorted(ctx.deps.seen_rows)[:10]
        raise ModelRetry(
            f"Citation {citation.id} names row_id {citation.row_id!r}, which no query returned in "
            f"this conversation. You may only cite rows you queried. Rows you have seen: "
            f"{known or 'none — query first'}."
        )
    for column, value in (citation.cells or {}).items():
        if column not in row.cells:
            raise ModelRetry(
                f"Citation {citation.id} names column {column!r}, which row {citation.row_id!r} "
                f"does not have. Its columns are: {sorted(row.cells)}."
            )
        actual = row.cells[column]
        if actual is None:
            raise ModelRetry(
                f"Citation {citation.id} gives a value for {column!r} in row "
                f"{citation.row_id!r}, but that cell is NULL — the query produced no value there. "
                f"Do not report a value for it."
            )
        if value != actual:
            raise ModelRetry(
                f"Citation {citation.id} gives {column!r} as {value!r}, but the row holds "
                f"{actual!r}. Copy the cell exactly, including any spaces, commas or symbols; "
                f"your answer text may present it more readably."
            )


def _validate_web_citation(ctx: RunContext[AnswerDeps], citation: AgentCitation) -> None:
    """The web lane's half of the grounding guardrail.

    Same rule as the other two lanes — cite only what a tool returned, and quote it exactly — but
    **this is the lane where the rule does the most work**. A chunk id means nothing outside this
    repo and a row id is obviously internal, so a model inventing either produces something
    self-evidently wrong. A *URL* is different: a model can write
    `https://www.cms.gov/newsroom/press-releases/...` that is plausible, well-formed, and never
    retrieved, and neither a reader nor a grader could tell by looking. Requiring a `result_id` that
    only a search could have assigned is what turns "do not invent sources" from an instruction into
    something the model cannot do.

    The quotation check is **whitespace-normalized**, matching the chunk path rather than the row
    path. Extracted web text wraps and re-wraps arbitrarily, so a byte-exact comparison would reject
    genuinely verbatim quotations and send the model into a retry loop it cannot win. A table cell
    has no wrapping to survive and its whitespace is data, which is why `_validate_row_citation` is
    stricter. The two rules disagree on purpose; each is right about its own evidence.
    """
    result = ctx.deps.seen_results.get(citation.result_id or "")
    if result is None:
        known = sorted(ctx.deps.seen_results)[:10]
        raise ModelRetry(
            f"Citation {citation.id} names result_id {citation.result_id!r}, which no web search "
            f"returned in this conversation. You may only cite results you retrieved — never a URL "
            f"you did not get back from web_search. Results you have seen: "
            f"{known or 'none — search first'}."
        )
    if _normalize(citation.snippet or "") not in _normalize(result.content):
        raise ModelRetry(
            f"Citation {citation.id}'s snippet is not present in the content of result "
            f"{citation.result_id!r} ({result.url}). Copy the supporting words exactly from that "
            f"result's text rather than paraphrasing, summarising, or writing what you expect the "
            f"page to say."
        )


# ---------------------------------------------------------------- mapping out ----------------


def _row_citation(citation: AgentCitation, row: Row) -> Citation:
    """One relational-lane citation, built from the row rather than from the model.

    Same rule as the chunk path: the model contributes *which* row and *which* cells, both already
    validated, and every displayed field is derived here. The snippet is the cited cells rendered
    one per line — a row shown as a paragraph hides which columns were read, which is the part that
    makes it evidence.

    `doc_id` and `chunk_id` stay `None`: these are the first citations in this repo that are not
    chunks, and the frontend renders that as a different card rather than an empty drill-down.
    """
    label = (
        f"{source_label(row.source)} · {row.view.rsplit('/', 1)[-1]} ({row.partition})"
        if row.source and row.partition
        else "Structured query result"
    )
    return Citation(
        id=citation.id,
        # `structured_api` has meant *deterministic row-level lookup* since Phase 0. Whether the
        # row came from a vendored mirror or (Phase 3) a live endpoint is a property of the
        # citation, not a fourth lane — so nothing about the contract moves here.
        source_type="structured_api",
        title=label,
        url=source_url(row.source),
        doc_id=None,
        chunk_id=None,
        snippet="\n".join(
            f"{column}: {'NULL' if value is None else value}"
            for column, value in (citation.cells or {}).items()
        ),
        score=None,
    )


def _web_citation(citation: AgentCitation, result: WebResult) -> Citation:
    """One web-lane citation, built from the result rather than from the model.

    Same rule as the other two lanes: the model contributes *which* result and *which words*, both
    already validated, and every displayed field is derived here. A title the model would like the
    reader to see cannot become the title the reader sees — which matters most in this lane, since
    the title is the only place a reader learns who published the claim.

    **The domain is in the title on purpose.** The citation card shows a title and hides the URL
    behind a link, so without this a reader cannot tell a CMS page from a forum post without
    clicking. For a health question, who is saying something is part of the claim — and surfacing
    the source is precisely the alternative this design chose over filtering the web to an
    allowlist (docs/web_search_tool.md §7).

    `doc_id` and `chunk_id` stay `None`: there is no corpus document behind a web result, and the
    `url` is the drill-down.
    """
    dated = f", {result.published_date}" if result.published_date else ""
    return Citation(
        id=citation.id,
        source_type="web",
        title=f"{result.title} — {result.domain}{dated}",
        url=result.url,
        doc_id=None,
        chunk_id=None,
        snippet=citation.snippet or "",
        score=None,
    )


def _citations(answer: AgentAnswer, deps: AnswerDeps) -> list[Citation]:
    """Build the wire citations from the real evidence — never from the model.

    The model supplies an identifier and a quotation (a `chunk_id` + `snippet`, a `row_id` +
    `cells`, or a `result_id` + `snippet`), all already validated. Everything else — title, url,
    doc_id, source_type — is read off the `Chunk`, the `Row` or the `WebResult`, so a title the
    model would like the reader to see cannot become the title the reader sees.
    """
    citations: list[Citation] = []
    for citation in answer.citations:
        if citation.is_row:
            citations.append(_row_citation(citation, deps.seen_rows[citation.row_id or ""]))
            continue
        if citation.is_web:
            citations.append(_web_citation(citation, deps.seen_results[citation.result_id or ""]))
            continue
        chunk = deps.seen_chunks[citation.chunk_id or ""]
        citations.append(
            Citation(
                id=citation.id,
                source_type="reference",
                title=chunk.citation_label,
                url=chunk.url or None,
                doc_id=chunk.doc_id,
                chunk_id=chunk.id,
                snippet=citation.snippet or "",
                score=None,
            )
        )
    return citations


def _claims(answer: str, citation_ids: set[str]) -> list[AnswerClaim]:
    """Split the answer into sentence-ish spans and attach the markers each one carries.

    Derived rather than asked of the model, because `AnswerClaim.text` must be a **verbatim**
    substring of the answer (Phase 4's hover-to-highlight does an index lookup, not a fuzzy match)
    and a model reproducing its own prose character-for-character is a coin flip. Splitting on the
    markers themselves is exact by construction: each span ends just after the `[cN]` that backs
    it, so `"".join(spans)` is a prefix of the answer and every span is a real slice of it.

    Text after the last marker — a closing caveat, or an entire uncited answer — carries no claim.
    That is correct: a claim is a span *plus what backs it*, and nothing backs that text.
    """
    claims: list[AnswerClaim] = []
    cursor = 0
    for match in MARKER_RE.finditer(answer):
        span = answer[cursor : match.end()].strip()
        cursor = match.end()
        marker = match.group(1)
        if span and marker in citation_ids:
            claims.append(AnswerClaim(text=span, citation_ids=[marker]))
    return claims


def _usage(usage: RunUsage, model: str | None, latency_ms: int) -> Usage:
    """Map PydanticAI's usage onto the contract's.

    `total_tokens` is summed here rather than read: `RunUsage` carries input, output and several
    cache-tier counts separately, with no single total. `latency_ms` is wall clock for the whole
    run — every tool call, every retry — which is the number a reader waiting on the answer
    actually experienced.
    """
    return Usage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.input_tokens + usage.output_tokens,
        latency_ms=latency_ms,
        model=model,
    )


# ---------------------------------------------------------------- streaming ------------------


def _partial_answer(buffer: str) -> str | None:
    """Read the `answer` field out of a half-written JSON payload, or `None` if it is not one.

    This is how the answer streams despite the output being a structured object rather than plain
    text: the model emits the object as JSON fragments, and `allow_partial` parses a prefix of it.

    Identifying *which* streamed part is the output is done by shape rather than by event
    bookkeeping — a buffer that parses to an object with a string `answer` is the output, and none
    of the four tools takes an argument by that name. The alternative was tracking part indices
    across `FinalResultEvent`, which differs between tool-output, native-output and prompted-output
    modes and between providers; this works the same for all of them, and for a provider that sends
    no deltas at all (the final flush below covers that case).
    """
    if not buffer.lstrip().startswith("{"):
        return None
    try:
        parsed = from_json(buffer, allow_partial="trailing-strings")
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    value = parsed.get("answer")
    return value if isinstance(value, str) else None


async def stream_answer(
    request: ChatRequest,
    index: CorpusIndex,
    *,
    model: str | None = None,
    toolset: Toolset | None = None,
    vectors: VectorIndex | None = None,
    structured: StructuredStore | None = None,
    web: WebSearchClient | None = None,
) -> AsyncIterator[StreamEvent]:
    """Answer one question, as the SSE event sequence the frontend already renders.

    Event order mirrors what the Phase 0 stub established, because the UI was built against it: the
    trace fills in first (you want to see what it is doing while it thinks), then the answer
    streams, then citations land, then `done` carries the authoritative object.

    `toolset` and `vectors` travel together in practice but are separate arguments on purpose: the
    toolset decides which tools are *registered* (and so what the model is told it can do), while
    `vectors` is the handle those tools need. Passing a store without selecting a vector toolset is
    harmless; selecting one without a store is what the caller must not do, and `routes/chat.py`
    turns that into a 503 rather than letting it reach the model.
    """
    started = time.perf_counter()
    resolved_model = model or get_config().agent.model
    limits = get_config().agent

    # `plan_year` finally does something. It has ridden in the contract since Phase 0, and here it
    # chooses which partition of the vendored plan data the structured tools may read — so a
    # question about a year that is not on disk abstains instead of answering from another year.
    deps = AnswerDeps(
        index=index,
        vectors=vectors,
        structured=structured,
        web=web,
        plan_year=request.plan_year,
    )
    conversation_id = request.conversation_id or f"conv_{uuid.uuid4().hex[:12]}"
    message_id = f"msg_{uuid.uuid4().hex[:12]}"

    yield StartEvent(conversation_id=conversation_id, message_id=message_id)

    trace: list[TraceStep] = []

    def step(kind, summary, **extra) -> StepEvent:
        entry = TraceStep(index=len(trace), kind=kind, summary=summary, **extra)
        trace.append(entry)
        return StepEvent(step=entry)

    where = (
        "the reference corpus and the vendored plan data" if structured else "the reference corpus"
    )
    yield step("plan", f"Search {where} for: {request.message}")

    # No store, no structured tools — whatever the configuration says. `routes/chat.py` turns a
    # configured-but-missing store into a 503 before this point; a caller that builds deps by hand
    # (a test, a script) gets a reference-only agent rather than tools that would raise on use.
    agent = build_agent(model, toolset, structured=structured is not None, web=web is not None)
    buffers: dict[int, str] = {}
    streamed = ""
    drained = 0
    result = None

    async with agent.run_stream_events(
        request.message,
        deps=deps,
        usage_limits=UsageLimits(
            # Loop safety from day one (docs/plan.md, Phase 1a). Phase 4 adds cycle detection on
            # top of these rather than introducing the idea. Without them a model that keeps
            # reformulating a query it will never satisfy runs until the request times out, and the
            # user sees a spinner rather than an abstention.
            request_limit=limits.request_limit,
            tool_calls_limit=limits.tool_calls_limit,
        ),
    ) as events:
        async for event in events:
            if isinstance(event, PartStartEvent):
                # A new part reuses an index the previous model request already used, so the buffer
                # has to be cleared rather than appended to.
                buffers[event.index] = ""
            elif isinstance(event, PartDeltaEvent):
                delta = event.delta
                if isinstance(delta, ToolCallPartDelta) and isinstance(delta.args_delta, str):
                    buffers[event.index] = buffers.get(event.index, "") + delta.args_delta
                elif isinstance(delta, TextPartDelta):
                    buffers[event.index] = buffers.get(event.index, "") + delta.content_delta
                else:
                    continue
                answer_so_far = _partial_answer(buffers[event.index])
                if answer_so_far is None:
                    continue
                if answer_so_far.startswith(streamed):
                    addition = answer_so_far[len(streamed) :]
                    if addition:
                        streamed = answer_so_far
                        yield TokenEvent(delta=addition)
                elif not streamed.startswith(answer_so_far):
                    # **A retry is writing a different answer.** The grounding validator rejected
                    # the previous draft and the model is producing another one, which no longer
                    # extends what the reader has been shown. Appending would render the rejected
                    # draft followed by the real answer — and the rejected draft is by construction
                    # the ungrounded one, which is the single worst thing this UI can display.
                    #
                    # The `startswith` guard on this branch matters: early fragments of the retry
                    # ("A ded") are usually still a prefix of what was streamed, and resetting on
                    # those would flicker the answer away and back on every keystroke. This fires
                    # only once the two genuinely diverge.
                    streamed = answer_so_far
                    yield TokenEvent(delta=answer_so_far, reset=True)
            elif isinstance(event, AgentRunResultEvent):
                result = event.result

            # Tool steps are taken from `deps.trace` rather than from the tool events, because the
            # tools record the arguments, the real duration, and a summary of what came back —
            # none of which the event stream carries.
            while drained < len(deps.trace):
                yield step(
                    deps.trace[drained].kind,
                    deps.trace[drained].summary,
                    tool=deps.trace[drained].tool,
                    input=deps.trace[drained].input,
                    duration_ms=deps.trace[drained].duration_ms,
                )
                drained += 1

        if result is None:  # pragma: no cover - the event stream always ends with a result
            result = events.result
        if result is None:  # pragma: no cover - unreachable; a run either ends or raises
            raise RuntimeError("the agent run produced no result")

    answer: AgentAnswer = result.output
    citations = _citations(answer, deps)

    yield step(
        "synthesis",
        (
            "Declined to answer from the reference corpus"
            if answer.abstained
            else f"Synthesized an answer from {len(citations)} cited passage(s)"
        ),
        duration_ms=int((time.perf_counter() - started) * 1000),
    )

    # Whatever the delta path did not manage to stream — because the provider sent the output in
    # one piece, or because a retry replaced an earlier draft. `done` is authoritative either way,
    # but the UI shows the accumulated tokens until then, so a silent gap here reads as a truncated
    # answer.
    if answer.answer != streamed:
        extends = answer.answer.startswith(streamed)
        # `reset` when the final answer is not an extension of what was streamed — the same
        # abandoned-draft case the delta loop handles above, arriving here when the provider sent
        # the output in one piece rather than in fragments.
        yield TokenEvent(
            delta=answer.answer[len(streamed) :] if extends else answer.answer,
            reset=not extends,
        )

    for citation in citations:
        yield CitationEvent(citation=citation)

    yield DoneEvent(
        response=ChatResponse(
            conversation_id=conversation_id,
            message_id=message_id,
            abstained=answer.abstained,
            answer=answer.answer,
            claims=_claims(answer.answer, {c.id for c in citations}),
            citations=citations,
            trace=trace,
            usage=_usage(
                result.usage,
                resolved_model,
                int((time.perf_counter() - started) * 1000),
            ),
        )
    )


async def answer_question(
    request: ChatRequest,
    index: CorpusIndex,
    *,
    model: str | None = None,
    toolset: Toolset | None = None,
    vectors: VectorIndex | None = None,
    structured: StructuredStore | None = None,
    web: WebSearchClient | None = None,
) -> ChatResponse:
    """The same answer, without the stream.

    Implemented by draining `stream_answer` rather than beside it. Two answer paths that "should"
    agree is precisely the kind of thing that silently stops agreeing; there is only one here.
    """
    async for event in stream_answer(
        request,
        index,
        model=model,
        toolset=toolset,
        vectors=vectors,
        structured=structured,
        web=web,
    ):
        if isinstance(event, DoneEvent):
            return event.response
    raise RuntimeError("the agent stream ended without a done event")  # pragma: no cover
