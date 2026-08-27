"""The shape every export passes through, whatever it started as.

Two things want to leave this application as a file: a table (four surfaces
produce one, all of them `ResolvedColumn[]` + `Row[]`) and a report (markdown
prose the agent wrote). Four formats want to receive it. Writing eight
converters is how CSV and XLSX end up disagreeing about what a percent is, so
both sources normalise into `ExportDocument` first and every writer reads only
that. Nothing below this line knows where the data came from.

**`Cell` carries both `value` and `display`, and that is the load-bearing
decision here.** XLSX writes `value` -- a real number, so the column can still
be summed, sorted and charted in Excel, which is the entire reason to offer XLSX
rather than a renamed CSV. PDF and DOCX write `display` -- `%2,89`, `₺1.234,56`,
Turkish decimal comma and all -- so the page reads exactly like the screen it
was exported from. Neither writer has to do the other's job, and no writer ever
reformats a number.

`display` arrives from the browser rather than being computed here. That is
deliberate and follows the rule `api/schemas/components.py` already states: the
formatting rules live in TypeScript (`UI/src/lib/cell-display.ts`), and a Python
copy of them would drift the first time one side learned a new column type. The
browser has already formatted every cell in order to draw it; the export asks
for that answer instead of guessing at it a second time.

Everything in this module is a frozen dataclass with no third-party imports, so
the two `from_*` readers and the four writers can be tested without a database,
a browser or a language model.
"""

from dataclasses import dataclass, field
from datetime import datetime

#: What a table shows for an absent value, matching `BLANK_CELL` in
#: `UI/src/lib/cell-display.ts`. A cell that is empty on screen is empty in the
#: file, rather than becoming the string "None" in one format and "" in another.
BLANK = "—"


@dataclass(frozen=True)
class Column:
    """One column heading, and how its cells behave.

    `type` is the contract's column type (`money`, `percent`, `date`, `link`,
    `bank`, `bool`, `badge`, `number`, `text`). Writers use it to pick a number
    format or an alignment; an unrecognised value is treated as `text`, the same
    forgiveness `UI/src/lib/contract.ts` extends to a producer that invents one.
    """

    key: str
    label: str
    type: str = "text"
    align: str = "left"
    #: ISO currency for `money` columns, driving the XLSX number format.
    currency: str = ""
    #: `(min, max)` decimal places for a `number` column, or None for the
    #: default. An FX rate quoted to four places is the case this exists for:
    #: rounding 47,4487 to 47 in the spreadsheet erases the comparison.
    decimals: tuple[int, int] | None = None


@dataclass(frozen=True)
class Cell:
    """One cell, twice: as a datum and as the text a reader saw.

    `href` covers both `link` cells and the citation column -- a URL is a URL,
    and every writer that can make a hyperlink should make one out of either.
    `note` is the hover text the screen carries (`cell_notes`, or the row's
    `cite_note`); `tone` is a badge's state (`ok` / `warn` / `bad` / `neutral`).
    """

    value: str | float | bool | None = None
    display: str = BLANK
    type: str = "text"
    href: str = ""
    note: str = ""
    tone: str = ""

    @property
    def blank(self) -> bool:
        return self.value is None or self.value == ""


@dataclass(frozen=True)
class TableBlock:
    """A grid. `rows` are parallel to `columns` -- one `Cell` per column, in order.

    Positional rather than keyed because every writer here iterates columns in
    order anyway, and a dict would let a row silently carry a key no column
    declares.
    """

    columns: list[Column]
    rows: list[list[Cell]]
    title: str = ""
    #: The producer's own note under the table, if it wrote one.
    note: str = ""

    def __post_init__(self) -> None:
        width = len(self.columns)
        for index, row in enumerate(self.rows):
            if len(row) != width:
                raise ValueError(
                    f"row {index} has {len(row)} cells for {width} columns"
                )


@dataclass(frozen=True)
class ProseBlock:
    """Rendered markdown, already HTML.

    Held as HTML rather than as markdown because HTML is the hub every document
    writer reads from: WeasyPrint styles it with the same stylesheet the tables
    use, and pandoc converts it to DOCX. Keeping markdown here would mean each
    writer parsing it again, differently.
    """

    html: str


Block = TableBlock | ProseBlock


@dataclass(frozen=True)
class Citation:
    """One source, as a numbered entry in the document's source list."""

    label: str
    url: str


@dataclass(frozen=True)
class ExportDocument:
    """One file's worth of content, before anyone has chosen a format."""

    title: str
    blocks: list[Block]
    subtitle: str = ""
    generated_at: datetime | None = None
    citations: list[Citation] = field(default_factory=list)

    @property
    def tables(self) -> list[TableBlock]:
        """Just the grids -- what CSV and XLSX can represent."""
        return [b for b in self.blocks if isinstance(b, TableBlock)]
