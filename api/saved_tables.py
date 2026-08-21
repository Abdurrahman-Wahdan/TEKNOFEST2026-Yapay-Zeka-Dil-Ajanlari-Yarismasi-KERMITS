"""Turning a table the agent wrote into a row on the user's dashboard.

The agent hands over a header row and a matrix of strings. What has to reach the
database is `TableProps` -- the shape `UI/src/lib/contract.ts` validates and
`TableWidget` renders -- so that a saved table goes through the *same* renderer as
a produced topic-page table. There is no second table renderer, and there must
not be one.

Everything here except `save_table_view` is pure, and `save_table_view` takes its
session factory as an argument, so the whole module is testable without a database
and without a language model. That is deliberate: the model host is the one piece
of this feature nobody can reach yet.

**Nothing here truncates table data.** No row cap, no cell-length cap, no
"showing 25 of 30". A table the agent saved is the table the user sees. Only the
slug is clipped because it is an identifier; the human-readable title is not.
"""

import hashlib
import json
import logging
import re
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db.models import SavedView
from .db.session import session_scope

logger = logging.getLogger(__name__)

# The synthetic source column, following `KAYNAK_KEY` in
# `UI/src/components/widgets/CompareTablesBrowser.tsx`. A row carries its citation
# as `cite_url`, which the table renderer uses for the row key and the hover note
# but never *shows* -- the produced tables surface it by adding a column of the
# contract's `link` type, and a saved table has the same need for the same reason.
CITE_KEY = "kaynak"
CITE_LABEL = "Kaynak"

# The slug remains a bounded identifier. The title is unbounded text.
SLUG_CHARS = 80

# Turkish letters, transliterated to ASCII. Needed because the slug pattern is
# `^[a-z0-9-]{1,80}$`: "Konut finansmanı" has to become `konut-finansmani`, and a
# stripped `ı` would silently merge distinct titles.
#
# `dataprep/compare/store.py::_slugify` is NOT reusable here -- its `[^\w\s-]`
# uses Python's Unicode-default `\w`, which *keeps* ç/ğ/ı/ö/ş/ü, so it produces
# `konut-finansmanı-karşılaştırması` and the write 422s.
_TR = str.maketrans(
    {
        "ç": "c", "Ç": "c",
        "ğ": "g", "Ğ": "g",
        "ı": "i", "İ": "i",
        "ö": "o", "Ö": "o",
        "ş": "s", "Ş": "s",
        "ü": "u", "Ü": "u",
    }
)


def slugify(title: str, fallback: str = "tablo") -> str:
    """A title as an identifier: lowercase ASCII letters, digits and hyphens.

    Transliteration happens **before** lowercasing, and that order is load-bearing
    rather than stylistic: `"İ".lower()` is `i` + U+0307 in Python but `i̇` in
    JavaScript, and `UI/src/lib/saved-view.ts` mirrors this function. Lowering
    first would make the two disagree on any title starting with a Turkish İ --
    the same table saved twice, under two slugs.

    The 80-character cut is the column's limit on an identifier. The title keeps
    its own text; see `save_table_view`.
    """
    text = title.translate(_TR).lower()
    text = unicodedata.normalize("NFKD", text)
    # Strips the combining marks NFKD just split off, so an accented Latin letter
    # becomes its base rather than vanishing.
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:SLUG_CHARS] or fallback


def _column_keys(labels: list[str]) -> list[str]:
    """One key per header label: slugified, deduped, never empty.

    Duplicated headers are not a malformed table -- an FX board legitimately has
    two columns called "Alış". They have to become distinct keys, because `cells`
    is a dict and the second would otherwise overwrite the first.
    """
    keys: list[str] = []
    seen: dict[str, int] = {}
    for index, label in enumerate(labels):
        key = slugify(str(label), fallback=f"col{index + 1}")
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            key = f"{key}-{seen[key]}"
        keys.append(key)
    return keys


def table_props(args: dict[str, Any]) -> dict[str, Any]:
    """The tool's arguments as `TableProps`.

    The wire shape is a header list plus a matrix of rows, not a list of
    `{cells: {...}}` objects. Three reasons, in order of how much they cost when
    ignored:

    1. A nested object is the likeliest thing for the model to get wrong, and tool
       arguments arrive split across stream chunks -- a malformed object loses the
       whole table rather than one cell.
    2. A header-plus-matrix is exactly what a markdown table already is, so the
       agent's path and the "save this chat table" button produce identical props.
    3. It is lossless: `TablePropsSchema` requires only `rows`.

    Columns are emitted **without a `type`**. `inferColumnType` in
    `UI/src/lib/contract.ts` reads the actual values and decides, which is better
    than guessing here -- and coercing "2,89%" or "28.410 TL" into numbers would
    destroy "↓ 0,26" for no gain, since `cellDisplayText` renders the strings
    correctly as text.
    """
    labels = [str(c) for c in (args.get("columns") or [])]
    keys = _column_keys(labels)
    cite_urls = args.get("cite_urls") or []

    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(args.get("rows") or []):
        # A model that ignored the matrix contract and sent the stored shape
        # anyway is handed straight through -- it is already what we want.
        if isinstance(raw, dict) and "cells" in raw:
            row = dict(raw)
        else:
            values = raw if isinstance(raw, list) else [raw]
            cells: dict[str, Any] = {}
            for position, value in enumerate(values):
                # A row longer than the header keeps its extra cells under
                # generated keys. Dropping them would lose data silently, and a
                # visible unnamed column is the better failure.
                key = keys[position] if position < len(keys) else f"col{position + 1}"
                cells[key] = value
            for key in keys[len(values):]:
                # `null`, not "": the contract reads null as "not found", which is
                # what a short row means, and renders it as an em dash.
                cells[key] = None
            row = {"cells": cells}

        if index < len(cite_urls) and cite_urls[index]:
            row["cite_url"] = str(cite_urls[index])
        rows.append(row)

    columns: list[dict[str, Any]] = [
        {"key": key, "label": label} for key, label in zip(keys, labels)
    ]

    # Only when something was actually cited. An empty source column on every
    # uncited table would be a column of em dashes claiming a citation exists.
    if any(row.get("cite_url") for row in rows):
        columns.append({"key": CITE_KEY, "label": CITE_LABEL, "type": "link"})
        for row in rows:
            # `null` for a row with no source, so it reads as an em dash rather
            # than an empty link.
            row["cells"][CITE_KEY] = row.get("cite_url")

    props: dict[str, Any] = {"columns": columns, "rows": rows}
    for field in ("title", "subtitle", "notes"):
        value = args.get(field)
        if value:
            props[field] = str(value)
    return props


@dataclass(frozen=True)
class SavedTable:
    """Just enough of the written row to tell the model and the client about it."""

    slug: str
    title: str


def fingerprint(name: str, args: dict[str, Any]) -> str:
    """A stable identity for one tool call, used to notice a repeat.

    This is what lets the agent loop run without a pass limit: a call whose
    fingerprint has already executed is not executed again, so "make me five
    tables" costs five passes while a model stuck re-saving the same table costs
    one write and then stops.

    `sort_keys` because argument order is not meaningful and a model will not be
    consistent about it.
    """
    try:
        payload = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        # Unhashable arguments should not be able to break the guard that stops
        # an infinite loop, so fall back to the repr.
        payload = repr(args)
    return f"{name}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def save_table_view(
    args: dict[str, Any],
    user_id: uuid.UUID,
    scope: Callable[[], Iterator[Session]] = session_scope,
) -> tuple[str, SavedTable | None]:
    """Write one agent-authored table to the user's dashboard.

    Returns `(note, saved)`, where `note` is prose the model reads back as the
    tool's result. **This never raises**, and that is not politeness. A `tool_call`
    that throws would surface as an `error` StreamEvent, and `api/routers/chat.py`
    discards the entire assembled answer on an error -- so a failed save would
    delete a perfectly good answer. A refusal is a sentence, following
    `banks/tools.py::_answer`.

    A slug collision **overwrites**. `PUT /me/views/{slug}` is already an upsert,
    so suffixing would make this tool behave differently from the HTTP route on
    the same storage; and it makes a repeated question a refresh rather than
    `konut-2`, `konut-3`. The cost is real and worth naming: two genuinely
    different tables with the same title clobber each other. The tool description
    is where that is addressed, by asking for a distinguishing title.
    """
    try:
        title = str(args.get("title") or "").strip()
        if not title:
            return ("The table needs a title. Nothing was saved.", None)
        if not args.get("rows"):
            return (
                f"The table {title!r} had no rows, so nothing was saved. "
                "Send the rows you want the user to see.",
                None,
            )

        props = table_props(args)
        # The table carries its own title, so the widget heads itself rather than
        # relying on the card around it.
        props.setdefault("title", title)

        requested = str(args.get("slug") or "").strip()
        slug = slugify(requested or title)

        stored_title = title

        with scope() as store:
            view = store.scalar(
                select(SavedView).where(
                    SavedView.user_id == user_id, SavedView.slug == slug
                )
            )
            replaced = view is not None
            if view is None:
                view = SavedView(user_id=user_id, slug=slug)
                store.add(view)
            view.title = stored_title
            view.components = [{"type": "table", "props": props}]
            view.generated = True

        verb = "Updated" if replaced else "Saved"
        return (
            f"{verb} the table {stored_title!r} on the user's AI Overview page "
            f"({len(props['rows'])} rows). They can open it from the sidebar.",
            SavedTable(slug=slug, title=stored_title),
        )
    except Exception as exc:  # noqa: BLE001 - the agent must never see a traceback
        logger.exception("Saving a table failed")
        return (
            f"Saving the table failed unexpectedly ({type(exc).__name__}). "
            "Tell the user it was not saved; do not retry with the same arguments.",
            None,
        )
