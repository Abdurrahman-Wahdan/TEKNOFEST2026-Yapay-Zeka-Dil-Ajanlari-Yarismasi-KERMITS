"""The comparison-table pool on the wire — `data/_tables/*.json`, produced by
`dataprep.compare` (the traversal agent, not a live bank endpoint).

Two shapes: a lightweight summary for the "pick a table" browsing UI (category
-> subcategory -> table), and a detail shape that matches `TableProps` on the
frontend exactly, so a table here renders through the SAME `TableWidget` an
AI-produced topic-page table already uses — no second table renderer.
"""

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
