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
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic_ai import ModelRetry, RunContext

from health_coverage_navigator.agent.index import MAX_HITS, MAX_PATTERN_CHARS, CorpusIndex
from health_coverage_navigator.agent.models import ChunkHit, CorpusOverview
from health_coverage_navigator.api.models import TraceStep
from health_coverage_navigator.chunking.models import Chunk
from health_coverage_navigator.config import RetrievalConfig, Toolset, get_config
from health_coverage_navigator.corpus import CorpusName
from health_coverage_navigator.vectors.store import VectorIndex


@dataclass(slots=True)
class AnswerDeps:
    """Everything one agent run needs, and everything it records.

    Mutable by design and scoped to a single run — never shared between requests. `CorpusIndex` and
    `VectorIndex` are the two things here that *are* shared: both are read-only after construction
    and expensive to build (~200 ms for the BM25 index, a paid embedding run for the store), so
    they are passed in rather than rebuilt.
    """

    index: CorpusIndex

    vectors: VectorIndex | None = None
    """The embedding store, or `None` when this run has no vector tool. `None` is a real, expected
    state — a lexical-only eval run, or an app whose store has never been built — and
    `select_tools` is what guarantees `vector_search` is not registered when it would be."""

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
    """Rank passages by **keyword** relevance to a query.

    Matching is on **words, not meaning**: a passage is found because it contains the query's terms,
    so a question phrased in a patient's words may miss a document written in the regulator's.
    Query with the terms you expect the *source* to use, not with the user's sentence — search
    "deductible" or "out-of-pocket limit", not "what exactly is a deductible?", because a rare
    filler word like "exactly" will outweigh the term you care about.

    Best when you know the vocabulary the documents use: a defined benefit term, a programme name.
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
    — use one of the ranked searches for those.

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


async def vector_search(
    ctx: RunContext[AnswerDeps],
    query: str,
    k: int = 5,
    source: CorpusName | None = None,
) -> list[ChunkHit]:
    """Rank passages by **meaning** rather than wording.

    Finds passages that are about the same thing as your query even when they share none of its
    words — so a question in a patient's language can reach a document written in a regulator's.
    Unlike `search_corpus`, you do **not** need to guess the source's vocabulary: phrase the query
    as the underlying question, in a full sentence if that expresses it best.

    It is correspondingly weaker where `search_corpus` is strong. A passage that is merely on a
    related topic can outrank the one that actually defines the term, and an exact identifier — an
    NCD number, a specific dollar figure — is better found with `grep_corpus`. Read what comes
    back rather than trusting the order.

    Its score is a similarity, on a different scale from `search_corpus`'s, so the two cannot be
    compared across calls. When both tools return the same passage, that agreement is stronger
    evidence than either result alone; when they disagree, read both before choosing.

    Args:
        query: What you want to find, in natural language. A full question works well here.
        k: How many passages to return, at most 10.
        source: Restrict to one corpus, as described by `list_documents`.
    """
    if ctx.deps.vectors is None:  # pragma: no cover - `select_tools` makes this unreachable
        raise ModelRetry(
            "Semantic search is not available in this session. Use search_corpus for keyword "
            "search or grep_corpus for an exact string."
        )

    started = time.perf_counter()
    ranked = await ctx.deps.vectors.search(query, min(max(k, 0), MAX_HITS), source=source)
    # Resolved through the *same* `CorpusIndex` the lexical tools use, which is what makes a vector
    # hit citable: `remember` records chunks by looking them up here, and the grounding validator
    # only accepts what `remember` recorded. An id the index cannot resolve is dropped rather than
    # surfaced — it would be uncitable anyway — and `VectorIndex.open` is what stops that from
    # happening quietly at scale, by refusing a store built against different chunks.
    hits = [
        ChunkHit.of(chunk, score)
        for chunk_id, score in ranked
        if (chunk := ctx.deps.index.chunk(chunk_id)) is not None
    ]
    ctx.deps._record(
        "vector_search",
        {"query": query, "k": k, "source": source},
        _summarize(hits),
        int((time.perf_counter() - started) * 1000),
    )
    return ctx.deps.remember(hits)


#: Tools every configuration gets. These are how a hit is *widened or oriented*, not how one is
#: found, so removing them from a vector-only run would measure "lost the ability to widen a hit"
#: alongside "vector vs lexical" — two effects on one number.
NAVIGATION_TOOLS = (get_chunk, list_documents)

#: Phase 1a's ranked-and-exact retrieval over words.
LEXICAL_TOOLS = (search_corpus, grep_corpus)

#: Phase 1b's ranked retrieval over meaning.
VECTOR_TOOLS = (vector_search,)


def select_tools(toolset: Toolset) -> list[Callable[..., object]]:
    """Which tools the agent may see, for one run.

    Phase 1b's eval axis (docs/plan.md §1b): lexical-only / vector-only / both is *one runner with
    a flag*, not three code paths, and this function is the flag. `runtime._build_agent` caches on
    the result, so the return has to depend on nothing but the argument.

    Order is the order the model sees the tools in, and it is preserved deliberately: the
    general-purpose ranker first, the recovery moves last. Under `both`, lexical leads because it
    is the cheaper call and the one that wins on exact vocabulary; the prompt says when to reach
    past it.
    """
    ranked = {
        "lexical": LEXICAL_TOOLS,
        "vector": VECTOR_TOOLS,
        "both": LEXICAL_TOOLS + VECTOR_TOOLS,
    }[toolset]
    return [*ranked, *NAVIGATION_TOOLS]


def needs_vectors(toolset: Toolset) -> bool:
    """Whether this configuration cannot run without the vector store."""
    return toolset in ("vector", "both")


__all__ = [
    "LEXICAL_TOOLS",
    "MAX_HITS",
    "MAX_PATTERN_CHARS",
    "NAVIGATION_TOOLS",
    "VECTOR_TOOLS",
    "AnswerDeps",
    "get_chunk",
    "grep_corpus",
    "list_documents",
    "needs_vectors",
    "search_corpus",
    "select_tools",
    "vector_search",
]
