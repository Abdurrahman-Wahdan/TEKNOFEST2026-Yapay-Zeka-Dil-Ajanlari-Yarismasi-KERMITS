"""build_all: 10 bankanın raporu TEK LLM çağrısında tabloya işlenir.

Sıralı yol (build_row × 10) tablo başına ~3.9 dk sürüyor ve aynı tabloyu 10
kez taşıdığı için ~4.4 kat fazla bağlam üretiyordu. Tek çağrı hem daha az
bağlam taşır hem hızlıdır; ham chunk'lar prompt'a KONMAZ.
"""
from dataprep.compare import synth


def _rapor(bank, offers=True, pids=()):
    return {"bank": bank, "offers": offers, "attributes": {},
            "sources": [{"point_id": p, "url": f"https://{bank}/{p}",
                          "note": "n", "gecerlilik_baslangic": "",
                          "gecerlilik_bitis": "", "validity_status": ""}
                         for p in pids]}


def _cevap(monkeypatch, d):
    monkeypatch.setattr(synth.vlm, "call_json", lambda *a, **k: d)


def test_tum_bankalar_tek_cagrida(monkeypatch):
    cagri = []
    monkeypatch.setattr(synth.vlm, "call_json",
                        lambda *a, **k: cagri.append(1) or {
                            "docstring": "d", "columns": ["x"],
                            "rows": {"a": {"x": "1"}, "b": {"x": "2"}},
                            "kaynak_haritasi": {}})
    out = synth.build_all(None, "K", [_rapor("a"), _rapor("b")])
    assert len(cagri) == 1, "birden fazla LLM çağrısı yapıldı"
    assert set(out["rows"]) == {"a", "b"}


def test_sahte_banka_elenir(monkeypatch):
    _cevap(monkeypatch, {"docstring": "d", "columns": ["x"],
                          "rows": {"a": {"x": "1"}, "geçerlilik_alanı": {"x": "?"}},
                          "kaynak_haritasi": {}})
    out = synth.build_all(None, "K", [_rapor("a")])
    assert set(out["rows"]) == {"a"}


def test_raporu_gelen_banka_kaybolmaz(monkeypatch):
    """Model bir bankayı unutursa satırı BOŞ kalır, silinmez."""
    _cevap(monkeypatch, {"docstring": "d", "columns": ["x"],
                          "rows": {"a": {"x": "1"}}, "kaynak_haritasi": {}})
    out = synth.build_all(None, "K", [_rapor("a"), _rapor("b")])
    assert out["rows"]["b"] == {}


def test_mevcut_tablo_verisi_korunur(monkeypatch):
    onceki = {"docstring": "d", "columns": ["x"],
              "rows": {"eski": {"x": "KORUNMALI"}}, "cell_sources": {}}
    _cevap(monkeypatch, {"docstring": "d2", "columns": ["x"],
                          "rows": {"a": {"x": "1"}}, "kaynak_haritasi": {}})
    out = synth.build_all(onceki, "K", [_rapor("a")])
    assert out["rows"]["eski"]["x"] == "KORUNMALI"


def test_kaynak_haritasi_banka_bazli(monkeypatch):
    _cevap(monkeypatch, {"docstring": "d", "columns": ["x"],
                          "rows": {"a": {"x": "1"}, "b": {"x": "2"}},
                          "kaynak_haritasi": {"a": {"x": ["p1"]},
                                               "b": {"x": ["p2"]}}})
    out = synth.build_all(None, "K", [_rapor("a", pids=["p1"]),
                                       _rapor("b", pids=["p2"])])
    cs = out["cell_sources"]
    assert cs["a"]["x"][0]["url"] == "https://a/p1"
    assert cs["b"]["x"][0]["url"] == "https://b/p2"


def test_uydurma_point_id_yazilmaz(monkeypatch):
    _cevap(monkeypatch, {"docstring": "d", "columns": ["x"],
                          "rows": {"a": {"x": "1"}},
                          "kaynak_haritasi": {"a": {"x": ["YOK"]}}})
    out = synth.build_all(None, "K", [_rapor("a", pids=["p1"])])
    assert "a" not in out["cell_sources"]


def test_hayalet_sutun_anahtari_yoksayilir(monkeypatch):
    _cevap(monkeypatch, {"docstring": "d", "columns": ["x"],
                          "rows": {"a": {"x": "1"}},
                          "kaynak_haritasi": {"a": {"olmayan": ["p1"]}}})
    out = synth.build_all(None, "K", [_rapor("a", pids=["p1"])])
    assert "olmayan" not in out["cell_sources"].get("a", {})


def test_llm_ulasilamazsa_none(monkeypatch):
    _cevap(monkeypatch, None)
    assert synth.build_all(None, "K", [_rapor("a")]) is None


def test_bos_rapor_listesi_none(monkeypatch):
    assert synth.build_all(None, "K", []) is None
