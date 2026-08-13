"""The Qdrant payload for a chunk, and the point id that keeps sync idempotent.

The payload is what comes back with every search hit — the link the agent cites,
the fields it filters on, and the flags that tell it how much to trust the text.
The web link is the priority field; the rest is a lean, useful set.

The point id is derived from the chunk id, deterministically, so re-indexing a
changed chunk *replaces* its point instead of adding a duplicate.

    from index.payload import point_id, build_payload, PAYLOAD_INDEXES
"""

import uuid

# A fixed namespace so uuid5(chunk_id) is stable across runs and machines. Never
# change this: it would orphan every existing point.
NAMESPACE = uuid.UUID("6b1f2c9a-3d4e-5a6b-8c7d-0e1f2a3b4c5d")

# Payload fields that get a Qdrant index, so filtered search stays fast. Keyword
# unless noted. `campaign_end` is a datetime index for the expiry range filter.
PAYLOAD_INDEXES: dict[str, str] = {
    "bank": "keyword",
    "site": "keyword",
    "doc_kind": "keyword",
    "source_type": "keyword",
    "audience": "keyword",
    "lang": "keyword",
    "from_vision": "bool",
    "campaign_end": "datetime",
}


def point_id(chunk_id: str) -> str:
    """The Qdrant point id for a chunk.

    Qdrant rejects a raw string id; uuid5 turns the stable chunk id into a stable
    UUID, so the same chunk always lands on the same point and an upsert replaces
    it rather than duplicating.
    """
    return str(uuid.uuid5(NAMESPACE, chunk_id))


def build_payload(document: dict, *, unit_kind: str, text: str, cite_url: str,
                  text_hash: str = "", heading_path: str = "",
                  page_number: int | None = None, order: int = 0,
                  from_vision: bool = False,
                  linked_from: tuple[str, ...] = ()) -> dict:
    """The payload stored with one chunk's vector.

    `document` is the raw JSON document the chunk came from; the per-unit fields
    (text, cite_url, heading, page number, order, from_vision) come from the unit.
    """
    payload = {
        # ----- the link (priority) -----
        "cite_url": cite_url,
        "url": document.get("url", ""),

        # ----- display -----
        "title": document.get("title", ""),
        "text": text,
        "unit_kind": unit_kind,
        "order": order,

        # ----- filters -----
        "bank": document.get("bank", ""),
        "site": document.get("site", ""),
        "source_type": document.get("source_type", ""),
        "doc_kind": document.get("doc_kind", ""),
        "audience": document.get("audience", ""),
        "lang": document.get("lang", ""),

        # ----- campaign start / provenance -----
        "campaign_start": document.get("campaign_start", ""),
        "date_source": document.get("date_source", ""),

        # ----- trust -----
        "from_vision": from_vision,
        "low_confidence": bool(document.get("low_confidence")),

        # ----- identity / sync -----
        "doc_id": document.get("doc_id", ""),
        # The unit's hash, stored (not indexed) so the nightly scroll can tell an
        # unchanged chunk from a changed one without re-embedding to compare.
        "text_hash": text_hash,

        # ----- PDF <-> page linkage -----
        "attachments": list(document.get("attachments", ())),
        "linked_from": list(linked_from),
    }
    # campaign_end is stored ONLY when it exists. Qdrant's IsEmpty matches a
    # missing field but not an empty string, so leaving "" here would make the
    # "active or undated" query-time filter silently exclude every product and
    # fee page. Absent instead of "" makes IsEmpty do the right thing, and keeps
    # the datetime index clean of non-dates.
    end = document.get("campaign_end", "")
    if end:
        payload["campaign_end"] = end
    if heading_path:
        payload["heading_path"] = heading_path
    if page_number is not None:
        payload["page_number"] = page_number
    return payload
