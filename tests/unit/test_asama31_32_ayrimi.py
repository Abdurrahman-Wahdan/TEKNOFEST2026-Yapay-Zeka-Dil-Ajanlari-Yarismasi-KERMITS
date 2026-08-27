"""AŞAMA 3.1 (page image) ve 3.2 (pdf image) ayrı çalıştırılabilmeli.

İkisi AYNI ledger dosyasını (_content_ledger.json) paylaşıyor. Ledger tam
okunup tam yazıldığı için, 3.2'nin 3.1'in kayıtlarını ezmesi teorik bir risk —
bu testler o riskin gerçekleşmediğini sabitliyor.
"""

import json
import shutil

import pytest

from dataprep import content as c
from dataprep import vlm as v

pytestmark = pytest.mark.unit


@pytest.fixture
def banka(tmp_path, monkeypatch):
    """1 page + 1 pdf içeren sahte banka; aşama 2 çıktısı hazır."""
    kaynak = next(__import__("pathlib").Path("data").rglob("*.pdf"), None)
    if kaynak is None:
        pytest.skip("örnek PDF yok")
    site = tmp_path / "data" / "testbank_site"
    (site / "docs").mkdir(parents=True)
    (site / "tr").mkdir(parents=True)
    (site / "tr" / "a.md").write_text(
        "---\nurl: http://x/a\n---\nsayfa metni yeterince uzun olsun burada", encoding="utf-8")
    shutil.copy(kaynak, site / "docs" / "b.pdf")
    (site / "_pdf_clean" / "docs").mkdir(parents=True)
    (site / "_pdf_clean" / "docs" / "b.md").write_text(
        "---\nurl: http://x/b\n---\npdf metni burada yeterince uzun", encoding="utf-8")
    (site / "_catalog.json").write_text(json.dumps({
        "http://x/a": {"kind": "page", "path": "tr/a.md", "hash": "ha", "status": "ok", "images": []},
        "http://x/b": {"kind": "pdf", "path": "docs/b.pdf", "hash": "hb", "status": "ok"}}))
    (site / "_pdf_clean_ledger.json").write_text(json.dumps(
        {"http://x/b": {"source_hash": "hb", "relevance": "gerekli"}}))
    monkeypatch.setattr(c, "__file__", str(tmp_path / "dataprep" / "content.py"))
    monkeypatch.setattr(v, "call_json",
                        lambda *a, **k: {"content": "x", "musteri_icerigi": "gerekli"})
    return site


def _ledger(site) -> set:
    p = site / "_content_ledger.json"
    return set(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else set()


def test_31_sadece_page_isler(banka):
    c.process_bank_images("testbank", workers=1, only_kind="page")
    assert _ledger(banka) == {"http://x/a"}


def test_32_sadece_pdf_isler(banka):
    c.process_bank_images("testbank", workers=1, only_kind="pdf")
    assert _ledger(banka) == {"http://x/b"}


def test_32_calisinca_31in_kayitlari_korunur(banka):
    """Asıl risk: ortak ledger'ın ezilmesi."""
    c.process_bank_images("testbank", workers=1, only_kind="page")
    c.process_bank_images("testbank", workers=1, only_kind="pdf")
    assert _ledger(banka) == {"http://x/a", "http://x/b"}


def test_tekrar_kosu_idempotent(banka):
    c.process_bank_images("testbank", workers=1, only_kind="page")
    c.process_bank_images("testbank", workers=1, only_kind="pdf")
    once = _ledger(banka)
    c.process_bank_images("testbank", workers=1, only_kind="page")
    assert _ledger(banka) == once


def test_32_asama2_ciktisi_yoksa_llme_gitmez(banka, monkeypatch):
    """pdf_text_not_ready: aşamalar ayrık kalmalı."""
    (banka / "_pdf_clean" / "docs" / "b.md").unlink()
    cagri = {"n": 0}
    monkeypatch.setattr(v, "call_json",
                        lambda *a, **k: (cagri.__setitem__("n", cagri["n"] + 1), {})[1])
    c.process_bank_images("testbank", workers=1, only_kind="pdf")
    assert cagri["n"] == 0
    assert _ledger(banka) == set()          # ledger'a yazılmaz -> tekrar denenir
