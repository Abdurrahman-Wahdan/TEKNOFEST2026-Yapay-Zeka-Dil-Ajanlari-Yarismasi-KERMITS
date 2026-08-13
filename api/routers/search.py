"""Searching the corpus index -- what the campaign feed and the citation panel read.

One collection holds web sections and PDF pages together, so a search returns
both in one ranked list. The filters narrow; they do not switch stores.

`active_only` defaults to True and should almost never be turned off in the UI:
322 of 422 dated campaigns were already expired when the corpus was crawled, so
an unfiltered search cheerfully recommends offers that ended two years ago.
Expiry is evaluated at query time against the stored `campaign_end`, never baked
into the index -- which is why the same index answers correctly tomorrow.
"""

import logging

from fastapi import APIRouter, HTTPException, Query, status

from index.retrieve import search as retrieve

from ..converters import chunk_out
from ..schemas.banks import SearchResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/search", tags=["search"])


@router.get("", response_model=SearchResponse)
def search_corpus(
    q: str = Query(min_length=2, max_length=500),
    bank: str | None = Query(default=None),
    doc_kind: str | None = Query(
        default=None, description="campaign | product | fee | ..."
    ),
    source_type: str | None = Query(
        default=None,
        description="web | pdf. Omitted searches both together -- the point of "
        "the single collection. Set it only to narrow.",
    ),
    audience: str | None = Query(default=None, description="retail | corporate"),
    active_only: bool = Query(
        default=True, description="Hide expired campaigns. Leave on."
    ),
    k: int = Query(default=8, gt=0, le=50),
) -> SearchResponse:
    """The passages most relevant to the query, each with an openable citation."""
    try:
        hits = retrieve(
            q,
            bank=bank,
            doc_kind=doc_kind,
            source_type=source_type,
            audience=audience,
            active_only=active_only,
            k=k,
        )
    except Exception as exc:
        # Qdrant being down is the realistic cause, and it is not the caller's
        # fault. 503 rather than 500 tells the frontend to offer a retry.
        logger.exception("Search failed for %r", q)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The search index is unavailable.",
        ) from exc

    return SearchResponse(
        query=q, active_only=active_only, hits=[chunk_out(h) for h in hits]
    )
