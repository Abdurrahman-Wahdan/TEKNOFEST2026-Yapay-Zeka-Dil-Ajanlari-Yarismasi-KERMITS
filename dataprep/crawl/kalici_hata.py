"""KALICI HATA SINIFLANDIRMASI — tekrar denemenin ANLAMSIZ olduğu durumlar.

SORUN: her koşu aynı ölü URL'leri baştan deniyordu. kuveytturk'te 5106 işlenen
isteğin 801'i FAIL'di ve bunların 749'u GERÇEK 404 (silinmiş eski sayfalar).
Bu URL'ler her koşuda yeniden denenip hem zaman hem WAF kotası harcıyor —
kuveytturk 6 kez hız frenine girdi, crawl saatlerce sürdü.

ÇÖZÜM: hatayı KALICI / GEÇİCİ diye ayır.
  * KALICI (404/410/401/403, bozuk URL, ölü DNS) -> diske yazılır, sonraki
    koşuda ATLANIR. Ama SİLİNMEZ: kayıt kalır, istenirse sıfırlanabilir.
  * GEÇİCİ (timeout, 5xx, bağlantı) -> her koşuda YENİDEN denenir.

VERİ KAYBI YOK: kalıcı hata yalnız SUNUCUNUN "bu sayfa yok/erişemezsin" dediği
adresler için işaretlenir. Geçici hiçbir şey kalıcı sayılmaz; şüpheli her durum
GEÇİCİ kabul edilir (yeniden denenir).

Kayıt: data/<banka>_site/_kalici_hatalar.json
    {url: {"sebep": "HTTP 404", "ilk": "...", "son": "...", "kez": 3}}
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path

# Sunucunun "bu adres yok / sana kapalı" dediği kodlar. Bunlar retry ile
# düzelmez — sayfa silinmiş ya da erişim kalıcı olarak reddedilmiş.
KALICI_KODLAR = (400, 401, 403, 404, 405, 410, 414, 451)

# URL'in KENDİSİ bozuksa istek atmanın anlamı yok.
_BOZUK_KALIP = re.compile(
    r"\{\{|\}\}|\{%|%\}|\$\{"          # şablon artığı: {{x}}, {%= x %}, ${x}
    r"|~/templates/"                    # CMS şablon yolu
    r"|^https?://[^/]*[^\w.\-:/]"      # host'ta geçersiz karakter
    r"|^https?://[^./]+/"              # noktasız host: https://blog/... (canlı
                                        # örnek: kuveytturk'ün BOZUK 301'i)
    r"|[�]"                        # bozuk kodlama (mojibake)
)

_kilit = threading.Lock()


def _yol(site: Path) -> Path:
    return site / "_kalici_hatalar.json"


def yukle(site: Path) -> dict:
    try:
        return json.loads(_yol(site).read_text(encoding="utf-8"))
    except Exception:
        return {}


def bozuk_url(url: str) -> bool:
    """URL'in kendisi geçersiz mi? (istek atmadan anlaşılır)"""
    return bool(_BOZUK_KALIP.search(url or ""))


def kalici_mi(sebep: str) -> bool:
    """Bu hata sebebi tekrar denemeyle düzelir mi?

    ŞÜPHEDE GEÇİCİ SAY: yalnız kesin bilinen kalıcı durumlar True döner.
    """
    s = (sebep or "").lower()
    if "dns yok" in s or "ölü host" in s:
        return True
    m = re.search(r"http\s*(\d{3})", s)
    if m and int(m.group(1)) in KALICI_KODLAR:
        return True
    return False


def kaydet(site: Path, url: str, sebep: str) -> None:
    """Kalıcı hatayı diske yaz (sonraki koşu atlasın)."""
    if not kalici_mi(sebep) and not bozuk_url(url):
        return
    with _kilit:
        d = yukle(site)
        now = datetime.now().isoformat(timespec="seconds")
        kayit = d.get(url) or {"ilk": now, "kez": 0}
        kayit.update(sebep=sebep, son=now, kez=kayit.get("kez", 0) + 1)
        d[url] = kayit
        try:
            site.mkdir(parents=True, exist_ok=True)
            tmp = _yol(site).with_suffix(".tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            tmp.replace(_yol(site))
        except Exception:
            pass


def atlanacak(site: Path) -> set[str]:
    """Sonraki koşuda denenmeyecek URL kümesi."""
    return set(yukle(site))
