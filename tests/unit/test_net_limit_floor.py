"""Uyarlanabilir sınırlayıcı: limit dipte KİLİTLENMEMELİ.

Canlı olay (2026-08-18 20:26): üç istek 122s'de ReadTimeout'a düştü, AIMD bunu
tıkanıklık sanıp limiti 8 -> 2'ye indirdi ve orada KİLİTLENDİ — çünkü büyüme
yalnızca "hızlı" isteklerle mümkündü, dipteyken ise istekler doğal olarak
yavaşlıyor. Sonuç: sunucu boş beklerken dakikada 2-3 yanıt. Buradaki testler o
kilidin geri gelmesini engelliyor.
"""

import time

import pytest

from dataprep import net_limit as nl

pytestmark = pytest.mark.unit


@pytest.fixture
def sem():
    return nl.AdaptiveSemaphore(8, 2, 40)


# ----- timeout'un sınıflandırılması -----

def test_zombi_timeout_limiti_yariya_indirmez(sem):
    """Süresi read-timeout'a YAPIŞAN hata tıkanıklık değil; tünel bıraktı."""
    sem.report(ok=False, duration=nl.READ_TIMEOUT_HINT + 2)
    assert sem.limit == 7                      # -1, yarıya değil
    assert sem._stats["zombie"] == 1


def test_gercek_tikaniklik_hala_yariya_indirir(sem):
    """Zombi ayrımı, gerçek tıkanıklık tepkisini bozmamalı."""
    sem.report(ok=False, duration=10.0)
    assert sem.limit == 4


# ----- dipten çıkış -----

def test_dipte_yavas_ama_basarili_istekler_limiti_yukseltir():
    """Asıl hata buydu: dipteyken büyüme sinyali hiç üretilemiyordu."""
    sem = nl.AdaptiveSemaphore(2, 2, 40)
    sem._last_shrink = 0
    for _ in range(4):
        sem.report(ok=True, duration=100.0)    # orta bölge: ne hızlı ne tehlikeli
    assert sem.limit > 2


def test_hic_basari_yoksa_zaman_tabanli_yoklama(monkeypatch):
    """Hepsi zombi ise başarı sinyali de gelmez; körlemesine yoklama şart."""
    monkeypatch.setattr(nl, "STUCK_AT_FLOOR_AFTER", 0.3)
    sem = nl.AdaptiveSemaphore(2, 2, 40)
    sem._active = 2                            # kota dolu
    with sem._cv:
        sem._probe_if_stuck_at_floor()         # sayacı başlat
    time.sleep(0.4)
    with sem._cv:
        sem._probe_if_stuck_at_floor()         # süre doldu -> +1
    assert sem.limit == 3
    assert sem._stats["probe"] == 1


def test_yoklama_guvenli_kopmada_geri_iner(monkeypatch):
    """Yoklama iyimser; yanılırsa ilk kopmada geri alınmalı."""
    monkeypatch.setattr(nl, "STUCK_AT_FLOOR_AFTER", 0.0)
    sem = nl.AdaptiveSemaphore(2, 2, 40)
    with sem._cv:
        sem._probe_if_stuck_at_floor()
        sem._probe_if_stuck_at_floor()
    yukselmis = sem.limit
    sem.report(ok=False, duration=5.0)         # gerçek tıkanıklık
    assert sem.limit < yukselmis


def test_limit_tavani_asilmaz():
    sem = nl.AdaptiveSemaphore(40, 2, 40)
    sem._last_shrink = 0
    for _ in range(50):
        sem.report(ok=True, duration=1.0)
    assert sem.limit == 40
