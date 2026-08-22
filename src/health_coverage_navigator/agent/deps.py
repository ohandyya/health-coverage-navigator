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
from health_coverage_navigator.live.marketplace import MarketplaceClient
from health_coverage_navigator.live.nppes import NppesClient
from health_coverage_navigator.live.openfda import OpenFdaClient
from health_coverage_navigator.structured.models import Row
from health_coverage_navigator.structured.store import StructuredStore
from health_coverage_navigator.vectors.store import VectorIndex
from health_coverage_navigator.web.client import WebSearchClient
from health_coverage_navigator.web.models import WebResult


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

    web: WebSearchClient | None = None
    """The web lane's Tavily client, or `None` when this run has no web tool. `None` is a real,
    expected state for the same reason `vectors` and `structured` are — no key on this machine, or
    a run that deliberately excluded the lane — and `select_tools` is what guarantees `web_search`
    is not registered when the client would be missing."""

    seen_results: dict[str, WebResult] = field(default_factory=dict)
    """Every web result any tool returned this run, keyed by result id. The citable set for the web
    lane — `seen_chunks`'s and `seen_rows`'s counterpart.

    This is the reason a web citation can be trusted at all. A URL is guessable and a model can
    write a plausible one it never retrieved; a `result_id` exists only because a search assigned
    it, so the validator checking membership here is checking something the model cannot fake."""

    web_searches: int = 0
    """How many web searches this run has made. Numbers the result ids (`web#s2.1`) *and* enforces
    `web.max_searches_per_run` — the web is the first lane where an unchecked loop spends money."""

    openfda: OpenFdaClient | None = None
    """The live lane's openFDA client, or `None` when this run has no live tools.

    `None` is a real, expected state for the same reason `vectors`, `structured` and `web` are — a
    run that deliberately excluded the lane — but it arrives for a *different* reason than the
    others. There is no credential that can be missing here (docs/structured-api-tools.md §4), so
    `None` only ever means "this run did not want the lane", never "this machine cannot have it"."""

    marketplace: MarketplaceClient | None = None
    """The live lane's CMS Marketplace client, or `None` when this run has no key or no live tools.

    **Independently `None` from `openfda`, deliberately.** This is the only Phase 3 source with a
    credential, so a deployment can perfectly reasonably have the FDA tools and not these — and the
    agent is told which questions it therefore cannot answer, rather than the whole lane being
    withheld because one third of it is unconfigured."""

    nppes: NppesClient | None = None
    """The live lane's NPI registry client, or `None` when this run has no live tools. Keyless like
    `openfda`, so `None` here only ever means the configuration excluded the lane."""

    market_year: int | None = None
    """CMS's current plan year, resolved at most once per run and cached here.

    `/market-years` is asked rather than hardcoded (§7c), but asking it per tool call would spend
    the lookup budget on a value that cannot change mid-run. Distinct from `plan_year`, which is
    what the *request* pinned: this is what CMS says today, and it is the fallback when the request
    pinned nothing."""

    live_cache: dict[str, object] = field(default_factory=dict)
    """Live-API results already fetched **in this run**, keyed by tool and normalised arguments.

    Run-scoped and in-memory, deliberately not persistent (docs/structured-api-tools.md §13c). The
    value of caching here is almost entirely *within* a run — `find_drug` then
    `check_drug_coverage` then `drug_recalls` on the same drug is the common shape — while a
    cross-run disk cache would introduce staleness into the one lane whose selling point is being
    current. *"Is this covered now"* answered from yesterday is the failure this lane exists to
    avoid.

    **The key must carry every dimension the result depends on**, which this repo has now got wrong
    three times in two lanes: the resolved `year`, not the caller's `None`, and for `find_plans` the
    whole household — two people in one ZIP with different incomes see different plans."""

    live_calls: int = 0
    """How many live-API lookups this run has made, across every live tool. Numbers the record ids
    (`fda#r2.1`) *and* enforces `live.max_calls_per_run`.

    **One counter for all three upstreams, not one each.** The ceiling exists to stop a runaway
    loop, and a loop that alternates between two services is exactly as runaway as one that hammers
    a single service — three separate budgets would each look healthy while the run made three times
    the calls."""

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

    def remember_results(self, results: list[WebResult]) -> list[WebResult]:
        """Mark web results as citable. Returns them unchanged, so it can wrap a return value.

        Results are recorded whole, like rows and unlike chunks: a chunk can be re-read from the
        corpus by id, but a web result exists nowhere after the run except here, so the validator
        has to hold the text it will check a quotation against.
        """
        for result in results:
            self.seen_results[result.result_id] = result
        return results

    def next_web_search(self) -> int:
        """The sequence number for the next web search."""
        self.web_searches += 1
        return self.web_searches

    def cached_live(self, key: str) -> object | None:
        """A live result already fetched this run, or `None`.

        A hit costs no HTTP request and no budget — which is the point: a compound question that
        asks about one drug three ways should pay for it once."""
        return self.live_cache.get(key)

    def remember_live(self, key: str, value: object) -> object:
        """Record a live result under its cache key. Returns it, so it can wrap a return value."""
        self.live_cache[key] = value
        return value

    def next_live_call(self) -> int:
        """The sequence number for the next live-API lookup.

        Shared across the live tools so a record id says which *call* in the run produced it, not
        which call to that particular upstream — the trace reads in one sequence and the ids should
        too.
        """
        self.live_calls += 1
        return self.live_calls
