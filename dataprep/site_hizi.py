"""BANKA SİTELERİNE giden CANLI isteklerin hız kapısı — host başına 1 istek/sn.

KAPSAM:

  * LLM/VLM istekleri (kendi sunucumuz) BU SINIRA TABİ DEĞİL — onlar
    dataprep/net_limit.py::NET_SEM ile yönetilir (50 eşzamanlı).
  * Bankaların KENDİ sitelerine giden her istek (sayfa indirme, PDF indirme,
    görsel indirme, sitemap okuma, doğrulama fetch'i) host başına en fazla
    1 req/sn.

NEDEN: WAF/rate-limit engellemeleri ölçüldü. kuveytturk 50 eşzamanlıda IP
bloğu yedi (981 ConnectTimeout, TLS "Client hello"da askıya alma); albaraka
aynı koşuda 190 hata + 25 kez otomatik fren. 1 req/sn'ye inince kuveytturk
1792 sayfayı 10 hatayla, TEK bir fren olmadan indirdi. Yani bu sınır hız
kaybı değil, GERÇEK verimlilik: bloklanan bir koşu sıfır veri getiriyor.

HOST BAŞINA: sınır banka başına değil HOST başına uygulanır — aynı anda 10
banka çalışsa bile her site kendi 1 req/sn kotasını görür, birbirlerini
yavaşlatmazlar. Alt alan adları (asset.emlakkatilim.com.tr gibi) ayrı host
sayılır; onlar genelde CDN'dir ve ayrı kotayı hak eder.

KULLANIM:
    from dataprep.site_hizi import bekle_async, bekle_sync
    await bekle_async(url)     # asenkron yollarda (crawl)
    bekle_sync(url)            # senkron yollarda (görsel indirme)
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
from urllib.parse import urlparse

# Saniyedeki maksimum istek (host başına). 0/negatif -> sınır kapalı.
RPS = float(os.environ.get("SITE_RPS", "1.0"))
_MIN_ARALIK = (1.0 / RPS) if RPS > 0 else 0.0

_kilit = threading.Lock()
_son: dict[str, float] = {}          # host -> bir sonraki isteğin en erken zamanı


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _sira_al(url: str) -> float:
    """Bu istek için beklenmesi gereken süreyi (sn) hesaplar ve slotu REZERVE
    eder. Rezervasyon kilit altında yapılır: iki çağıran aynı slotu alamaz."""
    if not _MIN_ARALIK:
        return 0.0
    h = _host(url)
    if not h:
        return 0.0
    simdi = time.monotonic()
    with _kilit:
        musait = _son.get(h, 0.0)
        baslangic = max(simdi, musait)
        _son[h] = baslangic + _MIN_ARALIK
    return max(0.0, baslangic - simdi)


# --- DNS'İ ÇÖZÜLMEYEN HOST'LAR (ölü alt alan adları) ---------------------
# Sitelerin sitemap/HTML'inde bazen ARTIK VAR OLMAYAN alt alan adları geçiyor
# (test/staging kalıntısı). Örnek: krmsltst.emlakkatilim.com.tr — DNS kaydı
# YOK, ama crawl her seferinde bağlantı zaman aşımını bekliyor. Ölçüldü
# (2026-08-23): tek koşuda 8 kez denendi, her biri retry'larıyla dakikalar
# harcadı ve sayaç ilerlemedi (dışarıdan "asıldı" gibi görünüyordu).
#
# Bir host için DNS BİR KEZ çözülemezse o host kara listeye alınır ve sonraki
# istekler ANINDA reddedilir. Kalıcı bir DNS hatası retry ile düzelmez.
# Ana banka alan adları ASLA kara listeye alınamaz (geçici DNS/ağ aksamalarında kilitlenmeyi önler)
_KORUNAN_ALAN_ADLARI = {
    "kuveytturk.com.tr", "turkiyefinans.com.tr", "albaraka.com.tr", "albarakaturk.com.tr",
    "vakifkatilim.com.tr", "ziraatkatilim.com.tr", "emlakkatilim.com.tr",
    "hayatfinans.com.tr", "tombank.com.tr", "dunyakatilim.com.tr", "adilkatilim.com.tr"
}

_olu_hostlar: set[str] = set()


def olu_host(url: str) -> bool:
    """Bu host'un DNS'i çözülmüyor mu? (bir kez bakılır, sonuç hatırlanır)"""
    h = _host(url)
    if not h:
        return False
    # Ana banka siteleri asla ölü host sayılamaz
    if any(h == k or h.endswith("." + k) for k in _KORUNAN_ALAN_ADLARI):
        return False
    if h in _olu_hostlar:
        return True
    return False


# Kaç DNS hatasından sonra host ölü sayılsın. TEK hata yetmez: geçici bir
# çözümleme hıçkırığı, o host'un KALAN TÜM sayfalarını sessizce eler.
# (Ana banka alan adları zaten _KORUNAN_ALAN_ADLARI ile mutlak korumalı;
# bu eşik onların ALT ALAN ADLARI ve CDN'ler için ikinci savunma hattı.)
_OLU_ESIK = int(os.environ.get("OLU_HOST_ESIK", "5"))
_dns_hata: dict[str, int] = {}


def olu_isaretle(url: str) -> None:
    """DNS hatası bildirir. Host ancak _OLU_ESIK kez hata alırsa ölü sayılır;
    araya başarılı bir istek girerse sayaç sıfırlanır (dns_basarili)."""
    h = _host(url)
    if not h:
        return
    if any(h == k or h.endswith("." + k) for k in _KORUNAN_ALAN_ADLARI):
        return  # Ana banka domainlerini asla kara listeye alma
    with _kilit:
        n = _dns_hata.get(h, 0) + 1
        _dns_hata[h] = n
        if n >= _OLU_ESIK:
            _olu_hostlar.add(h)


def dns_basarili(url: str) -> None:
    """Bu host'tan başarılı yanıt geldi — hata sayacını sıfırla."""
    h = _host(url)
    if not h:
        return
    with _kilit:
        _dns_hata.pop(h, None)


async def bekle_async(url: str) -> None:
    """Asenkron yollar (crawl fetch) — sırası gelene kadar bekler."""
    b = _sira_al(url)
    if b > 0:
        await asyncio.sleep(b)


def bekle_sync(url: str) -> None:
    """Senkron yollar (görsel indirme) — sırası gelene kadar bekler."""
    b = _sira_al(url)
    if b > 0:
        time.sleep(b)
