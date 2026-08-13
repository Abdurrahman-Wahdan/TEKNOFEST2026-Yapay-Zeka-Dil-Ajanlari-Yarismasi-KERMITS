"""Searching the index — one query, both web and PDF, ranked together.

The default search returns HTML sections and PDF pages in one ranked list; a
caller narrows by bank, kind, audience or source only when it wants to. The one
filter that is on by default is campaign expiry: 322 of 422 dated campaigns were
already expired when the corpus was crawled, so an unfiltered search would
happily recommend offers that ended two years ago.

    from index.retrieve import search

    for hit in search("Kuveyt Türk konut finansmanı kâr payı oranı"):
        print(hit.score, hit.cite_url)
"""

import logging
from datetime import date

from config.settings import settings
from embeddings import get_embedding
from vector_stores.client import get_qdrant_client

from .embed_text import query_text
from .models import RetrievedChunk

logger = logging.getLogger(__name__)


def _filter(bank, doc_kind, source_type, audience, active_only, today):
    """The Qdrant filter for a search, or None when nothing constrains it."""
    from qdrant_client.models import (
        DatetimeRange, FieldCondition, Filter, IsEmptyCondition, MatchValue,
        PayloadField,
    )

    must = []
    for key, value in (("bank", bank), ("doc_kind", doc_kind),
                       ("source_type", source_type), ("audience", audience)):
        if value:
            must.append(FieldCondition(key=key, match=MatchValue(value=value)))

    if active_only:
        # Keep a chunk if it has no end date (products, fees, undated campaigns)
        # OR its end date is today or later. campaign_end is absent on non-dated
        # chunks, so IsEmpty is what lets them through -- mirrors dates.is_active.
        must.append(Filter(should=[
            IsEmptyCondition(is_empty=PayloadField(key="campaign_end")),
            FieldCondition(key="campaign_end",
                           range=DatetimeRange(gte=f"{today}T00:00:00Z")),
        ]))

    return Filter(must=must) if must else None


def search(query: str, *, bank: str | None = None, doc_kind: str | None = None,
           source_type: str | None = None, audience: str | None = None,
           active_only: bool = True, today: str | None = None,
           k: int | None = None, collection: str | None = None
           ) -> list[RetrievedChunk]:
    """The chunks most relevant to `query`, filtered and ranked.

    Args:
        source_type: None (default) searches web pages and PDFs together — the
            whole point of the single collection. Set it only to narrow.
        active_only: apply the campaign expiry filter. On by default.
        today: ISO date the expiry filter compares against; defaults to today, so
            the same index answers correctly on any day with no re-indexing.
    """
    collection = collection or settings.QDRANT_COLLECTION_CHUNKS
    k = k or settings.INDEX_RETRIEVE_TOP_K
    today = today or date.today().isoformat()

    vector = get_embedding().embed_query(query_text(query))
    query_filter = _filter(bank, doc_kind, source_type, audience, active_only, today)

    client = get_qdrant_client()
    hits = client.query_points(
        collection_name=collection, query=vector, query_filter=query_filter,
        limit=k, with_payload=True).points

    return [RetrievedChunk(
        score=hit.score,
        cite_url=(hit.payload or {}).get("cite_url", ""),
        text=(hit.payload or {}).get("text", ""),
        payload=hit.payload or {}) for hit in hits]
