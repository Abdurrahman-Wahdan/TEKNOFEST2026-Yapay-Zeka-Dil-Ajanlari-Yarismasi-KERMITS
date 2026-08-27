"""Kapanış (SIGTERM/kill) sırasında veri kaybı olmadığının kanıtı.

Yavaş VLM yüzünden koşan bir iş sık sık durdurulup yeniden başlatılıyor. Kural:
bir öğe TAM olarak işlenmediyse ledger'a "işlendi" YAZILMAZ — böylece sonraki
koşuda baştan denenir. En kötü sonuç sınırlı iş TEKRARI olmalı, veri kaybı asla.
Buradaki her test o zincirin bir halkasını tutuyor.
"""

import json
import tempfile
from pathlib import Path

import pytest

from dataprep import content as c
from dataprep import vlm as v

pytestmark = pytest.mark.unit


@pytest.fixture
def stopping():
    """Kapanış bayrağını kaldırıp testten sonra mutlaka indir."""
    v.STOPPING.set()
    yield
    v.STOPPING.clear()


@pytest.fixture
def kopuk_ag(monkeypatch):
    def patla(body):
        raise RuntimeError("ağ koptu")
    monkeypatch.setattr(v, "_stream_once", patla)


# ----- kapanış sonsuz retry'ı bloklamamalı -----

def test_kapanista_post_bos_doner_sonsuz_beklemez(stopping, kopuk_ag):
    """'asla pes etme' sunucu hatası içindir; operatör durdurduysa geçerli değil."""
    assert v._post({"messages": [{"role": "user", "content": "x"}]}) == ""


def test_kapanista_call_json_bos_sozluk_doner(stopping, kopuk_ag):
    assert v.call_json(v.txt_msg("test")) == {}


# ----- yarım veri "tamamlanmış" sayılmamalı -----

def test_yarim_kesilmis_json_parse_edilmez():
    """Kısmi yanıt geçerli JSON'a benzeyebilir; yutulursa eksik veri kalıcılaşır."""
    tam = '{"decorative": false, "content": "Kampanya", "gecerlilik_bitis": "2026-09-30"}'
    assert v._try_parse_json(tam[:40]) is None
    assert v._try_parse_json(tam) is not None      # yanlış pozitif olmasın


def test_akis_koparsa_kismi_icerik_atilir_bastan_denenir(monkeypatch):
    cagrilar = {"n": 0}

    def sahte(body):
        cagrilar["n"] += 1
        if cagrilar["n"] == 1:
            return ("YARIM ICERIK", False)          # akış ortada koptu
        return ('{"content":"TAM"}', True)

    monkeypatch.setattr(v, "_stream_once", sahte)
    out = v._post({"messages": [{"role": "user", "content": "x"}]})
    assert "YARIM" not in out
    assert out == '{"content":"TAM"}'


def test_kopma_sonrasi_istek_sifirdan_gider(monkeypatch):
    """İkinci deneme, ilkiyle BİREBİR aynı istek olmalı — kısmi yanıt sızmamalı.

    "Kaldığın yerden devam et" yolu bilerek kaldırıldı: model tekrar/atlama
    yapabilir ve sessizce bozuk içerik üretebilirdi.
    """
    gonderilen = []
    cagrilar = {"n": 0}

    def sahte(body):
        cagrilar["n"] += 1
        gonderilen.append([m.get("content") for m in body["messages"]])
        if cagrilar["n"] == 1:
            return ("YARIM KALMIS ICERIK", False)      # akış ortada koptu
        return ('{"content":"TAM"}', True)

    monkeypatch.setattr(v, "_stream_once", sahte)
    out = v._post({"messages": [{"role": "user", "content": "orijinal soru"}]})

    assert gonderilen[1] == gonderilen[0]               # sıfırdan, aynı istek
    assert not any("YARIM" in str(m) for m in gonderilen[1])
    assert "YARIM" not in out


def test_cok_parcali_belgede_bir_parca_duserse_none(stopping, kopuk_ag):
    """Kısmi metin döndürülseydi çağıran onu ledger'a 'işlendi' yazardı."""
    dates = {"start": "", "end": "", "guess": "", "relevance": "gerekli"}
    assert c.clean_text("A" * 9000, "http://x/y.pdf", dates) is None


def test_gorsel_ulasilmazsa_all_ok_false():
    class UlasilamayanCache:
        data: dict = {}

        def examine(self, png):
            return None

    _, all_ok = c.clean_images([("g1", b"x"), ("g2", b"y")], UlasilamayanCache(), {})
    assert all_ok is False


# ----- ledger yazımı -----

def test_ledger_atomik_yazilir_yarim_tmp_birakmaz():
    path = Path(tempfile.mkdtemp()) / "l.json"
    c._write_ledger(path, {"u1": {"status": "ok"}})
    assert json.loads(path.read_text(encoding="utf-8")) == {"u1": {"status": "ok"}}
    assert not path.with_suffix(path.suffix + ".tmp").exists()


def test_ledger_sure_dolunca_da_yazilir():
    """Yavaş VLM'de 20 öğe saatler sürebiliyor; sadece sayıya bakmak yetmez."""
    import time
    assert c._should_flush(3, time.time() - 99) is True
    assert c._should_flush(3, time.time()) is False
    assert c._should_flush(c._LEDGER_EVERY_N, time.time()) is True


# ----- geri dönüşsüz silme -----

def _gereksiz_pdf_kos(tmp_path, monkeypatch, *, kapanis: bool) -> dict:
    """Tek 'gereksiz' PDF'li sahte bir banka üzerinde aşama 2'yi koştur."""
    site = tmp_path / "data" / "testbank_site"
    (site / "docs").mkdir(parents=True)
    ham = site / "docs" / "a.pdf"
    ham.write_bytes(b"%PDF-1.4 sahte")
    temiz = site / "_pdf_clean" / "docs" / "a.md"
    temiz.parent.mkdir(parents=True)
    temiz.write_text("temiz metin", encoding="utf-8")
    (site / "_catalog.json").write_text(json.dumps(
        {"http://x/a.pdf": {"kind": "pdf", "path": "docs/a.pdf", "hash": "h1", "status": "ok"}}))

    def sahte_clean_text(body, url, dates, title=""):
        dates["relevance"] = "gereksiz"          # LLM 'gereksiz' dedi
        return "temizlenmis metin"

    monkeypatch.setattr(c, "clean_text", sahte_clean_text)
    monkeypatch.setattr(c, "_pdf_text", lambda p: "A" * 500)
    monkeypatch.setattr(c, "__file__", str(tmp_path / "dataprep" / "content.py"))
    if kapanis:
        v.STOPPING.set()
    c.process_bank_pdf_text("testbank", workers=1)
    led = site / "_pdf_clean_ledger.json"
    return {"ham_var": ham.exists(), "temiz_var": temiz.exists(),
            "ledger": json.loads(led.read_text(encoding="utf-8")) if led.exists() else {}}


def test_normal_kosuda_gereksiz_pdf_silinir(tmp_path, monkeypatch):
    """Kapanış koruması normal davranışı BOZMAMALI — gereksizler yine silinir."""
    r = _gereksiz_pdf_kos(tmp_path, monkeypatch, kapanis=False)
    assert r["ham_var"] is False                 # ham PDF silindi
    assert r["temiz_var"] is False               # türetilmiş .md de silindi
    assert r["ledger"]["http://x/a.pdf"]["relevance"] == "gereksiz"


def test_kapanista_gereksiz_pdf_silinmez_ertelenir(tmp_path, monkeypatch, stopping):
    """Kapanışta 'gereksiz' oyu eksik veriye dayanabilir; silme geri alınamaz."""
    r = _gereksiz_pdf_kos(tmp_path, monkeypatch, kapanis=True)
    assert r["ham_var"] is True                  # ham PDF KORUNDU
    assert r["ledger"] == {}                     # işlendi sayılmadı -> tekrar denenecek


def test_is_dongusu_ve_vlm_ayni_bayraga_bakar():
    """Ayrı bayrak olsaydı retry döngüsü kapanışı saatlerce bloklardı."""
    assert c._STOPPING is v.STOPPING
