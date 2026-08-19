"""`AnswerDeps` — everything one agent run needs, and everything it records.

A leaf module on purpose. Both tool modules (`tools.py` for the reference lane,
`structured_tools.py` for the relational one) need this type, and `tools.py` needs the structured
tools to build `select_tools` — so leaving `AnswerDeps` in `tools.py` closes an import cycle. Same
shape as `api/deps.py` existing so `app.py` and the routers do not import each other
(docs/relational-tool.md §9).
"""

from dataclasses import dataclass, field

from health_coverage_navigator.agent.index import CorpusIndex
from health_coverage_navigator.agent.models import ChunkHit
from health_coverage_navigator.api.models import TraceStep
from health_coverage_navigator.chunking.models import Chunk
from health_coverage_navigator.config import RetrievalConfig, get_config
from health_coverage_navigator.structured.models import Row
from health_coverage_navigator.structured.store import StructuredStore
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

    structured: StructuredStore | None = None
    """The relational lane's DuckDB handle, or `None` when this run has no structured tools.
    `None` is a real, expected state for the same reason `vectors` is: the Parquet mirrors are
    git-ignored, so a fresh clone has none — and `select_tools` is what guarantees the tools are
    not registered when the handle would be missing."""

    plan_year: int | None = None
    """The year the request pinned. Read by the structured tools to choose a partition, and the
    reason a question about an unvendored year abstains instead of answering from another year."""

    seen_rows: dict[str, Row] = field(default_factory=dict)
    """Every row any structured tool returned this run, keyed by row id. The citable set for the
    relational lane — `seen_chunks`'s counterpart, and what the grounding validator checks a row
    citation against."""

    queries: int = 0
    """How many structured queries this run has made. Numbers the row ids (`#q2.1`), so a citation
    says which call produced the row."""

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

    def remember_rows(self, rows: list[Row]) -> None:
        """Mark queried rows as citable. The relational lane's `remember`.

        Rows are recorded whole, not by identifier: the validator has to check the *values* a
        citation quotes, and a row id alone would let a model cite a real row with invented cells.
        """
        for row in rows:
            self.seen_rows[row.row_id] = row

    def next_query(self) -> int:
        """The sequence number for the next structured query."""
        self.queries += 1
        return self.queries
