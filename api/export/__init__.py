"""Turning what is on screen into a file.

Five surfaces in this application produce something a user wants to keep -- the
live board on `/compare`, the comparison-table pool behind `/urunler` and
`/kampanyalar`, the AI-saved tables on `/ai-overview`, tables inside chat
answers, and automation reports -- and until now none of it could leave the
browser.

The shape of the solution is one pivot and two fan-outs:

    TableProps  ─┐                      ┌─ csv    (display text, `;`, BOM)
                 ├─ ExportDocument ─────┼─ xlsx   (typed values + number formats)
    report md ──┘         │             ├─ pdf    ─┐
                          └── HTML ─────┴─ docx   ─┘ one stylesheet, both

`ExportDocument` (`document.py`) is the pivot: both sources normalise into it and
every writer reads only it, so CSV and XLSX cannot end up disagreeing about what
a percent is. HTML (`html.py`) is the second pivot, for the two document formats
only -- WeasyPrint styles it, pandoc converts it, and neither can drift from the
other because there is one description of the page.

**Markdown is an inlet, never the hub.** It is untyped: a `money` cell rendered
to markdown is the string `₺1.234,56`, and a spreadsheet column of strings
cannot be summed, which is the entire reason XLSX exists rather than a renamed
CSV.

**Nothing here truncates.** No row cap, no cell-length cap, no "showing 25 of
30". The one clipped value in the package is a worksheet name, because Excel's
31-character limit is real and a sheet name is an identifier rather than
content.
"""

from .document import ExportDocument
from .errors import ExportEmpty, ExportUnavailable
from .from_report import report_document
from .from_table import table_document
from .html import render_html
from .writers import WRITERS

__all__ = [
    "ExportDocument",
    "ExportEmpty",
    "ExportUnavailable",
    "WRITERS",
    "render_html",
    "report_document",
    "table_document",
]
