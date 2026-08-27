"""İndekslenemeyen tablo kuyruğa alınır ve OTOMATİK tamamlanır.

Eskiden hata yalnızca loglanıp unutuluyordu: tablo diskte kalıyor ama aramada
görünmüyordu, bu da mükerrerlik kontrolünü körleştirip aynı konuda ikinci bir
tablo açılmasına yol açıyordu ('kasko sigortası' 4 kez). Dışarıdan nöbetçi
script GEREKMEZ — pipeline kuyruğu kendi boşaltır.
"""
import pytest

from dataprep.compare import pipeline, store


@pytest.fixture
def kok(tmp_path, monkeypatch):
    for ad in ("ROOT",):
        monkeypatch.setattr(store, ad, tmp_path)
    monkeypatch.setattr(store, "REGISTRY", tmp_path / "_registry.json")
    monkeypatch.setattr(store, "SUBCATS", tmp_path / "_subcategories.json")
    monkeypatch.setattr(store, "LEDGER", tmp_path / "_page_ledger.json")
    monkeypatch.setattr(store, "INDEKS_KUYRUK", tmp_path / "_indeks_kuyrugu.json")
    return tmp_path


def test_basarisiz_indeks_kuyruga_alinir(kok, monkeypatch):
    monkeypatch.setattr(pipeline, "index_table",
                        lambda *a, **k: (_ for _ in ()).throw(Exception("no tunnel")))
    pipeline._indeksle("t1", "Konu", "ürün", "alt", "d")
    assert store.load_index_queue() == ["t1"]


def test_basarili_indeks_kuyruktan_duser(kok, monkeypatch):
    store.queue_for_index("t1")
    monkeypatch.setattr(pipeline, "index_table", lambda *a, **k: None)
    pipeline._indeksle("t1", "Konu", "ürün", "alt", "d")
    assert store.load_index_queue() == []


def test_ayni_id_iki_kez_girmez(kok):
    store.queue_for_index("t1")
    store.queue_for_index("t1")
    assert store.load_index_queue() == ["t1"]


def test_kuyruk_otomatik_bosaltilir(kok, monkeypatch):
    tid = store.create_table("K", "d", ["a"], {"b": {"a": "1"}}, {}, "ürün", "s")
    store.queue_for_index(tid)
    cagrilan = []
    monkeypatch.setattr(pipeline, "index_table",
                        lambda i, *a, **k: cagrilan.append(i))
    pipeline._kuyrugu_bosalt()
    assert cagrilan == [tid]
    assert store.load_index_queue() == []


def test_silinmis_tablo_kuyruktan_duser(kok, monkeypatch):
    store.queue_for_index("olmayan-tablo")
    monkeypatch.setattr(pipeline, "index_table", lambda *a, **k: None)
    pipeline._kuyrugu_bosalt()
    assert store.load_index_queue() == []


def test_servis_hala_bozuksa_kuyrukta_kalir(kok, monkeypatch):
    tid = store.create_table("K", "d", ["a"], {"b": {"a": "1"}}, {}, "ürün", "s")
    store.queue_for_index(tid)
    monkeypatch.setattr(pipeline, "index_table",
                        lambda *a, **k: (_ for _ in ()).throw(Exception("no tunnel")))
    pipeline._kuyrugu_bosalt()
    assert store.load_index_queue() == [tid], "kuyruktan haksız yere düştü"


def test_bozuk_kuyruk_dosyasi_cokmez(kok):
    (kok / "_indeks_kuyrugu.json").write_text("{bozuk", encoding="utf-8")
    assert store.load_index_queue() == []
    store.queue_for_index("t1")
    assert store.load_index_queue() == ["t1"]
