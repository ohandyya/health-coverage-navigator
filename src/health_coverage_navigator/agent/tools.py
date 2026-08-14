"""The four tools, and the run-scoped state they write into.

`index.py` does the searching. This module does the two things that only make sense *inside an
agent run*:

**The trace.** Every call appends a `tool_call` / `tool_result` pair to `deps.trace` with the real
arguments and a real duration. That is a user-facing feature from the first agent phase, not a
debug view (docs/plan.md, Phase 1a) — with four narrow tools instead of one `retrieve`, a bad query
and the recovery from it are both visible in the trace instead of buried inside a ranking function.

**What the agent has actually seen.** Every chunk any tool returns is recorded in
`deps.seen_chunks`. The output validator then refuses to accept a citation of anything else, which
turns "answer only from what the tools returned" from a sentence in a prompt into a condition the
model cannot talk its way past. It is also what lets `runtime.py` build every `Citation` from the
real chunk — title, url, doc_id, score — so the only thing the model contributes to a citation is
*which* chunk, and that is checked.

Why four tools and not one `retrieve(query)`: docs/plan.md §1a. Briefly — a single call hides the
search strategy inside a ranking function, which is the part of the exercise worth doing, and it
makes Phase 2's lane routing a change of kind rather than of degree. The cost is accepted: more
surface for the agent to get wrong.
"""

import re
import time
from dataclasses import dataclass, field

from pydantic_ai import ModelRetry, RunContext

from health_coverage_navigator.agent.index import MAX_HITS, MAX_PATTERN_CHARS, CorpusIndex
from health_coverage_navigator.agent.models import ChunkHit, CorpusOverview
from health_coverage_navigator.api.models import TraceStep
from health_coverage_navigator.chunking.models import Chunk
from health_coverage_navigator.config import RetrievalConfig, get_config
from health_coverage_navigator.corpus import CorpusName


@dataclass(slots=True)
class AnswerDeps:
    """Everything one agent run needs, and everything it records.

    Mutable by design and scoped to a single run — never shared between requests. `CorpusIndex` is
    the one thing here that *is* shared: it is read-only after construction and costs ~200 ms to
    build, so it is passed in rather than rebuilt.
    """

    index: CorpusIndex

    retrieval: RetrievalConfig = field(default_factory=lambda: get_config().retrieval)
    """BM25 parameters, resolved once per run. Held here rather than read inside `search_corpus`
    so a test can score against explicit values without reaching into the cached global config."""

    trace: list[TraceStep] = field(default_factory=list)
    """Appended to by `_record`, drained by the streaming loop in `runtime.py`."""

    seen_chunks: dict[str, Chunk] = field(default_factory=dict)
    """Every chunk any tool returned this run, keyed by chunk id. The citable set."""

    def _record(
        self,
        tool: str,
        arguments: dict[str, object],
        summary: str,
        duration_ms: int,
    ) -> None:
        """One tool call, as two trace steps.

        Two rather than one because the panel shows them as separate moments: the call is what the
        agent *decided* to do and carries the arguments, the result is what came back. Collapsing
        them would lose the distinction between "asked a bad question" and "asked a good question
        and the corpus is empty" — which is the distinction the trace exists to make visible.
        """
        base = len(self.trace)
        self.trace.append(
            TraceStep(
                index=base,
                kind="tool_call",
                tool=tool,
                input=arguments,
                summary=f"{tool}({', '.join(f'{k}={v!r}' for k, v in arguments.items())})",
            )
        )
        self.trace.append(
            TraceStep(
                index=base + 1,
                kind="tool_result",
                tool=tool,
                summary=summary,
                duration_ms=duration_ms,
            )
        )

    def remember(self, hits: list[ChunkHit]) -> list[ChunkHit]:
        """Mark hits as citable. Returns them unchanged, so it can wrap a return value."""
        for hit in hits:
            chunk = self.index.chunk(hit.chunk_id)
            if chunk is not None:
                self.seen_chunks[chunk.id] = chunk
        return hits


def _summarize(hits: list[ChunkHit]) -> str:
    if not hits:
        return "no matches"
    top = hits[0]
    scored = f", top score {top.score:.2f}" if top.score is not None else ""
    return f"{len(hits)} chunk{'s' if len(hits) != 1 else ''} returned{scored} — {top.label}"


# ---------------------------------------------------------------- the tools ------------------
#
# Every docstring below is read by the model as the tool's description, so they are written for
# that reader: what the tool is *for*, when to reach for it instead of another one, and what a
# bad result means. That is why they are longer than a normal internal docstring and why they
# name their siblings.


def list_documents(
    ctx: RunContext[AnswerDeps],
    source: CorpusName | None = None,
    limit: int = 20,
) -> CorpusOverview:
    """Describe what is in the reference corpus: how many documents, from which sources, and a
    sample of their titles.

    Use this to orient before searching an unfamiliar topic, and to answer questions about your own
    coverage ("what can you tell me about?"). Also worth a call when `search_corpus` comes back
    empty and you need to know whether the topic is genuinely absent or you used the wrong words.

    Args:
        source: Restrict to one corpus. `healthcare_gov` is HealthCare.gov consumer and glossary
            content (ACA marketplace); `medicare_pubs` is the Medicare & You handbook and related
            CMS booklets; `medicare_ncd` is Medicare National Coverage Determinations, the national
            rules on whether a specific item or service is covered.
        limit: How many document titles to sample. The reply says whether it truncated.
    """
    started = time.perf_counter()
    overview = ctx.deps.index.list_documents(source=source, limit=limit)
    ctx.deps._record(
        "list_documents",
        {"source": source, "limit": limit},
        f"{overview.total_documents:,} documents, {overview.total_chunks:,} chunks",
        int((time.perf_counter() - started) * 1000),
    )
    return overview


def search_corpus(
    ctx: RunContext[AnswerDeps],
    query: str,
    k: int = 5,
    source: CorpusName | None = None,
) -> list[ChunkHit]:
    """Rank passages by lexical relevance to a query. This is the general-purpose search — start
    here.

    Matching is on **words, not meaning**: a passage is found because it contains the query's terms,
    so a question phrased in a patient's words may miss a document written in the regulator's.
    Query with the terms you expect the *source* to use, not with the user's sentence — search
    "deductible" or "out-of-pocket limit", not "what exactly is a deductible?", because a rare
    filler word like "exactly" will outweigh the term you care about.

    If the results look off-topic, reformulate and search again rather than settling. If they look
    right but land mid-definition, widen the best one with `get_chunk`. For an exact string — a
    specific NCD number, a statutory phrase — `grep_corpus` is the better tool.

    Args:
        query: Search terms. Keywords work better than a full sentence.
        k: How many passages to return, at most 10.
        source: Restrict to one corpus, as described by `list_documents`.
    """
    started = time.perf_counter()
    retrieval = ctx.deps.retrieval
    hits = ctx.deps.index.search(query, k, k1=retrieval.bm25_k1, b=retrieval.bm25_b, source=source)
    ctx.deps._record(
        "search_corpus",
        {"query": query, "k": k, "source": source},
        _summarize(hits),
        int((time.perf_counter() - started) * 1000),
    )
    return ctx.deps.remember(hits)


def grep_corpus(
    ctx: RunContext[AnswerDeps],
    pattern: str,
    source: CorpusName | None = None,
    ignore_case: bool = True,
    limit: int = 10,
) -> list[ChunkHit]:
    """Find passages containing an exact string or regular expression.

    Use this when you know the precise wording and ranking would only get in the way: an NCD
    section number ("240.4"), a defined term you want every occurrence of, a statutory phrase. It
    returns matches in corpus order, not by relevance, so it is the wrong tool for an open question
    — use `search_corpus` for those.

    Args:
        pattern: A Python regular expression, at most 200 characters. Escape regex metacharacters
            (`.` `(` `[` `*` `+` `?`) when you mean them literally.
        source: Restrict to one corpus.
        ignore_case: Case-insensitive by default.
        limit: How many matches to return, at most 10.
    """
    started = time.perf_counter()
    try:
        hits = ctx.deps.index.grep(pattern, source=source, ignore_case=ignore_case, limit=limit)
    except re.error as exc:
        raise ModelRetry(
            f"{pattern!r} is not a valid regular expression: {exc}. Escape any literal "
            f"metacharacters, or use search_corpus if you meant a plain-language query."
        ) from exc
    except ValueError as exc:
        raise ModelRetry(str(exc)) from exc

    ctx.deps._record(
        "grep_corpus",
        {"pattern": pattern, "source": source, "ignore_case": ignore_case, "limit": limit},
        _summarize(hits),
        int((time.perf_counter() - started) * 1000),
    )
    return ctx.deps.remember(hits)


def get_chunk(
    ctx: RunContext[AnswerDeps],
    chunk_id: str,
    before: int = 1,
    after: int = 1,
) -> list[ChunkHit]:
    """Read the passages surrounding a chunk, in its own document.

    The recovery move when a search hit lands mid-sentence or mid-definition and the part you need
    is just outside it. Neighbouring passages overlap slightly, so some text repeats — that is the
    chunking design, not a duplicate result.

    Args:
        chunk_id: A `chunk_id` from an earlier result. An unknown id returns nothing.
        before: How many preceding passages to include.
        after: How many following passages to include.
    """
    started = time.perf_counter()
    hits = ctx.deps.index.get_chunk(chunk_id, before=before, after=after)
    summary = (
        f"{len(hits)} passage{'s' if len(hits) != 1 else ''} around {chunk_id}"
        if hits
        else f"no chunk with id {chunk_id!r}"
    )
    ctx.deps._record(
        "get_chunk",
        {"chunk_id": chunk_id, "before": before, "after": after},
        summary,
        int((time.perf_counter() - started) * 1000),
    )
    return ctx.deps.remember(hits)


#: Registered on the agent in `runtime.py`. Order is the order the model sees them in, so the
#: general-purpose one comes first and the two recovery moves come last.
TOOLS = (search_corpus, grep_corpus, get_chunk, list_documents)

__all__ = [
    "MAX_HITS",
    "MAX_PATTERN_CHARS",
    "TOOLS",
    "AnswerDeps",
    "get_chunk",
    "grep_corpus",
    "list_documents",
    "search_corpus",
]
