"""The export request on the wire.

Two sources, and they travel differently on purpose.

A **table** travels whole, in the request body. It has to: the `/compare` board
is assembled in the browser from live bank endpoints and has no server-side
identity, so there is no id to send. Sending the table also settles the scope
question for free -- the browser puts either the filtered, sorted, visible rows
or the entire table into `table`, and the server never has to know which, or
re-derive a filter it cannot see.

A **report** travels as an id. The server already holds it, so uploading its
body would only create a way for the exported file to disagree with the stored
one.

Cells arrive pre-formatted (`display`) alongside their raw `value`. See the
module docstring of `api/export/document.py` for why the browser is the one that
formats: the rules live in `UI/src/lib/cell-display.ts` and a Python copy would
drift.

**Nothing here caps anything.** No row limit, no cell-length limit. A user who
exports a 4,000-row table gets 4,000 rows.
"""

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

#: The formats a table can become. A report is prose, so it gets a subset --
#: see `ExportRequest` below.
TableFormat = Literal["csv", "xlsx", "pdf", "docx"]
DOCUMENT_FORMATS = frozenset({"pdf", "docx"})


class ExportCellIn(BaseModel):
    """One cell: the datum, and the text the screen showed for it."""

    value: str | float | bool | None = Field(
        default=None,
        description="The raw datum. XLSX writes this, so the column stays arithmetic.",
    )
    display: str = Field(
        default="",
        description=(
            "What the table drew, already formatted by `cellDisplayText`. PDF and "
            "DOCX write this, so the page reads exactly like the screen."
        ),
    )
    href: str = Field(default="", description="Link cells, and the source column.")
    note: str = Field(default="", description="The cell's hover note, if it has one.")
    tone: str = Field(
        default="",
        description="A badge cell's state: neutral | ok | warn | bad.",
    )


class ExportRowIn(BaseModel):
    """One row. `cells` is parallel to `columns` -- one entry each, in order."""

    cells: list[ExportCellIn]
    cite_url: str = Field(
        default="",
        description=(
            "Where this row came from. Becomes the appended source column: a rate "
            "without its source is a number nobody can check."
        ),
    )
    cite_note: str = Field(default="", description="Why that source supports the row.")


class Decimals(BaseModel):
    """A `number` column's precision, as `ResolvedColumn.decimals` resolved it."""

    min: int = 0
    max: int = 0


class ExportColumnIn(BaseModel):
    key: str
    label: str = ""
    type: str = Field(
        default="text",
        description="The contract's column type. Unknown values are treated as text.",
    )
    align: Literal["left", "center", "right"] = "left"
    currency: str = Field(default="", description="ISO code, for `money` columns.")
    decimals: Decimals | None = Field(
        default=None,
        description=(
            "Decimal places for a `number` column. Sent because Excel needs a "
            "number format, and an FX rate rounded to whole lira erases the very "
            "comparison the column exists for."
        ),
    )


class ExportTableIn(BaseModel):
    """A table as the user is looking at it."""

    title: str = ""
    subtitle: str = ""
    note: str = ""
    columns: list[ExportColumnIn]
    rows: list[ExportRowIn]

    @model_validator(mode="after")
    def _rows_match_columns(self) -> "ExportTableIn":
        width = len(self.columns)
        for index, row in enumerate(self.rows):
            if len(row.cells) != width:
                raise ValueError(
                    f"row {index} carries {len(row.cells)} cells for {width} columns"
                )
        return self


class TableSource(BaseModel):
    kind: Literal["table"] = "table"
    table: ExportTableIn


class ReportSource(BaseModel):
    kind: Literal["report"] = "report"
    report_id: uuid.UUID


Source = Annotated[TableSource | ReportSource, Field(discriminator="kind")]


class ExportRequest(BaseModel):
    """What to export, and as what."""

    format: TableFormat
    source: Source

    @model_validator(mode="after")
    def _format_suits_source(self) -> "ExportRequest":
        """A report is prose; a CSV of prose is a file nobody can open usefully.

        Refused here rather than in the router so the rule is visible in the
        OpenAPI schema and the frontend's generated types, and so the refusal is
        a 422 with a reason instead of a download that turns out to be useless.
        """
        if self.source.kind == "report" and self.format not in DOCUMENT_FORMATS:
            raise ValueError(
                "A report contains prose as well as tables, so it can only be "
                "exported as PDF or DOCX. Export a table from the report's own "
                "table controls to get CSV or XLSX."
            )
        return self
