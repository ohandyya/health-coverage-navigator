"""Citation drill-down: the full document behind a citation's `doc_id`.

docs/frontend_plan.md §8 requires that document ids resolve **through the corpus index, never by
concatenating user input onto a filesystem path** — localhost does not make path traversal
acceptable. That rule is satisfied structurally here rather than by sanitising input: the handler
is a dict lookup, every key in that dict came from a committed corpus file, and no filesystem
access derives from the request at all. A `{doc_id}` path parameter also does not match `/`, so a
traversal attempt cannot even reach the lookup as one segment. There is a test for it, so the
property is asserted rather than assumed.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from health_coverage_navigator.api.deps import AppContext, get_context
from health_coverage_navigator.api.models import CorpusDocument, ErrorResponse
from health_coverage_navigator.corpus import SHARED_FIELDS

router = APIRouter()


def to_document(record: dict) -> CorpusDocument:
    """Split a corpus record into the shared fields and a per-source `meta` passthrough."""
    return CorpusDocument(
        id=record["id"],
        source=record["source"],
        title=record["title"],
        url=record["url"],
        bite=record["bite"],
        text=record["text"],
        meta={k: v for k, v in record.items() if k not in SHARED_FIELDS},
    )


@router.get(
    "/corpus/{doc_id}",
    response_model=CorpusDocument,
    responses={404: {"model": ErrorResponse}},
    summary="The full document behind a citation",
)
def get_document(doc_id: str, ctx: Annotated[AppContext, Depends(get_context)]) -> CorpusDocument:
    record = ctx.docs.get(doc_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"unknown document id {doc_id!r}")
    return to_document(record)
