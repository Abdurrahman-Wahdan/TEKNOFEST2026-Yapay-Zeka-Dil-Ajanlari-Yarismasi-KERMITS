"""index_table geçici hatalarda SINIRSIZ retry yapar.

Tek denemede pes etmek, tünel değişiminin denk geldiği anda tabloyu diske
yazıp arama havuzuna GİRMEMEKLE sonuçlanıyordu (canlı: 03:00-03:05 arası
4 tablo). İndekssiz tablo mükerrerlik kontrolünde görünmez olur ve aynı
konuda ikinci bir tablo açılır ('kasko sigortası' 4 kez oluştu).
"""
import pytest

from dataprep.compare import retrieval as R


def test_gecici_hata_retry_edilir(monkeypatch):
    monkeypatch.setattr(R.time, "sleep", lambda s: None)
    monkeypatch.setattr(R.tunnel, "refresh_if_needed", lambda: False)
    kalan = [3]

    def cagri():
        if kalan[0]:
            kalan[0] -= 1
            raise Exception("<h1>no tunnel here :(</h1>")
        return "tamam"

    assert R._dayanikli(cagri, "test") == "tamam"
    assert kalan[0] == 0, "retry yapılmadı"


def test_kalici_hata_hemen_firlar(monkeypatch):
    monkeypatch.setattr(R.time, "sleep", lambda s: None)
    monkeypatch.setattr(R.tunnel, "refresh_if_needed", lambda: False)
    sayac = [0]

    def cagri():
        sayac[0] += 1
        raise Exception("404 Not Found")

    with pytest.raises(Exception):
        R._dayanikli(cagri, "test")
    assert sayac[0] == 1, "kalıcı hata retry edildi"


def test_tunel_yenilemesi_denenir(monkeypatch):
    monkeypatch.setattr(R.time, "sleep", lambda s: None)
    yenilendi = [0]
    monkeypatch.setattr(R.tunnel, "refresh_if_needed",
                        lambda: yenilendi.__setitem__(0, yenilendi[0] + 1))
    kalan = [1]

    def cagri():
        if kalan[0]:
            kalan[0] -= 1
            raise Exception("InternalServerError")
        return 1

    R._dayanikli(cagri, "test")
    assert yenilendi[0] == 1, "tünel yenilenmedi"


def test_400_403_kalici_degil():
    assert not R._indeks_kalici_hata(Exception("400 Bad gateway"))
    assert not R._indeks_kalici_hata(Exception("403"))
    assert not R._indeks_kalici_hata(Exception("no tunnel here"))


def test_401_404_413_422_kalici():
    for k in ("401", "404", "413", "422", "BadRequest"):
        assert R._indeks_kalici_hata(Exception(k)), k


def test_backoff_tavani_asilmaz(monkeypatch):
    sureler = []
    monkeypatch.setattr(R.time, "sleep", lambda s: sureler.append(s))
    monkeypatch.setattr(R.tunnel, "refresh_if_needed", lambda: False)
    kalan = [6]

    def cagri():
        if kalan[0]:
            kalan[0] -= 1
            raise Exception("gecici")
        return 1

    R._dayanikli(cagri, "test")
    assert max(sureler) <= R._INDEKS_BACKOFF_MAX




def test_retry_sinirsizdir(monkeypatch):
    """Sunucu uzun süre dönmese de pes edilmez (kullanıcı kararı)."""
    monkeypatch.setattr(R.time, "sleep", lambda s: None)
    monkeypatch.setattr(R.tunnel, "refresh_if_needed", lambda: False)
    kalan = [50]

    def cagri():
        if kalan[0]:
            kalan[0] -= 1
            raise Exception("gecici")
        return "tamam"

    assert R._dayanikli(cagri, "test") == "tamam"
    assert kalan[0] == 0


def test_embedder_URL_degisince_TAZELENIR():
    """Asıl kök neden: önbellekteki istemci eski URL'i taşıyordu; tünel
    değişince retry sonsuza dek ölü adrese gidiyordu (canlı: sunucu
    sağlıkken 09:00-10:11 arası sürekli 503)."""
    from config.settings import settings
    e1, _ = R._shared()
    e2, _ = R._shared()
    assert e1 is e2, "aynı URL'de önbellek çalışmıyor"
    eski = settings.VLLM_BASE_URL
    try:
        settings.VLLM_BASE_URL = "https://yeni-tunel.example"
        e3, _ = R._shared()
        assert e3 is not e1, "URL değişti ama eski istemci döndü"
    finally:
        settings.VLLM_BASE_URL = eski


def test_refresh_beklemeden_ONCE_cagrilir(monkeypatch):
    """Sıra önemli: önce tünel kontrolü, sonra bekleme/yeniden deneme."""
    sira = []
    monkeypatch.setattr(R.tunnel, "refresh_if_needed",
                        lambda: sira.append("refresh"))
    monkeypatch.setattr(R.time, "sleep", lambda s: sira.append("sleep"))
    kalan = [1]

    def cagri():
        if kalan[0]:
            kalan[0] -= 1
            raise Exception("gecici")
        return 1

    R._dayanikli(cagri, "test")
    assert sira == ["refresh", "sleep"], sira
