"""Kaynak eşleştirmesi TAHMİNE dayanmaz — yanlış kaynak, eksik kaynaktan kötüdür.

Karar (kullanıcı, 2026-08-19): bir hücreye "o bankanın herhangi bir kaynağı"
yedek olarak bağlanmaz. Banka doğru olsa bile o kaynak tam O SÜTUNUN bilgisini
içermeyebilir; alakasız bir URL/tarih damgalanması, hücrenin kaynaksız
kalmasından daha kötüdür (yanlış tarih sessizce doğru görünür).

Kaynaksız kalan hücreler sonda TOPLU, agentic bir URL/tarih atama adımıyla
eşleştirilir. Bu testler o ilkeyi korur: yalnız modelin AÇIKÇA eşleştirdiği
point_id'ler cell_sources'a girer.
"""
from dataprep.compare import synth


def _rapor(pids):
    return {"bank": "tombank",
            "sources": [{"point_id": p, "url": f"https://x/{p}.pdf",
                          "gecerlilik_baslangic": "", "gecerlilik_bitis": "",
                          "validity_status": ""} for p in pids]}


def _cagir(monkeypatch, cevap, table=None, report=None):
    monkeypatch.setattr(synth.vlm, "call_json", lambda *a, **k: cevap)
    return synth.build_row(table, "konu", "tombank", report or _rapor(["p1"]))


def test_modelin_eslestirdigi_kaynak_yazilir(monkeypatch):
    out = _cagir(monkeypatch, {
        "docstring": "d", "columns": ["akit_turu", "getiri"],
        "rows": {"tombank": {"akit_turu": "Karz", "getiri": "yok"}},
        "kaynak_haritasi": {"akit_turu": ["p1"]}})
    cs = out["cell_sources"]["tombank"]
    assert [s["point_id"] for s in cs["akit_turu"]] == ["p1"]


def test_eslestirilmeyen_hucreye_TAHMIN_kaynak_baglanmaz(monkeypatch):
    """Asıl korunan ilke: getiri sütunu eşleştirilmedi -> kaynaksız KALMALI."""
    out = _cagir(monkeypatch, {
        "docstring": "d", "columns": ["akit_turu", "getiri"],
        "rows": {"tombank": {"akit_turu": "Karz", "getiri": "yok"}},
        "kaynak_haritasi": {"akit_turu": ["p1"]}},
        report=_rapor(["p1", "p2"]))
    cs = out["cell_sources"]["tombank"]
    assert "getiri" not in cs, "alakasız kaynak bağlandı"


def test_harita_bos_ise_hic_kaynak_yazilmaz(monkeypatch):
    out = _cagir(monkeypatch, {
        "docstring": "d", "columns": ["a"],
        "rows": {"tombank": {"a": "x"}},
        "kaynak_haritasi": {}})
    assert "tombank" not in out["cell_sources"]


def test_hayalet_sutun_anahtari_yoksayilir(monkeypatch):
    out = _cagir(monkeypatch, {
        "docstring": "d", "columns": ["akit_turu"],
        "rows": {"tombank": {"akit_turu": "Karz"}},
        "kaynak_haritasi": {"olmayan_sutun": ["p1"]}})
    assert "olmayan_sutun" not in out["cell_sources"].get("tombank", {})


def test_baska_bankanin_satirina_kaynak_sizmaz(monkeypatch):
    onceki = {"docstring": "d", "columns": ["a"],
              "rows": {"adilkatilim": {"a": "x"}},
              "cell_sources": {"adilkatilim": {"a": [
                  {"point_id": "A1", "url": "https://adil",
                   "gecerlilik_baslangic": "", "gecerlilik_bitis": "",
                   "validity_status": ""}]}}}
    out = _cagir(monkeypatch, {
        "docstring": "d", "columns": ["a", "b"],
        "rows": {"adilkatilim": {"a": "x", "b": "y"},
                  "tombank": {"a": "x", "b": "y"}},
        "kaynak_haritasi": {"b": ["p1"]}}, table=onceki)
    cs = out["cell_sources"]
    # adilkatilim'in yeni 'b' hücresi kaynaksız kalır (tahmin yapılmaz)
    assert "b" not in cs["adilkatilim"]
    # ve kendi mevcut kaynağı bozulmaz
    assert cs["adilkatilim"]["a"][0]["url"] == "https://adil"
    # tombank kendi eşleştirdiği kaynağı alır
    assert cs["tombank"]["b"][0]["url"].endswith("p1.pdf")
