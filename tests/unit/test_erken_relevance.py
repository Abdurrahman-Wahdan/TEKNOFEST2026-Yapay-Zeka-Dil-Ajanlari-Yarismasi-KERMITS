"""Dev PDF'lerde erken relevance kararı.

albaraka'nın entegre faaliyet raporu 1.3M karakter = 163 chunk. Chunk'lar
ARDIŞIK işlendiği için (her biri öncekinin çıktısını bağlam alır) tamamını
işlemek 5-8 saat sürüyordu — ve ancak o zaman "gereksiz" olduğu anlaşılıyordu.
Karar, kararın maliyetinden sonra geliyordu.

Kural: belge _TUMU_ISLE_SINIRI chunk'a kadar TAMAMI işlenir. Aşıyorsa sadece
ilk _KARAR_CHUNK chunk oylanır; ÇOĞUNLUK "gereksiz" derse kalan işlenmez.
Oran kullanılmaz — maliyet belge boyuyla büyümesin, sabit kalsın.

NOT (2026-08-23): eşik 20'den 10'a çekildi (kullanıcı kararı: "ilk 10 chunk
paralel gönderilir"). Testler artık SABİT SAYI yazmaz, kodun kendi
sabitlerini (c._KARAR_CHUNK / c._TUMU_ISLE_SINIRI) okur — eşik yeniden
değişirse testler kendiliğinden uyar, sessizce ayrışmaz.
"""

import pytest

from dataprep import content as c
from dataprep import vlm as v

pytestmark = pytest.mark.unit


@pytest.fixture
def sahte_llm(monkeypatch):
    """Her chunk için sırayla verilen relevance oyunu döndürür; çağrı sayar."""
    def kur(*oylar):
        sayac = {"n": 0}

        def sahte(msgs, **kw):
            i = sayac["n"]
            sayac["n"] += 1
            return {"content": f"p{i}", "musteri_icerigi": oylar[min(i, len(oylar) - 1)]}

        monkeypatch.setattr(v, "call_json", sahte)
        return sayac
    return kur


def _belge(chunk_sayisi: int) -> str:
    return "A" * (c.CHUNK * chunk_sayisi)


def _dates() -> dict:
    return {"start": "", "end": "", "guess": "", "relevance": "gerekli"}


def test_dev_rapor_20_chunkta_kesilir(sahte_llm):
    """163 chunk'lık rapor: 5-8 saat yerine 20 çağrı."""
    sayac = sahte_llm("gereksiz")
    d = _dates()
    c.clean_text(_belge(163), "http://x/rapor.pdf", d)
    assert sayac["n"] == c._KARAR_CHUNK
    assert d["relevance"] == "gereksiz"


def test_devasa_belgede_de_ayni_maliyet(sahte_llm):
    """400 chunk da 1000 chunk da aynı: ilk 25. Maliyet boyla büyümez."""
    sayac = sahte_llm("gereksiz")
    c.clean_text(_belge(400), "http://x/devasa.pdf", _dates())
    assert sayac["n"] == c._KARAR_CHUNK


@pytest.mark.parametrize("delta", [-5, -1, 0])
def test_sinira_kadar_tamamen_islenir(sahte_llm, delta):
    """Sınıra KADAR (dahil) erken karar devreye GİRMEZ.

    Parametreler eşiğe GÖRECELİ verilir; eşik değişirse test kendiliğinden
    uyar (eskiden sabit 5/19/20 yazıyordu ve eşik 20->10 olunca sessizce
    yanlış şeyi doğrulamaya başlamıştı)."""
    n = c._TUMU_ISLE_SINIRI + delta
    sayac = sahte_llm("gereksiz")
    c.clean_text(_belge(n), "http://x/orta.pdf", _dates())
    assert sayac["n"] == n


def test_gerekli_belge_hic_kesilmez(sahte_llm):
    """Veri kaybı olmamalı: 'gerekli' oylanan dev belge tam işlenir."""
    sayac = sahte_llm("gerekli")
    d = _dates()
    c.clean_text(_belge(163), "http://x/kampanya.pdf", d)
    assert sayac["n"] == 163 + c._TUMU_ISLE_SINIRI
    assert d["relevance"] == "gerekli"


def test_azinlik_gereksiz_oyu_belgeyi_kesmez(sahte_llm):
    """Çoğunluk 'gerekli' olduğu sürece devam eder."""
    sayac = sahte_llm("gereksiz", "gerekli", "gerekli")
    c.clean_text(_belge(100), "http://x/karisik.pdf", _dates())
    assert sayac["n"] == 100 + c._TUMU_ISLE_SINIRI


# ----- aynı kural SAYFALAR için de geçerli -----
# Sayfalar genelde kısa, eşiğe ulaşmazlar (tamamı işlenir). Kural yalnızca
# anormal uzun sayfalarda devreye girer — orada da chunk'lar ardışık olduğu
# için maliyet saatlere çıkabiliyor.

from dataprep import pages as pg


@pytest.fixture
def sahte_sayfa_llm(monkeypatch):
    def kur(oy):
        sayac = {"n": 0}

        def sahte(prompt):
            i = sayac["n"]
            sayac["n"] += 1
            return {"content": f"p{i}", "relevance": oy,
                    "campaign_start": "", "campaign_end": "", "validity_status": ""}

        monkeypatch.setattr(pg, "_clean_one", sahte)
        return sayac
    return kur


def _sayfa(parca: int) -> str:
    return "A" * ((pg.CHUNK - pg.OVERLAP) * parca)


def test_anormal_uzun_sayfa_esikte_kesilir(sahte_sayfa_llm):
    """200 parça > 20 sınır -> ilk 20 oylanır."""
    sayac = sahte_sayfa_llm("gereksiz")
    _, dates = pg.clean_page(_sayfa(200), "http://x/devasa")
    assert sayac["n"] == c._KARAR_CHUNK
    assert dates.get("relevance") == "gereksiz"


def test_kisa_sayfa_tamamen_islenir(sahte_sayfa_llm):
    """Sınırın ALTINDA kalan sayfa: erken karar yok, tamamı işlenir."""
    n = c._TUMU_ISLE_SINIRI - 5
    sayac = sahte_sayfa_llm("gereksiz")
    pg.clean_page(_sayfa(n), "http://x/kisa")
    assert sayac["n"] == n


def test_uzun_ama_gerekli_sayfa_kesilmez(sahte_sayfa_llm):
    sayac = sahte_sayfa_llm("gerekli")
    pg.clean_page(_sayfa(60), "http://x/uzun-kampanya")
    assert sayac["n"] == 60 + c._TUMU_ISLE_SINIRI


def test_sayfa_ve_pdf_ayni_kurali_kullanir():
    assert pg._TUMU_ISLE_SINIRI == c._TUMU_ISLE_SINIRI
    assert pg._KARAR_CHUNK == c._KARAR_CHUNK
