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
from datetime import date
from pathlib import Path

import httpx

from config import tunnel
from config.settings import settings
from dataprep.net_limit import NET_SEM

log = logging.getLogger("dataprep.vlm")

VLM_PATH = "/gemma/v1/chat/completions"
VLM_MODEL = "google/gemma-4-31B-it"
TEMP_LADDER = (0.0, 0.3, 0.6, 1.0)

# Sunucu ~100 AÇIK bağlantıda (ngrok opn) tıkanıyor. urllib her istekte YENİ bağlantı
# açıp TIME_WAIT'te biriktiriyordu -> opn 100'ü aşıyordu. HAVUZLU client ile açık
# bağlantı max_connections ile SABİT tavanlanır (yeniden kullanım, churn yok).
# VLM_POOL env ile ayarlanır; çok-süreçli PDF aşamasında süreç başına düşük tutulur.
# NET_SEM 60 eşzamanlı isteğe izin veriyor; havuz tavanı bunun ALTINDA
# kalırsa istekler httpx'in pool kuyruğunda bekler (yeni darboğaz olur).
# Pay bırakmak için 80.
# 80 -> 25 (kullanıcı kararı 2026-08-23: "hiçbir aşamada 25'ten fazla istek
# atmayalım, boğulmasın"). NET_SEM zaten 25'te tutuyor ama o TEK savunma
# hattıydı: semaforu atlayan bir kod yolu olsa httpx 80 bağlantıya kadar
# açabilirdi. Havuzun kendisi de 25'e çekilerek İKİNCİ hat kuruldu —
# eşzamanlılık iki bağımsız yerden sınırlanır.
VLM_POOL = int(os.environ.get("VLM_POOL", "50"))
# PARALELLİK TAMAMEN KALDIRILDI (content.py WORKERS=1, bankalar da tek process
# içinde sırayla) — sunucuya HER ZAMAN sadece 1 istek gittiği için artık
# "kuyrukta bekletiliyor olabilir, sonsuza kadar bekleyelim" gerekçesi
# büyük ölçüde geçersiz: tek istek varsa sunucu hemen işlemeye başlar. 5
# dakika (300s) "gerçekten asıldı" ile "makul süren normal işlem"i ayırt
# etmeye yeter — read=None (tam sınırsız) YİNE DE yapılmadı, tünel/proxy
# bağlantıyı SESSİZCE (FIN/RST'siz) düşürürse sonsuza kadar fark edilmeden
# asılı kalabilir; bağlantı KOPARSA (ya da 300s dolarsa) _post()'un ÜSTEL
# backoff'lu sonsuz-retry'i devreye girer, hiçbir zaman gerçekten pes etmez.
# ÖLÜ BAĞLANTI KORUMASI — 5 dakika. Daha uzun bir değer (900s denendi) şu
# soruna yol açıyordu: ağ/IP değişince (modem reseti, VPN açma, sunucu
# kesintisi) işletim sistemi eski soketleri hâlâ ESTABLISHED gösteriyor,
# süreç onlardan yanıt bekliyor ve timeout dolana kadar HİÇBİR ŞEY yapmıyor —
# canlı yaşandı: 14 dakika boyunca tek istek atılmadı, süreci elle yeniden
# başlatmak gerekti. 300s: gerçek isteklerin (150-200s) üstünde ama ölü bir
# bağlantıda takılı kalmayı makul sürede kesiyor. Timeout sonrası _post()
# zaten sonsuz retry yapar ve YENİ BAĞLANTIYLA dener (bkz. _stream_once).
# 300 -> 120: ZOMBİ BAĞLANTI MALİYETİ. Canlı ölçüm (20:16:39-20:21:30):
# 8 bağlantı (= START_LIMIT'in TAMAMI) yanıt vermeden asılı kaldı, süreç
# 4dk51sn HİÇBİR istek atamadan bekledi — bu sırada sunucu boştaydı ve
# doğrudan curl'e 0.7s'de yanıt veriyordu. Ölü bağlantıyı 300s beklemek
# kotayı kilitliyor; 120s aynı durumda kaybı ~1 dakikaya indiriyor.
# Gerçek VLM çağrıları görsel başına 6-25s, uzun PDF üretimleri 150-200s
# olabiliyor — ama o uzun işler STREAMING olduğu için read timeout'u
# Read Timeout: 120s (kullanıcı kararı — tünelin tavanıyla tam senkron).
# 120s ile hiçbir istek erken kesilmez, sıfır risk.
_READ_TIMEOUT = float(os.environ.get("VLM_READ_TIMEOUT", "120"))
# Paket-arası sessizlik sınırı (saniye). Toplam süre sınırı AYRI ve daha
# uzundur (_READ_TIMEOUT); bu yalnız "akış durdu mu?" sorusunu yanıtlar.
_SESSIZLIK_SINIRI = float(os.environ.get("VLM_STREAM_IDLE", "120"))
# Bir isteğin EN FAZLA kaç kez deneneceği. Sonsuz retry saatler yakıyordu;
# limit sonunda öğe "başarısız" diye etiketlenir ve koşu İLERLER.
_MAX_DENEME = int(os.environ.get("VLM_MAX_DENEME", "12"))
_BASARISIZ_LOG = Path(__file__).resolve().parents[1] / "data" / "_basarisiz_ogeler.jsonl"


def _etiketle_basarisiz(payload: dict, deneme: int, sure: float) -> None:
    """Pes edilen isteği kalıcı olarak kaydet — sonra elle incelenebilir."""
    try:
        ozet = ""
        for m in (payload.get("messages") or []):
            icerik = m.get("content")
            if isinstance(icerik, str):
                ozet = icerik[:300]
            elif isinstance(icerik, list):
                for parca in icerik:
                    if isinstance(parca, dict) and parca.get("type") == "text":
                        ozet = (parca.get("text") or "")[:300]
                        break
            if ozet:
                break
        _BASARISIZ_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _BASARISIZ_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                "deneme": deneme, "sure_sn": round(sure),
                                "istek_ozeti": ozet}, ensure_ascii=False) + "\n")
    except Exception:
        pass
# Bir keep-alive soketi havuzda en fazla bu kadar boşta bekleyebilir.
_KEEPALIVE_EXPIRY = float(os.environ.get("VLM_KEEPALIVE_EXPIRY", "15"))


def _new_client() -> httpx.Client:
    """VLM istemcisi.

    KEEP-ALIVE ÖMRÜ SINIRLI (keepalive_expiry): aradaki tünel (lhr.life) soketi
    kendi tarafında yenilediğinde havuzdaki BAYAT keep-alive soketi ilk pakette
    bozuk gidiyor ve nginx bunu "400 Bad Request / 0 byte" ile reddediyor —
    sunucu access log'unda kanıtlandı (2026-08-18 23:00-23:01 UTC): 400 alan
    isteğin hemen ardından atılan istek 200 dönüyor, yani istek İÇERİĞİ değil
    SOKET bozuk. vLLM'in kendi logunda bu isteklerin izi bile yok; nginx
    katmanında kesiliyorlar.

    Çözüm: soketi tünelin ömür sınırından KISA tut (_KEEPALIVE_EXPIRY), böylece
    bayatlamış soket yeniden kullanılmadan atılır."""
    return httpx.Client(
        # read: PAKET-ARASI sessizlik sınırı — TOPLAM süre değil (o
        # _stream_once içinde ayrıca kontrol edilir). 900 iken sunucu her
        # 800 saniyede bir byte gönderse bile timeout HİÇ tetiklenmiyordu ve
        # süreç sonsuza kadar bekliyordu (canlı: emlakkatilim 20 dk "deneme
        # 1"de asılı, CPU %0 / bağlantı 0). Streaming'de token'lar saniyeler
        # içinde akar; 120 saniyelik sessizlik GERÇEK arızadır.
        timeout=httpx.Timeout(connect=10.0, read=_SESSIZLIK_SINIRI,
                              write=30.0, pool=30.0),
        headers={"Content-Type": "application/json", "Connection": "keep-alive"},
        limits=httpx.Limits(max_connections=VLM_POOL,
                            max_keepalive_connections=VLM_POOL,
                            keepalive_expiry=_KEEPALIVE_EXPIRY))


_client = _new_client()
_client_lock = threading.Lock()
_client_gen = 0
# ZOMBİ BAĞLANTI KORUMASI: sunucu tarafında bir reload/restart (ör. nginx config
# değişikliği) o an AÇIK olan keep-alive bağlantıları sunucu tarafından kapatır,
# ama httpx'in havuzu bunu HER ZAMAN güvenilir şekilde fark edip atmayabilir —
# canlı kanıtlandı: nginx buffer düzeltmesinden SONRA bile iki süreç (turkiyefinans,
# ziraatkatilim) 30+ dakika RemoteProtocolError'da TAKILI kaldı (sunucu düzelmişti,
# ama süreç YENİDEN BAŞLATILINCA -> yeni client/havuz -> ANINDA düzeldi). Süreci
# manuel restart etmeye gerek KALMASIN diye: bir isteğin KENDİ art arda birkaç
# başarısızlığından sonra TÜM havuzu (client'ı) burada KENDİMİZ sıfırdan
# yeniliyoruz. BİLEREK PAYLAŞILAN/GLOBAL bir "ardışık başarısızlık" sayacı
# KULLANMIYORUZ — canlı kanıtlandı: 5 worker'dan biri zombi bağlantıda tıkanıp
# kalırken DİĞER 4'ü başarılı istek atmaya devam ediyorsa, paylaşılan sayaç
# sürekli sıfırlanıp eşiğe HİÇ ulaşamıyordu (tıkalı worker sonsuza kadar
# kurtulamıyordu). Bunun yerine HER isteğin _post() döngüsündeki KENDİ deneme
# sayacı (attempt) kullanılır — o worker'ın kendi art arda başarısızlığı 4'e
# her ulaştığında (4, 8, 12, ...) SADECE O çağırır; diğerlerinin başarısı bunu
# etkilemez.
_FAIL_RESET_EVERY = 4


# eski client'ı hemen değil, bu kadar bekleyip kapat — _READ_TIMEOUT ile AYNI
# değer BİLEREK seçildi: VLM çağrıları 30-100+ saniye sürebiliyor, 20s gibi
# kısa bir bekleme bile üzerinde HÂLÂ gerçekten devam eden bir isteği (sunucu
# 499 — "istemci erken bağlantıyı kesti" görür) kesebiliyordu. _READ_TIMEOUT
# kadar beklersek, eski havuzdaki HERHANGİ bir istek ya kendi doğal sonucuna
# ulaşır ya da kendi doğal read-timeout'una uğrar — biz ASLA erken kesmeyiz.
# Bir bağlantının yaşayabileceği EN UZUN süre: 120s (kullanıcı kararı).
# _READ_TIMEOUT bunun üstüne çıkarılsa bile bağlantı 120s'de kapatılır.
# TAVAN KALDIRILDI (2026-08-23): eskiden min(_READ_TIMEOUT, 120) idi, yani
# read timeout 900'e çıksa bile eski havuz 120s sonra kapatılıyor ve HÂLÂ
# SÜREN sağlıklı bir istek koparılıyordu (sunucuda 499). Artık isteğin
# kendi zaman aşımı kadar beklenir — biz asla erken kesmeyiz.
_CLOSE_GRACE = _READ_TIMEOUT


def _reset_client_if_stuck(attempt: int) -> None:
    """HER BAŞARISIZLIKTA bağlantı havuzunu yeniler — böylece bir sonraki
    deneme GARANTİLİ olarak YENİ bir bağlantıyla gider, ölü sokete tekrar
    yazılmaz.

    Eskiden 4 hatada bir yenileniyordu; ağ/IP değişince (modem reseti, VPN,
    sunucu kesintisi) süreç eski ölü soketleri tekrar tekrar deneyip dakikalarca
    takılı kalıyordu (canlı yaşandı, 14 dakika). Ölü bağlantıyı yeniden
    kullanmanın hiçbir faydası yok: hata aldıysak o bağlantıya güvenilmez.

    KAPATMA POLİTİKASI (kullanıcı kararı, 2026-08-18): kesilen/başarısız olan
    bağlantı TAMAMEN KAPATILIR, aynı sokete geri dönülmez — her deneme sıfırdan
    yeni bir bağlantıyla gider. Kapatma _CLOSE_GRACE kadar ERTELENİR (anında
    değil) çünkü aynı havuzda BAŞKA thread'lerin GERÇEKTEN devam eden sağlıklı
    istekleri olabilir; onları koparmak sunucuda 499 üretir (canlı kanıtlandı).
    Gecikme 120s ile TAVANLI: bir bağlantı bundan uzun yaşamaz."""
    global _client, _client_gen
    with _client_lock:
        old = _client
        _client = _new_client()
        _client_gen += 1
        gen = _client_gen

    def _instant_close():
        try:
            old.close()                     # havuzdaki ölü/askıda kalmış TÜM soketler anında kapanır
        except Exception:
            pass

    threading.Thread(target=_instant_close, daemon=True).start()
    log.warning("    [VLM_HAVUZ_YENİLENDİ] istek başarısız (deneme %d) — eski bağlantılar ANINDA KAPATILDI, "
                "yeni bağlantı havuzu açıldı (nesil #%d)", attempt, gen)


def _note_success() -> None:
    pass


# --- düşük seviyeli çağrı (structured JSON + retry + sıcaklık) --------------
MAX_IMG_BYTES = 350_000     # Hızlı ağ transferi ve sunucu payload güvenliği
_MAX_DIM = 1280             # VLM'in yazıları en net okuduğu ve en hafif çalıştığı optimum piksel sınırı


def _fit_png(png: bytes) -> tuple[bytes, str]:
    """Görseli VLM'in en net okuyacağı optimum boyuta (max 1280px) ölçekle ve hafiflet."""
    import io
    from PIL import Image
    try:
        img = Image.open(io.BytesIO(png)).convert("RGB")
    except Exception:
        return png, "image/png"
    
    # 1) Uzun kenar 1280px'den büyükse okunabilirliği bozmadan orantılı küçült
    W, H = img.size
    if max(W, H) > _MAX_DIM:
        scale = _MAX_DIM / max(W, H)
        img = img.resize((max(1, int(W * scale)), max(1, int(H * scale))), Image.LANCZOS)
    
    # 2) JPEG kalitesini optimize ederek hafif payload üret
    for q in (88, 80, 72):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=q, optimize=True)
        if buf.tell() <= MAX_IMG_BYTES:
            return buf.getvalue(), "image/jpeg"
            
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=70, optimize=True)
    return buf.getvalue(), "image/jpeg"


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


# Retry backoff tavanı (saniye). Çok düşük olması sunucuyu boğuyor (bkz. _post).
_BACKOFF_MAX = float(os.environ.get("VLM_BACKOFF_MAX", "30"))


_WARN_EVERY = 300           # 5 dakikada bir [UZUN_SÜRELİ_HATA] uyarısı bas — DURMAZ,
                            # sadece uzun süredir başarısız olduğunu görünür/aranabilir yapar

# ---------------------------------------------------------------- kapanış
# ORTAK KAPANIŞ BAYRAĞI (content.py::_install_signal_handlers set eder).
# "ASLA PES ETME" ilkesi sunucu hatası içindir, KULLANICI İSTEĞİ için değil:
# operatör SIGTERM gönderdiyse sonsuz retry'ı sürdürmek süreci saatlerce
# kapanmaz hale getirir. Bayrak kalkınca retry döngüsü BEKLEMEYİ bırakır ve
# eldeki kısmi yanıtla döner — çağıran onu ledger'a "işlendi" YAZMAZ, yani
# o öğe sonraki koşuda baştan denenir: VERİ KAYBI YOK, sadece erken çıkış.
STOPPING = threading.Event()


# GEÇİCİ 4xx'ler: bunlar "istek hatalı" demek DEĞİL, "şimdi olmaz" demek —
# retry edilirse geçer. 403/429 bizde tünelden geliyor (vLLM auth kullanmıyor),
# 408/425 zaten zaman aşımı/erken istek.
#
# 400 DE BU LİSTEDE (2026-08-19, sunucu access log'uyla kanıtlandı): tünel
# soketi bayatladığında nginx isteği "400 / 0 byte" ile reddediyor ve istek
# vLLM'e HİÇ ULAŞMIYOR. Aynı istek saniyeler sonra 200 dönüyor — yani gövde
# değil SOKET bozuk. Bunu "kalıcı" sayıp atlamak sağlam bir öğeyi sessizce
# es geçmek demekti. (Gerçekten bozuk bir gövde olsaydı retry de 400 alırdı;
# o durumda öğe ledger'a yazılmadığı için yine kayıp olmaz.)
_GECICI_4XX = frozenset({400, 403, 408, 425, 429})


class _PermanentClientError(Exception):
    """GERÇEKTEN kalıcı 4xx — aynı istek tekrar gönderilse aynı sonucu verir
    (404 yanlış endpoint, 413 çok büyük gövde, 422 şema hatası).
    _GECICI_4XX bunun DIŞINDA: onlar normal retry döngüsüne düşer."""

    def __init__(self, status: int):
        super().__init__(f"HTTP {status}")
        self.status = status


def _stream_once(body: dict) -> tuple[str, bool]:
    """TEK bir streaming isteği yürütür. Dönen: (biriken içerik, tamamlandı_mı).

    STREAMING BİLİNÇLİ TERCİH (kanıtlı): stream OLMAYAN bir istekte üretim
    boyunca bağlantıdan HİÇ bayt akmaz — aradaki tünel (lhr.life) ~120 saniyelik
    IDLE zaman aşımı uyguladığı için uzun süren üretimler tam da bu yüzden
    koparılıyordu (sunucu access log'unda 499, bizde RemoteProtocolError; canlı
    ölçüldü: TÜM kopmaların süresi 120-125s'de kümelendi). Streaming'de her token
    anında akar, bağlantı ASLA boşta kalmaz — böylece o 120 saniyelik pencere
    GERÇEKTEN kullanılabilir olur.

    AMA 120s'İ AŞTIRMAZ: ölçüm (2026-08-18) tek istek sürelerinin _READ_TIMEOUT
    300s ayarlıyken bile 120-124s'de kümelendiğini gösterdi (120s: 116 kez,
    121s: 86 kez; 300'e HİÇ ulaşılmadı). Yani sınır tünelin SABİT ömür sınırı;
    streaming onu uzatmıyor, sadece pencerenin boşa gitmesini engelliyor.

    `tamamlandı_mı` False dönerse (bağlantı akışın ORTASINDA koptu) biriken
    kısmi yanıt ATILIR ve istek SIFIRDAN denenir — hiç yanıt üretilmemiş gibi
    (bkz. _post). Kısmi metni "kaldığın yerden" sürdürmek riskliydi: model
    tekrar/atlama yapabilir, sessizce bozuk içerik üretebilirdi."""
    out: list[str] = []
    finished = False
    url = settings.VLLM_BASE_URL.rstrip("/") + VLM_PATH
    _akis_basi = time.time()
    with NET_SEM:
        with _client.stream("POST", url, json=body) as r:
            if 400 <= r.status_code < 500:
                r.read()
                if r.status_code in _GECICI_4XX:
                    # GEÇİCİ -> normal retry döngüsüne düşsün (asla es geçme)
                    raise httpx.HTTPStatusError(
                        f"gecici {r.status_code}", request=r.request, response=r)
                raise _PermanentClientError(r.status_code)
            r.raise_for_status()                # 5xx -> çağırandaki retry'a düşer
            for line in r.iter_lines():
                # TOPLAM SÜRE SINIRI (2026-08-23). httpx'in `read` timeout'u
                # STREAMING'de İKİ PAKET ARASI süreyi ölçer — sunucu yavaş
                # yavaş token gönderdiği sürece toplam süre SINIRSIZ uzar ve
                # 900s'lik read timeout HİÇ tetiklenmez. Canlı: istekler
                # "deneme 1"de 323s / 905s / 4512s takılı kaldı, retry'a bile
                # geçemediler; süreç CPU %0 / bağlantı 0 ile ölü göründü.
                # Buradaki kontrol akışı keser, çağıranın retry'ı devreye girer
                # ve YENİ bağlantıyla denenir (veri kaybı yok — kısmi çıktı
                # finished=False ile atılır).
                if time.time() - _akis_basi > _READ_TIMEOUT:
                    raise httpx.ReadTimeout(
                        f"akış toplam {_READ_TIMEOUT:.0f}s'yi aştı (streaming "
                        f"paket-arası timeout'a takılmaz)", request=r.request)
                if STOPPING.is_set():           # kapanış istendi -> akışı BIRAK
                    # Eldeki token'lar korunur (finished=False dönüyoruz, çağıran
                    # kısmi yanıtı ledger'a "işlendi" yazmaz) — veri kaybı yok.
                    break
                if not line:
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line == "[DONE]":
                    finished = True
                    break
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for ch in d.get("choices") or []:
                    delta = ch.get("delta") or {}
                    piece = delta.get("content") or delta.get("reasoning") or ""
                    if piece:
                        out.append(piece)
                    if ch.get("finish_reason"):   # stop/length -> sunucu bitirdi
                        finished = True
    return "".join(out), finished


def _post(payload) -> str:
    """HAVUZLU client (açık bağlantı VLM_POOL ile tavanlı) + STREAMING. ASLA PES
    ETMEZ: hata olursa ÜSTEL (2x) backoff'la dener, 60s'e ulaşınca 60s'de
    SABİTLENİP SONSUZA KADAR o aralıkla denemeye devam eder — sunucu ne kadar
    uzun süre kapalı kalırsa kalsın veri kaybı/atlama YOK. Her _WARN_EVERY
    saniyede bir [UZUN_SÜRELİ_HATA] etiketiyle uyarı basar (durmadan devam
    eder). Kalıcı istemci hatası (4xx) HARİÇ: o asla düzelmeyeceği için tek
    denemede "" döner.

    AKIŞ KOPMASI: yarım yanıt ATILIR ve istek baştan denenir (basit ve
    öngörülebilir; kısmi/yarım içerik asla "tamamlanmış" sayılmaz)."""
    start = time.time()
    delay = 1.0
    attempt = 0
    last_warn = 0.0
    acc = ""                                    # bu turda biriken yanıt (kopmada sıfırlanır)
    # SINIRSIZ RETRY (kullanıcı kararı 2026-08-23: "VLLM sınırsız olacak.
    # Sınırsız ama fresh bağlantı ile. URL kontrolü ile.").
    # Deneme limiti KOYULMAZ — ama her hatada (a) havuz tamamen kapatılıp
    # TAZE bağlantı açılır (_reset_client_if_stuck) ve (b) tünel URL'i
    # doğrulanır (tunnel.refresh_if_needed → 1 dk merdiven + sağlık
    # yoklaması). Böylece "aynı ölü sokete sonsuza kadar vurma" durumu
    # oluşmaz; her deneme gerçekten YENİ bir bağlantı ve DOĞRULANMIŞ bir
    # adresle yapılır.
    while True:
        attempt += 1
        _t0 = time.time()                   # try DIŞINDA: hata yolunda da tanımlı
        try:
            body = dict(payload)
            body["stream"] = True
            piece, finished = _stream_once(body)
            acc += piece
            _note_success()                     # zombi-bağlantı sayacını sıfırla
            if finished:
                # UYARLANABİLİR SINIRLAYICI geri bildirimi: istek ne kadar
                # sürdü? Hızlıysa eşzamanlılık artırılabilir, tünelin ömür
                # sınırına yaklaşıyorsa artırılmamalı (bkz. net_limit.py).
                NET_SEM.report(ok=True, duration=time.time() - _t0)
                return acc.strip()
            # AKIŞ ORTADA KOPTU -> kısmi yanıtı AT, isteği BAŞTAN dene.
            # (Eskiden "kaldığın yerden devam et" ile sürdürülüyordu; canlı
            # loglarda bu yol HİÇ devreye girmedi [0 kez], buna karşılık kodu
            # ve hata alanını büyütüyordu — sadeleştirildi.) Kısmi metin ASLA
            # tamamlanmış sayılmaz: acc sıfırlanır, çağıran ya tam yanıt alır
            # ya hiç — yarım içerik ledger'a "işlendi" diye yazılamaz.
            if STOPPING.is_set():               # kapanışta yeniden deneme yok
                return ""
            # AKIŞ KESİLDİ -> KALDIĞI YERDEN DEVAM ETTİR (baştan DEĞİL).
            #
            # NEDEN ZORUNLU (ölçüldü 2026-08-23): tünelin 120s bağlantı ömrü
            # var, ama gerçek temizleme isteği (10911 karakterlik prompt,
            # ~8196 karakterlik çıktı) TEK BAŞINA 135s sürüyor. Yani istek
            # tünel limitinin ÜSTÜNDE; baştan denemek SONSUZ DÖNGÜ demek —
            # canlı olarak yaşandı, aynı sayfa saatlerce bitmedi.
            #
            # VERİ KAYBI YOK: metin kırpılmaz, çıktı kısaltılmaz. Modele
            # ürettiği kısım aynen geri verilir ve "tam buradan devam et"
            # denir; parçalar birleştirilince tam yanıt elde edilir.
            if not acc.strip():
                log.warning("    [YANIT_YARIM_KALDI] hiç içerik gelmedi — "
                            "istek baştan deneniyor")
                continue
            log.warning("    [YANIT_YARIM_KALDI] akış %d karakterde kesildi — "
                        "KALDIĞI YERDEN devam ettiriliyor (içerik korunuyor)",
                        len(acc))
            payload = dict(payload)
            temel = [m for m in payload.get("messages", [])
                     if m.get("role") != "assistant"]
            payload["messages"] = temel + [
                {"role": "assistant", "content": acc},
                {"role": "user", "content":
                    "Yanıtın bağlantı kesintisi yüzünden yarıda kaldı. "
                    "Yukarıdaki yarım yanıtı TEKRARLAMA ve BAŞTAN BAŞLAMA. "
                    "Kaldığın KARAKTERDEN itibaren, hiçbir şey atlamadan ve "
                    "hiçbir şeyi kısaltmadan devam et. Sadece devamını yaz."},
            ]
            continue
        except _PermanentClientError as exc:
            log.warning("    VLM %d (kalıcı) — retry yok, geçiliyor", exc.status)
            return acc.strip()                  # elde kısmi varsa onu ver, yoksa ""
        except Exception as exc:                # bağlantı/timeout/5xx -> ISRARLI retry
            elapsed = time.time() - start
            # BU İSTEĞİN süresi (elapsed TÜM denemelerin toplamı — sınıflandırma
            # için yanlış olur). Süreyi vermek ŞART: net_limit buna bakarak
            # "zombi bağlantı" (süre read-timeout'a yapışmış) ile gerçek
            # tıkanıklığı ayırıyor; süresiz gelen her hata tıkanıklık sayılıp
            # limiti YARIYA indirir (canlı: 8->2 çöküş, sunucu boşken).
            NET_SEM.report(ok=False, duration=time.time() - _t0)
            url_changed = tunnel.refresh_if_needed()          # tünel URL'i değişti mi kontrol et
            if url_changed:
                _reset_client_if_stuck(attempt)               # URL değiştiyse eski soketleri ANINDA çöpe at
                delay = 0.5                                   # Yeni URL'e beklemeden ANINDA bağlan
            else:
                _reset_client_if_stuck(attempt)               # Her hatada eski soketi ANINDA kapat
            if elapsed - last_warn >= _WARN_EVERY:
                log.warning("    [UZUN_SÜRELİ_HATA] %.0fs'dir başarısız (deneme %d): %s — "
                            "denemeye DEVAM ediyor (durmuyor, sadece bilgi amaçlı)",
                            elapsed, attempt, type(exc).__name__)
                last_warn = elapsed
            else:
                log.warning("    VLM istek hatası (deneme %d, %.0fs): %s — %.0fs sonra tekrar",
                            attempt, elapsed, type(exc).__name__, delay)
            if STOPPING.wait(delay):        # kapanış istendi -> bekleme BİTSİN
                log.warning("    [KAPANIŞ] retry beklemesi kesildi — eldeki %d karakterle "
                            "dönülüyor (öğe ledger'a yazılmaz, sonraki koşuda tekrar denenir)",
                            len(acc))
                return acc.strip()
            # BACKOFF TAVANI 30s. GEÇMİŞ: 60s -> 5s indirilmişti ("hatalar
            # zombi bağlantıdan, beklemek boşuna" varsayımıyla). CANLI ÖLÇÜM
            # bunu ÇÜRÜTTÜ (2026-08-19 01:00-01:55): 50 paralel istek 5s'de
            # bir yeniden denenince sunucu boğuldu — 55 dakikada 1567 deneme,
            # SIFIR başarılı yanıt; doğrudan curl bile 0.9s yerine 38.7s'de
            # döndü. Retry'ın kendisi yükü besleyip daha çok timeout üretiyor
            # (kendini besleyen döngü). 30s: sunucuya nefes aldırır, "asla pes
            # etme" ilkesi korunur (deneme sayısı hâlâ sınırsız).
            delay = min(delay * 2, _BACKOFF_MAX)



def _try_parse_json(raw: str) -> dict | None:
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
    return None


_JSON_NUDGE = {"role": "user", "content": (
    "Önceki cevabın geçerli JSON değildi. SADECE geçerli JSON döndür, "
    "başka hiçbir açıklama/metin ekleme.")}


def call_json(messages, max_tokens: int | None = None, _cycles: int = 2) -> dict:
    """STRUCTURED JSON; parse olmazsa sıcaklığı 0->0.3->0.6->1.0 artırır. Bu
    merdiven TAMAMEN tükenip hâlâ geçersiz JSON gelirse VAZGEÇMEZ — modele
    'önceki cevabın geçerli JSON değildi' uyarısı eklenir, sıcaklık 0.0'a
    resetlenir ve TÜM merdiven bu uyarıyla birlikte bir kez daha denenir
    (_cycles tur, varsayılan 2 -> toplam en fazla 8 istek)."""
    msgs = list(messages)
    for cycle in range(_cycles):
        for t in TEMP_LADDER:
            payload = {"model": VLM_MODEL, "messages": msgs, "temperature": t,
                       "response_format": {"type": "json_object"},
                       "chat_template_kwargs": {"enable_thinking": False}}  # thinking kapalı
            if max_tokens is not None:
                payload["max_tokens"] = max_tokens
            raw = _post(payload)
            if not raw:                   # bağlantı çöktü -> merdiveni deneme, hemen çık
                return {}
            d = _try_parse_json(raw)
            if d is not None:
                return d
            log.info("    (JSON parse edilemedi, sıcaklık %.1f ile tekrar)", t)
        if cycle + 1 < _cycles:
            log.info("    (sıcaklık merdiveni tükendi — 0.0'a resetlenip uyarıyla tekrar)")
            msgs = msgs + [_JSON_NUDGE]
    return {}


# --- GÖRSEL inceleme + hash-dedup cache ------------------------------------
def _examine_q(today: str) -> str:
    return (
        'Bu görseli incele. DEKORATİF mi (logo, ikon, banner, arka plan, boşluk, '
        'süsleme) yoksa bir KATILIM BANKASI ürünü, kampanyası, hizmeti, oranı/ücreti '
        'hakkında GERÇEK ve ÖNEMLİ bilgi mi içeriyor? '
        'Bilgi içeriyorsa o bilgiyi eksiksiz MARKDOWN olarak çıkar (tabloları markdown tablo yap). '
        'ÇOK ÖNEMLİ — KAMPANYA GEÇERLİLİK TARİHİ: Görselde geçen telif yılı (©), '
        'kuruluş tarihi, sürüm no gibi genel yılları ASLA kampanya tarihi sayma. '
        'SADECE bu ürün/kampanya/fırsatın açık geçerlilik başlangıç ve/veya bitiş '
        'tarihini (ör. "31 Aralık 2026 tarihine kadar geçerlidir") bildir. '
        'Açık kampanya tarihi yoksa boş bırak, UYDURMA. '
        f'BUGÜNÜN TARİHİ: {today} — göreceli/eksik yıl gibi ifadeleri buna göre değerlendir. '
        'SADECE şu JSON ile yanıt ver: {"decorative": true|false, "content": "<markdown ya da boş>", '
        '"gecerlilik_baslangic": "<YYYY-MM-DD ya da boş>", "gecerlilik_bitis": "<YYYY-MM-DD ya da boş>"}')


_MAX_IMG_H = 2048
# GENİŞLİK de bölünür (bkz. _chunk_tall_png): _fit_png uzun kenarı 1280px'e
# sıkıştırdığı için, bölünmeyen GENİŞ bir tablo görselinde küçük yazılar
# okunamaz hâle geliyordu. Bölme küçültmenin YERİNE geçer, bilgi kaybolmaz.
_MAX_IMG_W = 2048
_IMG_OVERLAP = 200


def _chunk_tall_png(png: bytes) -> list[bytes]:
    """Büyük görselleri %10 overlap'li parçalara böler — HEM dikey HEM YATAY.

    KULLANICI KARARI (2026-08-22): "görsel boyut alt ve üst limiti koymadan
    devam edeceğiz". Bu fonksiyon KÜÇÜLTMEZ, BÖLER — yani bilgi kaybı yoktur.

    NEDEN YATAY DA GEREKLİ: eskiden yalnız YÜKSEKLİK bölünüyordu. Bir görsel
    ÇOK GENİŞSE (tipik olarak ücret/oran tabloları) bölünmeden _fit_png'ye
    gidiyor ve orada uzun kenar 1280px'e SIKIŞTIRILIYORDU — tablodaki küçük
    yazılar okunamaz hâle gelip VLM'in onları atlamasına yol açıyordu. Artık
    genişlik de bölünüyor, her parça kendi doğal çözünürlüğünde kalıyor.

    Bölme sırası: önce satırlar (dikey), sonra her satır sütunlara (yatay).
    Overlap sayesinde sınıra denk gelen bir hücre en az bir parçada BÜTÜN
    kalır — 8196/%10 metin chunk'lamasıyla AYNI ilke."""
    import io
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(png)).convert("RGB")
    except Exception:
        return [png]
    W, H = im.size
    if H <= _MAX_IMG_H and W <= _MAX_IMG_W:
        return [png]

    def _araliklar(uzunluk: int, pencere: int) -> list[tuple[int, int]]:
        if uzunluk <= pencere:
            return [(0, uzunluk)]
        out, bas = [], 0
        while bas < uzunluk:
            son = min(bas + pencere, uzunluk)
            out.append((bas, son))
            if son >= uzunluk:
                break
            bas = son - _IMG_OVERLAP
        return out

    chunks = []
    for ust, alt in _araliklar(H, _MAX_IMG_H):
        for sol, sag in _araliklar(W, _MAX_IMG_W):
            buf = io.BytesIO()
            im.crop((sol, ust, sag, alt)).save(buf, format="PNG")
            chunks.append(buf.getvalue())
    return chunks


# TÜM BANKALARIN PAYLAŞTIĞI görsel cache (kullanıcı kararı 2026-08-23).
# Anahtar sha256(görsel) olduğu için EVRENSELDİR: aynı görsel hangi bankada
# geçerse geçsin bir kez incelenir. Banka başına ayrı dosya tutulduğunda
# ortak logolar/ikonlar/TKBB belgeleri 10 kez VLM'e gidiyordu.
ORTAK_GORSEL_CACHE = (Path(__file__).resolve().parents[1] / "data"
                      / "_ortak_image_cache.json")


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
        # ESKİ BANKA-İÇİ CACHE'LERİ BİR KEZ İÇERİ AL (veri kaybı olmasın):
        # ortak dosyaya geçtik ama daha önce banka başına birikmiş binlerce
        # görsel incelemesi var; onları yeniden VLM'e yollamak israf olurdu.
        if self.path == ORTAK_GORSEL_CACHE:
            for eski in self.path.parent.glob("*_site/_image_cache.json"):
                try:
                    for k, v in json.loads(eski.read_text(encoding="utf-8")).items():
                        self.data.setdefault(k, v)
                except Exception:
                    pass
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
        
        # 2048px üzeri görselleri %10 overlap ile parçala ve sırayla incele
        chunks = _chunk_tall_png(png)
        today = date.today().isoformat()
        q = _examine_q(today)

        contents = []
        is_decorative = True
        g_start = ""
        g_end = ""

        for part in chunks:
            d = call_json(img_msg(q, part))
            if not d:
                return None
            if not d.get("decorative", True) and (d.get("content") or "").strip():
                is_decorative = False
                contents.append(d["content"].strip())
            s = (d.get("gecerlilik_baslangic") or "").strip()
            e = (d.get("gecerlilik_bitis") or "").strip()
            if s and (not g_start or s < g_start):
                g_start = s
            if e and (not g_end or e > g_end):
                g_end = e

        merged_content = "\n\n".join(contents).strip()
        if not merged_content:
            is_decorative = True

        res = {
            "decorative": is_decorative,
            "content": merged_content,
            "gecerlilik_baslangic": g_start,
            "gecerlilik_bitis": g_end,
        }

        with self._lock:
            self.data[h] = res
            self._since_save += 1
            do_save = self._since_save >= 20
            if do_save:
                self._since_save = 0
        if do_save:
            self.save()
        return res
