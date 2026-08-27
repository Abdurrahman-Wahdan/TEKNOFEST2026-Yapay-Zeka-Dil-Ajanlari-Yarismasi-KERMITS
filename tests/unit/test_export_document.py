"""A table on screen becomes an `ExportDocument` without losing anything.

The two things worth pinning here are the ones a reader of the file would
notice and could not fix: the citation column nothing on screen draws, and the
distinction between a cell's datum and the text the user saw. Everything else in
this path is transcription.
"""

import pytest

from api.export.document import BLANK, TableBlock
from api.export.from_table import CITE_KEY, CITE_LABEL, source_label, table_document
from api.schemas.export import ExportTableIn

pytestmark = pytest.mark.unit


def _table(**overrides) -> ExportTableIn:
    payload = {
        "title": "Konut Finansmanı",
        "columns": [
            {"key": "banka", "label": "Banka", "type": "bank"},
            {"key": "oran", "label": "Kâr Oranı", "type": "percent", "align": "right"},
        ],
        "rows": [
            {
                "cells": [
                    {"value": "ziraat", "display": "Ziraat Katılım"},
                    {"value": 2.89, "display": "%2,89"},
                ],
                "cite_url": "https://www.ziraatkatilim.com.tr/konut",
                "cite_note": "Resmî oran tablosu",
            }
        ],
    }
    payload.update(overrides)
    return ExportTableIn.model_validate(payload)


def test_a_cell_keeps_its_datum_and_the_text_the_screen_showed():
    """The whole reason XLSX is worth having over a renamed CSV."""
    table = table_document(_table()).tables[0]
    rate = table.rows[0][1]

    assert rate.value == 2.89
    assert rate.display == "%2,89"


def test_the_source_url_becomes_a_column_no_screen_draws():
    table = table_document(_table()).tables[0]

    assert [column.key for column in table.columns][-1] == CITE_KEY
    assert table.columns[-1].label == CITE_LABEL
    assert table.rows[0][-1].href == "https://www.ziraatkatilim.com.tr/konut"
    # The URL, not the "Kaynak" call-to-action the table renders: whoever opens
    # the file cannot click a word that resolves to nothing.
    assert table.rows[0][-1].display == "https://www.ziraatkatilim.com.tr/konut"


def test_no_source_column_when_nothing_is_cited():
    """An uncited table gets no empty column tacked onto it."""
    rows = [{"cells": [{"value": "x", "display": "x"}, {"value": 1, "display": "1"}]}]
    table = table_document(_table(rows=rows)).tables[0]

    assert CITE_KEY not in [column.key for column in table.columns]
    assert len(table.rows[0]) == 2


def test_a_row_missing_its_citation_still_gets_a_cell():
    """Otherwise the row would be one cell short of its own header."""
    rows = [
        {
            "cells": [{"value": "a", "display": "a"}, {"value": 1, "display": "1"}],
            "cite_url": "https://example.tr",
        },
        {"cells": [{"value": "b", "display": "b"}, {"value": 2, "display": "2"}]},
    ]
    table = table_document(_table(rows=rows)).tables[0]

    assert len(table.rows[1]) == len(table.columns)
    assert table.rows[1][-1].display == BLANK
    assert table.rows[1][-1].blank


def test_an_empty_display_becomes_the_dash_the_table_draws():
    rows = [{"cells": [{"value": None, "display": ""}, {"value": 1, "display": "1"}]}]
    table = table_document(_table(rows=rows)).tables[0]

    assert table.rows[0][0].display == BLANK


def test_a_column_with_no_label_falls_back_to_its_key():
    columns = [{"key": "banka"}, {"key": "oran", "type": "percent"}]
    rows = [{"cells": [{"value": "a", "display": "a"}, {"value": 1, "display": "1"}]}]
    table = table_document(_table(columns=columns, rows=rows)).tables[0]

    assert table.columns[0].label == "banka"


def test_the_row_note_covers_a_cell_that_has_none():
    """`cite_note` is about the row, so it applies to every cell without its own."""
    table = table_document(_table()).tables[0]

    assert table.rows[0][0].note == "Resmî oran tablosu"


def test_a_row_of_the_wrong_width_is_refused_at_the_schema():
    """Caught on the wire rather than producing a ragged file."""
    with pytest.raises(ValueError, match="cells for 2 columns"):
        _table(rows=[{"cells": [{"value": "only one", "display": "only one"}]}])


def test_the_block_refuses_a_ragged_row_too():
    """Defence at the shape as well as at the schema: nothing else builds a
    `TableBlock`, but nothing else should be able to build a broken one."""
    from api.export.document import Cell, Column

    with pytest.raises(ValueError, match="row 0 has 1 cells"):
        TableBlock(columns=[Column("a", "A"), Column("b", "B")], rows=[[Cell()]])


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.ziraatkatilim.com.tr/konut", "ziraatkatilim.com.tr"),
        ("https://vakifkatilim.com.tr", "vakifkatilim.com.tr"),
        ("not a url", "not a url"),
    ],
)
def test_a_url_reads_as_its_host(url, expected):
    assert source_label(url) == expected
