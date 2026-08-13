"""Request-scoped application state, and the dependency that hands it to a route.

A separate leaf module rather than something in `app.py`, because `app.py` imports the routers and
the routers need the context — reading `request.app.state` from inside a route while importing the
accessor from `app.py` would be a cycle. Keeping `AppContext` and `get_context` here also means a
route parameter has a real type instead of whatever `app.state` widens to.
"""

from dataclasses import dataclass
from datetime import datetime

from fastapi import Request

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


def get_context(request: Request) -> AppContext:
    ctx = getattr(request.app.state, "ctx", None)
    if ctx is None:  # pragma: no cover - only reachable if the lifespan did not run
        raise RuntimeError("application context is missing; the lifespan did not run")
    return ctx
