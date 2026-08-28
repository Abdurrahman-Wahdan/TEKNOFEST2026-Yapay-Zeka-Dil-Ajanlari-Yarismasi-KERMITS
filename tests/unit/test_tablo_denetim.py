"""Tablo denetim ajanı: kaynak başına ajan, chunk chunk denetim, tarih damgası."""
import json

from dataprep.compare import store, tablo_denetim as T


def test_kesisim_en_dar_araligi_secer():
    """Kullanıcı kararı: 2 farklı tarih varsa ikisinde ORTAK olan en dar aralık."""
    assert T._kesistir([("2026-01-01", "2026-12-31"),
                        ("2026-03-01", "2026-06-30")]) == ("2026-03-01", "2026-06-30")


def test_kesisim_tek_yanli_tarihler():
    assert T._kesistir([("2026-01-01", ""), ("", "2026-06-30")]) == ("2026-01-01", "2026-06-30")


def test_kesisim_bos_liste():
    assert T._kesistir([]) == ("", "")


def test_kesisim_cakismayan_aralik_tek_yanli_kalir():
    # başlangıç bitişten sonraysa kesişim boştur -> bitiş düşürülür
    b, s = T._kesistir([("2026-09-01", ""), ("", "2026-03-01")])
    assert b == "2026-09-01" and s == ""


def test_json_coz_kod_blogunu_ayiklar():
    assert T._json_coz('```json\n{"a": 1}\n```')[0] == {"a": 1}
    assert T._json_coz('bla {"a": 2} son')[0] == {"a": 2}
    assert T._json_coz("gecersiz")[0] is None
    assert T._json_coz(None)[0] is None


def test_json_coz_STATIK_eksik_key_bildirir():
    """Statik alanlarda eksik key hata sayılır; hata metni modele gider."""
    d, h = T._json_coz('{"eksikler": []}', ("bilgi_var", "eksikler"))
    assert d is None and "bilgi_var" in h


def test_json_coz_DINAMIK_semaya_karismaz():
    """columns/rows modelin kararıdır — beklenti dayatılmaz."""
    d, h = T._json_coz('{"columns": ["a"], "rows": {}}')
    assert d is not None and h == ""


def test_kalici_hata_ayrimi():
    assert T._kalici_hata(Exception("404"))
    assert T._kalici_hata(Exception("422"))
    assert not T._kalici_hata(Exception("400 bad gateway"))
    assert not T._kalici_hata(Exception("no tunnel here"))


def test_tarih_damgasi_satir_bazli(tmp_path, monkeypatch):
    tablo = {"columns": ["a"], "rows": {"x": {"a": "1"}, "y": {"a": "2"}}}
    raporlar = [
        {"bank": "x", "tarihler": [("2026-01-01", "2026-12-31"),
                                    ("2026-03-01", "2026-06-30")],
         "eksikler": [], "yeni_sutun": [], "chunk": 2, "url": "u"},
        {"bank": "y", "tarihler": [], "eksikler": [], "yeni_sutun": [],
         "chunk": 1, "url": "u2"},
    ]
    T._tarih_damgala(tablo, raporlar)
    assert "Geçerlilik" in tablo["columns"]
    assert tablo["rows"]["x"]["Geçerlilik"] == "01/03/2026 - 30/06/2026"
    assert tablo["rows"]["y"]["Geçerlilik"] == "-"      # tarih yoksa "-"


def test_her_kaynak_ayri_ajana_gider(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "ROOT", tmp_path)
    monkeypatch.setattr(store, "REGISTRY", tmp_path / "_registry.json")
    monkeypatch.setattr(store, "SUBCATS", tmp_path / "_subcategories.json")
    monkeypatch.setattr(store, "LEDGER", tmp_path / "_page_ledger.json")
    tid = store.create_table(
        "K", "d", ["a"], {"x": {"a": "1"}, "y": {"a": "2"}}, {}, "ürün", "s",
        cell_sources={
            "x": {"a": [{"point_id": "p1", "url": "https://u1"}]},
            "y": {"a": [{"point_id": "p2", "url": "https://u2"}]}})
    cagrilan = []

    def sahte(tablo, url, bank):
        cagrilan.append((bank, url))
        return {"url": url, "bank": bank, "eksikler": [], "yeni_sutun": [],
                "tarihler": [], "chunk": 1}

    monkeypatch.setattr(T, "_denetle_kaynak", sahte)
    monkeypatch.setattr(store, "overwrite_table", lambda *a, **k: None)
    T.denetle_tablo(tid)
    assert sorted(cagrilan) == [("x", "https://u1"), ("y", "https://u2")]


def test_llm_ulasilamazsa_tablo_bozulmaz(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "ROOT", tmp_path)
    monkeypatch.setattr(store, "REGISTRY", tmp_path / "_registry.json")
    monkeypatch.setattr(store, "SUBCATS", tmp_path / "_subcategories.json")
    monkeypatch.setattr(store, "LEDGER", tmp_path / "_page_ledger.json")
    tid = store.create_table(
        "K", "d", ["a"], {"x": {"a": "KORUNMALI"}}, {}, "ürün", "s",
        cell_sources={"x": {"a": [{"point_id": "p", "url": "https://u"}]}})
    monkeypatch.setattr(T, "_denetle_kaynak", lambda t, u, b: {
        "url": u, "bank": b, "tarihler": [], "chunk": 1,
        "eksikler": [{"sutun": "a", "deger": "YENI", "bank": b, "url": u}],
        "yeni_sutun": []})
    monkeypatch.setattr(T, "_cagir", lambda *a, **k: None)   # LLM ulaşılamıyor
    T.denetle_tablo(tid)
    assert store.load_table(tid)["rows"]["x"]["a"] == "KORUNMALI"


def _kur(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "ROOT", tmp_path)
    monkeypatch.setattr(store, "REGISTRY", tmp_path / "_registry.json")
    monkeypatch.setattr(store, "SUBCATS", tmp_path / "_subcategories.json")
    monkeypatch.setattr(store, "LEDGER", tmp_path / "_page_ledger.json")


def test_ajan_ciktisi_oldugu_gibi_kabul_edilir(monkeypatch, tmp_path):
    """Kod, ajanın sütun kararlarına KARIŞMAZ (kullanıcı kararı 2026-08-20):
    agentic sistemde kural tabanlı düzeltme, ajanın bilinçli birleştirmesini
    de bozar."""
    _kur(monkeypatch, tmp_path)
    tid = store.create_table(
        "K", "d", ["Kâr Oranı", "Kâr Payı Oranı"],
        {"x": {"Kâr Oranı": "%35", "Kâr Payı Oranı": "%35"}}, {}, "ürün", "s",
        cell_sources={"x": {"Kâr Oranı": [{"point_id": "p", "url": "u"}]}})
    monkeypatch.setattr(T, "_denetle_kaynak", lambda t, u, b: {
        "url": u, "bank": b, "tarihler": [], "chunk": 1,
        "eksikler": [{"sutun": "Kâr Oranı", "deger": "%35", "bank": b, "url": u}],
        "yeni_sutun": []})
    monkeypatch.setattr(T, "_cagir", lambda *a, **k: json.dumps({
        "columns": ["Kâr Oranı"], "rows": {"x": {"Kâr Oranı": "%35"}}}))
    T.denetle_tablo(tid)
    d = store.load_table(tid)
    assert [c for c in d["columns"] if c != "Geçerlilik"] == ["Kâr Oranı"]


def test_uydurma_banka_anahtari_elenir(monkeypatch, tmp_path):
    """Tek kalan kod kısıtı: model sütun adını banka sanabiliyor."""
    _kur(monkeypatch, tmp_path)
    tid = store.create_table("K", "d", ["a"], {"x": {"a": "1"}}, {}, "ürün", "s",
                             cell_sources={"x": {"a": [{"point_id": "p", "url": "u"}]}})
    monkeypatch.setattr(T, "_denetle_kaynak", lambda t, u, b: {
        "url": u, "bank": b, "tarihler": [], "chunk": 1,
        "eksikler": [{"sutun": "a", "deger": "2", "bank": b, "url": u}],
        "yeni_sutun": []})
    monkeypatch.setattr(T, "_cagir", lambda *a, **k: json.dumps({
        "columns": ["a"], "rows": {"x": {"a": "2"}, "geçerlilik_alanı": {"a": "?"}}}))
    T.denetle_tablo(tid)
    assert set(store.load_table(tid)["rows"]) == {"x"}


def test_tablolar_PARALEL_islenir(monkeypatch):
    """Tablolar sırayla değil paralel işlenir (kullanıcı kararı 2026-08-20):
    bir tablo beklerken diğerleri ilerlesin."""
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    aktif = []
    ez = []
    sayac = threading.Lock()
    engel = threading.Barrier(T.TABLO_WORKERS, timeout=5)

    def sahte(tid, kuru=False):
        with sayac:
            aktif.append(1)
            ez.append(len(aktif))
        engel.wait()                      # hepsi aynı anda içeride olmalı
        with sayac:
            aktif.pop()

    with ThreadPoolExecutor(max_workers=T.TABLO_WORKERS) as ex:
        f = [ex.submit(sahte, f"t{i}") for i in range(T.TABLO_WORKERS)]
        for x in as_completed(f):
            x.result()
    assert max(ez) == T.TABLO_WORKERS, f"paralel çalışmadı: {ez}"


def test_tablo_workers_ayari():
    assert T.TABLO_WORKERS >= 2, "tablolar paralel işlenemez"
