"""Turning the agent's table into a dashboard row.

Two things here are worth more than the rest. The **slug parity table** is shared,
case for case, with `UI/src/lib/saved-view.test.ts`: two slugifiers that disagree
save the same title under two slugs, and Turkish `İ` lowercases differently in
Python and JavaScript, so this is a real divergence and not a hypothetical one.
Keep the two tables in step.

And the **no-truncation pin**: nothing on the way to the user's page caps rows or
cell length. A table missing its tail is a wrong answer that looks like a right
one.

No database and no model: `save_table_view` takes its session factory as an
argument precisely so this file needs neither.
"""

import uuid
from contextlib import contextmanager

import pytest

from api.saved_tables import (
    SLUG_CHARS,
    TITLE_CHARS,
    fingerprint,
    save_table_view,
    slugify,
    table_props,
)

pytestmark = pytest.mark.unit


class _FakeStore:
    """A session that records the object it was handed and answers one query."""

    def __init__(self, existing=None):
        self.existing = existing
        self.added = []
        self.committed = False

    def scalar(self, _statement):
        return self.existing

    def add(self, obj):
        self.added.append(obj)


class _View:
    """Stands in for a SavedView row, which needs no ORM to be assigned to."""

    def __init__(self, slug="x", title="", components=None, generated=False):
        self.slug = slug
        self.title = title
        self.components = components or []
        self.generated = generated


def _scope_for(store):
    @contextmanager
    def scope():
        yield store
        store.committed = True

    return scope


USER = uuid.uuid4()


def _save(args, existing=None):
    store = _FakeStore(existing=existing)
    note, saved = save_table_view(args, USER, scope=_scope_for(store))
    return note, saved, store


def _table(**kw):
    base = {
        "title": "Konut karşılaştırması",
        "columns": ["Banka", "Kâr oranı"],
        "rows": [["Kuveyt Türk", "%2,89"], ["Vakıf Katılım", "%2,95"]],
    }
    base.update(kw)
    return base


# --- slugify ----------------------------------------------------------------
#
# This case table is mirrored in UI/src/lib/saved-view.test.ts. Change both.


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Konut finansmanı karşılaştırması", "konut-finansmani-karsilastirmasi"),
        ("İhtiyaç kredisi", "ihtiyac-kredisi"),
        ("ığüşöç", "igusoc"),
        ("ÇĞİÖŞÜ", "cgiosu"),
        ("Kâr oranı", "kar-orani"),
        ("A  --  B", "a-b"),
        ("  boşluk  ", "bosluk"),
        ("2024 / 2025 (TL)", "2024-2025-tl"),
        ("already-a-slug", "already-a-slug"),
    ],
)
def test_slugify_produces_an_ascii_identifier(title, expected):
    assert slugify(title) == expected


def test_slugify_only_ever_emits_the_allowed_alphabet():
    # The pattern the write is validated against is ^[a-z0-9-]{1,80}$.
    import re

    for title in ["Konut finansmanı!", "%50 İndirim", "ÖZEL — kampanya", "a_b_c"]:
        assert re.fullmatch(r"[a-z0-9-]{1,80}", slugify(title)), title


def test_slugify_transliterates_before_lowering():
    # "İ".lower() is "i" + U+0307 in Python and "i̇" in JavaScript. Lowering first
    # would make the Python and TypeScript slugs disagree on this exact title.
    assert slugify("İzmir") == "izmir"
    assert "̇" not in slugify("İzmir")


def test_slugify_caps_the_identifier_at_the_column_width():
    assert len(slugify("uzun " * 40)) <= SLUG_CHARS


def test_slugify_falls_back_when_nothing_survives():
    assert slugify("!!!") == "tablo"
    assert slugify("", fallback="konu") == "konu"


# --- table_props ------------------------------------------------------------


def test_column_keys_come_from_the_labels():
    props = table_props(_table())
    assert props["columns"] == [
        {"key": "banka", "label": "Banka"},
        {"key": "kar-orani", "label": "Kâr oranı"},
    ]


def test_duplicate_headers_get_distinct_keys():
    # An FX board legitimately has two columns called "Alış". `cells` is a dict, so
    # the second would overwrite the first.
    props = table_props({"columns": ["Alış", "Alış", "Alış"], "rows": [["1", "2", "3"]]})
    assert [c["key"] for c in props["columns"]] == ["alis", "alis-2", "alis-3"]
    assert props["rows"][0]["cells"] == {"alis": "1", "alis-2": "2", "alis-3": "3"}


def test_an_empty_header_still_gets_a_key():
    props = table_props({"columns": ["Banka", ""], "rows": [["a", "b"]]})
    assert [c["key"] for c in props["columns"]] == ["banka", "col2"]


def test_columns_carry_no_type_so_the_frontend_infers_it():
    # inferColumnType reads the values; guessing here would coerce "↓ 0,26" into
    # nothing useful.
    for column in table_props(_table())["columns"]:
        assert "type" not in column


def test_rows_become_cells_keyed_by_column():
    rows = table_props(_table())["rows"]
    assert rows[0]["cells"] == {"banka": "Kuveyt Türk", "kar-orani": "%2,89"}


def test_a_short_row_pads_with_null_not_empty_string():
    # null means "not found" in the contract and renders as an em dash; "" would
    # render as a blank cell that looks like real data.
    props = table_props({"columns": ["a", "b", "c"], "rows": [["1"]]})
    assert props["rows"][0]["cells"] == {"a": "1", "b": None, "c": None}


def test_a_long_row_keeps_its_extra_cells():
    # Dropping them would lose data silently. An unnamed column is visible.
    props = table_props({"columns": ["a"], "rows": [["1", "2", "3"]]})
    assert props["rows"][0]["cells"] == {"a": "1", "col2": "2", "col3": "3"}


def test_cite_urls_attach_by_row_index():
    props = table_props(
        _table(cite_urls=["https://kuveytturk.com.tr/konut", ""]),
    )
    assert props["rows"][0]["cite_url"] == "https://kuveytturk.com.tr/konut"
    assert "cite_url" not in props["rows"][1]


def test_a_row_that_already_carries_cells_passes_through():
    # A model that ignored the matrix contract sent the stored shape anyway.
    props = table_props({"columns": ["a"], "rows": [{"cells": {"a": "1"}}]})
    assert props["rows"][0] == {"cells": {"a": "1"}}


def test_a_passed_through_row_still_gets_its_source_column():
    # Its cite_url came in on the row rather than in `cite_urls`, and it earns a
    # clickable source cell either way.
    props = table_props({"columns": ["a"], "rows": [{"cells": {"a": "1"}, "cite_url": "u"}]})
    assert props["rows"][0]["cells"] == {"a": "1", "kaynak": "u"}


def test_turkish_text_in_cells_is_untouched():
    props = table_props({"columns": ["Banka"], "rows": [["Vakıf Katılım — %2,95"]]})
    assert props["rows"][0]["cells"]["banka"] == "Vakıf Katılım — %2,95"


def test_optional_headings_are_carried_and_omitted_when_blank():
    props = table_props(_table(subtitle="24 ay", notes=""))
    assert props["subtitle"] == "24 ay"
    assert "notes" not in props


def test_nothing_is_truncated():
    # The no-truncation pin. No row cap, no cell-length cap, no "showing N of M".
    long_cell = "x" * 5000
    args = {
        "columns": ["a", "b"],
        "rows": [[f"row{i}", long_cell] for i in range(2000)],
    }
    props = table_props(args)
    assert len(props["rows"]) == 2000
    assert props["rows"][1999]["cells"]["a"] == "row1999"
    assert len(props["rows"][0]["cells"]["b"]) == 5000


def test_an_empty_table_is_still_well_formed():
    assert table_props({}) == {"columns": [], "rows": []}


# --- save_table_view --------------------------------------------------------


def test_a_new_table_is_added_and_flagged_generated():
    note, saved, store = _save(_table())
    assert saved is not None
    assert saved.slug == "konut-karsilastirmasi"
    assert len(store.added) == 1
    view = store.added[0]
    assert view.generated is True
    assert view.title == "Konut karşılaştırması"
    assert store.committed is True
    assert "Saved" in note


def test_the_component_is_a_single_table_spec():
    # One SavedView per table, and the shape TableWidget already renders.
    _, _, store = _save(_table())
    components = store.added[0].components
    assert len(components) == 1
    assert components[0]["type"] == "table"
    assert len(components[0]["props"]["rows"]) == 2


def test_the_table_carries_its_own_title():
    _, _, store = _save(_table())
    assert store.added[0].components[0]["props"]["title"] == "Konut karşılaştırması"


def test_an_existing_slug_is_overwritten_not_duplicated():
    existing = _View(slug="konut-karsilastirmasi", title="eski")
    note, saved, store = _save(_table(), existing=existing)
    assert store.added == []           # nothing new
    assert existing.title == "Konut karşılaştırması"
    assert existing.generated is True
    assert "Updated" in note
    assert saved.slug == "konut-karsilastirmasi"


def test_a_model_supplied_slug_is_slugified_too():
    _, saved, _ = _save(_table(slug="Konut Tablosu"))
    assert saved.slug == "konut-tablosu"


def test_the_title_is_clipped_to_the_column_width():
    long = "K" * 300
    _, saved, store = _save(_table(title=long))
    assert len(store.added[0].title) == TITLE_CHARS
    assert saved.title == long[:TITLE_CHARS]


def test_a_missing_title_is_refused_in_prose():
    note, saved, store = _save(_table(title="  "))
    assert saved is None
    assert store.added == []
    assert "title" in note.lower()


def test_a_table_with_no_rows_is_refused_in_prose():
    note, saved, _ = _save(_table(rows=[]))
    assert saved is None
    assert "no rows" in note.lower()


def test_a_failure_is_a_sentence_and_never_an_exception():
    # An exception would reach the router as an `error` frame, and the router
    # discards the whole assembled answer on an error. A failed save must not be
    # able to delete a good answer.
    @contextmanager
    def exploding():
        raise RuntimeError("the database went away")
        yield  # pragma: no cover

    note, saved = save_table_view(_table(), USER, scope=exploding)
    assert saved is None
    assert "RuntimeError" in note


def test_the_row_count_is_reported_back_to_the_model():
    note, _, _ = _save(_table())
    assert "2 rows" in note


# --- fingerprint ------------------------------------------------------------


def test_identical_calls_share_a_fingerprint():
    assert fingerprint("save_table", _table()) == fingerprint("save_table", _table())


def test_argument_order_does_not_change_the_fingerprint():
    a = {"title": "t", "columns": ["a"]}
    b = {"columns": ["a"], "title": "t"}
    assert fingerprint("save_table", a) == fingerprint("save_table", b)


def test_different_tables_get_different_fingerprints():
    assert fingerprint("save_table", _table()) != fingerprint(
        "save_table", _table(title="Başka")
    )


def test_the_tool_name_is_part_of_the_identity():
    assert fingerprint("save_table", {}) != fingerprint("look_at_page", {})


def test_unserialisable_arguments_still_fingerprint():
    # The guard that stops an infinite loop must not itself be breakable.
    assert fingerprint("save_table", {"x": object()})


# --- the source column ------------------------------------------------------


def test_a_cited_table_gains_a_clickable_source_column():
    # `cite_url` alone is stored but never rendered: the table renderer uses it
    # for the row key and the hover note, not as a cell. The produced tables
    # surface it with a synthetic `link` column, and so does this.
    props = table_props(_table(cite_urls=["https://kuveytturk.com.tr/konut", ""]))
    assert props["columns"][-1] == {"key": "kaynak", "label": "Kaynak", "type": "link"}
    assert props["rows"][0]["cells"]["kaynak"] == "https://kuveytturk.com.tr/konut"


def test_an_uncited_row_gets_null_in_the_source_column():
    props = table_props(_table(cite_urls=["https://a.example", ""]))
    assert props["rows"][1]["cells"]["kaynak"] is None


def test_an_uncited_table_gets_no_source_column():
    # A column of em dashes would claim a citation exists on every row.
    props = table_props(_table())
    assert [c["key"] for c in props["columns"]] == ["banka", "kar-orani"]
    assert "kaynak" not in props["rows"][0]["cells"]


def test_the_source_column_is_the_only_one_carrying_a_type():
    # Every other column is left untyped so the frontend infers it from values.
    props = table_props(_table(cite_urls=["https://a.example", ""]))
    typed = [c for c in props["columns"] if "type" in c]
    assert [c["key"] for c in typed] == ["kaynak"]
