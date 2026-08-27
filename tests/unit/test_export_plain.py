"""No exported file carries a decorative pictograph, in any of the four formats.

The agent writes its own headings, and a language model decorates them: the
report that prompted this arrived as `## 📊 Genel Karşılaştırma Özeti`. A picture
in a heading is fine on a screen and wrong in a file somebody forwards to a
credit committee, so it is removed on the way out.

**The hard part is what must survive**, and each of these is asserted below
because each would be silently eaten by a looser rule:

- `→`, which `agents/main/prompt.py` mandates for menu paths.
- `✓` and `✕`, which are how a `bool` cell is drawn
  (`UI/src/lib/cell-display.ts`) -- strip them and every yes/no column in the
  spreadsheet goes blank.
- `₺`, `—` and `·`, the currency sign, the blank cell and the meta separator.
- `©`, `®`, `™`, `✔`, `☑` and `✖`, which are `Emoji=Yes` in Unicode but are read
  as text: a legal mark in a bank's name, or the model's way of writing "yes".
"""

import shutil
import zipfile

import pytest

from api.export import WRITERS, render_html, report_document, table_document
from api.export.document import ProseBlock
from api.export.plain import without_emoji
from api.schemas.export import ExportTableIn

pytestmark = pytest.mark.unit

#: Every way a pictograph can be composed, and it has to be gone in all of them.
#: A rule that only knows about single code points leaves half of these behind as
#: an invisible joiner or a stray variation selector.
COMPOSED = {
    "📊": "a plain pictograph",
    "⚠️": "a text symbol forced to emoji by U+FE0F",
    "⚠": "the same symbol with no variation selector",
    "🇹🇷": "a flag, which is two regional indicators",
    "👨‍👩‍👧": "a ZWJ sequence",
    "🧑🏽": "a skin-tone modifier",
    "✅": "a pictograph that happens to mean yes",
}

#: Characters that are data or typography, not decoration.
KEPT = "→✓✕₺—·%©®™✔☑✖İıŞşĞğÜüÖöÇç«»…–"


def test_a_decorated_heading_keeps_the_heading_and_loses_the_picture():
    """The exact case from the report in the bug: the words are the content, the
    bar chart is not, and the heading must not be left starting with a space."""
    document = report_document(
        title="Sukuk Güncel Bilgi Takibi",
        body="## 📊 Genel Karşılaştırma Özeti\n\nMetin.\n",
    )
    prose = next(b for b in document.blocks if isinstance(b, ProseBlock))

    assert "<h2>Genel Karşılaştırma Özeti</h2>" in prose.html


def test_a_decorated_title_loses_the_picture_too():
    """The title becomes the filename and the first line of the page."""
    document = report_document(title="📊 Sukuk Takibi", body="Metin.\n")

    assert document.title == "Sukuk Takibi"


@pytest.mark.parametrize("emoji", list(COMPOSED), ids=list(COMPOSED.values()))
def test_a_pictograph_is_gone_however_it_was_composed(emoji):
    assert without_emoji(f"Oran {emoji} yükseldi") == "Oran yükseldi"


@pytest.mark.parametrize("char", list(KEPT), ids=[f"U+{ord(c):04X}" for c in KEPT])
def test_the_symbols_that_are_data_survive(char):
    """Each of these is either a datum, a unit or typography. Losing any one is
    worse than the emoji, because a reader cannot tell it was ever there."""
    assert without_emoji(f"a{char}b") == f"a{char}b"


def test_a_menu_path_is_untouched():
    """`prompt.py` mandates this arrow, so it appears in most reports."""
    path = "Mobil Şube → İnternet Şube → TradePlus"

    assert without_emoji(path) == path


def test_a_pictograph_between_two_words_leaves_exactly_one_space():
    assert without_emoji("Oran 📊 yükseldi") == "Oran yükseldi"


def test_a_pictograph_at_the_edge_leaves_no_space_behind():
    """Otherwise every decorated heading in the file starts with a blank."""
    assert without_emoji("📊 Genel Özet") == "Genel Özet"
    assert without_emoji("Genel Özet 📊") == "Genel Özet"


def test_a_list_marker_keeps_its_indentation():
    """The strip runs over markdown, where leading spaces are structure: eat the
    indent and a nested bullet becomes a top-level one."""
    assert without_emoji("  - 📊 Kâr oranı") == "  - Kâr oranı"


def _decorated_table():
    return table_document(
        ExportTableIn.model_validate(
            {
                "title": "📊 Sukuk Karşılaştırması",
                "columns": [
                    {"key": "banka", "label": "🏦 Banka", "type": "bank"},
                    {"key": "borsa", "label": "Borsa", "type": "bool"},
                ],
                "rows": [
                    {
                        "cells": [
                            {"value": "kuveyt", "display": "✅ Kuveyt Türk"},
                            {"value": True, "display": "✓"},
                        ],
                        "cite_url": "https://kuveytturk.com.tr",
                    }
                ],
            }
        )
    )


def test_a_table_loses_emoji_from_its_title_headers_and_cells():
    document = _decorated_table()
    table = document.tables[0]

    assert document.title == "Sukuk Karşılaştırması"
    assert table.columns[0].label == "Banka"
    assert table.rows[0][0].display == "Kuveyt Türk"


def test_a_bool_cell_still_says_yes():
    """The guard against the fix being worse than the bug."""
    assert _decorated_table().tables[0].rows[0][1].display == "✓"


@pytest.mark.parametrize("fmt", ["csv", "xlsx"])
def test_no_spreadsheet_format_emits_a_pictograph(fmt):
    payload = WRITERS[fmt].write(_decorated_table())
    text = payload.decode("utf-8", "replace") if fmt == "csv" else _xlsx_text(payload)

    assert "📊" not in text and "🏦" not in text and "✅" not in text
    assert "Kuveyt Türk" in text


def _xlsx_text(payload: bytes) -> str:
    import io

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return "".join(
            archive.read(name).decode("utf-8", "replace")
            for name in archive.namelist()
            if name.endswith(".xml")
        )


def test_the_html_hub_carries_no_pictograph():
    """PDF and DOCX are both rendered from this string, so an emoji absent here
    is absent from both files."""
    html = render_html(
        report_document(
            title="📊 Rapor", body="## 🔍 Banka Bazlı Detaylar\n\n- ✅ Uygun\n"
        )
    )

    for emoji in ("📊", "🔍", "✅"):
        assert emoji not in html
    assert "Banka Bazlı Detaylar" in html


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc is not installed")
def test_docx_carries_no_pictograph():
    payload = WRITERS["docx"].write(
        report_document(title="📊 Rapor", body="## 🔍 Detaylar\n\nMetin.\n")
    )
    import io

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        text = archive.read("word/document.xml").decode("utf-8")

    assert "📊" not in text and "🔍" not in text
    assert "Detaylar" in text
