"""Sunucu tarafı tünel URL'i zaman zaman değişiyor (host makine tüneli
yeniden kurunca) — eski URL sessizce ölü kalmaya başlar (offline/5xx/bağlantı
hatası). Bu modül canlı URL'i paylaşılan bir gist'ten çekip HEM
settings.VLLM_BASE_URL'i YERİNDE (bu süreç içinde, anında etkili) HEM DE
.env dosyasındaki VLLM_BASE_URL satırını (diske, kalıcı — sonraki YENİ
başlayan süreçler baştan doğru URL'i okusun diye) günceller.

Çağıranlar (vlm.py, crawl/policy.py, compare/*.py) bağlantı/5xx hatası
gördüğünde refresh_if_needed()'i tetikler — "büyük ihtimalle URL değişmiştir"
varsayımıyla. Ağ hatası olursa ya da URL aynıysa sessizce False döner, hiçbir
şeyi bozmaz."""
from __future__ import annotations

import logging
import re
import threading
import time
import urllib.request

from config.settings import ENV_FILE, settings

log = logging.getLogger("config.tunnel")

_GIST_URL = getattr(
    settings,
    "TUNNEL_GIST_URL",
    "https://gist.githubusercontent.com/dijitalkariyermerkezi/e91ef0ddbc60b3e241c6b3e602cad5c8/raw/tunnel_url.txt"
)
# Aynı süreçte paralel hata patlaması olduğunda hepsi aynı anda gist'e
# gitmesin diye: bir kontrolden sonra bu kadar saniye yeni kontrol yapılmaz.
# 10s -> 3s (2026-08-23): ANLIK URL değişimini hızlı yakalamak için. Bu bir
# yoklama döngüsü DEĞİL — yalnız bir istek HATA aldığında çağrılır, yani
# normal işleyişte hiç tetiklenmez. Sunucu düştüğünde ise sık kontrol
# istiyoruz: eski adrese takılı kalan her saniye boşa giden iştir.
_MIN_CHECK_INTERVAL = 3.0
# MERDİVEN ADIMLARI (saniye) — toplam 60s. İlk deneme ANINDA (0), sonra
# artan aralıklarla: çoğu kesinti ilk saniyelerde düzelir, uzayanlar için
# seyrek yoklama yeterli. Toplam 1 dakika (kullanıcı kararı 2026-08-23).
_MERDIVEN = (0, 5, 10, 15, 30)

_lock = threading.Lock()
_last_check = 0.0


def _fetch_live_url() -> str | None:
    try:
        # CACHE KIRICI (kanıtlı 2026-08-19): GitHub'ın CDN'i gist /raw/ yolunu
        # önbelleğe alıyor ve "Cache-Control: no-cache" başlığını YOK SAYIYOR —
        # canlı ölçümde başlıkla ESKİ URL (515725a3...) döndü, URL'e benzersiz
        # bir sorgu eklenince GERÇEK adres (c70c6f67...) geldi. Bu sessiz bir
        # arıza: süreç tünel değiştiğinde ölü bir adrese bağlanmayı sürdürürdü.
        url = f"{_GIST_URL}?t={int(time.time())}"
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0", "Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=5) as r:
            canli = r.read().decode("utf-8").strip()
        return canli or None
    except Exception as exc:
        log.warning("  (tünel gist'i okunamadı: %s)", exc)
        return None


def _persist_to_env(new_url: str) -> None:
    """.env'deki VLLM_BASE_URL satırını KALICI olarak günceller — sadece bu
    süreç değil, sonraki YENİ başlayacak süreçler de baştan doğru URL'i
    görsün diye. .env yoksa ya da satır bulunamazsa sessizce geçer (kritik
    değil — in-memory güncelleme zaten yeterli, bu sadece bir kolaylık)."""
    try:
        if not ENV_FILE.exists():
            return
        text = ENV_FILE.read_text(encoding="utf-8")
        new_text, n = re.subn(r"(?m)^VLLM_BASE_URL=.*$", f"VLLM_BASE_URL={new_url}", text)
        if n:
            ENV_FILE.write_text(new_text, encoding="utf-8")
    except Exception as exc:
        log.warning("  (.env güncellenemedi: %s)", exc)


def _saglikli(url: str, sn: float = 8.0) -> bool:
    """Bu adres GERÇEKTEN yanıt veriyor mu? (gemma /models yoklaması)

    Adresin "değişip değişmediğine" bakmak yetmez — asıl soru ÇALIŞIP
    çalışmadığıdır. Aday adresler bununla elenir."""
    try:
        req = urllib.request.Request(url.rstrip("/") + "/gemma/v1/models",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=sn) as r:
            return r.status == 200
    except Exception:
        return False


def _env_url() -> str | None:
    """.env dosyasındaki GÜNCEL VLLM_BASE_URL (diskten TAZE okunur).

    Süreç başlarken .env bir kez okunur; başka bir süreç adresi güncellerse
    bu süreç bunu göremez. Gist'e ulaşılamadığında ikinci kaynak olarak
    diskteki değere bakılır."""
    try:
        if not ENV_FILE.exists():
            return None
        m = re.search(r"(?m)^VLLM_BASE_URL=(.*)$", ENV_FILE.read_text(encoding="utf-8"))
        return m.group(1).strip() if m else None
    except Exception:
        return None


def refresh_if_needed() -> bool:
    """Bağlantı/sunucu hatası görülünce çağrılır. URL değiştiyse
    settings.VLLM_BASE_URL'i (bellekte) VE .env'i (diskte) günceller, True
    döner; değişmediyse ya da gist'e ulaşılamadıysa False döner (mevcut URL
    ile denemeye devam edilir)."""
    global _last_check
    with _lock:
        now = time.time()
        if now - _last_check < _MIN_CHECK_INTERVAL:
            return False
        _last_check = now

        # MERDİVEN YOKLAMASI (kullanıcı kararı 2026-08-23): gist GEÇ
        # güncellenebilir, bu yüzden TEK atışlık kontrol yetmez. 1 DAKİKA
        # boyunca artan aralıklarla (0/5/10/15/30s) tekrar tekrar bakılır;
        # çağıran bu süre boyunca BEKLER (bağlantı kopmaz, iş kaybolmaz).
        # İlk SAĞLIKLI adres bulunduğu an merdiven kesilir.
        return _merdiven_yokla()


def _merdiven_yokla() -> bool:
    """1 dakikalık merdiven: gist/.env/mevcut adaylarını tekrar tekrar yoklar.

    Neden merdiven: sunucu tarafında tünel yeniden kurulurken gist birkaç
    saniye-dakika GECİKMELİ güncelleniyor. Tek atışlık kontrol o pencereye
    denk gelirse eski adresi görüp "değişmemiş" diyor ve süreç ölü adrese
    saplanıyordu. Merdiven o pencereyi kapatır."""
    from config.settings import settings as _st
    for bekle in _MERDIVEN:
        if bekle:
            time.sleep(bekle)
        adaylar: list[str] = []
        for kaynak in (_fetch_live_url(), _env_url(), _st.VLLM_BASE_URL):
            if kaynak and kaynak not in adaylar:
                adaylar.append(kaynak)
        for aday in adaylar:
            if _saglikli(aday):
                if aday == _st.VLLM_BASE_URL:
                    return False              # mevcut adres zaten çalışıyor
                eski = _st.VLLM_BASE_URL
                _st.VLLM_BASE_URL = aday
                _persist_to_env(aday)
                log.warning("  [TÜNEL] URL değişti: %s -> %s (merdiven)", eski, aday)
                return True
    log.warning("  [TÜNEL] merdiven bitti (%.0fs) — hiçbir aday yanıt vermedi; "
                "mevcut adresle denemeye devam (iş KAYBOLMAZ)", sum(_MERDIVEN))
    return False


def _kullanilmayan_eski_yol() -> bool:
    # GİST GEÇ GÜNCELLENEBİLİR — bu yüzden TEK KAYNAĞA GÜVENİLMEZ
        # (kullanıcı kararı 2026-08-23: "gist geç güncellenir diye garanti
        # altına al"). Adaylar SIRAYLA denenir ve SAĞLIK YOKLAMASINDAN geçen
        # ilk adres kullanılır. Yani "adres değişti mi?" değil, "hangi adres
        # ÇALIŞIYOR?" sorusu sorulur.
        adaylar: list[str] = []
        for kaynak in (_fetch_live_url(), _env_url(), settings.VLLM_BASE_URL):
            if kaynak and kaynak not in adaylar:
                adaylar.append(kaynak)

        live = None
        for aday in adaylar:
            if _saglikli(aday):
                live = aday
                break

        if live is None:
            # HİÇBİRİ çalışmıyor -> sunucu gerçekten düşmüş. Adresi
            # DEĞİŞTİRME (mevcutla retry sürsün); çağıran sonsuz retry'da
            # bekler, veri kaybı olmaz.
            log.warning("  [TÜNEL] hiçbir aday adres yanıt vermiyor (%d aday "
                        "denendi) — sunucu düşmüş olmalı, mevcut adresle "
                        "denemeye devam", len(adaylar))
            return False

        if live == settings.VLLM_BASE_URL:
            return False
        old = settings.VLLM_BASE_URL
        settings.VLLM_BASE_URL = live
        _persist_to_env(live)
        log.warning("  [TÜNEL] URL değişti: %s -> %s", old, live)
        return True


# --- Uzak daldan gelen ortak API (llm/providers, embeddings/providers ve
# llm/context.py bu ikisini çağırır; testler de doğrudan kullanır). Gövdeleri
# bu modülün KENDİ altyapısına (_lock, log, _fetch_live_url) bağlandı.

def is_tunnel_failure(exc: BaseException) -> bool:
    """Yeni yayınlanmış bir tünele karşı tekrar denemenin işe yarayıp
    yaramayacağı.

    Doğrulama/kimlik hatalarında gist'e GİDİLMEZ: onlar uygulama hatasıdır,
    ters vekilin adresinin değiştiğine kanıt değildir. Bayat tünel 404 olarak
    görünür; vekil kesintileri listelenen ağ geçidi durumlarını ya da bir
    taşıma hatasını üretir.

    Burada durur çünkü konu *tünel*, sohbet değil: bu vekilden geçen her şey
    adres değiştiğinde aynı biçimde başarısız olur ve embedding istemcisinin
    de birebir aynı yanıta ihtiyacı vardır."""
    # OpenAI SDK'sı httpx bağlantı/reset/timeout istisnalarını kendi
    # APIConnectionError hiyerarşisine sarar. Yalnız httpx istisnalarına
    # bakmak LangChain'in verdiği asıl hatayı kaçırır. Zinciri de yürü ki
    # ileride bir sarmalayıcı bir katman daha eklerse doğru kalsın.
    from openai import APIConnectionError, APITimeoutError

    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (APIConnectionError, APITimeoutError,
                                ConnectionError, TimeoutError, OSError)):
            return True
        if getattr(current, "status_code", None) in {404, 502, 503, 504}:
            return True
        current = current.__cause__ or current.__context__
    return False


def refresh_after_failure(failed_base_url: str | None = None) -> bool:
    """Başarısız olmuş bir tünel adresini bir kez tazele, sonucu paylaş.

    Normal model kurulumunda ÇAĞRILMAZ — bedeli yalnız bir istek zaten
    başarısız olduktan sonra ödenir. ``failed_base_url`` sürü etkisini önler:
    başka bir istek o adresi çoktan değiştirmişse, bu çağıran gist'e ikinci
    kez gitmeden yenisini benimser."""
    failed = failed_base_url.rstrip("/") if failed_base_url else None
    with _lock:
        configured = (settings.VLLM_BASE_URL or "").rstrip("/")
        if failed is not None and configured != failed:
            log.info("  [TÜNEL] başka bir istek zaten tazelemiş: %s -> %s",
                     failed, configured)
            return True
    return refresh_if_needed()
