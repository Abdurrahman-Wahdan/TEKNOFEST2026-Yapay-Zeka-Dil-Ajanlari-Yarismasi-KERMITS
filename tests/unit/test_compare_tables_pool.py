"""The normalisation between the table files and the wire.

Every case here is one the real pool contains — the counts in the docstrings
came from running these rules over all 403 tables — because the failures this
guards against are not crashes. They are a table that renders ten banks when
four offer the product, or a column of product names deleted because one cell
in it said `sunulmuyor`.
"""

import datetime
import unicodedata

import pytest

from api import compare_tables_pool as pool

TODAY = datetime.date(2026, 8, 21)


def _table(columns, rows, sources=None):
    return {
        "id": "t", "topic": "T", "docstring": "", "category": "ürün", "subcategory": "s",
        "columns": columns, "rows": rows, "sources": sources or {},
    }


# --- which column answers "does this bank offer it" --------------------------
def test_dedicated_status_column_is_found():
    """229 tables have one: every filled cell is a verdict and nothing else, so
    showing it would print "Sunuluyor" once per row and say nothing."""
    table = _table(
        ["Sunum Durumu", "Ürün Adı"],
        {
            "kuveytturk": {"Sunum Durumu": "Sunuluyor", "Ürün Adı": "Sağlam Kart"},
            "adilkatilim": {"Sunum Durumu": "Sunulmuyor", "Ürün Adı": ""},
        },
    )
    assert pool.status_column(table) == "Sunum Durumu"


def test_a_sparse_field_full_of_sunulmuyor_is_not_a_status_column():
    """The regression this guards.

    `sunulmuyor` means two things: at row level "this bank offers nothing", at
    cell level "this field does not apply". `Puan Son Kullanma Tarihi` reads
    `sunulmuyor` for nine campaigns out of ten because their points never
    expire — every filled cell a sentinel, and not a status column. Reading the
    verdict off it moved six banks that run the campaign into "does not offer
    it", and it did that to 558 rows across 126 tables."""
    table = _table(
        ["Kampanya Adı", "Puan Son Kullanma Tarihi"],
        {
            "kuveytturk": {"Kampanya Adı": "Market %10", "Puan Son Kullanma Tarihi": "sunulmuyor"},
            "emlakkatilim": {"Kampanya Adı": "ParafPara", "Puan Son Kullanma Tarihi": "15 Ekim 2026"},
            "albaraka": {"Kampanya Adı": "Albaraka Usulü", "Puan Son Kullanma Tarihi": "sunulmuyor"},
            "adilkatilim": {"Kampanya Adı": "sunulmuyor", "Puan Son Kullanma Tarihi": "sunulmuyor"},
        },
    )
    assert pool.status_column(table) is None
    offers = {b: pool.offers(v, None) for b, v in table["rows"].items()}
    assert offers == {"kuveytturk": True, "emlakkatilim": True, "albaraka": True, "adilkatilim": False}


def test_a_column_of_nothing_but_refusals_is_not_a_status_column():
    """The same trap with no positive value left to give it away: a field none
    of the banks fills. A real status column always has banks on both sides, or
    the table would not be a comparison."""
    table = _table(
        ["Ürün Adı", "Fiziki Teslimat"],
        {
            "kuveytturk": {"Ürün Adı": "Gümüş Hesabı", "Fiziki Teslimat": "sunulmuyor"},
            "albaraka": {"Ürün Adı": "Gümüş Katılma", "Fiziki Teslimat": "sunulmuyor"},
        },
    )
    assert pool.status_column(table) is None
    assert pool.offers(table["rows"]["kuveytturk"], None) is True


def test_a_column_mixing_verdicts_with_content_is_not_nominated():
    """19 tables hold the verdict inside `Ürün Adı` — product names for the banks
    that offer it, `sunulmuyor` for the ones that do not. It is not nominated,
    because nominating it would drop it and take the product names along; the row
    rule reaches the same verdict without touching the column."""
    table = _table(
        ["Ürün Adı", "Vade"],
        {
            "kuveytturk": {"Ürün Adı": "Sağlam Kart", "Vade": "12 ay"},
            "adilkatilim": {"Ürün Adı": "sunulmuyor", "Vade": ""},
        },
    )
    assert pool.status_column(table) is None
    assert pool.offers(table["rows"]["adilkatilim"], None) is False
    assert pool.offers(table["rows"]["kuveytturk"], None) is True


def test_no_status_column_at_all():
    """172 tables have none. The verdict has to come from the row as a whole."""
    table = _table(
        ["Ürün Adı", "Vade"],
        {"kuveytturk": {"Ürün Adı": "Sağlam Kart", "Vade": "12 ay"}},
    )
    assert pool.status_column(table) is None


def test_producer_validity_column_is_never_the_status_column():
    """`Geçerlilik` is in all 403 tables and is mostly `-`, which must not be
    mistaken for a verdict."""
    table = _table(
        ["Geçerlilik", "Vade"],
        {"kuveytturk": {"Geçerlilik": "-", "Vade": "12 ay"}},
    )
    assert pool.status_column(table) != "Geçerlilik"


# --- the verdict itself -------------------------------------------------------
@pytest.mark.parametrize("verdict", ["Sunulmuyor", "sunulmuyor", " SUNULMUYOR ", "Yok", "Hayır"])
def test_offers_false_from_status_column(verdict):
    assert pool.offers({"Durum": verdict}, "Durum") is False


@pytest.mark.parametrize("verdict", ["Sunuluyor", "Evet", "Var", "Mevcut"])
def test_offers_true_from_status_column(verdict):
    assert pool.offers({"Durum": verdict}, "Durum") is True


def test_a_blank_status_cell_falls_through_to_the_row():
    """The one column that was supposed to answer left this bank out. The row
    still can, and giving up on the question would hide a bank for a reason
    nobody can see."""
    row = {"Durum": "", "Ürün Adı": "Sağlam Kart", "Vade": "12 ay"}
    assert pool.offers(row, "Durum") is True


def test_offers_falls_back_to_the_whole_row():
    """The previous dataset's convention, still used by 88 rows in this one."""
    row = {"Ürün Adı": "sunulmuyor", "Vade": "sunulmuyor", "Kapsam": "-"}
    assert pool.offers(row, None) is False


def test_a_row_with_any_real_content_offers():
    row = {"Ürün Adı": "sunulmuyor", "Vade": "12 ay"}
    assert pool.offers(row, None) is True


def test_the_producer_validity_column_does_not_count_as_content():
    """It is written for every row including the empty ones, so one
    `? - 31/12/2026` in it was enough to make a row of nothing but `sunulmuyor`
    read as a bank that offers something."""
    row = {"ürün adı": "sunulmuyor", "hedef kitle": "sunulmuyor", "Geçerlilik": "? - 31/12/2026"}
    assert pool.offers(row, None) is False


def test_the_status_column_does_not_count_as_content_either():
    """`Sunulmuyor` in the nominated column has already been read; counting it
    again in the row scan would be the same fact twice, and a `Sunuluyor` there
    would out-vote a row that is otherwise entirely empty."""
    row = {"Sunum Durumu": "Sunuluyor", "Ürün Adı": "", "Vade": ""}
    assert pool.offers(row, "Sunum Durumu") is True
    assert pool.offers({"Sunum Durumu": "", "Ürün Adı": "", "Vade": ""}, "Sunum Durumu") is None


def test_unclassifiable_row_is_none_not_false():
    """107 rows land here. None keeps the row in the table; False would hide a
    bank the data never actually ruled out."""
    assert pool.offers({"Durum": "", "Vade": "-"}, "Durum") is None
    assert pool.offers({"Vade": ""}, None) is None


# --- cells --------------------------------------------------------------------
@pytest.mark.parametrize("blank", ["", "-", "—", "belirtilmemiş", "  ", "Sunulmuyor"])
def test_every_absent_spelling_collapses_to_none(blank):
    """The new extraction writes "" and "-" interchangeably (9136 and 4001
    cells) and the old one wrote `belirtilmemiş`. One absence, one dash."""
    assert pool.cell(blank) is None


def test_real_content_survives_stripped():
    assert pool.cell("  60 aya kadar  ") == "60 aya kadar"


# --- validity -----------------------------------------------------------------
def _src(url, start="", end=""):
    return {"url": url, "gecerlilik_baslangic": start, "gecerlilik_bitis": end}


def test_no_dates_anywhere_is_unknown():
    assert pool.validity([_src("u")], {}, TODAY) == (None, None, pool.UNKNOWN)


def test_past_end_date_is_expired():
    assert pool.validity([_src("u", "2026-01-01", "2026-06-30")], {}, TODAY) == (
        "2026-01-01", "2026-06-30", pool.EXPIRED,
    )


def test_future_start_date_has_not_begun():
    assert pool.validity([_src("u", "2026-10-01", "2026-12-31")], {}, TODAY)[2] == pool.SCHEDULED


def test_open_ended_window_is_active():
    """217 sources publish an end with no start and 98 the reverse. Both are
    real answers, not missing data."""
    assert pool.validity([_src("u", end="2026-12-31")], {}, TODAY)[2] == pool.ACTIVE
    assert pool.validity([_src("u", start="2026-01-01")], {}, TODAY)[2] == pool.ACTIVE


def test_the_widest_window_wins():
    """A row citing several pages is only called expired once every page behind
    it has expired — marking a live offer dead is the visible error."""
    sources = [_src("a", "2026-01-01", "2026-06-30"), _src("b", "2024-04-14", "2026-12-31")]
    assert pool.validity(sources, {}, TODAY) == ("2024-04-14", "2026-12-31", pool.ACTIVE)


def test_the_url_pool_supplies_dates_the_table_copy_lacks():
    """58 dates and 763 statuses are only in `_url_havuzu.json`."""
    urls = {"u": {"gecerlilik_baslangic": "2026-05-01", "gecerlilik_bitis": "2026-12-31"}}
    assert pool.validity([_src("u")], urls, TODAY) == ("2026-05-01", "2026-12-31", pool.ACTIVE)


def test_an_unparseable_date_does_not_raise():
    assert pool.validity([_src("u", end="31/12/2026")], {}, TODAY)[2] == pool.UNKNOWN


def test_window_note_writes_an_open_end_as_open():
    assert pool.window_note("2026-05-01", "2026-12-31") == "01.05.2026 – 31.12.2026"
    assert pool.window_note(None, "2026-12-31") == "? – 31.12.2026"
    assert pool.window_note(None, None) == ""


# --- ids across filesystems ---------------------------------------------------
def test_an_id_in_either_unicode_form_finds_its_table():
    """macOS writes these filenames in NFD while the id inside each file is NFC,
    so `kredi-kartı-doğum-günü-kampanyaları` read off the disk did not equal the
    same name read out of the JSON. Half of a random sample of ten 404'd."""
    table_id = "kredi-kartı-doğum-günü-kampanyaları"
    nfc = unicodedata.normalize("NFC", table_id)
    nfd = unicodedata.normalize("NFD", table_id)
    assert nfc != nfd, "this id must actually differ between the two forms"
    assert pool.load_table(nfc) is not None
    assert pool.load_table(nfd) is not None


def test_a_traversing_id_is_refused():
    with pytest.raises(ValueError):
        pool.load_table("../../../etc/passwd")
