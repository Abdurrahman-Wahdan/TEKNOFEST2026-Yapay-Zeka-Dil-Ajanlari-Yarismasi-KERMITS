"""Turning documents into chunks: the unit boundaries, block rendering, and the
context header that rides into each vector."""

import pytest

from config.settings import settings
from index.chunk import chunks, linked_from_map
from index.embed_text import header_for, passage_text, query_text

pytestmark = pytest.mark.unit


def _page_doc(**over):
    doc = {
        "doc_id": "aaaa1111", "url": "https://www.kuveytturk.com.tr/kendim-icin/konut",
        "site": "kuveytturk", "bank": "Kuveyt Türk Katılım Bankası",
        "source_type": "page", "doc_kind": "product", "title": "Konut Finansmanı",
        "audience": "bireysel", "lang": "tr", "text_hash": "doc",
        "campaign_end": "", "sections": [
            {"heading_path": "Konut Finansmanı > Kâr Payı", "anchor": "kar-payi",
             "level": 2, "text": "Konut finansmanı kâr payı oranı bilgisi burada.",
             "order": 0, "cite_url": "https://www.kuveytturk.com.tr/kendim-icin/konut#kar-payi",
             "text_hash": "sec0"},
            {"heading_path": "Konut Finansmanı > Başvuru", "anchor": "",
             "level": 2, "text": "Başvuru için gereken belgeler burada anlatılır.",
             "order": 1, "cite_url": "https://www.kuveytturk.com.tr/kendim-icin/konut",
             "text_hash": "sec1"},
        ],
    }
    doc.update(over)
    return doc


def _pdf_doc(**over):
    doc = {
        "doc_id": "bbbb2222", "url": "https://x.com.tr/documents/ucretler.pdf",
        "site": "turkiyefinans", "bank": "Türkiye Finans Katılım Bankası",
        "source_type": "pdf", "doc_kind": "fees", "title": "Ücret Tarifesi",
        "audience": "", "lang": "tr", "text_hash": "doc", "campaign_end": "",
        # A PDF page is markdown plus items, not typed blocks. The item's
        # marker appears verbatim in the markdown, which is what pins it to its
        # place on the page; chunk._page_text appends what the item says.
        "pages": [
            {"number": 1, "cite_url": "https://x.com.tr/documents/ucretler.pdf#page=1",
             "text_hash": "pg1", "from_vision": False,
             "markdown": "## Ücret Tarifesi\n\n<table_1>",
             "items": [{"id": "table_1", "marker": "<table_1>",
                        "summary": "Ücret tarifesi tablosu",
                        "visible_text": "| Ücret | Tutar |\n|---|---|\n| EFT | 5 TL |",
                        "visual_representation": "iki sütunlu tablo"}]},
            {"number": 2, "cite_url": "https://x.com.tr/documents/ucretler.pdf#page=2",
             "text_hash": "pg2", "from_vision": True,
             "markdown": "İkinci sayfa içeriği burada.", "items": []},
        ],
    }
    doc.update(over)
    return doc


# ----- HTML sections -----

def test_each_html_section_is_a_chunk():
    out = chunks(_page_doc())
    assert len(out) == 2
    assert [c.chunk_id for c in out] == ["aaaa1111:section:0", "aaaa1111:section:1"]


def test_a_section_chunk_keeps_the_sections_hash_and_cite_url():
    out = chunks(_page_doc())
    assert out[0].text_hash == "sec0"
    assert out[0].cite_url.endswith("#kar-payi")
    assert out[1].cite_url == "https://www.kuveytturk.com.tr/kendim-icin/konut"  # no anchor


def test_the_embed_text_carries_bank_title_and_heading():
    out = chunks(_page_doc())
    assert out[0].embed_text.startswith("Kuveyt Türk Katılım Bankası — Konut Finansmanı — Konut")
    assert "kâr payı oranı" in out[0].embed_text


# ----- PDF pages -----

def test_each_pdf_page_is_a_chunk_with_a_page_citation():
    out = chunks(_pdf_doc())
    assert len(out) == 2
    assert out[0].cite_url.endswith("#page=1")
    assert out[1].cite_url.endswith("#page=2")


def test_a_pdf_table_survives_as_markdown_in_its_chunk():
    """The table lives in the item, and the chunk has to carry it.

    The markdown holds only the `<table_1>` marker, so a chunk built from the
    markdown alone would embed a page that mentions a table and contains none
    of its numbers -- unfindable by every query about a fee.
    """
    out = chunks(_pdf_doc())
    assert "| Ücret | Tutar |" in out[0].text
    assert "| EFT | 5 TL |" in out[0].text


def test_a_pdf_heading_becomes_a_markdown_heading():
    out = chunks(_pdf_doc())
    assert out[0].text.startswith("## Ücret Tarifesi")


def test_an_items_summary_is_searchable_beside_its_text():
    """What the item represents is part of the page, not metadata beside it."""
    out = chunks(_pdf_doc())
    assert "Ücret tarifesi tablosu" in out[0].text


def test_a_scanned_page_is_flagged_from_vision_in_its_payload():
    out = chunks(_pdf_doc())
    assert out[0].payload["from_vision"] is False
    assert out[1].payload["from_vision"] is True


def test_an_item_with_nothing_in_it_adds_nothing():
    doc = _pdf_doc()
    doc["pages"][0]["items"].append({"id": "figure_9", "marker": "<figure_9>",
                                     "summary": "", "visible_text": "",
                                     "visual_representation": ""})
    out = chunks(doc)
    assert "\n\n\n" not in out[0].text          # no gap from the empty block


# ----- campaigns embed whole -----

def test_a_campaign_is_one_chunk_not_split_by_section():
    doc = _page_doc(doc_kind="campaign", text="Kampanya 31.12.2026 tarihine kadar. "
                    "Katılım şartları aşağıda. " * 4, campaign_end="2026-12-31")
    out = chunks(doc)
    assert len(out) == 1
    assert out[0].chunk_id == "aaaa1111:document:0"
    assert out[0].cite_url == doc["url"]
    assert out[0].payload["campaign_end"] == "2026-12-31"


# ----- large-unit split -----

def test_a_unit_over_the_limit_is_split_on_paragraphs(monkeypatch):
    monkeypatch.setattr(settings, "INDEX_MAX_CHUNK_CHARS", 120)
    big = "\n\n".join(["Paragraf bir uzun metin." * 3, "Paragraf iki uzun metin." * 3,
                       "Paragraf üç uzun metin." * 3])
    doc = _page_doc()
    doc["sections"] = [{"heading_path": "Uzun", "anchor": "", "level": 2,
                        "text": big, "order": 0, "cite_url": "https://x/p",
                        "text_hash": "big"}]
    out = chunks(doc)
    assert len(out) > 1
    assert out[0].chunk_id == "aaaa1111:section:0#0"
    assert all(c.text_hash == "big" for c in out)   # splits share the unit hash


def test_a_single_huge_paragraph_is_split_not_passed_through(monkeypatch):
    """Splitting on blank lines alone let one real page through at 207,823
    characters -- enough to stall an embedding batch. A paragraph with no blank
    lines must still be broken, at sentence ends."""
    monkeypatch.setattr(settings, "INDEX_MAX_CHUNK_CHARS", 3500)
    doc = _page_doc()
    doc["sections"] = [{"heading_path": "Uzun", "anchor": "", "level": 2,
                        "text": "Bu bir cümledir. " * 5000, "order": 0,
                        "cite_url": "https://x/p", "text_hash": "huge"}]
    out = chunks(doc)
    assert len(out) > 1
    # Parçalar %10 overlap taşır (bkz. index/chunk.py::_with_overlap), bu yüzden
    # tavan gövde + overlap payıdır — gövdenin kendisi sınırı aşmaz.
    tavan = 3500 + settings.INDEX_CHUNK_OVERLAP_CHARS + 2
    assert all(len(c.text) <= tavan for c in out)


def test_text_with_no_boundary_at_all_is_still_cut(monkeypatch):
    """No blank lines, no sentence ends -- the hard cut is the last resort."""
    monkeypatch.setattr(settings, "INDEX_MAX_CHUNK_CHARS", 1000)
    doc = _page_doc()
    doc["sections"] = [{"heading_path": "Solid", "anchor": "", "level": 2,
                        "text": "x" * 9000, "order": 0,
                        "cite_url": "https://x/p", "text_hash": "solid"}]
    out = chunks(doc)
    tavan = 1000 + settings.INDEX_CHUNK_OVERLAP_CHARS + 2
    assert all(len(c.text) <= tavan for c in out)


def test_a_small_unit_is_not_split():
    out = chunks(_page_doc())
    assert all("#" not in c.chunk_id.split(":")[-1] for c in out)


# ----- linked_from inversion -----

def test_a_pdf_learns_which_pages_link_it():
    page = _page_doc(attachments=["bbbb2222"])
    mapping = linked_from_map([page])
    assert mapping["bbbb2222"] == ("aaaa1111",)


def test_a_pdf_chunk_carries_its_linked_from():
    out = chunks(_pdf_doc(), linked_from=("aaaa1111",))
    assert out[0].payload["linked_from"] == ["aaaa1111"]


# ----- embed-text asymmetry (Qwen3) -----

def test_a_qwen_query_gets_an_instruction_and_a_passage_does_not():
    q = query_text("konut kâr payı oranı", model="Qwen/Qwen3-Embedding-0.6B")
    p = passage_text("Kuveyt Türk — Konut", "gövde metni", model="Qwen/Qwen3-Embedding-0.6B")
    assert q.startswith("Instruct:")
    assert "Query: konut kâr payı oranı" in q
    assert not p.startswith("Instruct:")
    assert p.startswith("Kuveyt Türk — Konut")


def test_an_e5_model_prefixes_both_sides():
    assert query_text("soru", model="intfloat/multilingual-e5-large").startswith("query: ")
    assert passage_text("", "gövde", model="intfloat/multilingual-e5-large").startswith("passage: ")


def test_the_header_drops_empty_parts():
    assert header_for("Kuveyt Türk", "Konut", "") == "Kuveyt Türk — Konut"
    assert header_for("Kuveyt Türk", "", "") == "Kuveyt Türk"
