"""The comparison-table pool — `data/_tables/*.json`, built offline by
`dataprep.compare` (an agent that traverses every bank's site and synthesizes
a cross-bank table per comparable product/campaign). Static content, not a
live bank endpoint: refreshed by re-running that pipeline, not by this router.

Two pages read this: Ürünler (category=ürün) and Kampanyalar (category=kampanya)
-- the fixed two-value split `dataprep/compare/synth.py` enforces on every
table it creates.
"""

import glob
import json
import logging
from pathlib import Path as FilePath

from fastapi import APIRouter, HTTPException, Path, Query, status

from ..schemas.compare_tables import ColumnOut, RowOut, TableDetailOut, TableListOut, TableSummaryOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/compare-tables", tags=["compare-tables"])

ROOT = FilePath(__file__).resolve().parent.parent.parent / "data" / "_tables"
CATEGORIES = {"ürün", "kampanya"}

# data/_tables uses the crawl's site-folder slugs; the rest of the app (bank
# logos, GET /api/banks, contract.ts's KNOWN_BANKS) uses the shorter provider
# keys. Bridged here, once, rather than at every call site.
BANK_KEY = {
    "kuveytturk": "kuveytturk",
    "albaraka": "albaraka",
    "vakifkatilim": "vakif",
    "emlakkatilim": "emlak",
    "dunyakatilim": "dunya",
    "ziraatkatilim": "ziraat",
    "turkiyefinans": "turkiyefinans",
    "hayatfinans": "hayat",
    "tombank": "tom",
    "adilkatilim": "adil",
}


def _table_path(table_id: str) -> FilePath:
    """Resolve an id to its file, refusing anything outside the dir -- same
    belt-and-braces containment check `components.py` uses for its fixtures."""
    path = (ROOT / f"{table_id}.json").resolve()
    if not path.is_relative_to(ROOT.resolve()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Bad table id.")
    return path


def _load_all() -> list[dict]:
    tables = []
    for f in glob.glob(str(ROOT / "*.json")):
        if FilePath(f).name.startswith("_"):
            continue
        try:
            tables.append(json.loads(FilePath(f).read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            logger.exception("Could not read table fixture %s", f)
    return tables


@router.get("", response_model=TableListOut)
def list_tables(
    category: str = Query(description="'ürün' or 'kampanya'."),
) -> TableListOut:
    """Every table in one category, for the browse-by-subcategory picker."""
    if category not in CATEGORIES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown category {category!r}. Valid: {', '.join(sorted(CATEGORIES))}.",
        )
    matched = [t for t in _load_all() if t.get("category") == category]
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
    """One table, shaped for `<TableWidget />` -- a 'Banka' column first (bank
    keys the frontend already knows how to render), then every column the
    table itself declared, in the order the producer chose."""
    path = _table_path(table_id)
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No table {table_id!r}.")

    t = json.loads(path.read_text(encoding="utf-8"))
    columns = [ColumnOut(key="Banka", label="Banka", type="bank")] + [
        ColumnOut(key=c, label=c) for c in t.get("columns", [])
    ]

    rows: list[RowOut] = []
    for bank, values in t.get("rows", {}).items():
        sources = t.get("sources", {}).get(bank) or []
        cite_url = sources[0]["url"] if sources and sources[0].get("url") else None
        cite_note = sources[0].get("note") if sources else None
        cells: dict[str, str | float | bool | None] = {"Banka": BANK_KEY.get(bank, bank)}
        cells.update(values)
        rows.append(RowOut(cells=cells, cite_url=cite_url, cite_note=cite_note))

    return TableDetailOut(
        id=t["id"], title=t["topic"], subtitle=t.get("docstring", ""),
        columns=columns, rows=rows,
    )
