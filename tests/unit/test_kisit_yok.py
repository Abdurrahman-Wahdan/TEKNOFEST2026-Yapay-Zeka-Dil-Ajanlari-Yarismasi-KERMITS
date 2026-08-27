"""Hiçbir karakter/görsel kısıtı yok — en küçük içerik bile işlenir.

Kullanıcı kararı 2026-08-19: "TÜM SİTE VE PDF'LER eksiksiz, 1 karakter 1 KB
bile olsa İŞLENECEK" ve "asla karakter kısıtı ve görsel kısıtı kullanma".
"""
from dataprep import content, embed, images, pages
from dataprep.pdf import extract


def test_min_len_esikleri_kapali():
    assert content.MIN_LEN == 0
    assert pages.MIN_LEN == 0


def test_min_chunk_kapali():
    assert embed.MIN_CHUNK == 0


def test_gorsel_boyut_kisiti_kapali():
    """Görselde ALT sınır yok — en küçük görsel bile incelenir.

    `images.MAX_BYTES` diye bir sabit KODDA YOK (test yanlış adı kontrol
    ediyordu, AttributeError veriyordu). Doğrusu `images.MIN_PX`. Üst sınır
    zaten yok: büyük görsel KÜÇÜLTÜLMEZ, %10 overlap'li parçalara BÖLÜNÜR
    (vlm._chunk_tall_png) — bilgi kaybı olmaz."""
    assert extract.MIN_IMG_PX == 0
    assert images.MIN_PX == 0


def test_tek_karakterlik_govde_atlanmaz(monkeypatch):
    """MIN_LEN=0 iken kısa gövde de LLM'e gider (eskiden sessizce dönerdi)."""
    cagrildi = []
    monkeypatch.setattr(content.vlm, "call_json",
                        lambda *a, **k: cagrildi.append(1) or
                        {"content": "x", "musteri_icerigi": "gerekli"})
    content.clean_text("A", "https://x/y", {}, title="t")
    assert cagrildi, "kısa gövde LLM'e hiç gönderilmedi"


def test_kisa_chunk_indekslenir():
    assert embed._chunks("kısa") == ["kısa"]


def test_bos_chunk_yine_de_elenir():
    assert embed._chunks("   ") == []
