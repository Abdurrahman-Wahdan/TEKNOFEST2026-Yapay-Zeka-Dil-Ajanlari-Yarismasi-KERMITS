"""The stored turn the browser redraws from.

These assertions are the contract between `api/chat_parts.py` and
`UI/src/lib/chat/types.ts`. There is no shared definition -- the part union
belongs to the renderer -- so the ordering and the field names are pinned here,
because the failure mode is silent: a wrong key renders nothing and a wrong order
redraws yesterday's conversation in an order the user never saw.
"""

from types import SimpleNamespace

from api.chat_parts import (
    assistant_parts,
    format_location,
    parts_or_text,
    user_parts,
    web_citations,
)


def context(kind="table", label="Kuveyt Türk", body="| a |\n| - |", **location):
    return SimpleNamespace(
        kind=kind,
        label=label,
        body=body,
        location=SimpleNamespace(
            path=location.get("path", "/urunler"),
            page=location.get("page"),
            section=location.get("section"),
            table=location.get("table"),
            row=location.get("row"),
            column=location.get("column"),
        ),
    )


def attachment(kind="document", images=(1, 2, 3), filename="rapor.pdf"):
    return SimpleNamespace(
        id="handle-abc", filename=filename, kind=kind, images=tuple(images)
    )


# ----- format_location -----


def test_a_location_prints_the_same_trail_the_browser_prints():
    """Must match `page-locator.ts::formatLocation`, separator included."""
    printed = format_location(
        SimpleNamespace(
            path="/urunler",
            page="Ürünler",
            section=None,
            table="Konut Finansmanı",
            row="Ziraat Katılım",
            column=None,
        )
    )
    assert printed == "Ürünler › Konut Finansmanı › row “Ziraat Katılım”"


def test_the_path_stands_in_for_a_missing_page_name():
    assert format_location(
        SimpleNamespace(
            path="/compare", page=None, section=None, table=None, row=None, column=None
        )
    ) == "/compare"


def test_an_empty_location_prints_nothing_rather_than_separators():
    assert format_location(
        SimpleNamespace(
            path="", page=None, section=None, table=None, row=None, column=None
        )
    ) == ""


# ----- user_parts -----


def test_attachments_come_above_the_question():
    """The order the composer shows, not the order the request lists.

    A transcript that put the question first would redraw a turn differently from
    the way the user watched it being written.
    """
    parts = user_parts("Bu tabloyu karşılaştır", [context()], [], [])
    assert [p["type"] for p in parts] == ["context", "text"]
    assert parts[-1]["text"] == "Bu tabloyu karşılaştır"


def test_an_attachment_with_no_question_is_still_a_message():
    """No blank text part: it would draw an empty bubble above the whole message."""
    parts = user_parts("   ", [context()], [], [])
    assert [p["type"] for p in parts] == ["context"]


def test_a_context_carries_its_body_so_the_chip_can_expand():
    part = user_parts("x", [context(body="| a |")], [], [])[0]
    assert part["body"] == "| a |"
    assert part["kind"] == "table"
    assert part["label"] == "Kuveyt Türk"


def test_an_empty_body_is_omitted_rather_than_stored_blank():
    """Absent and present-but-empty differ: `body` is what makes a chip expandable."""
    assert "body" not in user_parts("x", [context(body="")], [], [])[0]


def test_a_capture_keeps_its_label_and_never_its_bytes():
    """The one rule that must not slip. A base64 image in a transcript is a bug."""
    capture = SimpleNamespace(
        label="Sayfa görüntüsü", data="AAAA" * 5000, media_type="image/webp"
    )
    part = user_parts("bak", [], [capture], [])[0]
    assert part == {"type": "context", "kind": "capture", "label": "Sayfa görüntüsü"}
    assert "AAAA" not in str(part)


def test_a_document_attachment_reports_its_page_count():
    part = user_parts("özetle", [], [], [attachment()])[0]
    assert part == {
        "type": "attachment",
        "filename": "rapor.pdf",
        "kind": "document",
        "attachmentId": "handle-abc",
        "pageCount": 3,
    }


def test_an_image_attachment_is_not_given_a_page_count():
    """An attached JPG has one image and zero pages. "1 sayfa" would be invented."""
    part = user_parts("bu ne", [], [], [attachment(kind="image", images=(1,))])[0]
    assert "pageCount" not in part
    assert part["kind"] == "image"


def test_everything_at_once_keeps_composer_order():
    parts = user_parts(
        "hepsini karşılaştır",
        [context(), context(kind="row", label="Vakıf Katılım")],
        [SimpleNamespace(label="Sayfa görüntüsü")],
        [attachment()],
    )
    assert [p["type"] for p in parts] == [
        "context", "context", "context", "attachment", "text",
    ]


# ----- assistant_parts -----


def test_an_answer_is_text_then_its_citations():
    parts = assistant_parts(
        "Gram altın 7.150 TL", [{"cite_url": "https://x", "bank": "vakif"}]
    )
    assert [p["type"] for p in parts] == ["text", "citations"]
    assert parts[1]["sources"] == [{"url": "https://x", "bank": "vakif"}]


def test_a_stored_citation_is_narrowed_to_what_the_renderer_reads():
    """`cite_url` -> `url`, `source_type` -> `sourceType`, and nothing else kept.

    `ChatMessage.citations` holds the full retrieved-chunk contract; the renderer
    reads `source.url` and `source.sourceType`. Storing the wide shape broke
    silently: `sourceGroup(undefined, undefined)` still classifies as "online",
    so a restored answer showed a sources block whose every entry had no link.
    """
    assert web_citations(
        [
            {
                "point_id": "p1",
                "cite_url": "https://vakif/altin",
                "title": "Altın",
                "bank": "vakif",
                "source_type": "live_web_page",
                "score": 0.91,
                "text": "a whole retrieved chunk nobody renders",
            }
        ]
    ) == [
        {
            "url": "https://vakif/altin",
            "title": "Altın",
            "bank": "vakif",
            "sourceType": "live_web_page",
        }
    ]


def test_a_citation_with_nothing_to_open_is_not_a_source():
    """Copied from the transport, which drops these on the live path too."""
    assert web_citations([{"cite_url": "", "title": "Altın"}]) == []


def test_an_empty_optional_field_is_omitted_rather_than_stored_null():
    assert web_citations([{"cite_url": "https://x", "title": "", "bank": None}]) == [
        {"url": "https://x"}
    ]


def test_an_uncited_answer_gets_no_empty_citations_block():
    assert [p["type"] for p in assistant_parts("Merhaba", [])] == ["text"]


def test_a_client_tool_note_sits_above_the_answer():
    """What it did, then what it said -- the order the browser renders live."""
    parts = assistant_parts(
        "Sayfada üç kampanya var.",
        [],
        [SimpleNamespace(label="Sayfaya bakıldı")],
    )
    assert [p["type"] for p in parts] == ["context", "text"]
    assert parts[0] == {
        "type": "context", "kind": "capture", "label": "Sayfaya bakıldı",
    }


def test_a_tool_result_contributes_only_its_label():
    """A result can hold a whole page capture. A transcript is not for that."""
    result = SimpleNamespace(label="Sayfaya bakıldı", image="AAAA" * 5000, text="x" * 999)
    part = assistant_parts("ok", [], [result])[0]
    assert part == {"type": "context", "kind": "capture", "label": "Sayfaya bakıldı"}


def test_an_unlabelled_tool_result_is_skipped():
    parts = assistant_parts("ok", [], [SimpleNamespace(label="")])
    assert [p["type"] for p in parts] == ["text"]


# ----- parts_or_text: the rows that predate the column -----


def test_a_stored_row_is_returned_as_stored():
    stored = [{"type": "text", "text": "hi"}, {"type": "citations", "sources": []}]
    message = SimpleNamespace(parts=stored, content="hi", role="assistant", citations=[])
    assert parts_or_text(message) == stored


def test_an_old_row_becomes_one_text_part():
    """Every conversation in the table today has `parts = []`."""
    message = SimpleNamespace(
        parts=[], content="Altın fiyatları nedir?", role="user", citations=[]
    )
    assert parts_or_text(message) == [
        {"type": "text", "text": "Altın fiyatları nedir?"}
    ]


def test_an_old_answer_keeps_the_citations_it_was_stored_with():
    """`citations` predates `parts`, so an old answer can still show its sources."""
    message = SimpleNamespace(
        parts=[],
        content="7.150 TL",
        role="assistant",
        citations=[{"cite_url": "https://x", "source_type": "indexed_document"}],
    )
    assert parts_or_text(message) == [
        {"type": "text", "text": "7.150 TL"},
        {
            "type": "citations",
            "sources": [{"url": "https://x", "sourceType": "indexed_document"}],
        },
    ]


def test_a_row_with_nothing_in_it_renders_nothing():
    """Rather than an empty bubble with no text and no attachment."""
    message = SimpleNamespace(parts=[], content="", role="assistant", citations=[])
    assert parts_or_text(message) == []


def test_the_returned_list_is_a_copy():
    """A caller mutating the response must not write into the ORM row's JSONB."""
    stored = [{"type": "text", "text": "hi"}]
    message = SimpleNamespace(parts=stored, content="hi", role="user", citations=[])
    returned = parts_or_text(message)
    returned.append({"type": "text", "text": "injected"})
    assert len(stored) == 1
