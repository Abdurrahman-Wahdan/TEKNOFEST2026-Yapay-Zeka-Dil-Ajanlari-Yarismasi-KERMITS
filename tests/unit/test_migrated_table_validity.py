"""The migrated per-cell date must govern the claim beside it, not the row."""

import datetime

import pytest

from api import compare_tables_pool as pool
from api.routers.compare_tables import get_table


pytestmark = pytest.mark.unit
TODAY = datetime.date(2026, 8, 27)


def test_only_the_expired_claim_is_removed_from_a_mixed_row():
    values = {
        "Ürün": "Banka Kartı",
        "Ürün (Geçerlilik)": "-",
        "Kart Ücreti": "Masrafsız",
        "Kart Ücreti (Geçerlilik)": "? - 17/08/2026",
        "Kampanya": "%1 nakit ödül",
        "Kampanya (Geçerlilik)": "02/07/2026 - 31/12/2026",
    }

    current = pool.current_values(values, TODAY)
    assert current["Ürün"] == "Banka Kartı"
    assert current["Kart Ücreti"] is None
    assert current["Kampanya"] == "%1 nakit ödül"
    assert pool.cell_validity(values, "Kart Ücreti", TODAY)[2] == pool.EXPIRED
    assert pool.cell_validity(values, "Kampanya", TODAY)[2] == pool.ACTIVE


def test_live_migrated_table_does_not_widen_the_expired_hayat_fee():
    detail = get_table("banka-kartı")
    hayat = next(row for row in detail.rows if row.cells["Banka"] == "hayat")

    assert hayat.cells["Kart Ücreti"] is None
    assert hayat.cells["Ödül ve Kampanya Sistemi"] is not None
    assert hayat.cite_url and hayat.cite_url.startswith("https://")
