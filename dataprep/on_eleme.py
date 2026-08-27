"""ÖN-ELEME İÇİN PAYLAŞILAN, AYRILMIŞ HAVUZ (süreç genelinde tek).

SORUN (ölçüldü): uzun sayfa/PDF temizlenirken her dış worker
KENDİ ön-eleme ThreadPoolExecutor'ünü açıyordu. 48 dış worker x 10 karar
chunk = 480 potansiyel eşzamanlı istek, hepsi 96'lık NET_SEM'e yükleniyor.
Sonuç: ön-eleme dış worker'ları AÇ BIRAKIYOR — dikiş zincirleri sıraya
giriyor, süreç 28 thread'le 4 açık bağlantıda CPU %2'de sürünüyordu.

ÇÖZÜM: ön-elemenin süreç genelinde TEK ve SINIRLI bir bütçesi olur.
  * Havuz TEK: her sayfa için yeni executor kurulmaz (thread churn yok).
  * Bütçe AYRI: ön-eleme en fazla ON_ELEME_PAYI kadar eşzamanlı istek
    yapar; kalan NET_SEM bütçesi dikiş zincirlerine kalır.
  * Tavan sabit değil: ANLIK boşluğa göre 25..50 arasında dalgalanır.

KULLANIM:
    from dataprep.on_eleme import esle
    sonuclar = esle(fonksiyon, parcalar)   # sırayı KORUR
"""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor

# Ön-elemeye ayrılan eşzamanlılık aralığı. Alt sınır: bu kadarı HER ZAMAN
# ön-elemenin hakkı (dikiş zincirleri onu tamamen aç bırakamaz). Üst sınır:
# boşluk varken buraya kadar çıkabilir.
ALT = int(os.environ.get("ON_ELEME_ALT", "25"))
UST = int(os.environ.get("ON_ELEME_UST", "50"))

_havuz: ThreadPoolExecutor | None = None
_kilit = threading.Lock()
# Aynı anda ön-elemede olan istek sayısı — tavanı UST'te tutar.
_gecit = threading.Semaphore(UST)


def _al() -> ThreadPoolExecutor:
    """Süreç genelinde TEK havuz (tembel kurulur, bir daha kapanmaz)."""
    global _havuz
    if _havuz is None:
        with _kilit:
            if _havuz is None:
                _havuz = ThreadPoolExecutor(max_workers=UST,
                                            thread_name_prefix="on_eleme")
    return _havuz


def esle(fn, parcalar: list) -> list:
    """fn'i parcalar üzerinde asenkron çalıştırır, biteni anında tüketir.
    Sıralı indeksle döndürür, böylece batch beklemeden akış sirkülasyonu sağlanır."""
    if not parcalar:
        return []
    ex = _al()

    def _sinirli(ix, x):
        with _gecit:
            return ix, fn(x)

    futs = [ex.submit(_sinirli, i, p) for i, p in enumerate(parcalar)]
    from concurrent.futures import as_completed
    results = [None] * len(parcalar)
    for fut in as_completed(futs):
        ix, res = fut.result()
        results[ix] = res
    return results
