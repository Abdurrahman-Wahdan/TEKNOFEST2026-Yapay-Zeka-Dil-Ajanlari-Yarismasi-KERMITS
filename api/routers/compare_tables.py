"""The comparison-table pool — `_tables/*.json`, built offline by
`dataprep.compare` (an agent that traverses every bank's site and synthesizes
a cross-bank table per comparable product/campaign). Static content, not a
live bank endpoint: refreshed by re-running that pipeline, not by this router.

Two pages read this: Ürünler (category=ürün) and Kampanyalar (category=kampanya)
-- the fixed two-value split `dataprep/compare/synth.py` enforces on every
table it creates.

Reading and normalising the files is `api/compare_tables_pool.py`; this router
only decides what the wire shape is. The split matters because the normalising
is the part with judgement in it -- which column answers "does this bank offer
it", which of two disagreeing validity records to believe -- and none of that
should be buried in a response builder.
"""

import logging

from fastapi import APIRouter, HTTPException, Path, Query, status

from .. import compare_tables_pool as pool
from .. import table_overviews as overviews
from ..db.models import TableOverview
from ..deps import DbSession
from ..schemas.compare_tables import (
    ColumnOut,
    RankedBankOut,
    RowOut,
    TableDetailOut,
    TableListOut,
    TableOverviewOut,
    TableOverviewRequest,
    TableOverviewStarted,
    TableOverviewState,
    TableSummaryOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/compare-tables", tags=["compare-tables"])

# The two derived columns, in the same hardcoded Turkish as `Banka` — this data
# domain has no English side to translate from.
#
# `Geçerlilik` deliberately reuses the name of the producer's own column, which
# `compare_tables_pool` drops: it holds the same idea, computed from the source
# dates the producer was summarising rather than from its summary of them. The
# reader sees one validity column either way, and it is now the one that agrees
# with the citations underneath it.
VALIDITY_KEY = "Geçerlilik"
VALID_TO_KEY = "Bitiş"

# Which of `Pill`'s tones each verdict earns. Sent as data because only this
# layer knows that `aktif` is the good one — `ProducedTable` draws badges for
# every producer in the app and could not work that out from the string.
VALIDITY_TONE = {
    pool.ACTIVE: "ok",
    pool.EXPIRED: "bad",
    pool.SCHEDULED: "warn",
    pool.UNKNOWN: "neutral",
}


@router.get("", response_model=TableListOut)
def list_tables(
    category: str = Query(description="'ürün' or 'kampanya'."),
) -> TableListOut:
    """Every table in one category, for the browse-by-subcategory picker."""
    if category not in pool.CATEGORIES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown category {category!r}. Valid: {', '.join(sorted(pool.CATEGORIES))}.",
        )
    matched = [t for t in pool.all_tables() if t.get("category") == category]
    subcats = sorted({t.get("subcategory", "") for t in matched if t.get("subcategory")})
    return TableListOut(
        category=category,
        subcategories=subcats,
        tables=[
            TableSummaryOut(
                id=t["id"], topic=t["topic"], docstring=t["docstring"],
                category=t.get("category", ""), subcategory=t.get("subcategory", ""),
            )
            for t in matched
        ],
    )


@router.get("/{table_id}", response_model=TableDetailOut)
def get_table(
    table_id: str = Path(description="An id from GET /api/compare-tables."),
) -> TableDetailOut:
    """One table, shaped for `<ProducedTable />`.

    Column order is: a `Banka` column first (bank keys the frontend already
    knows how to render), then every column the table itself declared in the
    order the producer chose, then the two derived validity columns.

    The validity pair is split rather than shown as one range string because the
    two halves are read differently: the verdict is a `badge`, which
    `resolveTable` makes filterable, and the end date is a `date`, which it makes
    sortable. One combined column would be neither — and filtering by whether an
    offer is still live, and sorting by when it ends, are the two things this
    data is for.
    """
    try:
        table = pool.load_table(table_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if table is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No table {table_id!r}.")

    urls = pool.url_records()
    now = pool.today()
    status_column = pool.status_column(table)

    # The producer's columns, minus the two the pool answers better: its own
    # validity summary, and a status column that says nothing but the verdict
    # already carried on the row. `pool.status_column` only nominates a column
    # that holds nothing else, so dropping it never takes content with it.
    hidden = {pool.PRODUCER_VALIDITY_COLUMN, status_column}
    declared = [c for c in table.get("columns", []) if c not in hidden]

    rows: list[RowOut] = []
    for bank, values in table.get("rows", {}).items():
        sources = table.get("sources", {}).get(bank) or []
        cite = sources[0] if sources else {}
        valid_from, valid_to, verdict = pool.validity(sources, urls, now)

        cells: dict[str, str | float | bool | None] = {"Banka": pool.BANK_KEY.get(bank, bank)}
        cells.update({c: pool.cell(values.get(c)) for c in declared})
        cells[VALIDITY_KEY] = verdict
        cells[VALID_TO_KEY] = valid_to

        note = pool.window_note(valid_from, valid_to)
        rows.append(
            RowOut(
                cells=cells,
                cite_url=cite.get("url") or None,
                cite_note=cite.get("note") or None,
                offers=pool.offers(values, status_column),
                cell_notes={VALIDITY_KEY: note} if note else {},
                cell_tones={VALIDITY_KEY: VALIDITY_TONE[verdict]},
            )
        )

    # Only the rows the page will actually draw. The ones `offers=False` sends to
    # its own card carry no columns of their own, so an end date only they have
    # is not a reason to give every other row an empty one.
    visible = [r for r in rows if r.offers is not False]

    # A derived column earns its place by carrying information, not by existing.
    # Across the pool, 53% of tables have no end date on any visible row and 48%
    # have the same verdict on all of them — drawn anyway, that is a column of
    # dashes and a filter with one option, on tables already 23 columns wide.
    #
    # "Carries information" is not the same as "varies", which is why the second
    # test names `UNKNOWN` rather than counting distinct values: a table where
    # every bank is `aktif` says something worth reading, and one where every
    # bank is `bilinmiyor` says only that nobody published a date.
    derived = []
    if any(r.cells[VALIDITY_KEY] != pool.UNKNOWN for r in visible):
        derived.append(ColumnOut(key=VALIDITY_KEY, label=VALIDITY_KEY, type="badge"))
    if any(r.cells[VALID_TO_KEY] is not None for r in visible):
        derived.append(ColumnOut(key=VALID_TO_KEY, label=VALID_TO_KEY, type="date"))

    columns = (
        [ColumnOut(key="Banka", label="Banka", type="bank")]
        + [ColumnOut(key=c, label=c) for c in declared]
        + derived
    )

    return TableDetailOut(
        id=table["id"], title=table["topic"], subtitle=table.get("docstring", ""),
        columns=columns, rows=rows,
    )


def _overview_out(row: TableOverview) -> TableOverviewOut:
    """A stored overview on the wire. `body` is the agent's validated output."""
    body = row.body or {}
    return TableOverviewOut(
        table_id=row.table_id,
        locale=row.locale,
        summary=body.get("summary", ""),
        recommended=[RankedBankOut(**r) for r in body.get("recommended", [])],
        not_recommended=[RankedBankOut(**r) for r in body.get("not_recommended", [])],
        caveat=body.get("caveat", ""),
        generated_at=row.updated_at,
        model=row.model,
    )


@router.get("/{table_id}/overview", response_model=TableOverviewState)
def get_overview(
    session: DbSession,
    table_id: str = Path(description="An id from GET /api/compare-tables."),
    locale: str = Query(default="tr", description="'tr' or 'en'."),
) -> TableOverviewState:
    """The overview, or what is happening instead.

    Deliberately not "generate one if it is missing". Writing an overview needs
    the page as the browser sees it — an outline and a screenshot this endpoint
    has no way to produce — so the client asks here first and only pays for a
    capture when the answer is `missing`. A GET that quietly cost a
    vision-model call would also be a GET that is not safe to retry.

    `generating` is the reason this returns a wrapper instead of a 404. A model
    reading a screenshot takes a minute on a quiet host and several on a busy
    one, and a client that cannot tell "still working" from "nothing here" has
    to guess a timeout — which is either too short for the busy host or leaves
    a card spinning at a dead one.
    """
    detail = get_table(table_id)
    locale = overviews.normalise_locale(locale)
    digest = overviews.source_hash(detail.model_dump(mode="json"))

    row = overviews.cached(session, table_id, locale, digest)
    if row is not None:
        return TableOverviewState(status="ready", overview=_overview_out(row))
    if overviews.running(table_id, locale):
        return TableOverviewState(status="generating")
    return TableOverviewState(status="missing")


@router.post(
    "/{table_id}/overview",
    response_model=TableOverviewStarted,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_overview(
    body: TableOverviewRequest,
    table_id: str = Path(description="An id from GET /api/compare-tables."),
) -> TableOverviewStarted:
    """Start writing the overview for one table. Poll the GET for the result.

    **Accepted, not written.** A generation takes 70-120 seconds, which is
    longer than the things between this and the browser are willing to hold a
    socket open -- the dev server's `/api` proxy cuts it at 30. So the work
    starts in a thread and the client polls the GET it already calls on
    arrival, which is the same row every later visitor will read.

    The table the agent reads is `get_table`'s own output, not the payload the
    caller sent: the browser supplies what it *saw* -- the outline and the
    screenshot -- and the figures come from the same function that rendered
    them, so nothing on the wire can talk the agent into summarising a table
    the server does not have.

    No authentication, like the rest of this router: the pool is public data.
    The cost of that is bounded by the cache, which answers every repeat for
    free, and by the single-flight lock, which collapses a stampede on one
    table into one call. If it ever needs a gate, it needs a rate limit rather
    than a login.
    """
    detail = get_table(table_id)
    if not (body.page.text or "").strip():
        # Without the outline there is nothing to summarise: the screenshot
        # alone would have the agent reading rates off an image, and this app
        # does not guess at a rate.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "An overview needs the page outline (`page.text`).",
        )
    # How much the model is about to read. An outline that is suddenly much
    # shorter than usual means the page changed shape and the reader is now
    # summarising less than it can see.
    logger.info(
        "Overview requested table=%s locale=%s outline=%d chars",
        table_id,
        body.locale,
        len(body.page.text or ""),
    )
    started = overviews.start(
        table=detail.model_dump(mode="json"),
        table_id=table_id,
        locale=body.locale,
        page_text=body.page.text,
    )
    return TableOverviewStarted(status="generating" if started else "already_running")
