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
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import PlainTextResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from health_coverage_navigator.agent.index import ChunksNotBuiltError, CorpusIndex, get_corpus_index
from health_coverage_navigator.agent.tools import needs_vectors
from health_coverage_navigator.api.deps import AppContext
from health_coverage_navigator.api.routes import chat, corpus, evals, health
from health_coverage_navigator.config import get_config
from health_coverage_navigator.corpus import load_doc_index
from health_coverage_navigator.evals.loader import load_gold_set
from health_coverage_navigator.paths import FRONTEND_DIST
from health_coverage_navigator.structured.catalog import StructuredNotBuiltError
from health_coverage_navigator.structured.store import StructuredStore
from health_coverage_navigator.vectors import embedder as embedder_module
from health_coverage_navigator.vectors.store import VectorIndex, VectorsNotBuiltError
from health_coverage_navigator.web import client as web_client_module
from health_coverage_navigator.web.client import WebSearchClient, WebSearchNotConfiguredError

DESCRIPTION = """
Answers U.S. health-coverage questions by routing each sub-question to the right source type:
indexed reference material, a structured public API, or the open web.

**Phase 1a — one lane is live.** Answers come from a PydanticAI agent searching the indexed
reference corpus with a full-text toolset, and it abstains rather than guess when the corpus does
not cover the question. `GET /api/health` reports which lanes are configured, and `stub: true` on
a server still serving canned answers.
"""

logger = logging.getLogger(__name__)


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


def _load_index() -> CorpusIndex | None:
    """The agent's corpus index, or `None` with an explanation on the console.

    Not fatal, deliberately. `chunks.jsonl` is git-ignored, so a fresh clone has none until
    `make chunk` runs — and `make types` imports this app before anything has been built. A server
    that refuses to boot would take `/api/health`, the eval dashboard, and the codegen down with
    it. Refusing to *answer* is `routes/chat.py`'s job, where the message can name the fix.
    """
    try:
        return get_corpus_index()
    except ChunksNotBuiltError as exc:
        logger.warning("reference lane unavailable: %s", exc)
        return None


async def _load_vectors() -> VectorIndex | None:
    """The agent's embedding store, or `None` with an explanation on the console.

    Two failure modes, treated **differently on purpose**:

    * `VectorsNotBuiltError` — nothing has been embedded here. Ordinary, like a missing
      `chunks.jsonl` but more so, since building costs a paid call. Logged and degraded to `None`;
      `routes/chat.py` decides whether that is fatal for the configured toolset.
    * `VectorsStaleError` — a store exists but was built against different chunks or a different
      model. **Deliberately allowed to propagate and stop the boot.** A missing store is visibly
      unbuilt; a stale one answers every query plausibly from the wrong passages, and it would be
      the citations — the one thing this tool must never get wrong — that were wrong. Failing at
      startup with a message naming `make embed` is the cheap version of that discovery.

    Skipped entirely when the configured toolset has no vector tool, so a lexical-only run is not
    blocked by a store it will never query.
    """
    config = get_config()
    if not needs_vectors(config.agent.toolset):
        return None
    embedder = embedder_module.openai_embedder(
        config.vectors.embedding_model, config.vectors.dimensions
    )
    try:
        return await VectorIndex.open(embedder)
    except VectorsNotBuiltError as exc:
        logger.warning("semantic search unavailable: %s", exc)
        return None


def _load_structured() -> StructuredStore | None:
    """The relational lane's store, or `None` with an explanation on the console.

    The same two-failure-mode split as `_load_vectors`, for the same reason:

    * `StructuredNotBuiltError` — the Parquet mirrors have never been downloaded here. Ordinary on
      a fresh clone; logged and degraded to `None`, and `routes/chat.py` decides whether that is
      fatal for the configured lanes.
    * `StructuredStaleError` — a mirror exists but disagrees with the committed `catalog.json`.
      **Allowed to propagate and stop the boot.** A missing mirror is visibly missing; a partial
      one answers "is this drug covered" from whichever rows happened to arrive, which is a wrong
      answer with a citation attached.

    Skipped entirely when the configured agent has no structured tools, so a reference-only
    deployment is not blocked by data it will never read.
    """
    if not get_config().agent.structured_tools:
        return None
    try:
        return StructuredStore.open()
    except StructuredNotBuiltError as exc:
        logger.warning("structured lane unavailable: %s", exc)
        return None


def _load_web() -> WebSearchClient | None:
    """The web lane's Tavily client, or `None` with an explanation on the console.

    The same shape as `_load_vectors` and `_load_structured`, with **only one failure mode instead
    of two** — and the absence of the second is the interesting part. Those lanes distinguish
    *missing* from *stale*, because a store built against different chunks answers plausibly from
    the wrong passages and must stop the boot. A search API has no local artifact to go stale: every
    call is fresh by construction. So the only question here is whether a credential exists, and a
    missing one is ordinary — degraded to `None`, with `routes/chat.py` deciding whether that is
    fatal for the configured lanes.

    Skipped entirely when the configured agent has no web tools, so a reference-only deployment does
    not warn about a key it will never use.
    """
    if not get_config().agent.web_tools:
        return None
    try:
        return web_client_module.WebSearchClient.open()
    except WebSearchNotConfiguredError as exc:
        logger.warning("web lane unavailable: %s", exc)
        return None


def create_app(*, dist_dir: Path | None = None, stub: bool = False) -> FastAPI:
    """Build the application.

    `stub` defaults to False from Phase 1a: the real agent answers, and `HealthResponse.stub` drops
    to false, which is what removes the UI's banner. Passing `stub=True` still serves `api/stub.py`
    — the Phase 0 canned answers, kept because they are what the contract tests assert against and
    what an offline demo can run on.

    `dist_dir` is a parameter rather than a constant read because the SPA-fallback tests need to
    point it at a `tmp_path` — a real test requirement, not gratuitous injection.
    """
    dist_dir = FRONTEND_DIST if dist_dir is None else dist_dir

    # `AsyncGenerator`, not `AsyncIterator`: `asynccontextmanager` drives the function with
    # `asend()` and `athrow()`, which are generator methods — `AsyncIterator` only promises
    # `__anext__`, so the old annotation was a quiet under-specification that typeshed now flags.
    # Spelled with both parameters because this annotation is evaluated at import time and
    # `requires-python` is `>=3.12`, where the single-argument form is not universally available.
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        # The gold set is 35 questions and loads in milliseconds. Eager because a malformed
        # questions.yaml should fail `make api-dev` at boot rather than on the first page load.
        #
        # The document index is all 2,056 documents across the three corpora: ~23 ms and ~12 MB,
        # measured. That number is the whole argument — it is cheaper than writing the lazy path,
        # and `uvicorn --reload` re-pays it invisibly. The rejected alternative was a
        # doc-id -> byte-offset sidecar, which saves the memory and costs a second file format,
        # a rebuild step, and a seek per request.
        #
        # The chunk index is Phase 1a's addition and the expensive one: 6,722 chunks plus a BM25
        # inverted index, ~200 ms and ~30 MB, measured. Eager for the same reason the others are —
        # paying it on the first question would make the first question look slow for a reason
        # that has nothing to do with the agent. It is the one entry allowed to be `None`, because
        # chunks.jsonl is git-ignored and a fresh clone has none; see `_load_index`.
        #
        # /api/health still reads the committed chunks_meta.json for its counts rather than this
        # index, so the numbers are answerable either way.
        ctx = AppContext(
            gold=load_gold_set(),
            docs=load_doc_index(),
            started_at=datetime.now(UTC),
            stub=stub,
            version=_version(),
            index=None if stub else _load_index(),
            vectors=None if stub else await _load_vectors(),
            structured=None if stub else _load_structured(),
            web=None if stub else _load_web(),
        )
        app.state.ctx = ctx
        try:
            yield
        finally:
            # The structured store is the only entry here that owns an OS resource rather than
            # plain memory: a DuckDB connection with ten Parquet files registered as views. Closed
            # on shutdown rather than left to process exit, because `create_app` is not only called
            # by `uvicorn` once — a test or an embedding host that builds and discards apps in one
            # process would leak a connection per app. `finally` rather than a line after the
            # `yield`, so a failure inside the application's lifetime still releases it.
            if ctx.structured is not None:
                ctx.structured.close()
            # The web client owns an `httpx.AsyncClient` and therefore a connection pool — the same
            # reason the DuckDB connection is closed here rather than left to process exit.
            if ctx.web is not None:
                await ctx.web.close()
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
