"""Request-scoped application state, and the dependency that hands it to a route.

A separate leaf module rather than something in `app.py`, because `app.py` imports the routers and
the routers need the context — reading `request.app.state` from inside a route while importing the
accessor from `app.py` would be a cycle. Keeping `AppContext` and `get_context` here also means a
route parameter has a real type instead of whatever `app.state` widens to.
"""

from dataclasses import dataclass
from datetime import datetime

from fastapi import Request

from health_coverage_navigator.agent.index import CorpusIndex
from health_coverage_navigator.evals.models import GoldSet
from health_coverage_navigator.structured.store import StructuredStore
from health_coverage_navigator.vectors.store import VectorIndex
from health_coverage_navigator.web.client import WebSearchClient


@dataclass(slots=True, frozen=True)
class AppContext:
    """Everything loaded once at startup.

    What is here and what is not is a deliberate cost/benefit call, argued in `app.py`'s lifespan.
    """

    gold: GoldSet
    docs: dict[str, dict]
    started_at: datetime
    stub: bool
    version: str

    index: CorpusIndex | None = None
    """The chunk + BM25 index the agent searches, or `None` when `chunks.jsonl` has never been
    built here. `None` is a real, expected state — the chunks are git-ignored, so a fresh clone has
    none — and it is not a failure to boot: `make types` imports this app, and `/api/health` and
    the eval dashboard must still work. It *is* a failure to answer, and `routes/chat.py` says so
    with a 503 that names `make chunk`."""

    vectors: VectorIndex | None = None
    """The embedding store `vector_search` queries, or `None` when it has never been built here.
    Same reasoning as `index` above, with one addition: building this one costs a paid embedding
    call, so `None` is a *more* ordinary state than a missing chunk file. Whether it is a failure
    to answer depends on `agent.toolset` — a `lexical` configuration does not need it, and
    `routes/chat.py` is where that question is asked."""

    structured: StructuredStore | None = None
    """The relational lane's DuckDB handle over the vendored plan mirrors, or `None` when they have
    never been downloaded here. Same shape as `vectors`, including the part that matters: whether
    `None` is fatal depends on `agent.structured_tools`, and only `routes/chat.py` decides."""

    web: WebSearchClient | None = None
    """The web lane's Tavily client, or `None` when this machine has no `TAVILY_API_KEY`. Same shape
    as the two above, with one difference worth noting: the others are `None` because a *build step*
    has not run here, this one because a *credential* is absent — so no `make` target fixes it and
    the 503 names `.env` instead. Whether `None` is fatal depends on `agent.web_tools`."""


def get_context(request: Request) -> AppContext:
    ctx = getattr(request.app.state, "ctx", None)
    if ctx is None:  # pragma: no cover - only reachable if the lifespan did not run
        raise RuntimeError("application context is missing; the lifespan did not run")
    return ctx
