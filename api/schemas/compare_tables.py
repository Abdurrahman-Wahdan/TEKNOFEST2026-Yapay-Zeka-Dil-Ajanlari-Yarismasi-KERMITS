"""The comparison-table pool on the wire — `data/_tables/*.json`, produced by
`dataprep.compare` (the traversal agent, not a live bank endpoint).

Two shapes: a lightweight summary for the "pick a table" browsing UI (category
-> subcategory -> table), and a detail shape that matches `TableProps` on the
frontend exactly, so a table here renders through the SAME `TableWidget` an
AI-produced topic-page table already uses — no second table renderer.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class TableSummaryOut(BaseModel):
    """One table, without its rows — enough to list and pick from."""

    id: str
    topic: str
    docstring: str
    category: str = Field(description="'ürün' or 'kampanya'.")
    subcategory: str


class TableListOut(BaseModel):
    """Every table in one category, plus the subcategories present in it.

    `subcategories` is derived from the tables actually returned, not the
    global registry — a category-scoped dropdown should never offer a
    subcategory that has zero tables under this category.
    """

    category: str
    subcategories: list[str]
    tables: list[TableSummaryOut]


class ColumnOut(BaseModel):
    key: str
    label: str
    type: str | None = None


class RowOut(BaseModel):
    cells: dict[str, str | float | bool | None]
    cite_url: str | None = None
    cite_note: str | None = Field(
        default=None,
        description="The pipeline's own note on why this source supports the row — shown as a hover title on the citation link, not a second dashboard.",
    )
    offers: bool | None = Field(
        default=None,
        description=(
            "Whether this bank offers the thing at all. Read here rather than in "
            "the browser because the producer answers it in a column it names "
            "itself — 93 different names across the pool, and in some tables no "
            "column at all. `null` means nothing in the row settles it, which is "
            "not the same as `false`: an unclassified row stays in the table."
        ),
    )
    cell_notes: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Column key -> a note to hover on that one cell. Carries the full "
            "validity window behind the verdict chip, so a table already running "
            "to 22 columns does not need a second date column to show a start date."
        ),
    )
    cell_tones: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Column key -> the tone its badge cell carries ('neutral' | 'ok' | "
            "'warn' | 'bad'). Sent as data because only this layer knows which "
            "of its own values is the good one; the table renderer draws badges "
            "for every producer and cannot infer it from the string."
        ),
    )


class TableDetailOut(BaseModel):
    """Shaped exactly like the frontend's `TableProps` (`UI/src/lib/contract.ts`)
    so it can be spread straight into `<TableWidget />` with no translation
    layer on the client."""

    id: str
    title: str
    subtitle: str = ""
    notes: str = ""
    columns: list[ColumnOut]
    rows: list[RowOut]


class PageContext(BaseModel):
    """What the browser saw, for the overview agent to read.

    Text only. A screenshot used to travel here too, and it was dropped: it cost
    minutes of vision prefill per table and carried nothing the outline does not
    — once `data-outline-list` taught the outline to keep short-line cards like
    "banks that do not offer this", which is the one thing the picture had been
    covering for.
    """

    text: str | None = Field(
        default=None,
        description=(
            "The page as a semantic outline, in markdown, wrapped in a "
            "`<page-snapshot>` element. Deliberately unbounded, like every "
            "other payload on its way to a model in this app: half a page "
            "answers a question about the other half wrongly."
        ),
    )


class TableOverviewRequest(BaseModel):
    """Ask for an overview of one table, offering the page as evidence."""

    locale: str = Field(default="tr", description="'tr' or 'en'. Anything else is Turkish.")
    page: PageContext = Field(default_factory=PageContext)


class TableOverviewState(BaseModel):
    """Whether there is an overview for this table, and if not, why not.

    A wrapper rather than "200 or 404", because "nobody has asked for one" and
    "one is being written right now" are different answers and the client acts
    on each differently: the first means start one, the second means keep
    waiting. Told apart by a 404 alone, a card either gives up on a slow model
    or spins forever on a dead one.
    """

    status: Literal["ready", "generating", "missing"]
    overview: "TableOverviewOut | None" = None


class TableOverviewStarted(BaseModel):
    """The answer to "please write one": it is being written."""

    status: Literal["generating", "already_running"] = Field(
        description=(
            "`already_running` means another reader asked first and the same "
            "row will answer both. Either way the client polls the GET."
        ),
    )


class RankedBankOut(BaseModel):
    bank: str
    why: str


class TableOverviewOut(BaseModel):
    """One table, as the overview agent read it."""

    table_id: str
    locale: str
    summary: str
    recommended: list[RankedBankOut] = Field(
        default_factory=list,
        description="At most two, best first. Empty when the page supports no pick.",
    )
    not_recommended: list[RankedBankOut] = Field(
        default_factory=list,
        description="At most two: weakest terms, or banks that do not offer this.",
    )
    caveat: str = ""
    generated_at: datetime
    model: str = ""
