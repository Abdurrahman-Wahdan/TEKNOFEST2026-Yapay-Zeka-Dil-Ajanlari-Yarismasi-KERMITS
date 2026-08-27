"""Report markdown survives the trip into a document.

The agent's answers are not a markdown subset -- `agents/main/prompt.py` requires
an inline `[title](url)` on every web-sourced claim and a literal `→` in menu
paths, on top of headings, lists and tables. Each of those is a thing a
hand-rolled converter drops silently, so each is asserted.
"""

import pytest

from api.export.document import ProseBlock, TableBlock
from api.export.from_report import report_document

pytestmark = pytest.mark.unit

BODY = """## Haftalık özet

Bu hafta oran değişti. Kaynak: [Ziraat konut sayfası](https://ziraatkatilim.com.tr/konut).

| Banka | Oran |
| --- | ---: |
| Ziraat | %2,89 |
| [Vakıf](https://vakifkatilim.com.tr) | %3,05 |

- **Ziraat** en uygun oranı veriyor.
- Mobil Şube → Hesap → Yatırım Hesabı Aç
"""


def test_a_markdown_table_becomes_a_table_not_prose():
    """Left in the prose it would reach Word as a web page's table. Lifted out,
    it goes through the path every other table in the application takes."""
    document = report_document(title="Rapor", body=BODY)

    assert [type(block).__name__ for block in document.blocks] == [
        "ProseBlock",
        "TableBlock",
        "ProseBlock",
    ]


def test_the_lifted_table_keeps_its_headers_alignment_and_links():
    table = next(b for b in report_document(title="R", body=BODY).blocks
                 if isinstance(b, TableBlock))

    assert [column.label for column in table.columns] == ["Banka", "Oran"]
    assert table.columns[1].align == "right"
    assert table.rows[1][0].display == "Vakıf"
    assert table.rows[1][0].href == "https://vakifkatilim.com.tr"


def test_inline_links_survive_in_the_prose():
    """The citation rule the agent is held to is only worth anything if the
    link is still a link in the file the user keeps."""
    prose = next(b for b in report_document(title="R", body=BODY).blocks
                 if isinstance(b, ProseBlock))

    assert 'href="https://ziraatkatilim.com.tr/konut"' in prose.html
    assert "Ziraat konut sayfası" in prose.html


def test_headings_lists_emphasis_and_the_arrow_all_survive():
    blocks = report_document(title="R", body=BODY).blocks
    html = "".join(b.html for b in blocks if isinstance(b, ProseBlock))

    assert "<h2>" in html
    assert "<li>" in html
    assert "<strong>" in html
    # The formatting rule in `agents/main/prompt.py` is a literal Unicode arrow.
    assert "→" in html


def test_citations_are_deduplicated_and_keep_their_first_order():
    document = report_document(
        title="R",
        body="Metin.",
        citations=[
            {"cite_url": "https://a.tr", "title": "A bankası"},
            {"cite_url": "https://b.tr", "title": ""},
            {"cite_url": "https://a.tr", "title": "A bankası"},
            {"cite_url": "", "title": "no url"},
        ],
    )

    assert [(c.label, c.url) for c in document.citations] == [
        ("A bankası", "https://a.tr"),
        # No title, so the host stands in rather than an empty bullet.
        ("b.tr", "https://b.tr"),
    ]


def test_a_table_with_no_header_row_is_dropped_rather_than_drawn_empty():
    document = report_document(title="R", body="| --- |\n| a |\n")

    assert not [b for b in document.blocks if isinstance(b, TableBlock)]


def test_a_short_row_is_padded_rather_than_losing_the_row():
    document = report_document(
        title="R", body="| A | B |\n| --- | --- |\n| tek |\n"
    )
    table = next(b for b in document.blocks if isinstance(b, TableBlock))

    assert len(table.rows) == 1
    assert table.rows[0][0].display == "tek"
    assert table.rows[0][1].blank


def test_an_empty_body_produces_no_blocks():
    """What the router turns into a refusal rather than a title page with
    nothing under it."""
    assert report_document(title="R", body="").blocks == []


def test_raw_html_in_a_report_is_escaped_not_executed():
    """`commonmark` escapes raw HTML rather than passing it through, which is
    what makes the template's `| safe` on prose safe."""
    document = report_document(title="R", body="<script>alert(1)</script>")
    html = "".join(b.html for b in document.blocks if isinstance(b, ProseBlock))

    assert "<script>" not in html
