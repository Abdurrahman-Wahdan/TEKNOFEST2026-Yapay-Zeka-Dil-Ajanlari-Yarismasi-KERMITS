"""Bozuk kopyalanmis point_id'ler kurtarilir, uydurmalar elenir.

Gozlem: LLM point_id'yi raporlarken bir karakter dusurebiliyor
('133b361-...' — UUID ilk blogu 7 hane). Kaynak GERCEK oldugu icin
tek harf yuzunden kanit dusurulmemeli; ama tamamen ilgisiz (uydurma)
bir id de asla yanlis bir kaynaga baglanmamali.
"""
from dataprep.compare.bank_agent import _resolve_sources, _yakin_point_id

GERCEK = "133b3610-b163-42fc-be60-f85a85c5c7c6"
BASKA = "aaaaaaaa-1111-2222-3333-444444444444"


def _pm():
    bos = {"gecerlilik_baslangic": "", "gecerlilik_bitis": "", "validity_status": ""}
    return {GERCEK: {"url": "https://x/y.pdf", **bos},
            BASKA: {"url": "https://z", **bos}}


def test_karakter_dusmus_id_kurtarilir():
    assert _yakin_point_id("133b361-b163-42fc-be60-f85a85c5c7c6", _pm()) == GERCEK


def test_dogru_id_aynen_bulunur():
    assert _yakin_point_id(GERCEK, _pm()) == GERCEK


def test_uydurma_id_elenir():
    assert _yakin_point_id("deadbeef-0000-0000-0000-000000000000", _pm()) is None


def test_bos_ve_cok_kisa_elenir():
    assert _yakin_point_id("", _pm()) is None
    assert _yakin_point_id("133b361", _pm()) is None


def test_kurtarilan_kaynak_url_ile_doner():
    src = [{"point_id": "133b361-b163-42fc-be60-f85a85c5c7c6", "note": "t"}]
    out = _resolve_sources(src, _pm(), "ziraatkatilim", "konu")
    assert len(out) == 1
    assert out[0]["url"] == "https://x/y.pdf"
    assert out[0]["point_id"] == GERCEK


def test_uydurma_kaynak_dusurulur():
    src = [{"point_id": "deadbeef-0000-0000-0000-000000000000", "note": "t"}]
    assert _resolve_sources(src, _pm(), "x", "y") == []
