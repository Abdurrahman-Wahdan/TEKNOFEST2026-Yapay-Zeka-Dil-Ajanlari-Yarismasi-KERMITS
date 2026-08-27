"""Tarih alanları HER ZAMAN yazılmalı — tek taraflı, çift taraflı ya da hiç yok.

Eskiden alan yalnızca DOLU olduğunda yazılıyordu. O yüzden "LLM'e hiç sorulmadı"
ile "soruldu ama belgede tarih yok" ayırt edilemiyordu; tek taraflı tarihte de
hangi tarafın bilindiği frontmatter'dan okunamıyordu.

Üç durum da açıkça görünmeli:
    "2026-08-01" / "2026-09-30"  -> iki taraf
    "2026-08-01" / ""            -> tek taraf (diğeri belgede yok)
    ""          / ""             -> hiç tarih yok
"""

import pytest

from dataprep import content as c
from dataprep import pages as pg

pytestmark = pytest.mark.unit

_DURUMLAR = [
    ("iki_taraf", "2026-08-01", "2026-09-30"),
    ("tek_taraf_baslangic", "2026-08-01", ""),
    ("tek_taraf_bitis", "", "2026-09-30"),
    ("hic_yok", "", ""),
]


def _satirlar(metin: str, anahtar: str) -> dict:
    out = {}
    for l in metin.splitlines():
        if l.startswith(anahtar):
            k, _, v = l.partition(":")
            out[k.strip()] = v.strip().strip('"')
    return out


@pytest.mark.parametrize("ad,bas,bit", _DURUMLAR)
def test_content_frontmatter_alanlari_hep_var(ad, bas, bit):
    metin = c._frontmatter("u", "b", "h", {"status": "x", "start": bas, "end": bit})
    alan = _satirlar(metin, "gecerlilik")
    assert alan["gecerlilik_baslangic"] == bas
    assert alan["gecerlilik_bitis"] == bit


@pytest.mark.parametrize("ad,bas,bit", _DURUMLAR)
def test_pdf_clean_frontmatter_alanlari_hep_var(ad, bas, bit):
    metin = c._pdf_clean_frontmatter("u", "h", {"start": bas, "end": bit})
    alan = _satirlar(metin, "gecerlilik")
    assert alan["gecerlilik_baslangic"] == bas
    assert alan["gecerlilik_bitis"] == bit


@pytest.mark.parametrize("ad,bas,bit", _DURUMLAR)
def test_pages_campaign_alanlari_hep_var(ad, bas, bit):
    """Crawl çıktısı aşama 3'ün girdisi — orada da eksik kalmamalı."""
    out = pg._set_front_dates('---\nurl: "x"\n---',
                              {"start": bas, "end": bit, "status": "bitmedi"})
    alan = _satirlar(out, "campaign_")
    assert alan["campaign_start"] == bas
    assert alan["campaign_end"] == bit


def test_tablo_katmani_uc_durumu_dogru_bicimler():
    """Uçtan uca: frontmatter -> tablo hücresi."""
    from dataprep.compare.tablo_tarih import bicimle
    assert bicimle("2026-08-01", "2026-09-30") == "01/08/2026 - 30/09/2026"
    assert bicimle("2026-08-01", "") == "01/08/2026 - ?"
    assert bicimle("", "2026-09-30") == "? - 30/09/2026"
    assert bicimle("", "") == "-"
