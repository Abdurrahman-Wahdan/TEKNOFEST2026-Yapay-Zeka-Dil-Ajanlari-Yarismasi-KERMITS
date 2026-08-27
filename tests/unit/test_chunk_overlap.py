"""Chunk boyutu 8196, %10 (~820 karakter) overlap.

Kullanıcı kararı 2026-08-19: metin temizleme dışındaki TÜM chunk'lar 8196
karakter ve gerekli her yerde %10 overlap taşır. Overlap olmadan sınıra denk
gelen bilgi ikiye bölünüyor ve arama yalnız yarısını bulabiliyordu.
"""
from config.settings import settings
from index.chunk import _split, _with_overlap


def test_ayar_degerleri():
    assert settings.INDEX_MAX_CHUNK_CHARS == 8196
    assert settings.INDEX_CHUNK_OVERLAP_CHARS == 820


def test_kisa_metin_bolunmez():
    assert _split("kısa metin", 8196) == ["kısa metin"]


def test_ilk_parca_overlapsiz():
    p = ["a" * 100, "b" * 100]
    assert _with_overlap(p)[0] == "a" * 100


def test_sonraki_parcalar_overlap_alir():
    onceki = "x " * 500                      # 1000 karakter
    p = [onceki, "yeni içerik"]
    out = _with_overlap(p)
    assert out[1].endswith("yeni içerik")
    assert len(out[1]) > len("yeni içerik"), "overlap eklenmedi"


def test_overlap_kelimeyi_ortadan_bolmez():
    onceki = "kelime " * 200
    out = _with_overlap([onceki, "SON"])
    kuyruk = out[1].split("\n\n")[0]
    assert not kuyruk.startswith("elime"), "yarım kelimeyle başladı"


def test_tek_parca_degismez():
    assert _with_overlap(["tek"]) == ["tek"]


def test_bos_liste_cokmez():
    assert _with_overlap([]) == []


def test_bolunmus_bilgi_bir_chunkta_butun_kalir():
    """Asıl amaç: sınıra denk gelen bilgi en az bir chunk'ta tam görünmeli."""
    govde = ("dolgu. " * 1200) + "KÂR ORANI %35 VADE 12 AY"
    parcalar = _split(govde, 8196)
    assert len(parcalar) > 1, "test anlamlı olsun diye bölünmeli"
    assert any("KÂR ORANI %35 VADE 12 AY" in p for p in parcalar)


def test_uzun_metin_8196_siniri(monkeypatch):
    govde = "a" * 20000
    parcalar = _split(govde, 8196)
    # overlap eklendiği için parçalar sınırı biraz aşabilir; taban gövde 8196
    assert all(len(p) <= 8196 + settings.INDEX_CHUNK_OVERLAP_CHARS + 2
               for p in parcalar)
