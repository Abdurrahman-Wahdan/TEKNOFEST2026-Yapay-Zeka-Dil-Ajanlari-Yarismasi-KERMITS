"""Yavaş bir öğe, arkasındaki BİTMİŞ işlerin ledger'a yazılmasını bloke etmemeli.

Canlı olay (2026-08-19 01:02): vakifkatilim'in todo listesinde 2. sırada 166
görselli bir sayfa vardı. ex.map sonuçları KUYRUK SIRASINA göre verdiği için
arkasındaki 192 tamamlanmış sayfa ledger'a yazılamadı — sayaç 50 dakika boyunca
hiç ilerlemedi ve o sayfalar sonraki koşuda baştan işlenecekti (veri kaybı yok,
ama saatlerce iş tekrarı). as_completed BİTEN'i hemen verir.
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

pytestmark = pytest.mark.unit


def _kuyruk_sirasi_bloke_eder_mi(kullan_as_completed: bool) -> int:
    """2. öğe yavaş; ilk 100ms içinde KAÇ sonuç toplanabildi?"""
    isler = [("hizli", 0.0)] + [("yavas", 1.0)] + [(f"hizli{i}", 0.0) for i in range(20)]

    def calis(it):
        ad, sure = it
        if sure:
            time.sleep(sure)
        return ad

    toplanan = []
    baslangic = time.time()
    with ThreadPoolExecutor(max_workers=8) as ex:
        if kullan_as_completed:
            futures = [ex.submit(calis, it) for it in isler]
            for fut in as_completed(futures):
                if time.time() - baslangic > 0.3:
                    break
                toplanan.append(fut.result())
        else:
            for sonuc in ex.map(calis, isler):
                if time.time() - baslangic > 0.3:
                    break
                toplanan.append(sonuc)
    return len(toplanan)


def test_as_completed_yavas_ogeyi_beklemez():
    """as_completed ile hızlılar hemen toplanır (ex.map'te 1 tanede tıkanırdı)."""
    assert _kuyruk_sirasi_bloke_eder_mi(kullan_as_completed=True) >= 15


def test_ex_map_bloke_ederdi():
    """Eski davranışın gerçekten sorunlu olduğunu sabitler (regresyon koruması)."""
    assert _kuyruk_sirasi_bloke_eder_mi(kullan_as_completed=False) <= 2


def test_content_py_as_completed_kullaniyor():
    """İki döngü de (aşama 2 ve aşama 3) as_completed'a bağlı olmalı."""
    from pathlib import Path

    from dataprep import content as c

    src = Path(c.__file__).read_text(encoding="utf-8")
    assert "ex.map(work, todo)" not in src        # eski hali kalmamalı
    assert src.count("as_completed(futures)") == 2
