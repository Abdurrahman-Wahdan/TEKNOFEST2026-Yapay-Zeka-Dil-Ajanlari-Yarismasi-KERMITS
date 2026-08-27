"""200 dönen ama görsel OLMAYAN yanıtlar kalıcı atlanmalı — gerçek görsel kaçmadan.

Canlı olay (2026-08-19, kuveytturk): 'SitemapNodePage-*.vsf' URL'lerinin tamamı
200 + text/html (197KB ana sayfa) dönüyordu — görsel silinmiş, site 404 yerine
sayfayı veriyor (soft 404). Kalan 519 sayfadaki 13.629 görselin ~%79'u bu
türdendi; her biri 5 kez denenip worker'ı meşgul ediyordu.

KRİTİK: karar content-type'a DEĞİL içeriğe dayanmalı — yanlış content-type ile
gelen GERÇEK görseller kaybolmamalı.
"""

import io

import pytest
from PIL import Image

from dataprep.images import MIN_PX, _to_png

pytestmark = pytest.mark.unit


def _gorsel(w: int, h: int, bicim: str = "PNG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (120, 90, 60)).save(buf, format=bicim)
    return buf.getvalue()


# ----- gerçek görseller KAÇMAMALI -----

@pytest.mark.parametrize("bicim", ["PNG", "JPEG", "WEBP", "GIF", "BMP"])
def test_gercek_gorsel_her_bicimde_gecer(bicim):
    """Site .vsf gibi tuhaf uzantı/content-type verse bile içerik görselse geçer."""
    assert _to_png(_gorsel(400, 300, bicim)) is not None


def test_buyuk_gorsel_gecer():
    assert _to_png(_gorsel(1200, 800)) is not None


def test_sinirdaki_gorsel_gecer():
    """MIN_PX=0 (alt sınır YOK) — 1x1 görsel bile geçmeli.

    Eskiden `_gorsel(MIN_PX, MIN_PX)` yazıyordu; MIN_PX 0'a çekilince
    (kullanıcı kararı: "alt/üst limit koymadan devam") bu 0x0 görsel
    üretmeye çalışıp ValueError veriyordu. Test artık ilkeyi doğruluyor:
    en küçük geçerli görsel elenmez."""
    assert _to_png(_gorsel(max(1, MIN_PX), max(1, MIN_PX))) is not None


# ----- görsel OLMAYANLAR atlanmalı -----

def test_html_atlanir():
    """Asıl vaka: soft 404 ile dönen HTML sayfası."""
    html = b'\r\n<!DOCTYPE html>\r\n<html dir="ltr" lang="tr">\r\n<head><title>Kuveyt</title>'
    assert _to_png(html) is None


def test_bos_yanit_atlanir():
    assert _to_png(b"") is None


def test_bozuk_veri_atlanir():
    assert _to_png(b"\x00\x01\x02 bozuk") is None


@pytest.mark.skipif(MIN_PX <= 0, reason="MIN_PX=0: alt sınır KASITLI olarak yok "
                    "(kullanıcı kararı) — elenecek 'küçük görsel' kavramı kalmadı")
def test_kucuk_ikon_atlanir():
    """MIN_PX altı: tracking pixel / ikon — içerik taşımaz."""
    assert _to_png(_gorsel(MIN_PX - 1, MIN_PX - 1)) is None


# ----- çağıran taraf: kalıcı atlama -----

def test_gorsel_degil_tekrar_denenmez(monkeypatch):
    """HTML dönen URL 5 kez denenmemeli — tek istekte kalıcı atlanmalı."""
    from dataprep import content as c

    class _Html:
        def __init__(self):
            self.n = 0

        def get(self, url):
            self.n += 1

            class R:
                status_code = 200
                content = b"<!DOCTYPE html><html><body>sayfa</body></html>"
                headers = {"content-type": "text/html"}

                def raise_for_status(self):
                    pass
            return R()

        def close(self):
            pass

    monkeypatch.setattr(c, "_host_throttle", lambda u: None)
    kutu = [_Html()]
    png, kalici = c._fetch_one_image(kutu, "http://x/soft404.vsf")
    assert png is None and kalici is True    # HTML -> KALICI (hemen cache)
    assert kutu[0].n == 1                     # TEK deneme, retry yok
