"""EMBED KİLİDİ — aynı bankaya aynı anda İKİ embed süreci girmesin.

NEDEN GEREKLİ: point_id determinist (uuid5(slug:url:type:...)), yani aynı
belge her zaman aynı id'yi üretir — bu, tekrar çalıştırmayı güvenli yapar.
AMA iki süreç AYNI bankayı AYNI anda işlerse:
  * ikisi de "Qdrant'ta yok" diye aynı chunk'ı hesaplar (çift LLM maliyeti),
  * ikisi de aynı id'ye upsert eder — son yazan kazanır. Aynı içerikse
    zararsız, ama biri ESKİ diskten biri YENİ diskten okuduysa eski sürüm
    yeniyi EZEBİLİR.
Kilit bu yarışı tamamen kapatır.

TASARIM: dosya bazlı (fcntl.flock) — süreçler arası çalışır, süreç ölse bile
kilit OS tarafından bırakılır (kalıntı kilit sorunu yok).

BANKALAR ARASI PARALELLİK KORUNUR: kilit BANKA BAŞINA. Farklı bankalar aynı
anda embed edilebilir; sadece aynı banka serileşir.
"""
from __future__ import annotations

import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path

KILIT_DIZIN = Path(__file__).resolve().parents[1] / "data" / "_kilitler"


@contextmanager
def banka_kilidi(slug: str, bekle: bool = True, log=None):
    """Bu banka için embed kilidini al. Varsayılan: SIRAYA GİR (bekle).

    bekle=False verilirse ve kilit başkasındaysa, blok ATLANIR (yield False).
    """
    KILIT_DIZIN.mkdir(parents=True, exist_ok=True)
    yol = KILIT_DIZIN / f"embed_{slug}.lock"
    f = yol.open("w")
    alindi = False
    t0 = time.time()
    try:
        if bekle:
            try:                       # önce çakışma var mı diye bak
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                alindi = True
            except BlockingIOError:
                if log:
                    log(f"  [KİLİT] {slug}: başka bir embed süreci çalışıyor "
                        f"— SIRAYA GİRİLDİ (bekleniyor)")
                fcntl.flock(f, fcntl.LOCK_EX)      # bloklayarak bekle
                alindi = True
                if log:
                    log(f"  [KİLİT] {slug}: sıra geldi ({time.time() - t0:.0f}s bekledi)")
        else:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                alindi = True
            except BlockingIOError:
                alindi = False
        if alindi:
            f.write(f"{os.getpid()}\n")
            f.flush()
        yield alindi
    finally:
        if alindi:
            try:
                fcntl.flock(f, fcntl.LOCK_UN)
            except Exception:
                pass
        f.close()
