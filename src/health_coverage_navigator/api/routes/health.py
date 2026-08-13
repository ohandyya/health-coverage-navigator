"""Liveness, plus which routing lanes are actually wired up.

The lane report is the useful half. The three-lane vocabulary exists in the contract from Phase 0
(docs/frontend_plan.md §4.2) but only `reference` has anything behind it until Phases 2 and 3, and
a UI that shows three badges with no way to know which are real is a UI that lies. `stub` is the
same argument at the whole-app level: while every answer is canned, the frontend says so.
"""

import json
from typing import Annotated

from fastapi import APIRouter, Depends

from health_coverage_navigator.api.deps import AppContext, get_context
from health_coverage_navigator.api.models import CorpusStatus, HealthResponse, LaneStatus
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


@router.get("/health", response_model=HealthResponse, summary="Liveness and configured lanes")
def get_health(ctx: Annotated[AppContext, Depends(get_context)]) -> HealthResponse:
    corpora = _corpus_status()
    documents = sum(c.documents for c in corpora)
    chunks = sum(c.chunks for c in corpora)

    lanes = [
        LaneStatus(
            source_type="reference",
            configured=bool(ctx.docs),
            detail=f"{documents:,} documents, {chunks:,} chunks across {len(corpora)} corpora",
        ),
        LaneStatus(
            source_type="structured_api",
            configured=False,
            detail="Phase 3 — no Marketplace, openFDA or NPPES tools yet",
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
