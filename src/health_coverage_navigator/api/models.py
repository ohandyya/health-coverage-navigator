"""The frozen HTTP contract — everything that crosses the browser boundary.

Why the models and not the routes own this: docs/frontend_plan.md §4.1. The answer is **not** a
string. The plan's hard requirement is that every claim carries a source-type label and the
retrieval or URL behind it, so the response is structured from day one — even in Phase 1a, where
only one lane exists. Getting that wrong means reworking the UI at Phase 2, 3 and 4; getting it
right means those phases add *values* to existing enums rather than reshaping anything.

The coupling is the point. These models generate `frontend/src/api/schema.d.ts` via `make types`,
so changing a field here surfaces as a TypeScript error in every component that no longer matches.
Changing anything in this module is a deliberate act, not a convenience.

Two deliberate absences:

**No `fastapi` import.** `evals/models.py` imports `SourceType` from here, so this module has to
stay cheap enough that `pytest tests/test_gold_set.py` does not drag in a web framework. Pure
pydantic, stdlib, and `corpus.py`.

**No eval wire models.** Those need `GoldQuestion`, and importing `evals` here would close the
import cycle the paragraph above opens. They live in `api/routes/evals.py`, next to the routes
that return them; FastAPI emits them into `components.schemas` under their class names regardless
of which module defines them, so codegen is unaffected. Do not "tidy" them back in here.
"""

import re
from datetime import datetime
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

from health_coverage_navigator.corpus import CorpusName

#: The three tool lanes (docs/plan.md). A `Literal` rather than a `StrEnum`, matching `CorpusName`
#: and `Difficulty`: TypeScript receives it as `Citation["source_type"]`, an alias into the
#: generated schema rather than a hand-written duplicate.
SourceType = Literal["reference", "structured_api", "web"]

#: Inline provenance markers in answer text, e.g. `[c1]`. `ChatResponse` validates that every one
#: of them resolves; `AnswerBody.tsx` turns them into links to the matching citation card.
MARKER_RE = re.compile(r"\[(c\d+)\]")


# ---- 1. answer provenance ----


class Citation(BaseModel):
    """One source behind part of an answer."""

    id: str
    """The token the answer text carries as `[c1]`."""

    source_type: SourceType
    title: str

    url: str | None = None
    """Canonical public URL, when the source has one. `None` for a corpus document that only
    exists locally — `CitationCard` must render both branches."""

    doc_id: str | None = None
    """Corpus document id, for the reference lane — what `GET /api/corpus/{doc_id}` resolves."""

    chunk_id: str | None = None
    """The retrieval unit, `source:doc_id#000` (see `chunking/models.py`). Free to carry, and it
    is the key you actually want when a retrieval looks wrong. Character offsets are deliberately
    *not* here: Phase 4's per-claim highlighting wants them, and adding them then is additive."""

    snippet: str
    """The retrieved text actually used, verbatim."""

    score: float | None = None
    """Retrieval score, for debugging. `None` when the backend does not produce one."""


class AnswerClaim(BaseModel):
    """A span of the answer plus what backs it.

    Phase 4 populates this properly; Phase 1a may emit a single claim covering the whole answer.
    `text` is a **verbatim substring** of `ChatResponse.answer` — Phase 4's hover-to-highlight has
    to locate it, and a paraphrase turns an index lookup into a fuzzy-match problem.
    """

    text: str
    citation_ids: list[str]


class TraceStep(BaseModel):
    """One step of the agent loop, as the trace panel shows it.

    Note that `kind` is the *shape* of the step and `tool` is which tool ran: a retrieval is
    `kind="tool_call", tool="retrieve"`, not `kind="retrieve"`. The UI labels a step
    `tool ?? kind`.
    """

    index: int
    kind: Literal["plan", "tool_call", "tool_result", "synthesis"]
    tool: str | None = None
    input: dict[str, Any] | None = None

    summary: str
    """Human-readable one-liner — what the trace panel shows without expanding."""

    duration_ms: int | None = None
    tokens: int | None = None


class Usage(BaseModel):
    """Cost and latency for one message.

    Every field is optional because there is not always a model behind an answer: the Phase 0 stub
    has none, and Phase 1a's lexical retrieval has no token cost. Phase 4 surfaces these per run.
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: int | None = None
    model: str | None = None


# ---- 2. chat ----


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)

    conversation_id: str | None = None
    """Omit to start a new conversation. Carried from day one but unused before multi-turn
    exists, which is cheaper than a contract change later (docs/frontend_plan.md §10.1)."""

    plan_year: int | None = Field(default=None, ge=2014, le=2100)
    """Pin the plan year — docs/plan.md's cross-cutting principle, because CMS keeps multiple
    years live at once and mixing them is this domain's most common correctness bug. 2014 is the
    first Marketplace plan year, so anything below it is a typo rather than a query."""


class ChatResponse(BaseModel):
    conversation_id: str
    message_id: str

    abstained: bool
    """First-class, never inferred from the answer text. The grounding guardrail makes the agent
    say *"not in my reference material"* rather than hallucinate, and the UI must render that as a
    visually distinct state. If the frontend had to pattern-match the prose to detect it, the
    guardrail would be one prompt tweak away from breaking silently."""

    answer: str
    """Markdown with `[c1]`-style inline markers. Rendered with raw HTML disabled."""

    claims: list[AnswerClaim]
    citations: list[Citation]
    trace: list[TraceStep]
    usage: Usage | None = None

    @model_validator(mode="after")
    def _check_provenance(self) -> Self:
        """Refuse to serve an answer whose provenance does not resolve.

        A dangling `[c3]` renders as a broken citation link. In a health-coverage tool a confident
        answer pointing at nothing is worse than an error, so this raises rather than degrades.
        Note what is deliberately *not* enforced: `abstained=True` does not have to imply an empty
        citation list — Phase 2's web lane could legitimately abstain while pointing somewhere.
        The stub's empty-citation abstention is asserted by a test, not frozen by the contract.
        """
        ids = [c.id for c in self.citations]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{self.message_id}: duplicate citation ids in {ids}")
        known = set(ids)
        for claim in self.claims:
            dangling = [cid for cid in claim.citation_ids if cid not in known]
            if dangling:
                raise ValueError(f"{self.message_id}: claim cites unknown citation ids {dangling}")
        orphans = sorted(set(MARKER_RE.findall(self.answer)) - known)
        if orphans:
            raise ValueError(f"{self.message_id}: answer has markers with no citation: {orphans}")
        return self


# ---- 3. SSE events (docs/frontend_plan.md §4.5) ----
#
# One stream feeds both the answer pane and the trace panel, so the events are typed and
# discriminated. Two refinements to §4.5's sketch, both forced by making the union real:
#
#   1. The discriminant lives **inside** the JSON payload as `type`, not only on the SSE `event:`
#      line. Without it `openapi-typescript` cannot emit a narrowable union, so `stream.ts` would
#      need a hand-written string->type map — exactly the duplicated contract CLAUDE.md forbids.
#      It also makes `StepEvent` and `DoneEvent` structurally distinguishable, which bare payloads
#      are not. The `event:` line is kept for readability under `curl` and is *derived* from the
#      payload, so the two cannot disagree.
#   2. The field is `type`, not `event`, to avoid colliding with the SSE keyword and with
#      `StreamEventEnvelope.event` below.


class StartEvent(BaseModel):
    type: Literal["start"] = "start"
    conversation_id: str
    message_id: str


class StepEvent(BaseModel):
    type: Literal["step"] = "step"
    step: TraceStep


class TokenEvent(BaseModel):
    type: Literal["token"] = "token"
    delta: str

    reset: bool = False
    """Discard everything streamed so far and start again from `delta`.

    **Set when the agent abandons a draft mid-stream**, which happens when the grounding guardrail
    rejects an answer and the retry writes different text. Without it a client that appends deltas
    shows the rejected draft *followed by* the real answer — and in a health tool the rejected draft
    is, by construction, the ungrounded one. `done` has always corrected this after the fact; this
    corrects it while the reader is watching.

    Additive and defaulted, so a client that ignores it behaves exactly as before."""


class CitationEvent(BaseModel):
    type: Literal["citation"] = "citation"
    citation: Citation


class DoneEvent(BaseModel):
    type: Literal["done"] = "done"

    response: ChatResponse
    """Authoritative. The client replaces its incrementally-built state with this, so a dropped or
    malformed `token` event cannot leave the UI showing something subtly wrong."""


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    code: Literal["internal", "timeout", "cancelled", "bad_request"] = "internal"
    message: str


StreamEvent = Annotated[
    StartEvent | StepEvent | TokenEvent | CitationEvent | DoneEvent | ErrorEvent,
    Field(discriminator="type"),
]


class StreamEventEnvelope(BaseModel):
    """Schema-only carrier so the SSE union reaches `components.schemas`.

    This is docs/frontend_plan.md §4.5's second gotcha made concrete: FastAPI only ever sees a
    `StreamingResponse`, so the union has to be attached to a route's `responses=` metadata — and
    that takes a *model*, not a bare annotated union. Without this wrapper the union never lands in
    `openapi.json` and the generated TypeScript silently lacks it.

    Never sent on the wire. `frontend/src/api/client.ts` unwraps it as
    `components["schemas"]["StreamEventEnvelope"]["event"]`.
    """

    event: StreamEvent


def sse_frame(
    event: StartEvent | StepEvent | TokenEvent | CitationEvent | DoneEvent | ErrorEvent,
) -> str:
    """Serialize one event as an SSE frame.

    The `event:` line is derived from the payload's own discriminant, which is what keeps the
    frame header and the JSON from ever disagreeing.
    """
    return f"event: {event.type}\ndata: {event.model_dump_json()}\n\n"


# ---- 4. health and corpus ----


class LaneStatus(BaseModel):
    """Whether one routing lane is wired up, and why not when it isn't."""

    source_type: SourceType
    configured: bool
    detail: str


class CorpusStatus(BaseModel):
    source: CorpusName
    documents: int
    chunks: int

    chunks_built: bool
    """`chunks.jsonl` is git-ignored, so a fresh clone has none until `make chunk` runs. The counts
    above come from the committed `chunks_meta.json` either way."""


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str

    stub: bool
    """True while every answer is canned. The UI shows a persistent banner on it — same reasoning
    as `abstained` being a boolean: fake output must never be mistakable for real output."""

    lanes: list[LaneStatus]
    corpora: list[CorpusStatus]


class CorpusDocument(BaseModel):
    """One corpus record, for citation drill-down.

    `meta` carries the per-source fields (`page`, `section_number`, `effective_date`, `pub_id`, …)
    as a passthrough dict. That is the opposite of the flat-optional-columns choice in
    `chunking/models.py`, and deliberately so: that choice was about Arrow/LanceDB filterability
    (docs/lancedb.md), which has no analogue in a JSON response. Flattening here would restate the
    per-source vocabulary in a third place.
    """

    id: str
    source: CorpusName
    title: str
    url: str
    bite: str
    text: str
    meta: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    detail: str


# ---- 5. eval runs ----
#
# The *run* models live here rather than in `routes/evals.py` because, unlike the gold-set wire
# models, they do not reference `GoldQuestion` — so they close no import cycle — and
# `evals/runner.py` needs them to write a run file.


class EvalQuestionResult(BaseModel):
    question_id: str
    passed: bool
    expected_source_type: SourceType | None = None
    expected_abstain: bool = False
    abstained: bool = False

    rank: int | None = None
    """1-based rank of the first expected doc among the returned citations. `None` renders as
    §5.2's "not retrieved"."""

    retrieved_doc_ids: list[str] = Field(default_factory=list)

    tools_used: list[str] = Field(default_factory=list)
    """Which tools the agent called, in order, with repeats collapsed to first appearance.

    Added at Phase 2 to close a gap docs/progress.md had been recording since 1b: **no question
    about tool *choice* could be answered from a run file at all.** "How often does it reach for
    `vector_search`?" and "how often does it widen with `get_chunk`?" were both answered from a
    handful of hand-captured traces rather than from the 175 agent questions on disk. Routing is
    this phase's headline metric, so the shape was close to a prerequisite rather than a nicety.

    Deliberately *not* what `routing_correct` is scored on — that reads the citations, because what
    the reader is shown is what the answer claims to rest on, and a lookup the agent ran and then
    ignored is not evidence. This field is what makes the *difference* between the two visible: a
    question with `web_search` here and no web citation is an agent that looked and then answered
    from somewhere else, which is a distinct and interesting failure."""

    metrics: dict[str, float] = Field(default_factory=dict)
    error: str | None = None


class EvalRunSummary(BaseModel):
    id: str
    created_at: datetime

    runner: str
    """Which answerer produced this run — `"stub"` at Phase 0, then `"bm25"` or `"vector"`
    (retrieval only, no model) or `"agent"`. Surfaced as a badge in the dashboard, because a metric
    measured against canned answers, or against a retriever with no model behind it, must never
    read as a real score."""

    toolset: str | None = None
    """Which retrieval tools the agent could see — `"lexical"`, `"vector"` or `"both"`. `None` for
    every runner with no agent in it, where the question does not arise. Recorded because Phase 1b's
    whole comparison is between runs that differ *only* in this, so a run that does not name it
    cannot take part in that comparison."""

    structured: bool | None = None
    """Whether the agent could see the relational lane over the vendored plan data (Phase 1-c).
    `None` for every runner with no agent in it. Recorded for the same reason as `toolset`: a
    comparison between two runs that differ only in this is only possible if a run says which it
    was."""

    web: bool | None = None
    """Whether the agent could see the web lane over Tavily (Phase 2). `None` for every runner with
    no agent in it. Recorded for the same reason as `toolset` and `structured`: a comparison between
    two runs that differ only in this is only possible if a run says which it was."""

    live: bool | None = None
    """Whether the agent could see the live-API half of the structured lane — openFDA, NPPES and
    (with a credential) the CMS Marketplace (Phase 3). `None` for every runner with no agent in it.

    Added 2026-08-27, later than the lane itself, and the gap is the reason the field is worth
    having: `run_2026-08-27_1` ran seven live questions and its header said
    `lanes reference + structured + web`, because the flag was computed and then dropped at both the
    print and the record. A run that cannot say which lanes were registered cannot be compared with
    one that had a different set, which is the entire job of the four fields above it."""

    config_fingerprint: str | None = None
    """`Config.fingerprint()` — a sha256 over every value in `config.yaml`. The answer to "what
    was this score measured under" for everything the repo *can* pin."""

    model: str | None = None
    """The model actually behind the answers, `None` when none was. Recorded separately from
    `config_fingerprint` because `config.yaml`'s model is a **floating alias** — OpenAI publishes
    no dated snapshot for that family — so the fingerprint proves which alias was configured but
    not what it resolved to on the day."""

    chunker_snapshot_id: dict[str, str] = Field(default_factory=dict)
    """Per-source `chunks_meta.json` snapshot ids, pinning a score to the chunk parameters it was
    measured under."""

    vectors_snapshot_id: str | None = None
    """`vectors_meta.json`'s snapshot id, pinning a score to the embeddings behind it — the chunk
    snapshots, the embedding model, its dimensionality, and the distance metric. `None` when no
    vector retrieval was involved.

    A separate field rather than an entry in `chunker_snapshot_id`, which is keyed by corpus name
    and would have to grow a key that is not a corpus."""

    n_questions: int
    n_passed: int
    duration_ms: int | None = None

    metrics: dict[str, float] = Field(default_factory=dict)
    """Free-form on purpose. docs/frontend_plan.md §5.2: "the table renders whatever metrics a run
    reports rather than hardcoding a fixed set" — recall@k and MRR at Phase 1a, groundedness at
    Phase 1, routing accuracy at 2/3. A typed struct would need editing every phase."""


class EvalRun(EvalRunSummary):
    results: list[EvalQuestionResult]


class EvalRunProgress(BaseModel):
    """One tick of a running eval, streamed to the dashboard while it works."""

    type: Literal["progress"] = "progress"
    run_id: str
    completed: int
    total: int
    result: EvalQuestionResult


class EvalRunStarted(BaseModel):
    type: Literal["started"] = "started"
    run_id: str
    total: int


class EvalRunFinished(BaseModel):
    type: Literal["finished"] = "finished"
    run: EvalRun


class EvalRunFailed(BaseModel):
    type: Literal["failed"] = "failed"
    run_id: str
    message: str


EvalRunEvent = Annotated[
    EvalRunStarted | EvalRunProgress | EvalRunFinished | EvalRunFailed,
    Field(discriminator="type"),
]


class EvalRunEventEnvelope(BaseModel):
    """Same schema-only trick as `StreamEventEnvelope`, for the eval-progress stream."""

    event: EvalRunEvent
