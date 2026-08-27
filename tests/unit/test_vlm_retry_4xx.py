"""VLM'den gelen GEÇİCİ hatalar es geçilmemeli — retry edilmeli.

Loglarda 58 adet "VLM 403 (kalıcı) — retry yok, geçiliyor" vardı. 403 bizde
tünelden geliyor (vLLM auth kullanmıyor), yani geçici; kalıcı sayıp atlamak
öğeyi gereksiz yere sonraki koşuya bırakıyordu. Ama 400/404/413 gerçekten
kalıcı: onlarda ısrar etmek sonsuz döngü olur.
"""

import httpx
import pytest

from dataprep import vlm as v

pytestmark = pytest.mark.unit


class _Kodlar:
    """Sırayla verilen HTTP kodlarını döndüren sahte _stream_once."""

    def __init__(self, *kodlar):
        self.kodlar = list(kodlar)
        self.n = 0

    def __call__(self, body):
        kod = self.kodlar[min(self.n, len(self.kodlar) - 1)]
        self.n += 1
        r = httpx.Response(kod, request=httpx.Request("POST", "http://x/gemma"),
                           content=b"{}")
        if 400 <= kod < 500:
            if kod in v._GECICI_4XX:
                raise httpx.HTTPStatusError(f"gecici {kod}", request=r.request, response=r)
            raise v._PermanentClientError(kod)
        return ('{"content":"OK"}', True)


@pytest.mark.parametrize("kod", [400, 403, 408, 425, 429])
def test_gecici_4xx_retry_edilir(monkeypatch, kod):
    st = _Kodlar(kod, 200)
    monkeypatch.setattr(v, "_stream_once", st)
    out = v._post({"messages": [{"role": "user", "content": "x"}]})
    assert st.n == 2                    # bir kez daha denendi
    assert "OK" in out


def test_gecici_4xx_israrla_denenir(monkeypatch):
    """Üst üste gelse bile pes etmemeli."""
    st = _Kodlar(403, 403, 403, 200)
    monkeypatch.setattr(v, "_stream_once", st)
    out = v._post({"messages": [{"role": "user", "content": "x"}]})
    assert st.n == 4
    assert "OK" in out


@pytest.mark.parametrize("kod", [404, 413, 422])
def test_kalici_4xx_sonsuz_donguye_girmez(monkeypatch, kod):
    """Aynı istek hep aynı sonucu verir; ısrar sonsuz döngü olurdu."""
    st = _Kodlar(kod)
    monkeypatch.setattr(v, "_stream_once", st)
    out = v._post({"messages": [{"role": "user", "content": "x"}]})
    assert st.n == 1
    assert out == ""


def test_kalici_4xx_ogeyi_kaybettirmez(monkeypatch):
    """Atlanan öğe boş döner -> call_json {} -> ledger'a yazılmaz -> tekrar denenir."""
    monkeypatch.setattr(v, "_stream_once", _Kodlar(404))
    assert v.call_json(v.txt_msg("x")) == {}
