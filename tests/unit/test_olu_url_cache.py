"""Ölü görsel URL'leri kalıcı hatırlanmalı — aynı URL tekrar indirilmemeli.

Canlı olay (2026-08-19, kuveytturk): tek bir SitemapNodePage URL'i 9 KEZ
indirildi (farklı sayfalarda tekrar ettiği için), her seferinde 197KB HTML
geldi. Kalan 515 sayfada 10.792 böyle URL vardı.

RİSK YOK ilkesi: karar kalıp tahminiyle değil, URL'in GERÇEK yanıtıyla verilir.
Her URL en az bir kez denenir; yalnızca sonucu hatırlanır.
"""


import pytest

from dataprep import content as c

pytestmark = pytest.mark.unit


@pytest.fixture
def temiz(tmp_path, monkeypatch):
    (tmp_path / "data" / "testbank_site").mkdir(parents=True)
    monkeypatch.setattr(c, "__file__", str(tmp_path / "dataprep" / "content.py"))
    c._OLU_URL.clear()
    yield tmp_path
    c._OLU_URL.clear()


def test_olu_url_diske_yazilir_ve_okunur(temiz):
    c._olu_ekle("testbank", "http://x/olu.vsf")
    c._OLU_URL.clear()                          # bellekten sil, diskten okusun
    assert "http://x/olu.vsf" in c._olu_yukle("testbank")


def test_ayni_url_iki_kez_indirilmez(temiz, monkeypatch):
    """Asıl kazanç: 9 kez inen URL artık 1 kez iner."""
    sayac = {"n": 0}

    def sahte(kutu, url):
        sayac["n"] += 1
        return None, True                       # ölü (HTML/4xx) -> KALICI

    monkeypatch.setattr(c, "_fetch_one_image", sahte)
    monkeypatch.setattr(c, "_new_img_client", lambda: type("C", (), {"close": lambda s: None})())
    monkeypatch.setattr(c, "_slug_of", lambda u: "testbank")

    url = "http://x/olu.vsf"
    c._fetch_page_images([url])                 # 1. sayfa
    c._fetch_page_images([url])                 # 2. sayfa — atlanmalı
    c._fetch_page_images([url])                 # 3. sayfa — atlanmalı
    assert sayac["n"] == 1


def test_gercek_gorsel_olu_listesine_GIRMEZ(temiz, monkeypatch):
    """En kritik kontrol: başarılı görsel asla kalıcı atlanmamalı."""
    monkeypatch.setattr(c, "_fetch_one_image", lambda kutu, url: (b"PNGVERISI", True))
    monkeypatch.setattr(c, "_new_img_client", lambda: type("C", (), {"close": lambda s: None})())
    monkeypatch.setattr(c, "_slug_of", lambda u: "testbank")

    items, _ = c._fetch_page_images(["http://x/gercek.png"])
    assert items == [("http://x/gercek.png", b"PNGVERISI")]
    assert "http://x/gercek.png" not in c._olu_yukle("testbank")


def test_slug_url_den_cozulur():
    assert c._slug_of("https://kuveytturk.com.tr/medium/x.vsf") == "kuveytturk"


def test_ag_hatasi_HEMEN_olu_sayilmaz(temiz, monkeypatch):
    """Geçici kesinti yüzünden sağlam bir görsel kalıcı kaybolmamalı.

    HTML/4xx tek seferde ölü sayılır (belirsizlik yok), ama ağ hatası
    _OLU_ESIK koşu üst üste tekrarlanmadan kalıcı sayılmaz.
    """
    monkeypatch.setattr(c, "_fetch_one_image", lambda kutu, url: (None, False))
    monkeypatch.setattr(c, "_new_img_client", lambda: type("C", (), {"close": lambda s: None})())
    monkeypatch.setattr(c, "_slug_of", lambda u: "testbank")
    url = "http://x/gecici.png"

    for _ in range(c._OLU_ESIK - 1):
        c._fetch_page_images([url])
        assert url not in c._olu_yukle("testbank")   # henüz ölü değil

    c._fetch_page_images([url])                      # eşiğe ulaştı
    assert url in c._olu_yukle("testbank")
