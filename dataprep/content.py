"""AŞAMA İSİMLENDİRMESİ (proje geneli):
  AŞAMA 1   = crawling            (dataprep.crawl — sayfa metinleri BURADA
                                   temizlenir, bu modülde tekrarlanmaz)
  AŞAMA 2   = PDF                 (PDF metin temizleme, --stage pdf-text)
  AŞAMA 3.1 = page image          (site sayfalarının görselleri, --stage images-page)
  AŞAMA 3.2 = pdf image           (PDF görselleri, --stage images-pdf)

Sayfa (HTML) ve PDF içeriğini TEK, birleşik, LLM-friendly bir belgeye
çevirir: metin + görseller (sırayla VLM ile, metin ile AYNI dosyaya, koddan
eklenen kaynak etiketiyle) — data/<bank>_site/content/<sayfa>.md üretir.

METİN — HTML ve PDF FARKLI yollardan gelir (ikisi de tek/ikinci bir LLM
temizleme turu YAŞAMAZ, gereksiz çift iş yok):
  * HTML: crawl sırasında (store.py -> pages.clean_page) zaten LLM ile
    temizlenmiş halde .md'ye yazılmış durumda — burada OLDUĞU GİBİ okunur,
    TEKRAR LLM'e verilmez. Crawl'ın bulduğu tarihler (campaign_start/end,
    frontmatter'da) buradaki sayfa-düzeyi tarih biriktiricisine katılır.
  * PDF: crawl ham binary indirdi, hiç temizlenmedi — burada İLK ve TEK kez
    (8k eşiği altı tek çağrı / üstü ~16k bağlamlı ardışık parçalama) temizlenir.

GÖRSELLER — CRAWLING V2: her görsel, bir metin parçası/sayfa gibi ele alınır.
Gömülü PDF görselleri VE metin katmanı olmayan (taranmış) PDF sayfalarının
TAMAMI (tek render edilmiş görsel olarak) web sayfası görselleriyle BİRE BİR
AYNI yoldan (vlm.ImageCache.examine) geçer — ayrı bir bant/zoom algoritması
YOK. Görsel inceleme şeması METİN temizlemeyle AYNI anahtarları kullanır
(gecerlilik_baslangic/gecerlilik_bitis) — banner'daki bir kampanya tarihi de
metindeki bir tarih kadar geçerli sayılır, TEK ortak tarih biriktiricisine
(HTML/PDF metninden + tüm görsellerden) fold edilir, status TEK seferde
sayfa sonunda çözülür (pages.py::_set_front_dates ile AYNI deterministik-önce
ilkesi: bitiş tarihi varsa kod hesaplar, yoksa modelin tahmini/varsayılan
'bilinmiyor' kullanılır).

Idempotent + resumable: data/<bank>_site/_content_ledger.json (url -> source_hash,
output_path, status, processed_at) — kaldığı yerden devam eder, ikinci tam
koşuda sadece değişen/yeni URL'ler işlenir (Catalog hash'iyle karşılaştırılır).

Paralellik: ThreadPoolExecutor(CONTENT_WORKERS, varsayılan 20).

Kullanım: python -m dataprep.content <bank> [<bank> ...]   (boş = tüm bankalar)
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from config import tunnel
from dataprep import on_eleme, vlm
from dataprep.images import BROWSER_HEADERS, _to_png
from dataprep.ledger import Ledger
from dataprep.pages import _split_front

log = logging.getLogger("dataprep.content")

# ---------------------------------------------------------------- dayanıklılık
# LEDGER YAZIM PENCERESİ: eskiden ledger SADECE her 20 öğede bir yazılıyordu.
# VLM yavaşladığında 20 öğe saatler sürebiliyor; o aralıkta süreç ölürse
# tamamlanmış işlerin ÇIKTISI diskte kalır ama "işlendi" kaydı kaybolur, yani
# veri kaybı değil ama saatlerce İŞ TEKRARI olur. Çözüm: öğe sayısına EK OLARAK
# süreye de bak — hangisi önce dolarsa yaz.
# Erken relevance kararı (bkz. clean_text / pages.clean_page):
#   toplam chunk <= _TUMU_ISLE_SINIRI -> tamamı işlenir (erken karar yok)
#   toplam chunk >  _TUMU_ISLE_SINIRI -> sadece ilk _KARAR_CHUNK oylanır
_TUMU_ISLE_SINIRI = int(os.environ.get("CONTENT_TUMU_ISLE_SINIRI", "10"))
_ON_ELEME_ESIGI = int(os.environ.get("ON_ELEME_ESIGI", "2"))
_ARDISIK_GEREKSIZ_SINIRI = int(os.environ.get("ARDISIK_GEREKSIZ", "10"))
_KARAR_CHUNK = int(os.environ.get("CONTENT_KARAR_CHUNK", "10"))

_LEDGER_EVERY_N = 20             # her N öğede bir yaz (eski davranış korunuyor)
_LEDGER_EVERY_SEC = 60.0         # ...VEYA son yazımdan bu kadar saniye geçtiyse

# ZARİF KAPANIŞ: SIGTERM/SIGINT gelince süreci ANINDA öldürmek yerine bayrağı
# kaldırıyoruz; döngü sıradaki öğeyi bitirip ledger'ı yazarak çıkıyor. Böylece
# normal `kill` SIFIR iş tekrarıyla sonuçlanır (sadece `kill -9` pencere bırakır).
# vlm.STOPPING ile AYNI nesne — hem iş döngüsü hem VLM retry döngüsü tek
# bayrağa bakar (yoksa retry sonsuza kadar bekleyip kapanışı bloklar).
_STOPPING = vlm.STOPPING


_SHUTDOWN_GRACE = float(os.environ.get("CONTENT_SHUTDOWN_GRACE", "45"))


def _shutdown_watchdog() -> None:
    """Kapanış bu süre içinde bitmezse süreci zorla sonlandır (bkz. _handler)."""
    time.sleep(_SHUTDOWN_GRACE)
    log.warning("[KAPANIŞ] %.0fs içinde temiz çıkış olmadı (açık HTTP okuması) — "
                "süreç zorla sonlandırılıyor", _SHUTDOWN_GRACE)
    logging.shutdown()
    os._exit(143)


def _install_signal_handlers() -> None:
    """SIGTERM/SIGINT -> _STOPPING. Yalnızca ana thread'de kurulabilir; kurulamazsa
    (örn. kütüphane olarak başka bir thread'den çağrıldıysa) sessizce geçilir —
    o durumda eski davranış (anında kesilme) geçerli olur."""
    def _handler(signum, _frame):
        if _STOPPING.is_set():                # ikinci sinyal -> ısrar var, hemen çık
            # os._exit: raise KeyboardInterrupt sinyal handler'ında ana döngüyü
            # kesmiyor (canlı testte 2. SIGTERM'de de çıkmadı). Bu yol kesin.
            log.warning("[KAPANIŞ] ikinci sinyal (%d) — ledger yazmadan HEMEN çıkılıyor", signum)
            logging.shutdown()
            os._exit(130)
        _STOPPING.set()
        log.warning("[KAPANIŞ] sinyal %d alındı — süren öğeler bitirilip ledger "
                    "yazıldıktan sonra çıkılacak (tekrar sinyal = anında çıkış)", signum)
        # EMNİYET SÜBABI: süreç o an açık bir HTTP okumasında (read=300s) bloke
        # olabilir; sinyal ancak okuma bitince işlenir. Watchdog, kapanış
        # _SHUTDOWN_GRACE içinde tamamlanmazsa süreci zorla bitirir. Ledger
        # yazılamamış olsa bile ÇIKTILAR diskte durur ve o öğeler sonraki
        # koşuda yeniden işlenir — veri kaybı değil, sınırlı iş tekrarı.
        threading.Thread(target=_shutdown_watchdog, daemon=True).start()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):         # ana thread değil
            pass


def _write_ledger(path: Path, done: dict) -> None:
    """Ledger'ı ATOMİK yaz: geçici dosyaya yazıp os.replace ile yerine koy. Yazma
    ortasında süreç ölürse yarım/bozuk JSON kalmaz (eski sürüm olduğu gibi durur)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _should_flush(j: int, last_write: float) -> bool:
    """N öğede bir VEYA _LEDGER_EVERY_SEC saniyede bir -> hangisi önce dolarsa."""
    return j % _LEDGER_EVERY_N == 0 or (time.time() - last_write) >= _LEDGER_EVERY_SEC


# WORKERS artık ASIL SINIRLAYICI DEĞİL — gerçek eşzamanlılık kontrolü
# dataprep/net_limit.py'deki UYARLANABİLİR sınırlayıcıda (AIMD): sistem
# sıkışmayı kendi tespit edip limiti yarıya indiriyor, işler hızlanınca
# kademeli artırıyor. Buradaki sayı sadece "kaç iş aynı anda kuyruğa
# girebilir" tavanı; net_limit gerçek ağ eşzamanlılığını zaten kısıyor,
# bu yüzden cömert bırakılabilir.
WORKERS = int(os.environ.get("CONTENT_WORKERS", "20"))
# Tek seferde LLM'e verilecek ham metin: 8196 KARAKTER (kullanıcı kararı
# 2026-08-19). Bu bizim TEK organik kırpmamızdır — başka hiçbir yerde, ne
# girdi ne çıktı tarafında bağlam kırpılmaz.
CHUNK = int(os.environ.get("CONTENT_CHUNK_CHARS", "8196"))
# EŞİK YOK (kullanıcı kararı 2026-08-19): "TÜM SİTE VE PDF'LER eksiksiz,
# 1 karakter 1 KB bile olsa İŞLENECEK". Eskiden 40 karakterin altındaki gövde
# LLM'e hiç gönderilmiyordu — taranmış bir PDF'in tek satırlık başlığı ya da
# kısa ama veri taşıyan bir sayfa böyle sessizce atlanabiliyordu.
MIN_LEN = int(os.environ.get("CONTENT_MIN_LEN", "0"))
# Bir PDF sayfasının metni bu uzunluğun ALTINDAYSA içerik büyük ihtimalle
# GÖRSELDEDİR -> gömülü görsellerin yanı sıra TAM SAYFA da render edilip VLM'e
# verilir. Bu bir kırpma/atlama eşiği DEĞİL, "ek olarak tam sayfayı da oku"
# eşiğidir: hiçbir içerik atlanmaz, aksine daha fazlası okunur.
_TAM_SAYFA_ESIK = int(os.environ.get("PDF_TAM_SAYFA_ESIK", "400"))

# AŞAMA 3 (PDF) ve AŞAMA 4 (sayfa) ORTAK promptu — kullanıcı kararı
# 2026-08-22: "3 ve 4 birebir aynı süreç, sadece birisi PDF birisi page".
# Talimat TEK kaynaktan gelir (dataprep/temizleme_prompt.py); eskiden burada
# ve pages.py'de AYRI tanımlıydı ve ayrışmışlardı (bkz. o modülün docstring'i).
from dataprep.temizleme_prompt import (ETIKET_Q, BASLIK as _BASLIK, GOAL as _GOAL,
                                        DATE_RULE as _DATE_RULE,
                                        RELEVANCE_RULE as _RELEVANCE_RULE,
                                        JSON_HEAD as _JSON_HEAD,
                                        JSON_TAIL as _JSON_TAIL)

_SINGLE_Q = (_GOAL + _DATE_RULE + _RELEVANCE_RULE + "\n\nKaynak URL: {url}{title_line}\n\nHam metin:\n\"\"\"{body}\"\"\""
             + _JSON_HEAD + "LLM-friendly markdown ya da boş" + _JSON_TAIL)
_CONT_Q = (_GOAL + _DATE_RULE + _RELEVANCE_RULE + "\n\nKaynak URL: {url}{title_line}\n\nBu, uzun bir belgenin "
           "DEVAM parçası. Önceki temiz çıktı:\n\"\"\"{prev}\"\"\"\nBunu "
           "TEKRARLAMA — bu parçadaki YENİ içeriği kaldığın yerden, kusursuz "
           "ve akıcı biçimde ekle; hiçbir ürün/kampanya detayını atlama.\n\n"
           "Ham metin (devam):\n\"\"\"{body}\"\"\""
           + _JSON_HEAD + "devam markdown ya da boş" + _JSON_TAIL)


def _title_line(title: str) -> str:
    t = (title or "").strip()
    return f"\nBaşlık/Dosya Adı: {t}" if t else ""


def _fold_dates(agg: dict, d: dict) -> None:
    """Sayfa düzeyinde tarih topla: en GEÇ bitiş, en ERKEN başlangıç (ISO -> lexik
    = kronolojik); durum_tahmini sadece hiç tarih yoksa anlamlı, son parçanınki
    kalır (parçalar arası çelişki riski düşük, konu genelde sayfa-sabit)."""
    e = (d.get("gecerlilik_bitis") or "").strip()
    if e and (not agg["end"] or e > agg["end"]):
        agg["end"] = e
    s = (d.get("gecerlilik_baslangic") or "").strip()
    if s and (not agg["start"] or s < agg["start"]):
        agg["start"] = s
    # DURUM kararı TAMAMEN modelin (bkz. _resolve_status). 'gecerli' de
    # kabul edilir — eskiden yalnız suresi_gecmis/bilinmiyor alınıyordu çünkü
    # 'gecerli' kararını kod hesaplıyordu; artık hesaplamıyoruz.
    dt = (d.get("durum") or d.get("durum_tahmini") or "").strip()
    if dt in ("gecerli", "suresi_gecmis", "bilinmiyor"):
        agg["durum"] = dt


def _majority_relevance(votes: list[str], toplam_chunk: int | None = None) -> str:
    """Chunk oylarından belge-düzeyi gerekli/gereksiz kararı.

    KURAL BELGE BOYUTUNA GÖRE DEĞİŞİR (kullanıcı kararı 2026-08-23):

      * KÜÇÜK belge (< _TUMU_ISLE_SINIRI chunk): TEK 'gerekli' oyu YETER.
        Kısa bir belgede tek bir parçada bile ürün/kampanya bilgisi varsa
        o belge değerlidir — çoğunluk aramak onu haksız yere elerdi
        (ör. 3 chunk'lık bir formun yalnız 1 chunk'ı ücret tablosu olabilir).

      * BÜYÜK belge (>= _TUMU_ISLE_SINIRI chunk): ÇOĞUNLUK gerekir.
        Faaliyet raporu gibi dev belgelerde tek bir parçada ürün adı geçmesi
        belgeyi "müşteri içeriği" yapmaz; kalabalığın ne dediğine bakılır.

    Eşitlikte ve oy yokken 'gerekli' kazanır — belirsizlikte veri elenmez.
    toplam_chunk verilmezse oy sayısı belge boyu sayılır (geriye uyumluluk).
    """
    if not votes:
        return "gerekli"
    n = toplam_chunk if toplam_chunk is not None else len(votes)
    if n < _TUMU_ISLE_SINIRI:
        return "gerekli" if "gerekli" in votes else "gereksiz"
    gereksiz = sum(1 for v in votes if v == "gereksiz")
    return "gereksiz" if gereksiz > len(votes) - gereksiz else "gerekli"


def _resolve_status(dates: dict) -> str:
    """KARARI TAMAMEN LLM VERİR (kullanıcı kararı 2026-08-22).

    Eskiden burada aritmetik bir kıyas vardı: bitiş tarihi varsa
    `end >= today` hesaplanıp LLM'in kararı EZİLİYORDU. Bu, projenin temel
    ilkesine ("kural tabanlı karar yok, modelin aklını kullan") aykırıydı ve
    modelin gördüğü bağlamı (ör. 'kampanya uzatılmıştır', 'her yıl tekrarlanır',
    süresiz ürün sayfası) yok sayıyordu. Artık model 'durum' alanını doğrudan
    döndürür; bu fonksiyon SADECE onu okur.

    Bilgi eksikse 'bilinmiyor' kalır — belirsizlikte içerik ELENMEZ."""
    d = (dates.get("durum") or dates.get("guess") or "").strip()
    return d if d in ("gecerli", "suresi_gecmis", "bilinmiyor") else "bilinmiyor"


def clean_text(body: str, url: str, dates: dict, title: str = "") -> str | None:
    """SADECE PDF metni için (HTML zaten crawl-time'da temiz — bkz. work()):
    8k eşiği altı TEK çağrı; üstü ~8k'lık ardışık parçalar + önceki TAM çıktı
    (~8k) bağlamıyla toplam ~16k'da kusursuz devam. Bulduğu tarihleri verilen
    `dates` sözlüğüne (paylaşılan sayfa-düzeyi biriktirici) FOLD eder — status
    çözümü work()'te, görsel tarihleriyle BİRLİKTE, tek seferde yapılır.

    None döner -> LLM'e HİÇ ulaşılamadı YA DA çok-parçalı bir belgenin
    ORTASINDA bir parça başarısız oldu (o ana kadarki KISMİ çıktıyı
    'tamamlanmış' diye YUTMAYIZ — çağıran bu URL'i ledger'a 'işlendi' diye
    YAZMAMALI, sonraki koşuda baştan denenmeli). Boş string ("") SADECE
    girdi gerçekten MIN_LEN altındaysa döner (bu bir hata değil)."""
    body = (body or "").strip()
    if not body:
        dates["relevance"] = "gereksiz"
        return ""
    tl = _title_line(title)
    today = date.today().isoformat()
    if MIN_LEN and len(body) < MIN_LEN:
        return body
    adim = CHUNK
    toplam_chunk = (len(body) + adim - 1) // adim

    def _etiketle(part_txt: str) -> str:
        d = vlm.call_json(vlm.txt_msg(ETIKET_Q.format(
            url=url or "-", title_line=tl, today=today, body=part_txt)))
        if not d:                      # LLM'e ulaşılamadı -> eleme YAPMA
            return "gerekli"
        rel = (d.get("musteri_icerigi") or "").strip()
        return rel if rel in ("gerekli", "gereksiz") else "gerekli"

    if len(body) <= CHUNK:
        # ÖNCE HIZLI GEREKLİ/GEREKSİZ ANALİZİ (1-2s)
        rel = _etiketle(body)
        if rel == "gereksiz":
            dates["relevance"] = "gereksiz"
            return ""
        # GEREKLİ İSE CLEAN TEXT AL
        d = vlm.call_json(vlm.txt_msg(_SINGLE_Q.format(url=url or "-", title_line=tl, today=today, body=body)))
        if not d:
            return None
        _fold_dates(dates, d)
        dates["relevance"] = "gerekli"
        return (d.get("content") or "").strip()

    parcalar = [body[k * adim : (k + 1) * adim] for k in range(toplam_chunk)]
    votes: list[str] = []

    if toplam_chunk >= _ON_ELEME_ESIGI:
        etiketler = on_eleme.esle(_etiketle, parcalar)

        ardisik = 0
        kesme = len(parcalar)
        for k, e in enumerate(etiketler):
            if e == "gereksiz":
                ardisik += 1
                if ardisik >= _ARDISIK_GEREKSIZ_SINIRI:
                    kesme = k + 1
                    log.info("  %s: %d ardışık 'gereksiz' chunk — %d/%d'ten "
                             "sonrasına BAKILMIYOR", url or "-", ardisik,
                             kesme, toplam_chunk)
                    break
            else:
                ardisik = 0

        islenecek = [(k, parcalar[k]) for k in range(kesme)
                     if etiketler[k] == "gerekli"]
        votes.extend(etiketler[:kesme])

        if not islenecek:
            log.info("  %s: %d/%d chunk'ın hiçbiri 'gerekli' değil — "
                     "temizlemeye HİÇ BAŞLANMIYOR", url or "-",
                     len(etiketler), toplam_chunk)
            dates["relevance"] = "gereksiz"
            return ""
        if len(islenecek) < toplam_chunk:
            log.info("  %s: %d/%d chunk 'gerekli' — sadece onlar temizleniyor",
                     url or "-", len(islenecek), toplam_chunk)
    else:
        islenecek = [(k, parcalar[k]) for k in range(len(parcalar))]

    # DİKİŞ YOK, PARALEL — pages.py ile BİREBİR AYNI ("3 ve 4 aynı süreç").
    # Kullanıcı kararı 2026-08-23: "gerekli denen chunk'lar kendisi için clean
    # text versin yeter, dikiş iptal". Chunk'lar birbirine bağımlı olmadığı
    # için hepsi paralel gider; çıktılar indekse göre sıralanıp birleşir.
    def _temizle(ix_part):
        k, part = ix_part
        return k, vlm.call_json(vlm.txt_msg(_SINGLE_Q.format(
            url=url or "-", title_line=tl, today=today, body=part)))

    sonuclar = (on_eleme.esle(_temizle, islenecek) if len(islenecek) > 1
                else [_temizle(islenecek[0])])

    sirali = []
    for k, d in sonuclar:
        if not d:
            return None                  # KISMİ çıktıyı ATIYORUZ
        _fold_dates(dates, d)
        if not votes:
            rel = (d.get("musteri_icerigi") or "").strip()
            votes.append(rel if rel in ("gerekli", "gereksiz") else "gerekli")
        piece = (d.get("content") or "").strip()
        if piece:
            sirali.append((k, piece))
    sirali.sort(key=lambda x: x[0])
    out_pieces = [c for _k, c in sirali]
    dates["relevance"] = _majority_relevance(votes, toplam_chunk)
    return "\n\n".join(out_pieces)


def clean_images(items: list[tuple[str, bytes]], cache: vlm.ImageCache, dates: dict) -> tuple[str, bool]:
    """Görselleri SIRAYLA, MEVCUT ImageCache (sha256-dedup) ile işler — prompt
    modele 'bu sayfanın görseli' diye AÇIKLAMAZ (multimodal girdi zaten yeterli);
    kaynak etiketi KODDAN eklenir. Dekoratif olmayan/içerikli sonuçlar birleşir.

    CRAWLING V2: her görsel, metin parçası gibi ele alınır — ImageCache.examine
    de AYNI gecerlilik_baslangic/gecerlilik_bitis alanlarını döndürür (bkz.
    vlm.py::_EXAMINE_Q), burada da AYNI _fold_dates ile paylaşılan `dates`
    biriktiricisine katılır (metin ve görsel tarihleri TEK bir sayfa-düzeyi
    sonuca birleşir, ayrı şema/anahtar yok).

    Dönen (markdown, all_ok): bir görsel VLM'e ulaşılamadığı için işlenemezse
    (examine() None döner) o görsel SESSİZCE atlanmaz — all_ok=False işaretiyle
    çağırana bildirilir; çağıran bu durumda sayfayı ledger'a 'işlendi' diye
    YAZMAMALI (source_hash eşleşirse bir daha asla denenmez, o görsel kalıcı
    kaybolur) — sonraki koşuda TÜM sayfa (zaten-başarılı görseller cache'ten
    bedavaya gelir, sadece başarısız olan yeniden VLM'e gider) tekrar denenir."""
    blocks = []
    all_ok = True
    for label, png in items:
        res = cache.examine(png)
        if not res:
            all_ok = False
            continue
        _fold_dates(dates, res)
        if not res.get("decorative") and res.get("content"):
            blocks.append(f"<!-- görsel: {label} -->\n{res['content']}\n")
    return "\n".join(blocks), all_ok


def _front_field(front: str, key: str) -> str:
    for line in front.splitlines():
        k, _, v = line.partition(":")
        if k.strip() == key:
            return v.strip().strip('"')
    return ""


_IMG_FETCH_WARN_EVERY = 300      # vlm.py::_post ile AYNI ilke: durmadan uyar, ASLA pes etme
_IMG_FETCH_RESET_EVERY = 4       # vlm.py::_reset_client_if_stuck ile AYNI zombi-bağlantı koruması
# Bir görsel için EN FAZLA kaç deneme (kullanıcı kararı, 2026-08-19): olmuyorsa
# zorlama, etiketle geç. Sayfa ledger'a yazılmadığı için sonraki koşuda yeniden
# denenir; ısrarla gelmiyorsa ölü kabul edilir.
_IMG_FETCH_MAX_TRY = int(os.environ.get("IMG_FETCH_MAX_TRY", "5"))

# ÖLÜ GÖRSEL URL CACHE'İ (kalıcı, disk). Bir URL "200 ama görsel değil" ya da
# "kalıcı 4xx" çıktıysa bir daha İNDİRİLMEZ.
#
# NEDEN: aynı görsel URL'i onlarca sayfada tekrar ediyor (menü/şablon). Canlı
# ölçüm (2026-08-19, kuveytturk): tek bir SitemapNodePage URL'i 9 KEZ indirildi,
# her seferinde 197KB HTML geldi. Kalan 515 sayfada 10.792 böyle URL var —
# hepsi tekrar tekrar inecekti (siteye gereksiz yük + saatlerce boşa zaman).
#
# RİSK YOK: karar KALIP TAHMİNİYLE değil, o URL'in GERÇEK yanıtıyla veriliyor.
# Her URL en az bir kez denenir; sadece sonucu hatırlanır. Gerçek görsel dönen
# bir URL asla bu listeye girmez.
# Ağ hatasıyla alınamayan bir URL kaç KOŞU üst üste gelmezse ölü sayılır.
# (HTML/4xx zaten TEK seferde kalıcı sayılır — orada belirsizlik yok.)
_OLU_ESIK = int(os.environ.get("IMG_OLU_ESIK", "3"))
_olu_sayac: dict[str, dict] = {}

_OLU_URL: dict[str, set] = {}
_olu_lock = threading.Lock()


def _olu_yol(slug: str) -> Path:
    return Path(__file__).resolve().parents[1] / "data" / f"{slug}_site" / "_olu_gorsel_url.json"


def _olu_yukle(slug: str) -> set:
    with _olu_lock:
        if slug in _OLU_URL:
            return _OLU_URL[slug]
        p = _olu_yol(slug)
        try:
            _OLU_URL[slug] = set(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            _OLU_URL[slug] = set()
        return _OLU_URL[slug]


def _olu_sayac_arttir(slug: str, url: str) -> int:
    """Ağ hatasıyla alınamayan URL'in sayacını artır, güncel değeri döndür.
    Disk üzerinde tutulur (koşular arası sayılsın)."""
    p = _olu_yol(slug).with_name("_olu_gorsel_sayac.json")
    with _olu_lock:
        d = _olu_sayac.get(slug)
        if d is None:
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                d = {}
            _olu_sayac[slug] = d
        d[url] = int(d.get(url, 0)) + 1
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            tmp.replace(p)
        except Exception:
            pass
        return d[url]


def _olu_ekle(slug: str, url: str) -> None:
    """URL'i ölü listesine ekle ve diske yaz (atomik)."""
    with _olu_lock:
        kume = _OLU_URL.setdefault(slug, set())
        if url in kume:
            return
        kume.add(url)
        p = _olu_yol(slug)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(sorted(kume), ensure_ascii=False), encoding="utf-8")
            tmp.replace(p)
        except Exception:
            pass

# SİTE-BAŞINA HIZ SINIRI (saniye) — bankanın SUNUCUSUNU korumak için, bizim
# tarafımızı değil. CANLI YAŞANDI: kuveytturk'ün 2101 URL'lik görsel taramasında
# saniyede onlarca istek atınca site bizi ENGELLEDİ (TCP açılıyor ama HTTPS
# sessiz kalıyor, curl bile giremiyor). IP değişince açıldı, aynı yoğunlukta
# tekrar kapandı — yani sorun IP değil, İSTEK HIZI. Aynı hosta ardışık iki
# istek arasında en az bu kadar beklenir; farklı hostlar birbirini beklemez.
_HOST_MIN_GAP = float(os.environ.get("IMG_HOST_MIN_GAP", "0.05"))
_host_last: dict[str, float] = {}
_host_lock = threading.Lock()


def _host_throttle(url: str) -> None:
    """Aynı siteye çok sık gitmeyi engeller (bkz. _HOST_MIN_GAP)."""
    try:
        host = urlsplit(url).netloc
    except Exception:
        return
    if not host:
        return
    while True:
        with _host_lock:
            simdi = time.time()
            son = _host_last.get(host, 0.0)
            bekle = _HOST_MIN_GAP - (simdi - son)
            if bekle <= 0:
                _host_last[host] = simdi
                return
        time.sleep(bekle)


def _img_ssl_ctx():
    """Görsel indirme için TLS 1.2 TAVANLI SSL bağlamı.

    NEDEN: kuveytturk.com.tr'nin bot koruması TLS 1.3 el sıkışmasında Python/
    curl'ün parmak izini tanıyıp bağlantıyı SESSİZCE düşürüyordu — TCP 443
    açılıyor, "Client hello" gidiyor, yanıt HİÇ gelmiyor (ConnectTimeout).
    Canlı ölçüm (2026-08-19): TLS 1.3 ile 0/8 başarı, TLS 1.2 ile 8/8 (0.1s).
    User-Agent/başlık taklidi işe yaramaz, çünkü onlar TLS kurulduktan SONRA
    gönderilir; sorun daha alt katmanda. Diğer bankalar her iki sürümle de
    çalıştığı için tavanı global koymak güvenli."""
    import ssl
    ctx = ssl.create_default_context()
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def _new_img_client():
    import httpx
    return httpx.Client(headers=BROWSER_HEADERS, follow_redirects=True,
                        verify=_img_ssl_ctx(),
                        timeout=httpx.Timeout(connect=6.0, read=20.0, write=10.0, pool=10.0))


def _fetch_one_image(client_box: list, url: str):
    """TEK görseli indirir. EN FAZLA _IMG_FETCH_MAX_TRY deneme (kullanıcı kararı,
    2026-08-19): olmuyorsa ZORLAMA, "ulasilamadi" etiketiyle geç.

    NEDEN SONSUZ RETRY DEĞİL (vlm.py::_post'tan farkı): VLM sunucusu geçici
    olarak düşer ve geri gelir — orada ısrar doğru. Ama bir GÖRSEL URL'i kalıcı
    ölü olabilir (silinmiş/taşınmış); orada ısrar worker'ı kilitler. Canlı
    olay: tek bir URL (SitemapNodePage-ThirdImage-22485) 662 kez denendi,
    2 saatte sadece 20 sayfa işlendi, aynı anda DİĞER kuveytturk görselleri
    200 dönüyordu. Yani site engeli değil, o URL ölü.

    VERİ KAYBI YOK: bu görsel eksik kalınca sayfa "işlendi" sayılmaz
    (all_ok=False -> ledger'a yazılmaz), sonraki koşuda TEKRAR denenir.
    Israrla ulaşılamıyorsa ölü kabul edilir — ama kararı biz veririz, süreç
    kilitlenerek değil.

    SADECE kalıcı 4xx'te (görsel gerçekten yok/kaldırılmış) hemen None döner. Art arda birkaç başarısızlıktan sonra client'ı (bu sayfaya
    özel, fonksiyon-scope'lu) yeniler — zombi bağlantı koruması vlm.py'deki AYNI
    desen. `client_box`: tek elemanlı liste, closure üzerinden client'ı DEĞİŞTİREBİLMEK
    için (yerel değişkene atama yapamayız, referansı taşıyan kutuyu güncelleriz)."""
    start = time.time()
    delay = 1.0
    attempt = 0
    last_warn = 0.0
    while True:
        attempt += 1
        try:
            _host_throttle(url)                 # siteyi yorma (ban koruması)
            r = client_box[0].get(url)
            if 400 <= r.status_code < 500:
                return None, True               # KALICI — bu görsel gerçekten yok, retry etme
            r.raise_for_status()
            png = _to_png(r.content)
            if png is None:
                # 200 döndü ama GÖRSEL DEĞİL ya da MIN_PX altı.
                # KALICI SKIP: retry etmenin faydası yok, aynı yanıt gelir.
                #
                # CANLI ÖLÇÜM (2026-08-19, kuveytturk): 'SitemapNodePage-*.vsf'
                # URL'lerinin TAMAMI 200 + text/html (197KB ana sayfa) dönüyor —
                # görsel silinmiş, site 404 yerine sayfayı veriyor (soft 404).
                # Kalan 519 sayfadaki 13.629 görselin ~%79'u bu türdendi; her biri
                # 5 kez denenip worker'ı meşgul ediyor, siteye gereksiz yük
                # bindiriyordu (muhtemelen engellenmelerin de sebebi).
                #
                # GERÇEK GÖRSEL KAÇMAZ: karar content-type'a DEĞİL, içeriğin
                # kendisine dayanıyor — _to_png PIL ile açmayı dener; açılıyorsa
                # (jpg/png/webp/gif, yanlış content-type'la gelse bile) görseldir
                # ve buraya hiç düşmez. Sadece PIL'in çözemediği (HTML/boş/bozuk)
                # ya da MIN_PX altı ikonlar atlanır.
                log.info("    [GÖRSEL_DEĞİL] %s: 200 döndü ama görsel değil/çok küçük "
                         "(%s, %d KB) — kalıcı atlandı",
                         url[:80], (r.headers.get("content-type") or "?")[:24],
                         len(r.content) // 1024)
                return None, True               # görsel değil -> KALICI
            return png, True
        except Exception as exc:
            # SİTE ENGELİ (ban) İHTİMALİNE KARŞI: bir hostta art arda hata
            # alıyorsak ısrar etmek banı UZATIR. O hosta özel bekleme süresini
            # geçici olarak büyütürüz (tüm thread'leri etkiler, çünkü _host_last
            # paylaşılan) — böylece siteye nefes aldırıp kendiliğinden açılmasına
            # izin veririz. Canlı yaşandı: kuveytturk 2101 URL'lik taramada
            # engelledi, IP değişince açıldı, aynı hızda tekrar kapandı.
            if attempt >= 3:
                try:
                    host = urlsplit(url).netloc
                    with _host_lock:
                        _host_last[host] = time.time() + min(30.0, 2.0 * attempt)
                except Exception:
                    pass
            elapsed = time.time() - start
            tunnel.refresh_if_needed()          # büyük ihtimalle tünel URL'i değişti (nadiren görsel de tünelden geçebilir)
            if attempt % _IMG_FETCH_RESET_EVERY == 0:
                try:
                    client_box[0].close()
                except Exception:
                    pass
                client_box[0] = _new_img_client()
                log.warning("    [GÖRSEL_HAVUZ_YENİLENDİ] %s: %d kez art arda başarısız, "
                            "bağlantı yenilendi", url, attempt)
            if attempt >= _IMG_FETCH_MAX_TRY:
                # TAVANA ULAŞILDI -> zorlamayı BIRAK, etiketle ve geç.
                log.warning("    [GÖRSEL_ULAŞILAMADI] %s: %d denemede alınamadı (%s) — "
                            "atlanıyor, sayfa ledger'a YAZILMAZ (sonraki koşuda tekrar denenir)",
                            url, attempt, type(exc).__name__)
                return None, False              # AĞ hatası -> geçici olabilir
            if elapsed - last_warn >= _IMG_FETCH_WARN_EVERY:
                log.warning("    [GÖRSEL_UZUN_SÜRELİ_HATA] %s: %.0fs'dir başarısız (deneme %d/%d): %s",
                            url, elapsed, attempt, _IMG_FETCH_MAX_TRY, type(exc).__name__)
                last_warn = elapsed
            time.sleep(delay)
            delay = min(delay * 2, 60)


def _slug_of(url: str) -> str:
    """Görsel URL'inden banka slug'ı (ölü-URL cache'i banka bazlı tutulur)."""
    try:
        host = urlsplit(url).netloc.lower()
    except Exception:
        return ""
    for site in (Path(__file__).resolve().parents[1] / "data").glob("*_site"):
        s = site.name[:-5]
        if s and s in host.replace(".", ""):
            return s
    return ""


def _fetch_page_images(urls: list[str]) -> tuple[list[tuple[str, bytes]], bool]:
    """HTML sayfasının içerik görsellerini indirir (images.py'nin TARAYICI-
    TAKLİDİ header'ları AYNEN yeniden kullanılır) — (url, png) çiftleri.

    Dönen (items, all_ok): ÖNCEDEN başarısız indirmeler SESSİZCE atlanıp
    (None -> yutuluyordu) sayfa hâlâ 'tamamlandı' sayılıyordu — bu, VLM
    examine() başarısızlığı için zaten çözdüğümüz SESSİZ VERİ KAYBI bug'ının
    AYNISI, sadece indirme aşamasında. Şimdi clean_images'ın all_ok deseniyle
    TUTARLI: kalıcı olmayan (retry tükenmedi, sonsuza kadar denendi ama script
    kesildi gibi bir durum olmaz çünkü ASLA pes etmiyoruz) bir başarısızlık
    burada artık MÜMKÜN DEĞİL — ya döner ya da (SADECE kalıcı 4xx'te) None."""
    if not urls:
        return [], True
    # SLUG URL BAŞINA çözülür (sayfanın ilkine göre DEĞİL): bir sayfanın görsel
    # listesi 3. taraf adreslerle başlayabiliyor (track.adform.net gibi tracker
    # pikselleri). Canlı ölçüm (2026-08-19): kalan 499 sayfanın 434'ünde İLK
    # görsel adform'du — tek slug kullanılınca o sayfaların ölü URL'leri hiç
    # cache'lenmiyor, aynı adres onlarca kez yeniden indiriliyordu.
    client_box = [_new_img_client()]
    out: list[tuple[str, bytes]] = []
    olu_cache: dict[str, set] = {}
    try:
        for u in urls:
            slug = _slug_of(u)
            if slug:
                olu = olu_cache.get(slug)
                if olu is None:
                    olu = olu_cache[slug] = _olu_yukle(slug)
                if u in olu:
                    continue                  # daha önce ölü bulundu -> indirme
            png, kalici = _fetch_one_image(client_box, u)
            if png:
                out.append((u, png))
            elif slug:
                if kalici:
                    _olu_ekle(slug, u)        # HTML/4xx: kesin ölü, hemen cache'le
                else:
                    # AĞ hatası (5 deneme tükendi): geçici olabilir. Üst üste
                    # _OLU_ESIK koşuda gelmezse ölü sayılır — geçici kesinti
                    # yüzünden sağlam bir görseli kalıcı kaybetmeyelim.
                    if _olu_sayac_arttir(slug, u) >= _OLU_ESIK:
                        _olu_ekle(slug, u)
    finally:
        try:
            client_box[0].close()
        except Exception:
            pass
    return out, True


def _pdf_pages(path: Path):
    """Her PDF sayfasını (page, has_text, text) olarak sırayla üretir — hem
    _pdf_text hem _pdf_images AYNI tarama mantığını kullanır (metin bloğu YOKSA
    o sayfa taranmış sayılır), iki AŞAMA (metin/aşama-2, görsel/aşama-3) arasında
    tutarlılık burada garanti edilir."""
    import pymupdf
    doc = pymupdf.open(path)
    try:
        for pno in range(doc.page_count):
            page = doc[pno]
            d = page.get_text("dict")
            items: list[tuple[float, float, str]] = []
            for b in d.get("blocks", []):
                if b.get("type") == 0:
                    txt = "".join(s["text"] for l in b.get("lines", []) for s in l.get("spans", [])).strip()
                    if txt:
                        items.append((b["bbox"][1], b["bbox"][0], txt))
            items.sort(key=lambda it: (round(it[0], 1), it[1]))
            yield page, pno, bool(items), "\n\n".join(t for _, _, t in items)
    finally:
        doc.close()


def _pdf_text(path: Path) -> str:
    """AŞAMA 2 (PDF metin temizleme) girdisi: sadece metin katmanı, sayfa
    sırasıyla — taranmış (metinsiz) sayfalar için pixmap RENDER ETMEZ (o iş
    sadece aşama 3'te, görsel gerektiğinde yapılır — gereksiz iş yok)."""
    return "\n\n".join(txt for _, _, has_text, txt in _pdf_pages(path) if has_text)


def _pdf_images(path: Path) -> list[tuple[str, bytes]]:
    """AŞAMA 3 (görsel işleme) girdisi: gömülü görseller VE metin katmanı
    olmayan (taranmış) sayfaların TAMAMI (tek render edilmiş görsel olarak) —
    bunlar clean_images ile web sayfası görselleriyle AYNI ortak adımdan
    geçer; özel bir bant/zoom algoritması burada YOK."""
    import pymupdf
    images: list[tuple[str, bytes]] = []
    for page, pno, has_text, _txt in _pdf_pages(path):
        if has_text:
            for b in page.get_text("dict").get("blocks", []):
                if b.get("type") == 1:
                    png = _to_png(b.get("image") or b"")
                    if png:
                        images.append((f"PDF sayfa {pno + 1}, gömülü görsel", png))
        # TAM SAYFA RENDER'I: metin katmanı OLMAYAN sayfalarda zorunlu; metin
        # OLAN ama İÇERİĞİ GÖRSELDE olan sayfalarda da gerekli (kanıtlı:
        # albaraka guncel-kar-paylasim-oranlari.pdf — 1.3 MB'lık taranmış tablo,
        # sayfada yalnız 24 karakterlik "9 Temmuz 2025 itibarıyla" başlığı vardı;
        # has_text=True sayıldığı için tam sayfa HİÇ render edilmedi ve tablonun
        # tamamı kayboldu). Kısa metinli sayfa = görsel içerikli sayfa sayılır.
        if not has_text or len((_txt or "").strip()) < _TAM_SAYFA_ESIK:
            pix = page.get_pixmap(matrix=pymupdf.Matrix(2.0, 2.0), alpha=False)
            images.append((f"PDF sayfa {pno + 1} (tam sayfa)", pix.tobytes("png")))
    return images


def _frontmatter(url: str, bank: str, content_hash: str, dates: dict) -> str:
    lines = ["---", f'url: "{url}"', f'bank: "{bank}"', f'content_hash: "{content_hash}"',
             f'processed_at: "{datetime.now(timezone.utc).isoformat(timespec="seconds")}"',
             f'validity_status: "{dates["status"]}"',
             f'content_relevance: "{dates.get("relevance") or "gerekli"}"']
    # TARİH ALANLARI HER ZAMAN YAZILIR (kullanıcı kararı, 2026-08-19).
    # Eskiden sadece DOLU olanlar yazılıyordu; alan yoksa "LLM'e hiç sorulmadı"
    # ile "soruldu ama belgede tarih yok" ayırt EDİLEMİYORDU. Artık üç durum da
    # açıkça görünür:
    #   "2026-08-01" / "2026-09-30"  -> iki taraf da bulundu
    #   "2026-08-01" / ""            -> tek taraf bulundu (diğeri belgede YOK)
    #   ""          / ""             -> hiç tarih yok
    # Tablo katmanı bunu "gg/aa/yyyy - ?" ve "-" biçimlerine çevirir
    # (bkz. compare/tablo_tarih.py::bicimle).
    lines.append(f'gecerlilik_baslangic: "{dates.get("start") or ""}"')
    lines.append(f'gecerlilik_bitis: "{dates.get("end") or ""}"')
    lines.append("---\n")
    return "\n".join(lines)


def _pdf_clean_frontmatter(url: str, content_hash: str, dates: dict) -> str:
    lines = ["---", f'url: "{url}"', f'content_hash: "{content_hash}"',
             f'content_relevance: "{dates.get("relevance") or "gerekli"}"']
    # bkz. _frontmatter: tarih alanları HER ZAMAN yazılır (boş olsa bile).
    lines.append(f'gecerlilik_baslangic: "{dates.get("start") or ""}"')
    lines.append(f'gecerlilik_bitis: "{dates.get("end") or ""}"')
    lines.append("---\n")
    return "\n".join(lines)


def _nazik_workers(slug: str, istenen: int) -> int:
    """WAF'a hassas bankalarda İÇERİK aşamalarını da yavaşlat.

    Kullanıcı kararı 2026-08-23: kuveytturk her yerde nazik kalmalı — sadece
    crawl'da değil, görsel indirme/işleme ve PDF aşamalarında da. Profil
    dataprep/crawl/hiz.py::NAZIK_BANKALAR'dan gelir (TEK kaynak)."""
    try:
        from dataprep.crawl.hiz import NAZIK_BANKALAR
    except Exception:
        return istenen
    pr = NAZIK_BANKALAR.get(slug)
    if not pr:
        return istenen
    n = max(1, min(istenen, int(pr.get("tavan", istenen))))
    if n < istenen:
        log.info("  [NAZİK MOD] %s: worker %d -> %d", slug, istenen, n)
    return n

def process_bank_pdf_text(slug: str, workers: int = WORKERS) -> None:
    workers = _nazik_workers(slug, workers)
    """AŞAMA 2 — SADECE PDF'ler: metin katmanını çıkarır, İLK ve TEK kez LLM
    ile temizler (crawl'da PDF hiç işlenmedi, ham binary indirildi). Ara
    çıktı: <bank>_site/_pdf_clean/<pdf-yolu>.md (temiz metin + tarihler,
    görsel YOK — o aşama 3'te). Ayrı ledger (_pdf_clean_ledger.json) ile
    idempotent/resumable; aşama 3 (process_bank_images) bu çıktıyı okur."""
    site = Path(__file__).resolve().parents[1] / "data" / f"{slug}_site"
    cat_path = site / "_catalog.json"
    if not cat_path.exists():
        log.warning("%s: katalog yok, atlanıyor", slug)
        return
    catalog: dict = json.loads(cat_path.read_text(encoding="utf-8"))
    ledger_path = site / "_pdf_clean_ledger.json"
    done: dict[str, dict] = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {}
    out_root = site / "_pdf_clean"
    plog = Ledger(site / "_processing_log.jsonl")

    todo: list[tuple[str, dict]] = []
    for url, rec in catalog.items():
        if rec.get("status") == "removed" or rec.get("kind") != "pdf":
            continue
        prior = done.get(url)
        if prior and prior.get("source_hash") == rec.get("hash"):
            # ÇIKTI GERÇEKTEN DURUYOR MU? (2026-08-22, aşama 3'teki AYNI
            # kontrol.) Yalnız hash'e bakmak, çıktı dosyası silinmiş/bozulmuş
            # olsa bile URL'i sonsuza dek atlamak demekti — sessiz veri kaybı.
            # "gereksiz" kayıtlarda çıktı zaten YOKTUR, onlar atlanır.
            if prior.get("relevance") == "gereksiz":
                continue
            _sfx = Path(rec["path"]).suffix
            _cikti = out_root / (rec["path"][:-len(_sfx)] + ".md" if _sfx else rec["path"] + ".md")
            if _cikti.exists():
                continue                  # hash aynı VE çıktı yerinde -> atla
            log.warning("  [PDF ÇIKTI KAYIP] %s -> %s yeniden üretilecek",
                        url[-70:], _cikti.name)
        todo.append((url, rec))
    log.info("%s (PDF metin): %d PDF katalogda, %d işlenecek (workers=%d)",
             slug, sum(1 for r in catalog.values() if r.get("kind") == "pdf"), len(todo), workers)

    def work(item: tuple[str, dict]):
        url, rec = item
        raw_path = site / rec["path"]
        out_rel = rec["path"][:-len(raw_path.suffix)] + ".md"
        out_path = out_root / out_rel
        if not raw_path.exists():
            # KAYNAK DOSYA YOK. İki sebebi olabilir, ikisi de KALICI:
            #   1) daha önce "gereksiz" bulunup SİLİNDİ (deleted_gereksiz) —
            #      ledger yazımı o koşuda kaybolmuşsa burada telafi edilir
            #   2) crawl hiç indiremedi (403/404/kopma)
            # Her koşuda tekrar denemek anlamsız: dosya kendiliğinden geri
            # gelmez. Ledger'a KALICI olarak işaretle ki bir daha todo'ya
            # girmesin; URL katalogda 'silindi/gereksiz' etiketiyle KALIR
            # (iz kaybolmaz, sonradan denetlenebilir).
            plog.record("pdf_text", url, decision="missing_source",
                        reason="kaynak dosya yok (silinmiş ya da hiç inmemiş) — kalıcı işaretlendi")
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            return url, {"source_hash": rec.get("hash", ""), "processed_at": now,
                         "relevance": "gereksiz", "status": "silindi"}
        try:
            dates = {"start": "", "end": "", "durum": "", "relevance": "gerekli"}
            cleaned = clean_text(_pdf_text(raw_path), url, dates, title=raw_path.stem)
            if cleaned is None:
                # LLM'e ulaşılamadı / bir parça başarısız oldu -> ledger'a YAZMA,
                # bu PDF sonraki koşuda BAŞTAN denensin (kısmi/boş içerik kalıcı
                # olarak 'işlendi' sayılmasın).
                plog.record("pdf_text", url, decision="llm_unreachable")
                return url, None
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            relevance = dates.get("relevance") or "gerekli"
            # KAPANIŞ EMNİYETİ: silme GERİ DÖNÜŞSÜZ. Kapanış sırasında LLM
            # yanıtları erken kesilebildiği için "gereksiz" oyu eksik veriye
            # dayanabilir — o an ASLA silme, ledger'a da yazma (bu PDF sonraki
            # koşuda baştan, tam veriyle değerlendirilir).
            if relevance == "gereksiz" and _STOPPING.is_set():
                plog.record("pdf_text", url, decision="deferred_shutdown",
                            reason="kapanış sırasında 'gereksiz' kararı — silme ertelendi")
                return url, None
            if relevance == "gereksiz":
                # İÇERİK-DÜZEYİNDE gereksiz -> HEM türetilmiş temiz metin (_pdf_clean)
                # HEM ham PDF'in kendisi SİLİNİR — "URL'i yeterli" ilkesi PDF için de
                # aynı: crawl zaten ham binary'yi indirmişti (relevance o an bilinmiyordu),
                # şimdi öğrenilince o da temizlenir. Ledger'a relevance ile kaydedilir ki
                # aşama 3 dosyaya hiç bakmadan atlayabilsin.
                out_path.unlink(missing_ok=True)
                raw_path.unlink(missing_ok=True)
                plog.record("pdf_text", url, decision="deleted_gereksiz")
                return url, {"source_hash": rec.get("hash", ""), "processed_at": now, "relevance": "gereksiz"}
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(_pdf_clean_frontmatter(url, rec.get("hash", ""), dates) + "\n" + cleaned + "\n",
                                encoding="utf-8")
            plog.record("pdf_text", url, decision="ok", reason=f"{len(cleaned)} char")
            return url, {"source_hash": rec.get("hash", ""), "processed_at": now, "relevance": relevance}
        except Exception as exc:
            log.warning("  %s HATA (pdf-metin): %s", url, exc)
            plog.record("pdf_text", url, decision="error", reason=str(exc))
            return url, None

    last_write = time.time()
    stopped_early = False
    # as_completed (ex.map DEĞİL): ex.map sonuçları KUYRUK SIRASINA göre verir,
    # yani sıradaki yavaş bir öğe arkasındaki BİTMİŞ işlerin ledger'a
    # yazılmasını bloke eder (head-of-line blocking; canlı yaşandı: 166
    # görselli tek sayfa yüzünden 192 tamamlanmış sayfa ledger'a düşemedi ve
    # sonraki koşuda hepsi TEKRAR işlenecekti). as_completed BİTEN'i hemen
    # verir — yavaş bir öğe artık kimseyi bekletmez.
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(work, it): it for it in todo}
        for j, fut in enumerate(as_completed(futures), 1):
            url, res = fut.result()
            if res is not None:
                done[url] = res
            if _should_flush(j, last_write):
                _write_ledger(ledger_path, done)
                last_write = time.time()
                log.info("  %s (PDF metin): %d/%d işlendi", slug, j, len(todo))
            if _STOPPING.is_set():            # zarif kapanış: eldekini yazıp çık
                stopped_early = True
                for f in futures:             # kuyrukta bekleyenleri iptal et
                    f.cancel()
                break
    _write_ledger(ledger_path, done)
    total_pdf = sum(1 for r in catalog.values() if r.get("kind") == "pdf")
    log.info("%s (PDF metin) %s: %d/%d PDF işlendi", slug,
             "YARIDA KESİLDİ (ledger yazıldı, sonraki koşu devam eder)" if stopped_early else "BİTTİ",
             len(done), total_pdf)


def process_bank_images(slug: str, workers: int = WORKERS,
                        only_kind: str | None = None) -> None:
    workers = _nazik_workers(slug, workers)
    """AŞAMA 3 — sayfalar (HTML+PDF): görselleri işler, metinle birleştirip
    nihai data/<bank>_site/content/<sayfa>.md'yi yazar. HTML metni crawl'dan
    (zaten temiz), PDF metni aşama 2'nin çıktısından (_pdf_clean/) okunur —
    hiçbiri burada TEKRAR LLM'e verilmez, sadece görseller yeni LLM çağrısı
    üretir (+ CRAWLING V2: her görsel de tarih taşıyabilir, aynı biriktiriciye
    katılır).

    only_kind: "page" -> AŞAMA 3.1 (page image)
               "pdf"  -> AŞAMA 3.2 (pdf image)
               None   -> ikisi birden (varsayılan). Ayrı çalıştırmak, bir tür
               diğerinin kuyruğunu bloke ettiğinde işe yarar (dev PDF'ler site
               sayfalarını bekletiyordu)."""
    site = Path(__file__).resolve().parents[1] / "data" / f"{slug}_site"
    cat_path = site / "_catalog.json"
    if not cat_path.exists():
        log.warning("%s: katalog yok, atlanıyor", slug)
        return
    catalog: dict = json.loads(cat_path.read_text(encoding="utf-8"))
    ledger_path = site / "_content_ledger.json"
    done: dict[str, dict] = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {}
    pdf_clean_ledger_path = site / "_pdf_clean_ledger.json"
    pdf_clean_done: dict[str, dict] = (json.loads(pdf_clean_ledger_path.read_text(encoding="utf-8"))
                                       if pdf_clean_ledger_path.exists() else {})
    cache = vlm.ImageCache(site / "_image_cache.json")   # BANKA İÇİ (kasıtlı)
    # Ortak/bankalararası cache DENENDİ ve GERİ ALINDI (2026-08-23): 10 banka
    # AYRI SÜREÇ olarak çalışıyor, ImageCache.save() ise yalnız SÜREÇ-İÇİ
    # kilitli — tek dosyaya 10 süreç yazınca son yazan diğerlerini siler ve
    # binlerce görsel incelemesi kaybolur. Ayrıca aynı görselin farklı
    # bankadaki BAĞLAMI farklı olabilir. Mükerrerlik zaten sha256 ile banka
    # içinde engelleniyor; bankalararası tekrar, veri güvenliği için kabul
    # edilmiş bilinçli bir maliyettir.
    out_root = site / "content"
    pdf_clean_root = site / "_pdf_clean"
    plog = Ledger(site / "_processing_log.jsonl")

    todo: list[tuple[str, dict]] = []
    for url, rec in catalog.items():
        if rec.get("status") == "removed" or rec.get("kind") not in ("page", "pdf"):
            continue
        if only_kind and rec.get("kind") != only_kind:
            continue                          # aşama 3.1 / 3.2 ayrımı
        prior = done.get(url)
        if prior and prior.get("source_hash") == rec.get("hash"):
            # ÇIKTI GERÇEKTEN DURUYOR MU? (2026-08-22) Eskiden yalnız hash'e
            # bakılıyordu: ledger "işlendi" diyorsa çıktı dosyası SİLİNMİŞ/
            # bozulmuş olsa bile URL sonsuza dek atlanıyor, hiçbir zaman
            # yeniden üretilmiyordu — sessiz veri kaybı. (Canlı örnek: kırık
            # frontmatter yüzünden 28 kuveytturk dosyası elle silindi, koşu
            # "1116 zaten güncel" deyip hiçbirini geri üretmedi.)
            # Not: "gereksiz" kayıtlarda çıktı zaten YOKTUR, onlar atlanır.
            cikti = prior.get("output_path") or ""
            if prior.get("status") == "gereksiz" or not cikti or (site / cikti).exists():
                continue                          # DEĞİŞMEMİŞ -> atla (resumable + incremental)
            log.warning("  [ÇIKTI KAYIP] %s -> %s yeniden üretilecek", url[-70:], cikti)
        todo.append((url, rec))
    _asama = {"page": "AŞAMA 3.1 page image",
              "pdf": "AŞAMA 3.2 pdf image"}.get(only_kind, "AŞAMA 3 (3.1+3.2)")
    log.info("%s (%s): %d URL katalogda, %d işlenecek (%d zaten güncel, workers=%d)",
             slug, _asama, len(catalog), len(todo), len(catalog) - len(todo), workers)

    def work(item: tuple[str, dict]):
        url, rec = item
        now0 = datetime.now(timezone.utc).isoformat(timespec="seconds")
        # ÖN-KONTROL — dosyaya HİÇ bakmadan: bu URL zaten (aşama 1'de HTML için
        # katalogda, aşama 2'de PDF için _pdf_clean_ledger'da) 'gereksiz'
        # etiketlenmişse (o aşamalarda içerik zaten SİLİNMİŞ olabilir) burada
        # tekrar dosya aramaya/LLM'e gitmeye gerek yok — direkt elenir.
        raw_path = site / rec["path"]
        is_pdf = raw_path.suffix.lower() == ".pdf"
        precheck_relevance = (pdf_clean_done.get(url, {}).get("relevance") if is_pdf
                              else rec.get("relevance"))
        if precheck_relevance == "gereksiz":
            plog.record("content", url, decision="excluded_gereksiz_precheck")
            return url, {"source_hash": rec.get("hash", ""), "output_path": "",
                         "status": "gereksiz", "processed_at": now0}
        if not raw_path.exists():
            # Kaynak yok -> KALICI (aşama 2'deki aynı kural): ya "gereksiz"
            # bulunup silindi, ya crawl hiç indiremedi. Dosya kendiliğinden
            # geri gelmeyeceği için ledger'a işaretle, todo'ya bir daha girmesin.
            plog.record("content", url, decision="missing_source",
                        reason="kaynak dosya yok (silinmiş ya da hiç inmemiş) — kalıcı işaretlendi")
            return url, {"source_hash": rec.get("hash", ""), "output_path": "",
                         "status": "silindi", "processed_at": now0}
        try:
            dates = {"start": "", "end": "", "durum": "", "relevance": "gerekli"}
            if raw_path.suffix.lower() == ".pdf":
                pdf_clean_path = pdf_clean_root / (rec["path"][:-len(raw_path.suffix)] + ".md")
                if not pdf_clean_path.exists():
                    # aşama 2 bu PDF'i henüz işlememiş -> atla (process_bank_pdf_text
                    # önce çalışmalı; burada asla LLM'e gitmeyiz, aşamalar ayrık kalır)
                    plog.record("content", url, decision="pdf_text_not_ready")
                    return url, None
                front, cleaned = _split_front(pdf_clean_path.read_text(encoding="utf-8"))
                text = cleaned
                _fold_dates(dates, {"gecerlilik_baslangic": _front_field(front, "gecerlilik_baslangic"),
                                    "gecerlilik_bitis": _front_field(front, "gecerlilik_bitis")})
            else:
                # HTML metni crawl-time'da ZATEN LLM ile temizlendi (store.py ->
                # pages.clean_page) ve tarihleri frontmatter'a yazıldı — burada
                # TEKRAR LLM'e vermek gereksiz çift iş; olduğu gibi okunur, crawl'ın
                # bulduğu tarihler dates'e fold edilir (görsellerinkiyle AYNI
                # biriktirici — CRAWLING V2: görsel de bir "sayfa" gibi katkı yapar).
                raw = raw_path.read_text(encoding="utf-8")
                front, cleaned = _split_front(raw)
                text = cleaned
                _fold_dates(dates, {"gecerlilik_baslangic": _front_field(front, "campaign_start"),
                                    "gecerlilik_bitis": _front_field(front, "campaign_end")})

            # AŞAMA 1/2'nin İÇERİK-DÜZEYİNDE verdiği gerekli/gereksiz etiketi —
            # URL bazlı ön-elemeden çok daha isabetli (LLM gerçek metni gördü).
            # 'gereksiz' ise görsel işleme (pahalı: ağ İNDİRME + VLM) HİÇ
            # YAPILMAZ (bu kontrol bilerek img_items çıkarımından ÖNCE) — ama
            # ledger'a YİNE DE 'işlendi' yazılır (source_hash eşleşen) — hem
            # kaynak .md/'_pdf_clean' dosyası ETİKETLİ olarak diskte kalır (iz
            # kaybolmaz, sonradan denetlenebilir) hem de sonraki koşuda CHANGED
            # tuzağına düşmez (digest zaten ham içerikten, bu etiketten değil).
            content_relevance = _front_field(front, "content_relevance") or "gerekli"
            if content_relevance == "gereksiz":
                now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                plog.record("content", url, decision="excluded_gereksiz",
                            reason="içerik-düzeyinde gereksiz etiketli, content/ üretilmedi")
                return url, {"source_hash": rec.get("hash", ""), "output_path": "",
                             "status": "gereksiz", "processed_at": now}

            if raw_path.suffix.lower() == ".pdf":
                img_items, fetch_ok = _pdf_images(raw_path), True
            else:
                img_items, fetch_ok = _fetch_page_images(rec.get("images") or [])
            image_md, images_ok = clean_images(img_items, cache, dates)
            images_ok = images_ok and fetch_ok
            body = cleaned
            if image_md:
                body = (body + "\n\n" + image_md) if body else image_md
            dates["status"] = _resolve_status(dates)
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            if not images_ok:
                # BİR VEYA DAHA FAZLA görsele VLM ulaşılamadı -> ledger'a YAZMA
                # (source_hash eşleşirse bir daha denenmez, o görsel kalıcı
                # kaybolur). Metin+diğer görseller yine de diske YAZILIR (veri
                # kaybı yok, ama bu URL "işlendi" sayılmadığı için sonraki
                # koşuda TEKRAR denenip eksik görsel tamamlanacak).
                plog.record("content", url, decision="partial_image_failure",
                            reason=f"{len(text)}->{len(body)} char (bazı görseller eksik)")
            if not body:
                plog.record("content", url, decision="empty")
                return url, {"source_hash": rec.get("hash", ""), "output_path": "",
                             "status": "empty", "processed_at": now}
            out_rel = rec["path"]
            if raw_path.suffix.lower() == ".pdf":
                out_rel = out_rel[:-len(raw_path.suffix)] + ".md"   # çıktı her zaman markdown
            out_path = out_root / out_rel
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(_frontmatter(url, slug, rec.get("hash", ""), dates) + "\n" + body + "\n",
                                encoding="utf-8")
            if not images_ok:
                return url, None          # diske yazıldı ama ledger'a İŞLENDİ diye YAZILMADI
            plog.record("content", url, decision="ok",
                        reason=f"{len(text)}->{len(body)} char, durum={dates['status']}")
            return url, {"source_hash": rec.get("hash", ""),
                        "output_path": str(out_path.relative_to(site)),
                        "status": "ok", "processed_at": now}
        except Exception as exc:
            log.warning("  %s HATA: %s", url, exc)
            plog.record("content", url, decision="error", reason=str(exc))
            return url, None

    last_write = time.time()
    stopped_early = False
    # as_completed (ex.map DEĞİL) — bkz. process_bank_pdf_text'teki aynı not:
    # tek yavaş sayfa, arkasındaki bitmiş sayfaların ledger'a yazılmasını
    # bloke etmemeli (canlı: vakifkatilim'de 166 görselli bir sayfa yüzünden
    # 192 tamamlanmış sayfa kaydedilemedi).
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(work, it): it for it in todo}
        for j, fut in enumerate(as_completed(futures), 1):
            url, res = fut.result()
            if res is not None:
                done[url] = res
            # sık kaydet: kesilirse iş kaybı az (resumable). N öğede bir VEYA
            # _LEDGER_EVERY_SEC saniyede bir — VLM yavaşken 20 öğe saatler
            # sürebildiği için sadece sayıya bakmak yetmiyor.
            if _should_flush(j, last_write):
                _write_ledger(ledger_path, done)
                cache.save()
                last_write = time.time()
                log.info("  %s: %d/%d işlendi", slug, j, len(todo))
            if _STOPPING.is_set():            # zarif kapanış: eldekini yazıp çık
                stopped_early = True
                for f in futures:             # kuyrukta bekleyenleri iptal et
                    f.cancel()
                break
    _write_ledger(ledger_path, done)
    cache.save()
    ok = sum(1 for v in done.values() if v.get("status") == "ok")
    log.info("%s %s: %d/%d URL işlendi (%d içerikli)", slug,
             "YARIDA KESİLDİ (ledger yazıldı, sonraki koşu devam eder)" if stopped_early else "BİTTİ",
             len(done), len(catalog), ok)


def process_bank(slug: str, workers: int = WORKERS) -> None:
    workers = _nazik_workers(slug, workers)
    """3 aşamalı sırayla çalıştırır: (2) PDF metin temizleme, (3) görsel işleme
    + nihai birleştirme. (1) HTML metin temizleme crawl'ın kendi işi, burada
    tekrarlanmaz — bkz. crawl/store.py::fetch_and_store -> pages.clean_page."""
    process_bank_pdf_text(slug, workers=workers)
    process_bank_images(slug, workers=workers)


def _replace_or_add_front_field(front: str, key: str, value: str) -> str:
    """Frontmatter'da `key` alanı varsa DEĞERİNİ değiştirir, yoksa (kapanış
    '---' satırından ÖNCE) ekler. Diğer alanlara/sıraya dokunmaz."""
    lines = front.splitlines()
    out, replaced = [], False
    for ln in lines:
        if ln.split(":", 1)[0].strip() == key:
            out.append(f'{key}: "{value}"')
            replaced = True
        else:
            out.append(ln)
    if not replaced:
        # son satır kapanış '---' -> ondan hemen önce ekle
        if out and out[-1].strip() == "---":
            out.insert(-1, f'{key}: "{value}"')
        else:
            out.append(f'{key}: "{value}"')
    return "\n".join(out)


def refresh_validity_status(slug: str) -> None:
    """LLM'SİZ, SAF TARİH HESABI: data/<bank>_site/content/*.md'deki HER
    dosyanın validity_status'unu BUGÜNE göre yeniden hesaplar.

    NEDEN GEREKLİ: process_bank_images source_hash eşleşen URL'leri atlar
    (kaynak değişmediyse LLM'i tekrar yormaz) — ama validity_status ZAMANA
    bağlı: bir kampanya sitede/PDF'te HİÇ değişmeden 'gecerli'den 'suresi_
    gecmis'e geçebilir (bitiş tarihi geçtikçe). Bu fonksiyon hash'e BAKMAZ,
    her content/*.md'yi HER ÇAĞRILDIĞINDA tazeler — istendiği kadar sık
    (günlük dahi) ücretsiz çalıştırılabilir, hiç LLM çağrısı yapmaz."""
    site = Path(__file__).resolve().parents[1] / "data" / f"{slug}_site"
    out_root = site / "content"
    if not out_root.exists():
        return
    changed = 0
    for p in out_root.rglob("*.md"):
        try:
            text = p.read_text(encoding="utf-8")
            front, body = _split_front(text)
            if not front:
                continue
            dates = {"start": _front_field(front, "gecerlilik_baslangic"),
                     "end": _front_field(front, "gecerlilik_bitis"),
                     "guess": _front_field(front, "validity_status")
                              if _front_field(front, "validity_status") in ("suresi_gecmis", "bilinmiyor") else ""}
            new_status = _resolve_status(dates)
            if _front_field(front, "validity_status") == new_status:
                continue
            new_front = _replace_or_add_front_field(front, "validity_status", new_status)
            p.write_text(new_front + "\n" + body, encoding="utf-8")
            changed += 1
        except Exception as exc:
            log.warning("  (%s tarih tazeleme atlandı: %s)", p, exc)
    if changed:
        log.info("%s: %d dosyanın validity_status'u güncellendi (LLM'siz)", slug, changed)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    _install_signal_handlers()      # SIGTERM/SIGINT -> zarif kapanış (iş tekrarı olmasın)
    ap = argparse.ArgumentParser(description="Sayfa/PDF içeriğini metin+görsel birleşik LLM-friendly belgeye çevir")
    ap.add_argument("banks", nargs="*")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--stage",
                    choices=["pdf-text", "images", "images-page", "images-pdf", "all"],
                    default="all",
                    help="AŞAMA 1 = crawling (ayrı komut: dataprep.crawl). "
                         "pdf-text = AŞAMA 2 (PDF metinleri) | "
                         "images-page = AŞAMA 3.1 (page image) | "
                         "images-pdf = AŞAMA 3.2 (pdf image) | "
                         "images = 3.1+3.2 | all = hepsi (varsayılan)")
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[1] / "data"
    banks = args.banks or sorted(os.path.basename(d)[:-5] for d in glob.glob(str(root / "*_site")))

    def _run(b: str) -> None:
        try:
            if args.stage == "images":
                process_bank_images(b, workers=args.workers)
            elif args.stage == "pdf-text":
                process_bank_pdf_text(b, workers=args.workers)
            elif args.stage == "pages":
                from dataprep import pages
                pages.process_bank(b, workers=args.workers)
            elif args.stage == "all":
                # KESİN VE DOĞRU SIRALAMA:
                # 1. Aşama: Görseller (Page Image + PDF Image) VLM ile incelenir ve tablolara çevrilir.
                log.info("%s: [AŞAMA 2/4] Görseller işleniyor...", b)
                process_bank_images(b, workers=args.workers)
                # 2. Aşama: PDF Metinleri LLM ile temizlenir ve etiketlenir.
                log.info("%s: [AŞAMA 3/4] PDF metinleri işleniyor...", b)
                process_bank_pdf_text(b, workers=args.workers)
                # 3. Aşama: Ham web sayfaları LLM ile temizlenir, etiketlenir ve görsel tablolarıyla birleşir.
                log.info("%s: [AŞAMA 4/4] Web sayfaları temizleniyor ve birleştiriliyor...", b)
                from dataprep import pages
                pages.process_bank(b, workers=args.workers)
                process_bank(b, workers=args.workers)
            else:
                process_bank(b, workers=args.workers)
        except Exception as exc:                      # noqa: BLE001
            # BİR bankanın çökmesi diğerlerini DURDURMAZ (ayrı process'lerin
            # sağladığı izolasyonun tek-process'teki karşılığı). Ledger sayesinde
            # o banka sonraki koşuda kaldığı yerden devam eder.
            log.error("%s: BANKA HATASI %s: %s", b, type(exc).__name__, exc)

    # BANKALAR PARALEL, ama TEK PROCESS içinde (bkz. net_limit.py "TEK SÜREÇ
    # KURALI"): ayrı process başlatmak her birine AYRI bir NET_SEM verir, o
    # zaman uyarlanabilir sınırlayıcı toplam yükü göremez ve gerçek
    # eşzamanlılık banka sayısı kadar katlanır. Tek process'te hepsi AYNI
    # NET_SEM'i paylaşır — asıl eşzamanlılık kontrolü zaten orada, buradaki
    # thread sayısı sadece "kaç banka aynı anda ilerlesin" demek.
    if len(banks) == 1:
        _run(banks[0])
    else:
        with ThreadPoolExecutor(max_workers=len(banks)) as ex:
            list(ex.map(_run, banks))
    
    # Tüm bankalar tamamlandığında açık HTTP client/socket havuzunu temizle ve süreci kesin sonlandır
    try:
        from dataprep import vlm
        with vlm._client_lock:
            vlm._client.close()
    except Exception:
        pass
    log.info("🏁 Tüm işlemler başarıyla bitti, süreç temiz şekilde kapatılıyor.")
    os._exit(0)


if __name__ == "__main__":
    main()
