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

from pydantic import BaseModel, Field, model_validator

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

#: The three citation shapes: which field identifies each, which companion field it requires, how to
#: name it to the model, and how to spell the pair. Module-level rather than a class attribute
#: because a leading-underscore name on a `BaseModel` is a pydantic private attribute, which this
#: is not — it is a constant the validator reads.
#:
#: Ordered as the lanes were built: passage (1a), row (1-c), web page (2).
CITATION_SHAPES = (
    ("chunk_id", "snippet", "a retrieved passage", "chunk_id + snippet"),
    ("row_id", "cells", "a queried row", "row_id + cells"),
    ("result_id", "snippet", "a web result", "result_id + snippet"),
)


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
    """One source the answer leans on: a retrieved passage, a queried row, or a web result.

    **One class with three shapes, not a union.** The lanes' evidence is genuinely different — a
    passage is quoted, a row is read, a web page is quoted from an extract — but they share one id
    space (`c1`, `c2`, ... across all three), and the model has to be able to mix them in a single
    list. A discriminated union would express that more precisely at the cost of an `anyOf` in the
    output schema, which is exactly the kind of structure a provider's strict-JSON mode handles
    unevenly. `_check_shape` recovers the precision, and its error message is what the model reads
    on a retry.
    """

    id: str = Field(pattern=CITATION_ID_RE.pattern)
    """`c1`, `c2`, ... The answer text refers to this as `[c1]`."""

    chunk_id: str | None = None
    """For a **passage**: the `chunk_id` of a hit a tool returned **in this conversation**.
    Anything else is rejected and you will be asked to try again."""

    row_id: str | None = None
    """For a **row**: the `row_id` of a row a query returned **in this conversation**."""

    result_id: str | None = None
    """For a **web page**: the `result_id` of a result `web_search` returned **in this
    conversation**. Cite this, never the URL — a URL you did not retrieve is not a source."""

    snippet: str | None = None
    """For a passage or a web page: the words from that source's text that support the claim,
    copied exactly. Not a paraphrase and not a summary — this is shown to the reader as what the
    source says."""

    cells: dict[str, str] | None = None
    """For a row: the columns you relied on, and their values copied **exactly** as the query
    returned them — including a trailing space, a comma, or a `$`. The stored value is the
    evidence; your prose may tidy it, the citation may not."""

    @model_validator(mode="after")
    def _check_shape(self) -> "AgentCitation":
        """Exactly one of the three shapes, complete.

        **Discriminated on the id field alone**, which is load-bearing rather than tidy. The
        original two-shape version detected a passage as `chunk_id is not None or snippet is not
        None` — and a web citation also carries a `snippet`, so every one of them would have been
        rejected as a malformed passage. Keying on the ids keeps each shape's test independent of
        which companion fields it happens to share with another.

        A half-filled citation is the realistic model error here — a `result_id` with no quotation,
        a `row_id` with a `snippet` — and catching it as a validation error means the model is told
        which half is missing rather than having the answer rejected a layer later by the grounding
        validator with a less specific complaint.
        """
        present = [
            (id_field, companion, label)
            for id_field, companion, label, _ in CITATION_SHAPES
            if getattr(self, id_field) is not None
        ]
        forms = ", ".join(form for *_, form in CITATION_SHAPES)

        if len(present) > 1:
            named = " and ".join(label for *_, label in present)
            raise ValueError(
                f"citation {self.id} mixes {named}. One citation points at one thing — use a "
                f"separate citation for each."
            )
        if not present:
            raise ValueError(f"citation {self.id} points at nothing. Give one of: {forms}.")

        id_field, companion, label = present[0]
        if not getattr(self, companion):
            raise ValueError(
                f"citation {self.id} of {label} needs {companion} as well as {id_field}, copied "
                f"exactly from what the tool returned."
            )
        return self

    @property
    def is_row(self) -> bool:
        return self.row_id is not None

    @property
    def is_web(self) -> bool:
        return self.result_id is not None


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
