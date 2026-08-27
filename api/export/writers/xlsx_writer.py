"""XLSX -- the one format where the figures stay figures.

This is the only writer that reads `Cell.value` rather than `Cell.display`, and
that difference is the whole justification for offering XLSX at all. A
spreadsheet of `"₺1.234,56"` strings is a renamed CSV: it cannot be summed,
sorted numerically, filtered by range or charted. Written as a number carrying
an Excel *number format*, the same cell still reads `₺1.234,56` on screen and is
1234.56 to every formula pointing at it.

Excel's number formats then do the localisation, which is why nothing here
formats a number itself. `#,##0.00` renders with `.` thousands and `,` decimals
on a Turkish machine and the other way round on an English one -- the file is
correct for whoever opens it, which is more than a baked string can manage.

One trap worth naming: Excel's `%` format code *multiplies by 100*. The banks
quote rates already in percent (`3.29` meaning 3.29%, see `formatRate` in
`UI/src/lib/format.ts`), so `0.00%` would publish every rate as 329%. The
literal `"%"0.00` is used instead.
"""

import io
import re
from datetime import date, datetime

import xlsxwriter

from ..document import Cell, Column, ExportDocument, TableBlock

MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
EXTENSION = "xlsx"

#: Currency symbols for the codes this application actually quotes. A code with
#: no symbol here is written as the code itself, which is correct for the metals
#: (XAU is quoted per gram and has no symbol) and honest for anything new.
_SYMBOLS = {"TRY": "₺", "USD": "$", "EUR": "€", "GBP": "£"}

#: Excel forbids these in a sheet name, and silently corrupts the file if given
#: one rather than refusing.
_ILLEGAL_SHEET = re.compile(r"[\[\]:*?/\\]")
_SHEET_NAME_MAX = 31

#: Column widths, in Excel's character units. The floor keeps a two-letter
#: header readable; the ceiling stops one long citation URL from making a column
#: nobody can see past.
_WIDTH_MIN = 9
_WIDTH_MAX = 52


def _sheet_name(title: str, index: int, taken: set[str]) -> str:
    """A legal, unique worksheet name.

    Truncated because Excel's limit is 31 characters -- the one place in this
    feature where something is clipped, and it is clipped because it is an
    identifier rather than content. Every cell of the table goes through whole.
    """
    cleaned = _ILLEGAL_SHEET.sub(" ", title).strip() or f"Tablo {index + 1}"
    candidate = cleaned[:_SHEET_NAME_MAX]
    suffix = 2
    while candidate.casefold() in taken:
        tail = f" ({suffix})"
        candidate = cleaned[: _SHEET_NAME_MAX - len(tail)] + tail
        suffix += 1
    taken.add(candidate.casefold())
    return candidate


def _number_format(column: Column) -> str:
    """The Excel format code for a column, or "" for text."""
    if column.type == "money":
        symbol = _SYMBOLS.get(column.currency.upper(), column.currency.upper())
        # Metals are quoted per gram at four places; `formatMoney` makes the same
        # distinction and for the same reason -- 0,0303 grams of gold rounded to
        # two places loses most of the number.
        places = 4 if column.currency.upper() in ("XAU", "XAG") else 2
        digits = f"#,##0.{'0' * places}"
        return f'{digits}" {symbol}"' if symbol else digits
    if column.type == "percent":
        return '"%"0.00'
    if column.type == "number":
        if column.decimals:
            _, most = column.decimals
            return f"#,##0.{'0' * most}" if most else "#,##0"
        return "#,##0.####"
    if column.type == "date":
        return "dd.mm.yyyy"
    return ""


def _as_datetime(value: object) -> datetime | date | None:
    """An ISO date string as a real date, or None if it is not one.

    Producers send `date` cells as ISO strings (`contract.ts` infers the type
    from exactly that shape). Anything else -- a Turkish `14.08.2026`, a range,
    a word -- is left as text rather than guessed at, because a date guessed
    wrong in a spreadsheet is a date that silently sorts wrong.
    """
    if isinstance(value, (datetime, date)):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _write_cell(
    sheet, row: int, col: int, cell: Cell, column: Column, formats: dict
) -> None:
    """One cell, as the most specific Excel type it honestly is."""
    style = formats.get(column.type)

    if cell.blank:
        sheet.write_blank(row, col, None, style)
    elif cell.href:
        # `write_url` keeps the URL live and shows the display text. Used for
        # `link` cells and for the appended source column alike.
        sheet.write_url(row, col, cell.href, formats["link"], cell.display)
    elif isinstance(cell.value, bool):
        # Before the number branch: `isinstance(True, int)` is True in Python,
        # so a boolean checked second becomes the number 1.
        sheet.write_boolean(row, col, cell.value, style)
    elif column.type == "date" and (moment := _as_datetime(cell.value)) is not None:
        sheet.write_datetime(row, col, moment, formats["date"])
    elif column.type in ("money", "percent", "number") and isinstance(
        cell.value, (int, float)
    ):
        sheet.write_number(row, col, float(cell.value), style)
    else:
        # Includes a numeric column whose producer sent a string. Writing the
        # display text keeps the table faithful; coercing it would invent data.
        sheet.write_string(row, col, cell.display, style)

    if cell.note:
        sheet.write_comment(row, col, cell.note)


def _widths(table: TableBlock) -> list[int]:
    """A width per column, from the longest thing actually in it."""
    widths = []
    for index, column in enumerate(table.columns):
        longest = len(column.label)
        for row in table.rows:
            longest = max(longest, len(row[index].display))
        widths.append(max(_WIDTH_MIN, min(_WIDTH_MAX, longest + 2)))
    return widths


def write_xlsx(document: ExportDocument) -> bytes:
    """The document's tables, one worksheet each."""
    buffer = io.BytesIO()
    book = xlsxwriter.Workbook(
        buffer,
        # No temp files: this runs inside a request, and a worker that cannot
        # write to /tmp should still be able to return a spreadsheet.
        {"in_memory": True, "constant_memory": False, "remove_timezone": True},
    )
    book.set_properties({"title": document.title, "comments": document.subtitle})

    header = book.add_format(
        {
            "bold": True,
            "font_color": "#FFFFFF",
            "bg_color": "#1E9DF1",
            "align": "left",
            "valign": "vcenter",
            "text_wrap": True,
            "border": 0,
        }
    )
    link = book.add_format({"font_color": "#1E9DF1", "underline": 1})

    taken: set[str] = set()
    for index, table in enumerate(document.tables):
        sheet = book.add_worksheet(
            _sheet_name(table.title or document.title, index, taken)
        )

        formats = {"link": link}
        for column in table.columns:
            code = _number_format(column)
            formats[column.type] = book.add_format(
                {"num_format": code, "align": column.align} if code
                else {"align": column.align}
            )

        for col, column in enumerate(table.columns):
            sheet.write_string(0, col, column.label, header)

        for row_index, row in enumerate(table.rows, start=1):
            for col, (cell, column) in enumerate(zip(row, table.columns, strict=True)):
                _write_cell(sheet, row_index, col, cell, column, formats)

        for col, width in enumerate(_widths(table)):
            sheet.set_column(col, col, width)

        sheet.freeze_panes(1, 0)
        if table.rows:
            # Excel's own filter row, so the recipient can narrow the table the
            # same way the user narrowed it on screen before exporting.
            sheet.autofilter(0, 0, len(table.rows), len(table.columns) - 1)
        sheet.set_row(0, 30)

    book.close()
    return buffer.getvalue()
