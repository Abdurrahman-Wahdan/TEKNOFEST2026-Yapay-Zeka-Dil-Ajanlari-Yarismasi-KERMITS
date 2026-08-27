"""Mükerrer tablo birleşirken REFERANSLAR kaybolmaz.

sources.update() aynı bankanın canon'daki kaynaklarını dup'ınkilerle EZİYORDU;
birleşen tablonun yarısının referansı sessizce kayboluyordu.
"""
import json
import pytest

from dataprep.compare import dedup, store, synth


@pytest.fixture
def kok(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path)
    monkeypatch.setattr(store, "REGISTRY", tmp_path / "_registry.json")
    monkeypatch.setattr(store, "SUBCATS", tmp_path / "_subcategories.json")
    monkeypatch.setattr(store, "LEDGER", tmp_path / "_page_ledger.json")
    monkeypatch.setattr(store, "URL_HAVUZ", tmp_path / "_url_havuzu.json")
    return tmp_path


def _src(pid, url):
    return {"point_id": pid, "url": url, "note": "n",
            "gecerlilik_baslangic": "", "gecerlilik_bitis": "",
            "validity_status": ""}


def test_ayni_bankanin_kaynaklari_EZILMEZ(kok, monkeypatch):
    a = store.create_table("A", "d", ["x"], {"tombank": {"x": "1"}},
                           {"tombank": [_src("p1", "https://a")]}, "ürün", "s")
    b = store.create_table("B", "d", ["x"], {"tombank": {"x": "2"}},
                           {"tombank": [_src("p2", "https://b")]}, "ürün", "s")
    monkeypatch.setattr(synth, "merge_tables", lambda x, y, c: {
        "docstring": "d", "columns": ["x"], "rows": {"tombank": {"x": "1"}},
        "category": "ürün", "subcategory": "s", "cell_sources": {}})
    monkeypatch.setattr(dedup, "index_table", lambda *a, **k: None)
    assert dedup.merge_pair(a, b)
    pidler = {s["point_id"] for s in store.load_table(a)["sources"]["tombank"]}
    assert pidler == {"p1", "p2"}, f"referans kayboldu: {pidler}"


def test_ayni_point_id_cogalmaz(kok, monkeypatch):
    a = store.create_table("A", "d", ["x"], {"tombank": {"x": "1"}},
                           {"tombank": [_src("p1", "https://a")]}, "ürün", "s")
    b = store.create_table("B", "d", ["x"], {"tombank": {"x": "2"}},
                           {"tombank": [_src("p1", "https://a")]}, "ürün", "s")
    monkeypatch.setattr(synth, "merge_tables", lambda x, y, c: {
        "docstring": "d", "columns": ["x"], "rows": {"tombank": {"x": "1"}},
        "category": "ürün", "subcategory": "s", "cell_sources": {}})
    monkeypatch.setattr(dedup, "index_table", lambda *a, **k: None)
    dedup.merge_pair(a, b)
    assert len(store.load_table(a)["sources"]["tombank"]) == 1


def test_farkli_bankalar_korunur(kok, monkeypatch):
    a = store.create_table("A", "d", ["x"], {"tombank": {"x": "1"}},
                           {"tombank": [_src("p1", "u1")]}, "ürün", "s")
    b = store.create_table("B", "d", ["x"], {"albaraka": {"x": "2"}},
                           {"albaraka": [_src("p2", "u2")]}, "ürün", "s")
    monkeypatch.setattr(synth, "merge_tables", lambda x, y, c: {
        "docstring": "d", "columns": ["x"],
        "rows": {"tombank": {"x": "1"}, "albaraka": {"x": "2"}},
        "category": "ürün", "subcategory": "s", "cell_sources": {}})
    monkeypatch.setattr(dedup, "index_table", lambda *a, **k: None)
    dedup.merge_pair(a, b)
    assert set(store.load_table(a)["sources"]) == {"tombank", "albaraka"}


def test_url_havuzu_guncellenir(kok, monkeypatch):
    a = store.create_table("A", "d", ["x"], {"tombank": {"x": "1"}},
                           {"tombank": [_src("p1", "https://a")]}, "ürün", "s")
    b = store.create_table("B", "d", ["x"], {"tombank": {"x": "2"}},
                           {"tombank": [_src("p2", "https://b")]}, "ürün", "s")
    monkeypatch.setattr(synth, "merge_tables", lambda x, y, c: {
        "docstring": "d", "columns": ["x"], "rows": {"tombank": {"x": "1"}},
        "category": "ürün", "subcategory": "s", "cell_sources": {}})
    monkeypatch.setattr(dedup, "index_table", lambda *a, **k: None)
    dedup.merge_pair(a, b)
    havuz = store.load_url_pool().get("tombank", {})
    assert {"https://a", "https://b"} <= set(havuz)


def test_llm_ulasilamazsa_hicbir_sey_degismez(kok, monkeypatch):
    a = store.create_table("A", "d", ["x"], {"tombank": {"x": "1"}},
                           {"tombank": [_src("p1", "u1")]}, "ürün", "s")
    b = store.create_table("B", "d", ["x"], {"tombank": {"x": "2"}},
                           {"tombank": [_src("p2", "u2")]}, "ürün", "s")
    monkeypatch.setattr(synth, "merge_tables", lambda x, y, c: None)
    assert dedup.merge_pair(a, b) is False
    assert store.load_table(b) is not None, "başarısız birleşmede tablo silindi"


def test_asiri_genelleme_uyarisi_promptta():
    assert "AŞIRI GENELLEME YAPMA" in dedup._SYSTEM
    assert "BULANIK" in dedup._SYSTEM


def test_birlesen_tablo_INDEKSTEN_de_dusar(kok, monkeypatch):
    """delete_table yalnız dosya+registry siliyordu; Qdrant'ta kalan hayalet
    kayıt mükerrerlik aramasını kirletiyordu (canlı: 58 hayalet)."""
    a = store.create_table("A", "d", ["x"], {"tombank": {"x": "1"}},
                           {"tombank": [_src("p1", "u1")]}, "ürün", "s")
    b = store.create_table("B", "d", ["x"], {"tombank": {"x": "2"}},
                           {"tombank": [_src("p2", "u2")]}, "ürün", "s")
    monkeypatch.setattr(synth, "merge_tables", lambda x, y, c: {
        "docstring": "d", "columns": ["x"], "rows": {"tombank": {"x": "1"}},
        "category": "ürün", "subcategory": "s", "cell_sources": {}})
    monkeypatch.setattr(dedup, "index_table", lambda *a, **k: None)
    dusen = []
    monkeypatch.setattr(dedup, "drop_table_index", lambda tid: dusen.append(tid))
    dedup.merge_pair(a, b)
    assert dusen == [b], f"silinen tablo indeksten düşmedi: {dusen}"


def test_indeks_silme_hatasi_birlesmeyi_bozmaz(kok, monkeypatch):
    a = store.create_table("A", "d", ["x"], {"tombank": {"x": "1"}}, {}, "ürün", "s")
    b = store.create_table("B", "d", ["x"], {"tombank": {"x": "2"}}, {}, "ürün", "s")
    monkeypatch.setattr(synth, "merge_tables", lambda x, y, c: {
        "docstring": "d", "columns": ["x"], "rows": {"tombank": {"x": "1"}},
        "category": "ürün", "subcategory": "s", "cell_sources": {}})
    monkeypatch.setattr(dedup, "index_table", lambda *a, **k: None)
    monkeypatch.setattr(dedup, "drop_table_index",
                        lambda tid: (_ for _ in ()).throw(Exception("qdrant down")))
    assert dedup.merge_pair(a, b) is True, "indeks hatası birleşmeyi düşürdü"
    assert store.load_table(b) is None
