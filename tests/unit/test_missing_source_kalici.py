"""Kaynak dosyası olmayan öğeler KALICI işaretlenmeli.

İki sebep var, ikisi de kalıcı: (1) daha önce "gereksiz" bulunup silindi,
(2) crawl hiç indiremedi (403/404/kopma). Dosya kendiliğinden geri gelmez,
dolayısıyla her koşuda tekrar denemek boşuna — loglarda aynı PDF için 36 kez
"missing_source" birikmişti ve sayaçlar hiç kapanmıyordu.

URL katalogda kalır (iz kaybolmaz), sadece ledger'da 'silindi' işaretlenir.
"""

import json

import pytest

from dataprep import content as c

pytestmark = pytest.mark.unit


def _kur(tmp_path, monkeypatch, *, kind: str) -> tuple:
    site = tmp_path / "data" / "testbank_site"
    site.mkdir(parents=True)
    yol = "docs/yok.pdf" if kind == "pdf" else "tr/yok.md"
    (site / "_catalog.json").write_text(json.dumps(
        {"http://x/yok": {"kind": kind, "path": yol, "hash": "h1", "status": "ok"}}))
    monkeypatch.setattr(c, "__file__", str(tmp_path / "dataprep" / "content.py"))
    return site


def test_asama2_kaynak_yoksa_kalici_isaretlenir(tmp_path, monkeypatch):
    site = _kur(tmp_path, monkeypatch, kind="pdf")
    c.process_bank_pdf_text("testbank", workers=1)
    led = json.loads((site / "_pdf_clean_ledger.json").read_text(encoding="utf-8"))
    kayit = led["http://x/yok"]
    assert kayit["source_hash"] == "h1"          # hash eşleşir -> bir daha todo'ya girmez
    assert kayit["status"] == "silindi"
    assert kayit["relevance"] == "gereksiz"


def test_asama2_ikinci_kosuda_tekrar_denenmez(tmp_path, monkeypatch, caplog):
    site = _kur(tmp_path, monkeypatch, kind="pdf")
    c.process_bank_pdf_text("testbank", workers=1)
    with caplog.at_level("INFO"):
        c.process_bank_pdf_text("testbank", workers=1)
    assert "0 işlenecek" in caplog.text          # todo boş


def test_asama3_kaynak_yoksa_kalici_isaretlenir(tmp_path, monkeypatch):
    site = _kur(tmp_path, monkeypatch, kind="page")
    c.process_bank_images("testbank", workers=1)
    led = json.loads((site / "_content_ledger.json").read_text(encoding="utf-8"))
    kayit = led["http://x/yok"]
    assert kayit["source_hash"] == "h1"
    assert kayit["status"] == "silindi"
    assert kayit["output_path"] == ""            # veri setine girmez
