"""Paylaşılan VLM (gemma) yardımcıları + GÖRSEL hash-dedup cache.

Hem web sayfaları hem PDF'ler için ortak:
  * structured JSON çağrısı (response_format=json_object) + 3-retry + sıcaklık merdiveni
  * examine_image(png): bir görseli İNCELE — "dekoratif mi yoksa KATILIM BANKASI
    ürün/kampanya bilgisi mi?" Bilgi içeriyorsa markdown çıkarır.
  * CACHE (hash bazlı): aynı görsel (logo/banner/arka plan) onlarca yerde tekrar
    eder; her BENZERSİZ görsel YALNIZCA BİR KEZ VLM'e sorulur, sonuç (dekoratif
    ya da çıkarılan md) diske kaydedilir; sonraki karşılaşmada LOKALDEN gelir.

Tüm metinlerde "Katılım Bankası" ifadesi kullanılır.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path

import httpx

log = logging.getLogger("dataprep.vlm")

BASE_URL = "https://unbundle-semisoft-mouth.ngrok-free.dev"
VLM_PATH = "/gemma/v1/chat/completions"
VLM_MODEL = "google/gemma-4-31B-it"
TEMP_LADDER = (0.0, 0.3, 0.6, 1.0)

# Sunucu ~100 AÇIK bağlantıda (ngrok opn) tıkanıyor. urllib her istekte YENİ bağlantı
# açıp TIME_WAIT'te biriktiriyordu -> opn 100'ü aşıyordu. HAVUZLU client ile açık
# bağlantı max_connections ile SABİT tavanlanır (yeniden kullanım, churn yok).
# VLM_POOL env ile ayarlanır; çok-süreçli PDF aşamasında süreç başına düşük tutulur.
VLM_POOL = int(os.environ.get("VLM_POOL", "99"))
# GRANÜLER timeout: hiçbir faz asılı kalmasın (connect/read/write/pool ayrı ayrı sınırlı).
_client = httpx.Client(
    timeout=httpx.Timeout(connect=10.0, read=90.0, write=30.0, pool=30.0),
    limits=httpx.Limits(max_connections=VLM_POOL, max_keepalive_connections=VLM_POOL))


# --- düşük seviyeli çağrı (structured JSON + retry + sıcaklık) --------------
MAX_IMG_BYTES = 450_000     # sunucu ~1MB body limiti; base64+json marjıyla güvenli


def _fit_png(png: bytes) -> tuple[bytes, str]:
    """Görseli sunucunun body limitine sığdır ama ÇÖZÜNÜRLÜĞÜ (DPI) koru:
      1) tam çözünürlükte JPEG kalitesini kademeli düşür (boyut/DPI aynı kalır),
      2) ancak yüksek kalitede bile sığmıyorsa çözünürlüğü kademeli küçült.
    Küçük görseller PNG olduğu gibi kalır. (bytes, mime) döner. 413'ü önler."""
    if len(png) <= MAX_IMG_BYTES:
        return png, "image/png"
    import io
    from PIL import Image
    img = Image.open(io.BytesIO(png)).convert("RGB")
    for q in (92, 88, 82, 75, 68):              # 1) DPI'ı koru, kaliteyi düşür
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=q)
        if buf.tell() <= MAX_IMG_BYTES:
            return buf.getvalue(), "image/jpeg"
    for scale in (0.85, 0.7, 0.55, 0.45, 0.35, 0.28):   # 2) zorunlu: kademeli küçült
        im2 = img.resize((max(1, int(img.width * scale)),
                          max(1, int(img.height * scale))), Image.LANCZOS)
        buf = io.BytesIO()
        im2.save(buf, "JPEG", quality=85)       # küçültünce kaliteyi yüksek tut
        if buf.tell() <= MAX_IMG_BYTES:
            return buf.getvalue(), "image/jpeg"
    return buf.getvalue(), "image/jpeg"         # son çare (en küçük ölçek, q85)


def img_msg(text: str, png: bytes) -> list:
    data, mime = _fit_png(png)                  # 413'ü önlemek için body limitine sığdır
    b64 = base64.b64encode(data).decode()
    return [{"role": "user", "content": [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]}]


def txt_msg(text: str) -> list:
    """Yalnız-metin (görselsiz) LLM mesajı — aynı gemma chat endpoint'i."""
    return [{"role": "user", "content": text}]


def txt_block(text: str) -> dict:
    """Çok-parçalı mesaj için metin bloğu."""
    return {"type": "text", "text": text}


def img_block(png: bytes) -> tuple[dict, int]:
    """Çok-parçalı mesaj için görsel bloğu (413'e karşı fit'li). (blok, gönderilen_byte)."""
    data, mime = _fit_png(png)
    b64 = base64.b64encode(data).decode()
    return ({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}, len(data))


MAX_RETRY_SECONDS = 600     # ısrarlı retry bütçesi: 10 dk aşılırsa "sorun" deyip vazgeç


def _post(payload, max_seconds: int = MAX_RETRY_SECONDS) -> str:
    """HAVUZLU client (açık bağlantı VLM_POOL ile tavanlı). İSRARLI retry: hata olursa
    ÜSTEL (2x) backoff'la sürekli dener; toplam süre max_seconds'ı (10 dk) aşacaksa
    vazgeçer ve "" döner (üst katman 'unreachable' olarak kaydedip geçer)."""
    start = time.time()
    delay = 1.0
    attempt = 0
    while True:
        attempt += 1
        try:
            r = _client.post(BASE_URL + VLM_PATH, json=payload)
            if 400 <= r.status_code < 500:          # KALICI istemci hatası (413/400/404...)
                log.warning("    VLM %d (kalıcı) — retry yok, geçiliyor", r.status_code)
                return ""                           # retry etme (413 asla düzelmez)
            r.raise_for_status()                    # 5xx -> aşağıdaki retry'a düşer
            m = r.json()["choices"][0]["message"]
            return (m.get("content") or m.get("reasoning") or "").strip()
        except Exception as exc:                    # bağlantı/timeout/5xx -> ISRARLI retry
            elapsed = time.time() - start
            # ReadTimeout = sunucu kabul etti ama YAVAŞ yanıtlıyor (taranmış-PDF bandı çok
            # ağır). Tekrar denemek aynı sonucu verir -> KISA vazgeç. ConnectError/outage
            # ise sunucu geri gelir -> uzun (max_seconds) ısrar et.
            cap = 100 if isinstance(exc, httpx.ReadTimeout) else max_seconds
            if elapsed + delay >= cap:              # bütçe aşıldı -> SORUN, geç
                log.warning("    VLM SORUN: %.0fs başarısız (deneme %d): %s — geçiliyor",
                            elapsed, attempt, type(exc).__name__)
                return ""
            log.warning("    VLM istek hatası (deneme %d, %.0fs): %s — %.0fs sonra tekrar",
                        attempt, elapsed, type(exc).__name__, delay)
            time.sleep(delay)
            delay = min(delay * 2, 60)              # 2x backoff, tek beklemede en fazla 60s


def call_json(messages, max_tokens: int = 4096) -> dict:
    """STRUCTURED JSON; parse olmazsa sıcaklığı 0->0.3->0.6->1.0 artırır."""
    for t in TEMP_LADDER:
        raw = _post({"model": VLM_MODEL, "messages": messages, "temperature": t,
                     "max_tokens": max_tokens, "response_format": {"type": "json_object"},
                     "chat_template_kwargs": {"enable_thinking": False}})  # thinking kapalı
        if not raw:                       # bağlantı çöktü -> sıcaklık merdivenini deneme, hemen çık
            break
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            import re
            mm = re.search(r"\{.*\}", raw, re.S)
            if mm:
                try:
                    return json.loads(mm.group(0))
                except json.JSONDecodeError:
                    pass
        log.info("    (JSON parse edilemedi, sıcaklık %.1f ile tekrar)", t)
    return {}


# --- GÖRSEL inceleme + hash-dedup cache ------------------------------------
_EXAMINE_Q = (
    'Bu görseli incele. DEKORATİF mi (logo, ikon, banner, arka plan, boşluk, '
    'süsleme) yoksa bir KATILIM BANKASI ürünü, kampanyası, hizmeti, oranı/ücreti '
    'hakkında GERÇEK ve ÖNEMLİ bilgi mi içeriyor? '
    'Bilgi içeriyorsa o bilgiyi eksiksiz MARKDOWN olarak çıkar (tabloları markdown tablo yap). '
    'SADECE şu JSON ile yanıt ver: {"decorative": true|false, "content": "<markdown ya da boş>"}')


class ImageCache:
    """sha256(görsel) -> {"decorative": bool, "content": str}. Diske kalıcı."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict[str, dict] = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self.data = {}
        self._since_save = 0
        self._lock = threading.Lock()         # thread-safe (banka-içi paralel examine)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            payload = json.dumps(self.data, ensure_ascii=False)
        self.path.write_text(payload, encoding="utf-8")

    def examine(self, png: bytes) -> dict | None:
        """Görseli incele; AYNI görsel daha önce görülmüşse LOKALDEN döner (VLM yok).
        VLM ulaşılamaz/boş yanıt verirse None döner ve CACHE'E YAZMAZ — böylece
        outage kalıcı 'dekoratif' damgası basmaz; görsel sonraki koşuda tekrar denenir.
        Çağıran taraf None'ı 'bu görseli atla' diye ele almalı. Thread-safe: yavaş VLM
        çağrısı kilit DIŞINDA yapılır (aynı hash'i iki thread nadiren 2 kez sorabilir)."""
        h = hashlib.sha256(png).hexdigest()
        with self._lock:
            if h in self.data:                # daha önce skip/extract edilmiş -> lokal
                return self.data[h]
        d = call_json(img_msg(_EXAMINE_Q, png), max_tokens=4096)   # kilit DIŞINDA (yavaş)
        if not d:                             # VLM ulaşılamadı / hiç parse edilemedi
            return None                       # cache'e yazma, atla (sonraki koşu tamamlar)
        res = {"decorative": bool(d.get("decorative", True)),
               "content": (d.get("content") or "").strip()}
        if not res["content"]:                # içerik yoksa dekoratif say (tutarlılık)
            res["decorative"] = True
        with self._lock:
            self.data[h] = res
            self._since_save += 1
            do_save = self._since_save >= 20
            if do_save:
                self._since_save = 0
        if do_save:
            self.save()
        return res
