"""The Qdrant payload and the deterministic point id."""

import pytest

from index.payload import PAYLOAD_INDEXES, build_payload, point_id

pytestmark = pytest.mark.unit


DOC = {
    "doc_id": "aaaa1111", "url": "https://www.kuveytturk.com.tr/kampanyalar/x",
    "bank": "Kuveyt Türk Katılım Bankası", "site": "kuveytturk",
    "source_type": "page", "doc_kind": "campaign", "title": "Colin's Kampanyası",
    "audience": "bireysel", "lang": "tr", "campaign_end": "2026-12-31",
    "campaign_start": "2026-08-06", "date_source": "label", "low_confidence": False,
    "attachments": ["ffff9999"],
}


# ----- point id -----

def test_the_point_id_is_stable_across_runs():
    """A non-deterministic id would re-add a duplicate every night."""
    assert point_id("aaaa1111:section:0") == point_id("aaaa1111:section:0")


def test_different_chunks_get_different_point_ids():
    assert point_id("aaaa1111:section:0") != point_id("aaaa1111:section:1")


def test_the_point_id_is_a_uuid():
    import uuid
    uuid.UUID(point_id("aaaa1111:page:3"))     # raises if not a valid UUID


# ----- payload -----

def test_the_cite_url_is_always_present():
    """The link is the priority field; a chunk without it cannot be cited."""
    payload = build_payload(DOC, unit_kind="document", text="gövde",
                            cite_url="https://www.kuveytturk.com.tr/kampanyalar/x")
    assert payload["cite_url"] == "https://www.kuveytturk.com.tr/kampanyalar/x"


def test_the_payload_carries_the_filter_fields():
    payload = build_payload(DOC, unit_kind="document", text="x", cite_url="u")
    for key, value in [("bank", "Kuveyt Türk Katılım Bankası"), ("site", "kuveytturk"),
                       ("source_type", "page"), ("doc_kind", "campaign"),
                       ("audience", "bireysel"), ("lang", "tr")]:
        assert payload[key] == value


def test_a_dated_campaign_stores_its_end_but_a_product_omits_it():
    """campaign_end is present only when it exists. Qdrant's IsEmpty matches a
    missing field, not an empty string, so a product must have NO campaign_end
    for the active-or-undated query filter to keep it."""
    campaign = build_payload(DOC, unit_kind="document", text="x", cite_url="u")
    assert campaign["campaign_end"] == "2026-12-31"
    product = build_payload({**DOC, "doc_kind": "product", "campaign_end": ""},
                            unit_kind="section", text="x", cite_url="u")
    assert "campaign_end" not in product


def test_the_payload_stores_display_text_without_a_header():
    """The header is only in the embed text; the payload text is what the agent
    shows, so it must be clean."""
    payload = build_payload(DOC, unit_kind="section", text="Sadece gövde metni.",
                            cite_url="u")
    assert payload["text"] == "Sadece gövde metni."
    assert "—" not in payload["text"]


def test_a_page_number_is_only_present_for_pages():
    page = build_payload(DOC, unit_kind="page", text="x", cite_url="u", page_number=7)
    section = build_payload(DOC, unit_kind="section", text="x", cite_url="u")
    assert page["page_number"] == 7
    assert "page_number" not in section


def test_a_heading_path_is_only_present_when_given():
    with_h = build_payload(DOC, unit_kind="section", text="x", cite_url="u",
                           heading_path="A > B")
    without = build_payload(DOC, unit_kind="document", text="x", cite_url="u")
    assert with_h["heading_path"] == "A > B"
    assert "heading_path" not in without


def test_attachments_and_linked_from_are_lists():
    payload = build_payload(DOC, unit_kind="section", text="x", cite_url="u",
                            linked_from=("dddd4444",))
    assert payload["attachments"] == ["ffff9999"]
    assert payload["linked_from"] == ["dddd4444"]


def test_from_vision_defaults_false_and_is_stored():
    assert build_payload(DOC, unit_kind="page", text="x", cite_url="u")["from_vision"] is False
    assert build_payload(DOC, unit_kind="page", text="x", cite_url="u",
                         from_vision=True)["from_vision"] is True


def test_the_indexed_fields_are_present_when_they_apply():
    """Every indexed field must exist in the payload when it applies. campaign_end
    is the one exception — indexed for range queries, present only on dated
    campaigns (a product legitimately has none)."""
    payload = build_payload(DOC, unit_kind="page", text="x", cite_url="u",
                            page_number=1)   # DOC is a dated campaign -> has all
    for field in PAYLOAD_INDEXES:
        assert field in payload, f"indexed field {field!r} missing from payload"


def test_campaign_end_is_indexed_as_datetime():
    assert PAYLOAD_INDEXES["campaign_end"] == "datetime"
