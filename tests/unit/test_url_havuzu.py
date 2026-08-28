"""Banka bazlı BENZERSİZ URL havuzu.

URL/tarih bağlaması en sonda toplu+agentic yapılacağı için, tablo üretimi
sırasında görülen her kaynak banka bazında ve benzersiz olarak biriktirilir.
Aynı URL birçok hücrede/tabloda tekrar geçer — havuz tek kayda indirger.
"""
import pytest

from dataprep.compare import store


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
    return tmp_path


def _src(pid, url, bas="", bit=""):
    return {"point_id": pid, "url": url, "gecerlilik_baslangic": bas,
            "gecerlilik_bitis": bit, "validity_status": ""}


def test_banka_bazli_kaydeder(kok):
    store.record_url_pool({"tombank": [_src("p1", "https://a")],
                            "albaraka": [_src("p2", "https://b")]}, "t1")
    h = store.load_url_pool()
    assert set(h) == {"tombank", "albaraka"}
    assert "https://a" in h["tombank"]
    assert "https://b" in h["albaraka"]


def test_ayni_url_cogalmaz(kok):
    store.record_url_pool({"tombank": [_src("p1", "https://a")]}, "t1")
    store.record_url_pool({"tombank": [_src("p1", "https://a")]}, "t1")
    store.record_url_pool({"tombank": [_src("p1", "https://a")]}, "t2")
    h = store.load_url_pool()
    assert list(h["tombank"]) == ["https://a"]
    assert h["tombank"]["https://a"]["point_ids"] == ["p1"]
    assert h["tombank"]["https://a"]["tables"] == ["t1", "t2"]


def test_ayni_url_farkli_point_id_birikir(kok):
    store.record_url_pool({"tombank": [_src("p1", "https://a")]}, "t1")
    store.record_url_pool({"tombank": [_src("p2", "https://a")]}, "t1")
    h = store.load_url_pool()
    assert h["tombank"]["https://a"]["point_ids"] == ["p1", "p2"]


def test_mevcut_tarih_EZILMEZ(kok):
    store.record_url_pool({"tombank": [_src("p1", "https://a", "2026-01-01", "2026-06-30")]}, "t1")
    store.record_url_pool({"tombank": [_src("p1", "https://a", "1999-01-01", "1999-12-31")]}, "t2")
    k = store.load_url_pool()["tombank"]["https://a"]
    assert k["gecerlilik_baslangic"] == "2026-01-01"
    assert k["gecerlilik_bitis"] == "2026-06-30"


def test_bos_tarih_sonradan_doldurulur(kok):
    store.record_url_pool({"tombank": [_src("p1", "https://a")]}, "t1")
    store.record_url_pool({"tombank": [_src("p1", "https://a", "2026-02-02", "")]}, "t2")
    assert store.load_url_pool()["tombank"]["https://a"]["gecerlilik_baslangic"] == "2026-02-02"


def test_urlsiz_kaynak_havuza_girmez(kok):
    store.record_url_pool({"tombank": [_src("p1", ""), _src("p2", "   ")]}, "t1")
    assert store.load_url_pool().get("tombank", {}) == {}


def test_bos_girdi_dosya_olusturmaz(kok):
    store.record_url_pool({}, "t1")
    assert store.load_url_pool() == {}


def test_bozuk_dosya_cokmez(kok):
    (kok / "_url_havuzu.json").write_text("{bozuk", encoding="utf-8")
    assert store.load_url_pool() == {}
    store.record_url_pool({"tombank": [_src("p1", "https://a")]}, "t1")
    assert "https://a" in store.load_url_pool()["tombank"]
