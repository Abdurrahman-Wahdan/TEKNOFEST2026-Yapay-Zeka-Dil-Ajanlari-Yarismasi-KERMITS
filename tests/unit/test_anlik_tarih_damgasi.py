"""Anlik tarih damgasi OPT-IN'dir (TABLO_ANLIK_TARIH=1).

Karar (kullanici, 2026-08-19): URL/tarih baglamasi EN SONDA, toplu ve agentic
yapilacak. Tablo uretimi sirasinda yalnizca KAYNAK HAVUZU biriktirilir.
Bu testler hem varsayilan KAPALI davranisi hem de acikken damganin dogru
calistigini korur.
"""
import json
import pytest

from dataprep.compare import store

SRC = [{"point_id": "p1", "url": "https://x", "gecerlilik_baslangic": "2026-01-01",
        "gecerlilik_bitis": "2026-06-30", "validity_status": ""}]


def _izole(monkeypatch, tmp_path):
    """store'un TÜM yollarını tmp_path'e taşı — ROOT tek başına YETMEZ:
    REGISTRY/LEDGER/SUBCATS/URL_HAVUZ modül yüklenirken sabitlenir, sadece
    ROOT yamalanınca testler GERÇEK data/_tables/_registry.json'a yazar
    (canlı koşuda kanıtlandı: registry'ye 7 adet sahte 'k' kaydı düştü)."""
    monkeypatch.setattr(store, "ROOT", tmp_path)
    monkeypatch.setattr(store, "REGISTRY", tmp_path / "_registry.json")
    monkeypatch.setattr(store, "LEDGER", tmp_path / "_page_ledger.json")
    monkeypatch.setattr(store, "SUBCATS", tmp_path / "_subcategories.json")
    monkeypatch.setattr(store, "URL_HAVUZ", tmp_path / "_url_havuzu.json")


@pytest.fixture
def kok(tmp_path, monkeypatch):
    _izole(monkeypatch, tmp_path)
    monkeypatch.setenv("TABLO_ANLIK_TARIH", "1")
    return tmp_path


def test_varsayilan_kapali_damga_yok(tmp_path, monkeypatch):
    """Varsayilan: tarih sutunu EKLENMEZ — havuz biriktirme asamasi."""
    _izole(monkeypatch, tmp_path)
    monkeypatch.delenv("TABLO_ANLIK_TARIH", raising=False)
    tid = store.create_table("K", "d", ["a"], {"tombank": {"a": "x"}},
                             {"tombank": []}, "urun", "t")
    d = json.loads((tmp_path / f"{tid}.json").read_text(encoding="utf-8"))
    assert d["columns"] == ["a"]
    assert "a (Gecerlilik)" not in d["columns"]


def _oku(kok, tid):
    return json.loads((kok / f"{tid}.json").read_text(encoding="utf-8"))


def test_create_table_aninda_damgalar(kok):
    tid = store.create_table(
        "K", "d", ["akit_turu"], {"tombank": {"akit_turu": "Karz"}},
        {"tombank": []}, "ürün", "t",
        cell_sources={"tombank": {"akit_turu": SRC}})
    d = _oku(kok, tid)
    assert "akit_turu (Geçerlilik)" in d["columns"]
    assert d["rows"]["tombank"]["akit_turu (Geçerlilik)"] == "01/01/2026 - 30/06/2026"


def test_kaynaksiz_hucre_tire_alir(kok):
    tid = store.create_table(
        "K", "d", ["a"], {"tombank": {"a": "x"}}, {"tombank": []}, "ürün", "t")
    d = _oku(kok, tid)
    assert d["rows"]["tombank"]["a (Geçerlilik)"] == "-"


def test_sunulmuyor_hucre_de_alan_alir(kok):
    tid = store.create_table(
        "K", "d", ["a"], {"tombank": {"a": "sunulmuyor"}}, {"tombank": []}, "ürün", "t")
    d = _oku(kok, tid)
    assert "a (Geçerlilik)" in d["columns"]


def test_tek_tarafli_tarih_soru_isareti(kok):
    src = [{"point_id": "p", "url": "u", "gecerlilik_baslangic": "2026-03-01",
            "gecerlilik_bitis": "", "validity_status": ""}]
    tid = store.create_table(
        "K", "d", ["a"], {"tombank": {"a": "x"}}, {"tombank": []}, "ürün", "t",
        cell_sources={"tombank": {"a": src}})
    d = _oku(kok, tid)
    assert d["rows"]["tombank"]["a (Geçerlilik)"] == "01/03/2026 - ?"


def test_overwrite_table_da_damgalar(kok):
    tid = store.create_table("K", "d", ["a"], {"tombank": {"a": "x"}},
                             {"tombank": []}, "ürün", "t")
    store.overwrite_table(tid, "d2", ["a", "b"],
                          {"tombank": {"a": "x", "b": "y"}}, {"tombank": []},
                          "ürün", "t",
                          cell_sources={"tombank": {"b": SRC}})
    d = _oku(kok, tid)
    assert d["rows"]["tombank"]["b (Geçerlilik)"] == "01/01/2026 - 30/06/2026"
    assert d["rows"]["tombank"]["a (Geçerlilik)"] == "-"


def test_her_veri_sutununun_tarih_sutunu_var(kok):
    tid = store.create_table(
        "K", "d", ["a", "b", "c"],
        {"tombank": {"a": "1", "b": "sunulmuyor", "c": "3"}},
        {"tombank": []}, "ürün", "t")
    d = _oku(kok, tid)
    veri = [c for c in d["columns"] if not c.endswith(" (Geçerlilik)")]
    for c in veri:
        assert f"{c} (Geçerlilik)" in d["columns"]
        assert d["rows"]["tombank"].get(f"{c} (Geçerlilik)")
