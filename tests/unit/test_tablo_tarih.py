"""Tablo hücrelerine gg/aa/yyyy geçerlilik tarihi damgalama.

Kural (kullanıcı, 2026-08-19): her hücrenin KENDİ URL'inden tarih okunur.
"gg/aa/yyyy - gg/aa/yyyy"; eksik taraf "?" ile yazılır ("01/08/2026 - ?" veya
"? - 30/09/2026") çünkü tek tarih yalnız bırakılırsa okuyan onun başlangıç mı
bitiş mi olduğunu anlayamaz; hiç tarih yoksa "-".
Hücre bazlı olması şart: bir satırın sütunları farklı belgelerden gelir ve
geçerlilikleri farklıdır.
"""

import json

import pytest

from dataprep.compare import tablo_tarih as tt

pytestmark = pytest.mark.unit


# ----- format -----

@pytest.mark.parametrize("bas,bit,beklenen", [
    ("2026-08-01", "2026-09-30", "01/08/2026 - 30/09/2026"),
    ("2026-08-01", "", "01/08/2026 - ?"),
    ("", "2026-09-30", "? - 30/09/2026"),
    ("", "", "-"),
    ("gecersiz", "", "-"),
])
def test_bicim(bas, bit, beklenen):
    assert tt.bicimle(bas, bit) == beklenen


def test_iso_cevrimi():
    assert tt.iso_to_ggaayyyy("2026-12-31") == "31/12/2026"
    assert tt.iso_to_ggaayyyy("") == ""


# ----- hücre bazlı tarih -----

def test_kaynak_kaydindaki_tarih_kullanilir():
    """En ucuz yol: cell_sources kaydı zaten tarihi taşıyorsa LLM/dosya gerekmez."""
    kaynaklar = [{"url": "http://x/a", "gecerlilik_baslangic": "2026-08-01",
                  "gecerlilik_bitis": "2026-09-30"}]
    assert tt.hucre_tarihi(kaynaklar) == "01/08/2026 - 30/09/2026"


def test_kaynaksiz_hucre_tire_alir():
    assert tt.hucre_tarihi([]) == "-"


def test_birden_cok_kaynak_en_genis_aralik():
    """Hücre birkaç belgeden derlendiyse aralık hepsini kapsamalı."""
    kaynaklar = [
        {"url": "http://x/a", "gecerlilik_baslangic": "2026-08-01", "gecerlilik_bitis": "2026-08-31"},
        {"url": "http://x/b", "gecerlilik_baslangic": "2026-07-15", "gecerlilik_bitis": "2026-09-30"},
    ]
    assert tt.hucre_tarihi(kaynaklar) == "15/07/2026 - 30/09/2026"


def test_tek_tarih_belirsiz_birakmaz():
    """Asıl kural: "01/08/2026" tek başına başlangıç mı bitiş mi belli değil."""
    assert tt.hucre_tarihi([{"url": "u", "gecerlilik_baslangic": "2026-08-01"}]) == "01/08/2026 - ?"
    assert tt.hucre_tarihi([{"url": "u", "gecerlilik_bitis": "2026-09-30"}]) == "? - 30/09/2026"


def test_url_belgesinden_okur(tmp_path, monkeypatch):
    """Kaynak kaydında tarih yoksa URL'in content/*.md dosyasına bakılır."""
    site = tmp_path / "data" / "testbank_site"
    (site / "content").mkdir(parents=True)
    (site / "content" / "a.md").write_text(
        '---\nurl: "http://x/a"\ngecerlilik_baslangic: "2026-03-01"\n'
        'gecerlilik_bitis: "2026-04-15"\n---\ngövde', encoding="utf-8")
    (site / "_catalog.json").write_text(json.dumps(
        {"http://x/a": {"kind": "page", "path": "a.md", "hash": "h"}}))
    monkeypatch.setattr(tt, "_KOK", tmp_path / "data")
    tt._url_cache.clear()
    tt._katalog_cache.clear()
    assert tt.hucre_tarihi([{"url": "http://x/a"}]) == "01/03/2026 - 15/04/2026"


# ----- tablo damgalama -----

def test_her_hucre_kendi_tarihini_alir():
    """Asıl kural: aynı satırın iki sütunu FARKLI tarih alabilmeli."""
    tablo = {
        "columns": ["Kâr Payı", "Ücret"],
        "rows": {"bankaA": {"Kâr Payı": "%35", "Ücret": "0 TL"}},
        "cell_sources": {"bankaA": {
            "Kâr Payı": [{"url": "http://x/1", "gecerlilik_baslangic": "2026-08-01",
                          "gecerlilik_bitis": "2026-08-31"}],
            "Ücret": [{"url": "http://x/2", "gecerlilik_bitis": "2026-12-31"}],
        }},
    }
    assert tt.tabloyu_damgala(tablo) is True
    satir = tablo["rows"]["bankaA"]
    assert satir["Kâr Payı (Geçerlilik)"] == "01/08/2026 - 31/08/2026"
    assert satir["Ücret (Geçerlilik)"] == "? - 31/12/2026"


def test_kaynaksiz_hucreye_de_alan_eklenir():
    """'Her satırda tarih olsun': alan hiç eksik olmamalı, boşsa '-'."""
    tablo = {"columns": ["Ücret"], "rows": {"bankaA": {"Ücret": "0 TL"}}, "cell_sources": {}}
    tt.tabloyu_damgala(tablo)
    assert tablo["rows"]["bankaA"]["Ücret (Geçerlilik)"] == "-"


def test_tarih_sutunu_veri_sutununun_yanina_gelir():
    tablo = {"columns": ["A", "B"], "rows": {"x": {"A": "1", "B": "2"}}, "cell_sources": {}}
    tt.tabloyu_damgala(tablo)
    assert tablo["columns"] == ["A", "A (Geçerlilik)", "B", "B (Geçerlilik)"]


def test_tekrar_damgalama_idempotent():
    tablo = {"columns": ["A"], "rows": {"x": {"A": "1"}}, "cell_sources": {}}
    tt.tabloyu_damgala(tablo)
    assert tt.tabloyu_damgala(tablo) is False      # değişiklik yok
    assert tablo["columns"] == ["A", "A (Geçerlilik)"]
