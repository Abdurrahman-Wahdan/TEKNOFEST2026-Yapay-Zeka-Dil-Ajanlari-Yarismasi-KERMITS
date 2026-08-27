"""Each format is actually that format, and keeps what only it can keep.

These assert the things that are invisible until somebody opens the file on
another machine: the delimiter Excel expects on a Turkish system, whether a rate
is still a number, whether Turkish letters survived, whether page two of a long
table still has its headers.
"""

import re
import shutil
import zipfile
from datetime import datetime

import pytest

from api.export import WRITERS, render_html, table_document
from api.export.writers.csv_writer import DELIMITER
from api.export.writers.xlsx_writer import _number_format, _sheet_name
from api.export.document import Column
from api.schemas.export import ExportTableIn

pytestmark = pytest.mark.unit

MOMENT = datetime(2026, 8, 27, 14, 32)


def _document(rows=None, columns=None, title="Konut Finansmanı Karşılaştırması"):
    return table_document(
        ExportTableIn.model_validate(
            {
                "title": title,
                "columns": columns
                or [
                    {"key": "banka", "label": "Banka", "type": "bank"},
                    {
                        "key": "oran",
                        "label": "Kâr Oranı",
                        "type": "percent",
                        "align": "right",
                    },
                    {
                        "key": "taksit",
                        "label": "Taksit",
                        "type": "money",
                        "currency": "TRY",
                        "align": "right",
                    },
                    {"key": "tarih", "label": "Güncelleme", "type": "date"},
                ],
                "rows": rows
                or [
                    {
                        "cells": [
                            {"value": "ziraat", "display": "Ziraat Katılım"},
                            {"value": 2.89, "display": "%2,89"},
                            {"value": 41234.56, "display": "₺41.234,56"},
                            {"value": "2026-08-20", "display": "20 Ağustos 2026"},
                        ],
                        "cite_url": "https://ziraatkatilim.com.tr/konut",
                    },
                    {
                        "cells": [
                            {"value": "vakif", "display": "Vakıf Katılım"},
                            {"value": 3.05, "display": "%3,05"},
                            {"value": None, "display": ""},
                            {"value": "2026-07-01", "display": "1 Temmuz 2026"},
                        ],
                        "cite_url": "https://vakifkatilim.com.tr",
                    },
                ],
            }
        ),
        generated_at=MOMENT,
    )


# ----- CSV -----


def test_csv_opens_in_turkish_excel():
    """The BOM and the semicolon are the whole reason this is not four lines of
    browser JavaScript. Without the BOM every `ş` is mojibake; without the
    semicolon the file lands entirely in column A."""
    data = WRITERS["csv"].write(_document())

    assert data.startswith(b"\xef\xbb\xbf")
    header = data.decode("utf-8-sig").splitlines()[0]
    assert header.split(DELIMITER)[:2] == ["Banka", "Kâr Oranı"]


def test_csv_writes_what_the_screen_showed():
    text = WRITERS["csv"].write(_document()).decode("utf-8-sig")

    assert "%2,89" in text
    assert "₺41.234,56" in text


def test_csv_leaves_a_blank_cell_blank():
    """The `—` on screen is a drawing of absence. Imported into another system
    it is a character somebody has to strip out of every column."""
    row = WRITERS["csv"].write(_document()).decode("utf-8-sig").splitlines()[2]

    assert row.split(DELIMITER)[2] == ""
    assert "—" not in row


def test_csv_carries_the_source_column():
    text = WRITERS["csv"].write(_document()).decode("utf-8-sig")

    assert "Kaynak" in text.splitlines()[0]
    assert "https://ziraatkatilim.com.tr/konut" in text


# ----- XLSX -----


def _sheet_xml(data: bytes) -> str:
    with zipfile.ZipFile(__import__("io").BytesIO(data)) as book:
        return book.read("xl/worksheets/sheet1.xml").decode()


def test_xlsx_keeps_figures_as_numbers():
    """The entire justification for XLSX over a renamed CSV: a column of
    `"₺1.234,56"` strings cannot be summed, sorted or charted."""
    xml = _sheet_xml(WRITERS["xlsx"].write(_document()))

    # `t="s"` marks a shared string. A numeric cell carries no `t` at all.
    assert re.search(r'<c r="B2"[^>]*><v>2\.89</v>', xml)
    assert re.search(r'<c r="C2"[^>]*><v>41234\.56</v>', xml)
    assert 't="s"' not in re.search(r'<c r="B2"[^>]*>', xml).group(0)


def test_xlsx_writes_a_date_as_a_date():
    """So the column sorts chronologically rather than alphabetically."""
    xml = _sheet_xml(WRITERS["xlsx"].write(_document()))

    # Excel's serial for 2026-08-20; a string would be `t="s"`.
    assert re.search(r'<c r="D2"[^>]*><v>4\d{4}</v>', xml)


def test_xlsx_makes_the_source_column_clickable():
    xml = _sheet_xml(WRITERS["xlsx"].write(_document()))

    assert "<hyperlinks>" in xml
    assert xml.count("<hyperlink ") == 2


def test_xlsx_freezes_the_header_and_offers_a_filter():
    xml = _sheet_xml(WRITERS["xlsx"].write(_document()))

    assert 'state="frozen"' in xml
    assert "<autoFilter" in xml


def test_xlsx_leaves_an_absent_figure_empty():
    xml = _sheet_xml(WRITERS["xlsx"].write(_document()))
    cell = re.search(r'<c r="C3"[^>]*/>', xml)

    assert cell, "an absent money cell should be a blank cell, not a dash"


@pytest.mark.parametrize(
    "column,expected",
    [
        # Excel's `%` format code multiplies by 100. The banks already quote in
        # percent, so `0.00%` would publish every 3.29% rate as 329%.
        (Column("a", "A", type="percent"), '"%"0.00'),
        (Column("a", "A", type="money", currency="TRY"), '#,##0.00" ₺"'),
        # Gold is quoted per gram; two places would round 0,0303 g away.
        (Column("a", "A", type="money", currency="XAU"), '#,##0.0000" XAU"'),
        (Column("a", "A", type="number", decimals=(0, 4)), "#,##0.0000"),
        (Column("a", "A", type="text"), ""),
    ],
)
def test_xlsx_number_formats(column, expected):
    assert _number_format(column) == expected


def test_worksheet_names_are_legal_and_unique():
    """Excel's 31-character cap is real, and it corrupts the file rather than
    refusing when given a longer or duplicated name."""
    taken: set[str] = set()
    first = _sheet_name("Konut finansmanı karşılaştırma tablosu 2026", 0, taken)
    second = _sheet_name("Konut finansmanı karşılaştırma tablosu 2026", 1, taken)

    assert len(first) <= 31 and len(second) <= 31
    assert first != second
    assert not set("[]:*?/\\") & set(first)


# ----- PDF -----


def test_pdf_is_a_pdf_and_spells_turkish():
    data = WRITERS["pdf"].write(_document())

    assert data.startswith(b"%PDF")
    # Extracted rather than searched for in the raw bytes: PDF text is compressed
    # and encoded, so a byte-level search proves nothing either way.
    assert "Karşılaştırması" in _pdf_text(data)


def test_pdf_repeats_the_header_on_every_page():
    """Page two of a 300-row export must not be an unlabelled grid of numbers."""
    rows = [
        {
            "cells": [
                {"value": f"b{n}", "display": f"Banka {n}"},
                {"value": 2.5, "display": "%2,50"},
                {"value": 1000.0, "display": "₺1.000,00"},
                {"value": "2026-08-20", "display": "20 Ağustos 2026"},
            ]
        }
        for n in range(120)
    ]
    text = _pdf_text(WRITERS["pdf"].write(_document(rows=rows)))

    assert text.count("KÂR ORANI") > 1, "the header band did not repeat"


def _pdf_text(data: bytes) -> str:
    """The PDF's text, via poppler -- already a dependency of this repo."""
    pdftotext = shutil.which("pdftotext")
    if pdftotext is None:
        pytest.skip("poppler's pdftotext is not installed")
    import subprocess
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as workspace:
        source = Path(workspace) / "x.pdf"
        source.write_bytes(data)
        done = subprocess.run(
            [pdftotext, "-layout", str(source), "-"], capture_output=True, check=True
        )
    return done.stdout.decode("utf-8", "replace")


# ----- DOCX -----


def _docx_document_xml(data: bytes) -> str:
    with zipfile.ZipFile(__import__("io").BytesIO(data)) as book:
        return book.read("word/document.xml").decode()


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc is not installed")
def test_docx_is_a_real_word_table_not_a_picture_of_one():
    xml = _docx_document_xml(WRITERS["docx"].write(_document()))

    assert "<w:tbl>" in xml
    assert "Ziraat Katılım" in xml
    assert "<w:hyperlink" in xml


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc is not installed")
def test_docx_does_not_print_the_title_twice():
    """`<head><title>` is what pandoc turns into a `Title`-styled block, which
    collided with the `<h1>` already in the body."""
    xml = _docx_document_xml(WRITERS["docx"].write(_document()))

    assert 'w:pStyle w:val="Title"' not in xml
    assert xml.count("Karşılaştırması") == 1


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc is not installed")
def test_the_word_reference_document_carries_the_brand():
    from api.export.writers.docx_writer import ACCENT, reference_doc

    with zipfile.ZipFile(reference_doc()) as book:
        theme = book.read("word/theme/theme1.xml").decode()
        styles = book.read("word/styles.xml").decode()

    assert f'<a:srgbClr val="{ACCENT}"/>' in theme
    assert f'w:fill="{ACCENT}"' in styles


# ----- the HTML hub -----


def test_a_wide_table_asks_for_a_landscape_page():
    """22-column comparison tables exist in the pool. On portrait A4 every
    column becomes an unreadable sliver."""
    columns = [{"key": f"c{n}", "label": f"K{n}"} for n in range(12)]
    rows = [{"cells": [{"value": n, "display": str(n)} for n in range(12)]}]

    assert 'class="wide"' in render_html(_document(rows=rows, columns=columns))


def test_a_narrow_table_stays_portrait():
    assert 'class="wide"' not in render_html(_document())


def test_the_meta_line_counts_rows_for_a_table():
    assert "2 satır" in render_html(_document())


def test_the_stylesheet_is_inlined_not_linked():
    """Both consumers get a string with no base URL: a linked stylesheet would
    resolve against a directory that does not exist for either."""
    html = render_html(_document())

    assert "<style>" in html and "@page" in html
    assert "<link" not in html
