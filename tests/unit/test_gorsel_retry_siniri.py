"""Görsel indirme SINIRLI, VLM SINIRSIZ retry yapar.

Ayrım kasıtlı: VLM sunucusu geçici düşer ve geri gelir — orada ısrar doğru.
Ama bir görsel URL'i kalıcı ölü olabilir (silinmiş/taşınmış); orada ısrar
worker'ı kilitler. Canlı olay (2026-08-19): tek URL 662 kez denendi, 2 saatte
sadece 20 sayfa işlendi, aynı anda diğer görseller 200 dönüyordu.
"""

import pytest

from dataprep import content as c
from dataprep import vlm as v

pytestmark = pytest.mark.unit


class _HepHata:
    def __init__(self):
        self.n = 0

    def get(self, url):
        self.n += 1
        raise ConnectionError("timeout")

    def close(self):
        pass


def test_gorsel_indirme_5_denemede_pes_eder(monkeypatch):
    """Ölü URL süreci kilitlememeli."""
    monkeypatch.setattr(c, "_host_throttle", lambda u: None)
    monkeypatch.setattr(c.time, "sleep", lambda s: None)
    monkeypatch.setattr(c, "_new_img_client", lambda: _HepHata())
    kutu = [_HepHata()]
    png, kalici = c._fetch_one_image(kutu, "http://x/olu.png")
    assert png is None and kalici is False   # ağ hatası -> geçici
    toplam = kutu[0].n + sum(getattr(k, "n", 0) for k in [kutu[0]])
    assert kutu[0].n <= c._IMG_FETCH_MAX_TRY


def test_tavan_5_tir():
    assert c._IMG_FETCH_MAX_TRY == 5


def test_vlm_retry_hala_sinirsiz(monkeypatch):
    """VLM tarafına dokunulmamalı: sunucu geri gelir, ısrar doğru."""
    sayac = {"n": 0}

    def sahte(body):
        sayac["n"] += 1
        if sayac["n"] < 12:                    # 5'ten ÇOK fazla
            raise ConnectionError("gecici")
        return ('{"content":"OK"}', True)

    monkeypatch.setattr(v, "_stream_once", sahte)
    monkeypatch.setattr(v.STOPPING, "wait", lambda s: False)
    out = v._post({"messages": [{"role": "user", "content": "x"}]})
    assert sayac["n"] == 12                    # pes etmedi
    assert "OK" in out


def test_kalici_4xx_hemen_gecilir(monkeypatch):
    """404 gibi kalıcı hatada 5 kez denemeye gerek yok."""
    class _NotFound:
        def __init__(self): self.n = 0
        def get(self, url):
            self.n += 1
            class R: status_code = 404
            return R()
        def close(self): pass

    monkeypatch.setattr(c, "_host_throttle", lambda u: None)
    kutu = [_NotFound()]
    png, kalici = c._fetch_one_image(kutu, "http://x/yok.png")
    assert png is None and kalici is True    # 4xx -> kalıcı
    assert kutu[0].n == 1                      # tek deneme
