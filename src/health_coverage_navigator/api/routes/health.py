"""Liveness, plus which routing lanes are actually wired up.

The lane report is the useful half. The three-lane vocabulary exists in the contract from Phase 0
(docs/frontend_plan.md §4.2) but only `reference` has anything behind it until Phases 2 and 3, and
a UI that shows three badges with no way to know which are real is a UI that lies. `stub` is the
same argument at the whole-app level: while every answer is canned, the frontend says so.
"""

import json
from typing import Annotated

from fastapi import APIRouter, Depends

from health_coverage_navigator.agent.tools import needs_vectors
from health_coverage_navigator.api.deps import AppContext, get_context
from health_coverage_navigator.api.models import CorpusStatus, HealthResponse, LaneStatus
from health_coverage_navigator.config import get_config
from health_coverage_navigator.corpus import CORPUS_NAMES, chunks_meta_path, chunks_path

router = APIRouter()


def _corpus_status() -> list[CorpusStatus]:
    """Per-source counts, read from the committed manifests.

    `chunks_meta.json` rather than `chunks.jsonl` on purpose: the chunks themselves are git-ignored
    and a fresh clone has none, but the manifest records `input.doc_count` and `output.chunk_count`
    and is committed. So the counts are always answerable, and `chunks_built` reports separately
    whether `make chunk` has actually run here.
    """
    statuses: list[CorpusStatus] = []
    for source in CORPUS_NAMES:
        meta_path = chunks_meta_path(source)
        if not meta_path.is_file():
            statuses.append(CorpusStatus(source=source, documents=0, chunks=0, chunks_built=False))
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        statuses.append(
            CorpusStatus(
                source=source,
                documents=meta["input"]["doc_count"],
                chunks=meta["output"]["chunk_count"],
                chunks_built=chunks_path(source).is_file(),
            )
        )
    return statuses


def _retrieval_detail(ctx: AppContext) -> str:
    """How the reference lane is being searched — lexical, semantic, or both.

    Reported inside the existing `reference` lane rather than as a fourth `LaneStatus`, because
    Phase 1a and 1b are two ways of searching **one** lane, not two lanes. The `source_type`
    vocabulary is the frozen contract's three-value routing dimension; adding `vector` to it would
    say the agent routes to it, which is exactly the confusion the phase order avoids.

    `detail` is a free string, so this needs no contract change and no `make types`.
    """
    configured = get_config().agent.toolset
    if not needs_vectors(configured):
        return "lexical search (BM25)"
    if ctx.vectors is None:
        return f"toolset '{configured}' needs the vector store — run `make embed` and restart"
    manifest = ctx.vectors.manifest
    lexical = "BM25 + " if configured == "both" else ""
    return (
        f"{lexical}semantic search ({manifest.row_count:,} vectors, "
        f"{manifest.embedding_model}, {manifest.snapshot_id})"
    )


def _structured_detail(ctx: AppContext) -> str:
    """What the relational lane has behind it, or what to run to give it something."""
    if not get_config().agent.structured_tools:
        return "relational tools are off in config.yaml (`agent.structured_tools`)"
    if ctx.structured is None:
        return "plan data not built on this machine — run `make puf` and restart"
    overview = ctx.structured.overview(plan_year=None)
    rows = sum(table.rows for table in overview.tables)
    where = ", ".join(f"{source} {part}" for source, part in sorted(overview.partitions.items()))
    return f"{len(overview.tables)} tables, {rows:,} rows · {where} · Phase 3 adds live APIs"


@router.get("/health", response_model=HealthResponse, summary="Liveness and configured lanes")
def get_health(ctx: Annotated[AppContext, Depends(get_context)]) -> HealthResponse:
    corpora = _corpus_status()
    documents = sum(c.documents for c in corpora)
    chunks = sum(c.chunks for c in corpora)

    lanes = [
        LaneStatus(
            source_type="reference",
            # The *index*, not the document count. Citation drill-down works off `ctx.docs`, but
            # answering needs the chunks, and chunks.jsonl is git-ignored — so a fresh clone has
            # documents and no lane. Reporting `configured` off `docs` would show a live lane on a
            # server that can only 503.
            configured=ctx.index is not None,
            detail=(
                f"{documents:,} documents, {chunks:,} chunks across {len(corpora)} corpora "
                f"· {_retrieval_detail(ctx)}"
                if ctx.index is not None
                else "corpus not chunked on this machine — run `make chunk` and restart"
            ),
        ),
        LaneStatus(
            source_type="structured_api",
            # Phase 1-c: the lane goes live on **vendored** plan data, ahead of Phase 3's live
            # APIs. Reported off the store rather than off the config, for the same reason the
            # reference lane is reported off the index and not off the document count: a lane that
            # is configured but has no data behind it can only 503, and calling that "configured"
            # would be a health endpoint that lies.
            configured=ctx.structured is not None,
            detail=_structured_detail(ctx),
        ),
        LaneStatus(
            source_type="web",
            configured=False,
            detail="Phase 2 — no web-search tool yet",
        ),
    ]

    return HealthResponse(
        status="ok",
        version=ctx.version,
        stub=ctx.stub,
        lanes=lanes,
        corpora=corpora,
    )
