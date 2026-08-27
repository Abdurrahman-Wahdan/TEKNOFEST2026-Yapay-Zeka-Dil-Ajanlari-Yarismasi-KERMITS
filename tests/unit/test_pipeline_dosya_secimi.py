"""Pipeline her sayfayı EN ZENGİN sürümüyle, bir kez işlemeli.

Bir URL diskte 2-3 dosyayla temsil ediliyor:
  content/<yol>.md    -> AŞAMA 3: metin + GÖRSEL bilgisi + tarihler (EN ZENGİN)
  <yol>.md            -> AŞAMA 1: sadece temizlenmiş HTML metni
  _pdf_clean/<yol>.md -> AŞAMA 2: PDF metni
  _raw/<yol>.md       -> ham HTML, frontmatter YOK

Canlı ölçüm (2026-08-19, kuveytturk): rglob ham listeyi verdiği için 1662 URL'in
845'inde ÖNCE crawl çıktısı işleniyor, content/ sürümü "zaten işlendi" diye
atlanıyordu — görsellerden gelen bilgi ve tarihler tabloya HİÇ girmiyordu.
"""

from pathlib import Path

import pytest

from dataprep.compare.pipeline import _tercihli_dosyalar

pytestmark = pytest.mark.unit


@pytest.fixture
def site(tmp_path):
    s = tmp_path / "testbank_site"
    for alt, govde in (
        ("content/a.md", "zengin: metin + gorsel"),
        ("a.md", "sadece metin"),
        ("_raw/a.md", "ham html"),
        ("_pdf_clean/b.md", "pdf metni"),
        ("content/c.md", "yalniz content"),
        ("d.md", "yalniz crawl"),
    ):
        p = s / alt
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f'---\nurl: "http://x/{Path(alt).stem}"\n---\n{govde}', encoding="utf-8")
    return s


def test_content_surumu_tercih_edilir(site):
    secilen = _tercihli_dosyalar(site)
    a = [p for p in secilen if p.name == "a.md"]
    assert len(a) == 1
    assert "content" in a[0].parts          # crawl çıktısı DEĞİL


def test_raw_hic_secilmez(site):
    """_raw'da frontmatter yok; karşılığı content'te tam olarak var."""
    assert not any("_raw" in p.parts for p in _tercihli_dosyalar(site))


def test_content_disi_benzersiz_icerik_KACMAZ(site):
    """content'te olmayanlar (PDF, henüz işlenmemiş sayfa) yine de gelmeli."""
    adlar = {p.name for p in _tercihli_dosyalar(site)}
    assert "b.md" in adlar                  # _pdf_clean
    assert "d.md" in adlar                  # yalnız crawl çıktısı


def test_ayni_sayfa_iki_kez_gelmez(site):
    secilen = _tercihli_dosyalar(site)
    assert len(secilen) == len({p.name for p in secilen})


def test_gercek_veride_gereksiz_secilmez():
    """content/ yalnızca 'gerekli' içerir; seçim de öyle olmalı."""
    import re
    kok = Path("data")
    if not kok.exists():
        pytest.skip("veri yok")
    for s in list(kok.glob("*_site"))[:2]:
        for p in _tercihli_dosyalar(s)[:50]:
            m = re.search(r'content_relevance:\s*"?(\w+)', p.read_text(encoding="utf-8")[:600])
            if m:
                assert m.group(1) != "gereksiz"
