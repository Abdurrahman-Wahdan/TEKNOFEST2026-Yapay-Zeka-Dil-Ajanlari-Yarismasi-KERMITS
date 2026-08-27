"""A table the user is looking at, as an `ExportDocument`.

Mostly a straight transcription -- the browser has already resolved the columns,
filtered and sorted the rows and formatted every cell -- with one addition this
module owns: **the source column**.

Every row in this application carries a `cite_url`, and no table draws it as a
column. `/urunler` and `/kampanyalar` surface it as a "Kaynak" link cell,
`/compare` puts it in a hover, and a chat table often has it only as the row's
own citation. That is a reasonable screen: the URL is long, the affordance is
what matters, and a reader can hover.

A file has no hover. A rate in a spreadsheet with no source next to it is a
number nobody can check three weeks later, which is the state every one of these
exports would otherwise arrive in. So the column is appended here, once, for all
four surfaces -- not at the call sites, where three of four would forget it.

Deliberately **not** also collected into the document's citation list. That list
exists for prose, where a link is inline and a reader needs a bibliography at the
end. A table already shows every URL in a column of its own; repeating two
hundred of them under the table is noise, not provenance.
"""

from datetime import datetime
from urllib.parse import urlsplit

from agents.shared.clock import TZ

from ..schemas.export import ExportTableIn
from .document import BLANK, Cell, Column, ExportDocument, TableBlock

#: The appended source column, following `CITE_KEY` in `api/saved_tables.py` and
#: `KAYNAK_KEY` in `CompareTablesBrowser.tsx`. One key across the application, so
#: a table exported from `/compare` and the same table exported from `/urunler`
#: do not disagree about what the source column is called.
CITE_KEY = "kaynak"
CITE_LABEL = "Kaynak"


def _cite_cell(url: str, note: str) -> Cell:
    """The source cell for one row.

    `display` is the URL itself, not the "Kaynak" call-to-action the table draws.
    `UI/src/lib/cell-display.ts` already records the reason for the split: the
    affordance belongs to the UI, the datum is the URL -- and a file is read by
    whoever it was sent to, who cannot click a word that resolves to nothing.
    """
    if not url:
        return Cell(value=None, display=BLANK, type="link")
    return Cell(value=url, display=url, type="link", href=url, note=note)


def table_document(
    table: ExportTableIn, generated_at: datetime | None = None
) -> ExportDocument:
    """One table, ready for any of the four writers."""
    has_citations = any(row.cite_url for row in table.rows)

    columns = [
        Column(
            key=column.key,
            # A producer may omit the label; the key is the honest fallback and
            # is what the table itself shows in that case.
            label=column.label or column.key,
            type=column.type,
            align=column.align,
            currency=column.currency,
            decimals=(
                (column.decimals.min, column.decimals.max)
                if column.decimals is not None
                else None
            ),
        )
        for column in table.columns
    ]
    if has_citations:
        columns.append(Column(key=CITE_KEY, label=CITE_LABEL, type="link"))

    rows: list[list[Cell]] = []
    for row in table.rows:
        cells = [
            Cell(
                value=cell.value,
                # An empty `display` means the browser drew nothing there, which
                # is the dash -- not the empty string, which reads in a
                # spreadsheet as a cell somebody cleared.
                display=cell.display or BLANK,
                type=column.type,
                href=cell.href,
                note=cell.note or row.cite_note,
                tone=cell.tone,
            )
            for cell, column in zip(row.cells, table.columns, strict=True)
        ]
        if has_citations:
            cells.append(_cite_cell(row.cite_url, row.cite_note))
        rows.append(cells)

    return ExportDocument(
        # No fallback title invented here. The browser always has one -- it is
        # drawing a heading above the table -- and a Turkish default string
        # hard-coded in Python is a translation living outside `messages/tr.json`.
        title=table.title,
        subtitle=table.subtitle,
        generated_at=generated_at or datetime.now(TZ),
        blocks=[
            TableBlock(
                columns=columns, rows=rows, title=table.title, note=table.note
            )
        ],
    )


def source_label(url: str) -> str:
    """A URL as something a person can read -- its host, without `www.`.

    Used by the report path for its bibliography. Kept here because it is the
    same question this module answers about a `cite_url`, and two copies would
    render the same source under two different names in two formats.
    """
    host = urlsplit(url).netloc
    return host[4:] if host.startswith("www.") else host or url
