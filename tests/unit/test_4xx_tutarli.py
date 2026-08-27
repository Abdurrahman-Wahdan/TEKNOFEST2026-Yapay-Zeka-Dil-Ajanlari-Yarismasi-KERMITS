"""TÜM LLM yollarında 4xx kararı AYNI olmalı.

Sunucu access log'u (2026-08-18 23:00 UTC) gösterdi: tünel soketi bayatlayınca
nginx isteği "400 / 0 byte" ile reddediyor, istek vLLM'e HİÇ ulaşmıyor; aynı
istek saniyeler sonra 200 dönüyor. 403 de tünelden geliyor (vLLM auth yok).
Bunları "kalıcı" sayıp pes etmek işi sessizce yarıda bırakıyordu.

499 bize hiç GELMEZ: nginx'e özgü "istemci gitti" kodudur, sunucuda loglanır,
istemciye gönderilmez. Bizde ReadTimeout olarak görünür ve sınırsız retry'a düşer.
"""

import pytest

pytestmark = pytest.mark.unit

from dataprep.compare.bank_agent import _is_permanent as bank
from dataprep.compare.classify_agent import _is_permanent as classify
from dataprep.compare.dedup import _is_permanent as dedup
from dataprep.crawl.policy import _is_permanent as policy

_YOLLAR = [("bank_agent", bank), ("classify_agent", classify),
           ("dedup", dedup), ("crawl_policy", policy)]


@pytest.mark.parametrize("ad,fn", _YOLLAR)
@pytest.mark.parametrize("kod", ["400", "403", "429", "408"])
def test_gecici_4xx_kalici_sayilmaz(ad, fn, kod):
    """Tünel kaynaklı — retry edilmeli."""
    assert fn(Exception(f"HTTP {kod} error")) is False


@pytest.mark.parametrize("ad,fn", _YOLLAR)
@pytest.mark.parametrize("kod", ["401", "404", "413", "422"])
def test_gercekten_kalici_4xx_pes_edilir(ad, fn, kod):
    """Aynı istek hep aynı sonucu verir — ısrar sonsuz döngü olur."""
    assert fn(Exception(f"HTTP {kod} error")) is True


def test_vlm_yolu_da_ayni_karari_verir():
    """vlm.py ayrı bir mekanizma kullanıyor; kararı tutarlı olmalı."""
    from dataprep import vlm
    assert 400 in vlm._GECICI_4XX
    assert 403 in vlm._GECICI_4XX
    assert 404 not in vlm._GECICI_4XX


def test_backoff_tavanlari_hizali():
    """Retry fırtınası (5s) sunucuyu boğmuştu; 60s ise gereksiz yavaştı."""
    from dataprep import vlm
    from dataprep.compare import bank_agent
    assert vlm._BACKOFF_MAX == bank_agent._BACKOFF_MAX == 30.0
