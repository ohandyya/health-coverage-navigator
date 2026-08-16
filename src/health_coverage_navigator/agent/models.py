"""The models the *language model* sees — tool results going in, `AgentAnswer` coming out.

Deliberately separate from `api/models.py`, which is the frozen HTTP contract. Two different
audiences with two different jobs:

- `api/models.py` is a wire format a TypeScript client is generated from. It must not churn.
- This module is a **prompt surface**. Every field name and docstring here is text the model
  reads, so it is tuned for the model's benefit and is free to change when a prompt change makes
  it better.

Collapsing the two would tie a prompt experiment to a frontend recompile, and would put the
model's raw output — a `chunk_id` it may have invented — directly into the response object the
browser trusts. `runtime.py` maps between them, and that mapping is where the invention is caught.

`ChunkHit.text` is the chunk **verbatim**, never the `retrieval_text` the index was built from:
the context header is lexical signal for BM25 (docs/chunking.md §6), but showing it to the model
as if it were part of the source document would invite it into a quotation.
"""

import re

from pydantic import BaseModel, Field

from health_coverage_navigator.chunking.models import Chunk
from health_coverage_navigator.corpus import CorpusName

#: The inline provenance markers the answer must carry. Same shape as `api/models.MARKER_RE`,
#: restated rather than imported: this one governs what the model is asked to produce and the
#: other governs what the browser is served. They agree today; if a prompt experiment ever moves
#: this one, the contract must not move with it.
MARKER_RE = re.compile(r"\[(c\d+)\]")

#: What a citation id must look like. Enforced here so a model answering with `[1]` or `[source1]`
#: is corrected by a retry rather than producing a response the contract validator rejects with a
#: 500 (`api/models.ChatResponse._check_provenance`).
CITATION_ID_RE = re.compile(r"^c\d+$")


class ChunkHit(BaseModel):
    """One retrieval unit, as the agent sees it."""

    chunk_id: str
    """Cite this exact string. It is the only handle that resolves back to a source."""

    source: CorpusName
    label: str
    """Human-readable provenance, e.g. `Medicare & You 2026, p. 41`."""

    context: str
    """Where this passage sits in its document — title, section, page."""

    text: str
    """The passage itself, verbatim. Quote from this and nowhere else."""

    score: float | None = None
    """How well this passage matched, when the tool that produced it ranks. **Comparable only
    within one result list.** `search_corpus` returns a BM25 score and `vector_search` returns a
    similarity; the two are different scales, so a 0.8 from one says nothing about a 12.4 from the
    other, and neither has an absolute meaning — a low score is not evidence of a bad match. Use it
    to order results from a single call, never to choose between two calls."""

    @classmethod
    def of(cls, chunk: Chunk, score: float | None = None) -> "ChunkHit":
        return cls(
            chunk_id=chunk.id,
            source=chunk.source,
            label=chunk.citation_label,
            context=chunk.context_header,
            text=chunk.text,
            score=score,
        )


class DocumentSummary(BaseModel):
    """One document in the corpus, without its text."""

    doc_id: str
    source: CorpusName
    title: str
    chunks: int


class CorpusOverview(BaseModel):
    """What `list_documents` reports: the shape of the corpus, then a sample of it.

    Counts first and documents second because the counts are the part that answers *"what can you
    tell me about?"* honestly. A sample of 20 titles out of 2,056 would otherwise read as the whole
    corpus.
    """

    total_documents: int
    total_chunks: int
    documents_by_source: dict[str, int]
    documents: list[DocumentSummary]
    truncated: bool
    """True when `documents` is a sample rather than the full listing."""


class AgentCitation(BaseModel):
    """One source the answer leans on."""

    id: str = Field(pattern=CITATION_ID_RE.pattern)
    """`c1`, `c2`, ... The answer text refers to this as `[c1]`."""

    chunk_id: str
    """The `chunk_id` of a hit a tool returned **in this conversation**. Anything else is rejected
    and you will be asked to try again."""

    snippet: str = Field(min_length=1)
    """The words from that chunk's `text` that support the claim, copied exactly. Not a paraphrase
    and not a summary — this is shown to the reader as what the source says."""


class AgentAnswer(BaseModel):
    """The agent's complete response.

    `claims` are deliberately absent: the contract requires `AnswerClaim.text` to be a verbatim
    substring of the answer, and a model reproducing its own prose character-for-character is a
    coin flip that fails the contract validator when it loses. `runtime.py` derives them from the
    answer instead, which is exact by construction. docs/frontend_plan.md §4.2 permits a coarse
    claim at Phase 1a.
    """

    abstained: bool
    """True when the reference corpus does not answer the question. Say so rather than guessing —
    this is a health-coverage tool and a confident wrong answer is the worst outcome available."""

    answer: str = Field(min_length=1)
    """Markdown. Every factual sentence ends with the marker of the citation backing it, e.g.
    `... before your plan starts to pay. [c1]`. When abstaining, explain what the corpus covers and
    why this question falls outside it."""

    citations: list[AgentCitation] = Field(default_factory=list)
    """Empty only when abstaining."""
