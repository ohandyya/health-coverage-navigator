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


def get_context(request: Request) -> AppContext:
    ctx = getattr(request.app.state, "ctx", None)
    if ctx is None:  # pragma: no cover - only reachable if the lifespan did not run
        raise RuntimeError("application context is missing; the lifespan did not run")
    return ctx
