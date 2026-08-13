"""The FastAPI application: lifespan, router wiring, and serving the built frontend.

Two servers in development, one process otherwise (docs/frontend_plan.md §2). In development Vite
serves the UI on :5173 and proxies `/api` here, so the browser only ever talks to one origin —
which is why **there is no CORS configuration anywhere in this repo**, and why adding a permissive
one "just in case" would reopen exactly what that topology closes. In production `make serve`
builds the SPA and this module mounts it, so there is one process, one port, one command.

Both modes bind to `127.0.0.1`. There is no auth, so `0.0.0.0` would expose the app to the whole
network (§8); the Makefile targets pass the host explicitly and nothing here ever binds.
"""

import importlib.metadata
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import PlainTextResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from health_coverage_navigator.api.deps import AppContext
from health_coverage_navigator.api.routes import chat, corpus, evals, health
from health_coverage_navigator.corpus import load_doc_index
from health_coverage_navigator.evals.loader import load_gold_set
from health_coverage_navigator.paths import FRONTEND_DIST

DESCRIPTION = """
Answers U.S. health-coverage questions by routing each sub-question to the right source type:
indexed reference material, a structured public API, or the open web.

**Phase 0 — every answer on this server is canned.** `GET /api/health` reports `stub: true` while
that is the case.
"""


def _version() -> str:
    try:
        return importlib.metadata.version("health-coverage-navigator")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover - editable install always
        return "0.0.0+unknown"


class SpaStaticFiles(StaticFiles):
    """StaticFiles that falls back to `index.html`, because React Router owns unknown paths.

    `html=True` only serves `index.html` for *directories*, so a hard refresh on `/evals` is a 404
    without this. The `assets/` carve-out matters: without it a missing hashed bundle returns
    `index.html` with a 200 and `Content-Type: text/html`, and the browser reports a module parse
    error instead of a missing file. A missing asset is a build bug and should read as one.
    """

    #: Everything under this prefix is a build artifact, never a client route. Compared as a whole
    #: path segment, because Starlette hands a directory request through as `assets` with no
    #: trailing slash — a `startswith("assets/")` test misses that and falls back to index.html.
    BUILD_PREFIX = "assets"

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or path.split("/", 1)[0] == self.BUILD_PREFIX:
                raise
            return await super().get_response("index.html", scope)


def _mount_frontend(app: FastAPI, dist_dir: Path) -> None:
    """Mount the built SPA, or explain its absence.

    Mounted **last**, after every router: Starlette matches in registration order and a mount at
    `/` matches everything left, so mounting earlier would silently shadow `/api`. There is a test
    for that, because "the static mount ate my API" is an order-dependent failure with no symptom
    other than the wrong response body.
    """
    if dist_dir.is_dir() and (dist_dir / "index.html").is_file():
        app.mount("/", SpaStaticFiles(directory=dist_dir, html=True), name="frontend")
        return

    # No build yet. This is the *normal* state during `make dev` (Vite serves the UI) and during
    # `make types` (which imports this app before the frontend has ever been built), so it must not
    # be an error. `StaticFiles(check_dir=False)` was rejected: it defers the failure to request
    # time with a far worse message than this one.
    @app.get("/", include_in_schema=False)
    def frontend_not_built() -> PlainTextResponse:
        return PlainTextResponse(
            "Frontend not built.\n\n"
            "  make dev    - Vite on :5173 with hot reload (what you usually want)\n"
            "  make serve  - build the SPA and serve it from this process on :8000\n",
            status_code=503,
        )


def create_app(*, dist_dir: Path | None = None, stub: bool = True) -> FastAPI:
    """Build the application.

    `dist_dir` is a parameter rather than a constant read because the SPA-fallback tests need to
    point it at a `tmp_path` — a real test requirement, not gratuitous injection.
    """
    dist_dir = FRONTEND_DIST if dist_dir is None else dist_dir

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # The gold set is 35 questions and loads in milliseconds. Eager because a malformed
        # questions.yaml should fail `make api-dev` at boot rather than on the first page load.
        #
        # The document index is all 2,056 documents across the three corpora: ~23 ms and ~12 MB,
        # measured. That number is the whole argument — it is cheaper than writing the lazy path,
        # and `uvicorn --reload` re-pays it invisibly. The rejected alternative was a
        # doc-id -> byte-offset sidecar, which saves the memory and costs a second file format,
        # a rebuild step, and a seek per request.
        #
        # Chunks are deliberately NOT loaded: chunks.jsonl is git-ignored and may not exist on a
        # fresh clone, so the API must boot without it. /api/health reads the committed
        # chunks_meta.json instead.
        app.state.ctx = AppContext(
            gold=load_gold_set(),
            docs=load_doc_index(),
            started_at=datetime.now(UTC),
            stub=stub,
            version=_version(),
        )
        yield
        app.state.ctx = None

    app = FastAPI(
        title="Health Coverage Navigator",
        description=DESCRIPTION,
        version=_version(),
        lifespan=lifespan,
    )

    app.include_router(health.router, prefix="/api", tags=["health"])
    app.include_router(chat.router, prefix="/api", tags=["chat"])
    app.include_router(corpus.router, prefix="/api", tags=["corpus"])
    app.include_router(evals.router, prefix="/api", tags=["evals"])

    _mount_frontend(app, dist_dir)
    return app


app = create_app()
