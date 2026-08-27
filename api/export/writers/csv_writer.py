"""CSV, for Excel on a Turkish machine.

Two details decide whether this file opens correctly or arrives as one mangled
column, and both are the reason CSV is generated here rather than in four lines
of browser JavaScript.

**The delimiter is `;`, not `,`.** Excel does not read a delimiter from the
file; it uses the list separator from the operating system's regional settings,
and on a Turkish system that is `;` because `,` is already the decimal mark.
A comma-delimited file double-clicked on a Turkish Windows machine lands entirely
in column A.

**The file starts with a UTF-8 BOM.** Without it Excel decodes the bytes as the
system's legacy code page, and every `ş`, `ğ` and `ı` in a Turkish bank name
arrives as mojibake. The BOM is the only signal Excel honours here.

**Cells carry `display`, not `value`.** CSV has no types, so there is nothing to
preserve by writing the raw number -- and `2.89` written canonically is read by
Turkish Excel as two thousand eight hundred and ninety. Writing what the screen
showed means the file always opens as the user expects. Anyone who wants the
figures as arithmetic wants XLSX, which is the next button along in the same
dialog and does keep them typed.
"""

import csv
import io
from codecs import BOM_UTF8

from ..document import ExportDocument

MEDIA_TYPE = "text/csv; charset=utf-8"
EXTENSION = "csv"

DELIMITER = ";"
#: CRLF, which is what Excel writes and what every tool reading CSV accepts.
LINE_ENDING = "\r\n"


def write_csv(document: ExportDocument) -> bytes:
    """Every table in the document, one after another."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(
        buffer,
        delimiter=DELIMITER,
        quoting=csv.QUOTE_MINIMAL,
        lineterminator=LINE_ENDING,
    )

    for index, table in enumerate(document.tables):
        if index:
            # A separating blank line, then the next table's own name. Only
            # reached by a document carrying more than one table; a table export
            # carries exactly one.
            writer.writerow([])
        if len(document.tables) > 1 and table.title:
            writer.writerow([table.title])

        writer.writerow([column.label for column in table.columns])
        for row in table.rows:
            # A blank cell is left blank. The `—` on screen is a *drawing* of
            # absence; imported into another system it is a character somebody
            # then has to strip out of every column.
            writer.writerow(["" if cell.blank else cell.display for cell in row])

    return BOM_UTF8 + buffer.getvalue().encode("utf-8")
