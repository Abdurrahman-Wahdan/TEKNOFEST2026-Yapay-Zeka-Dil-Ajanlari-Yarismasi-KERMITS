"""STORE — ilgili sayfayı indir, kaydet ve değişimi izle.

Kritik kural: inen markdown formatı ve PDF davranışı, mevcut download_sites
motorundan BİREBİR kullanılır (write_doc / frontmatter / url_to_path /
pdf_text_cached / _extract_markdown_sync). Buraya eklenen tek yeni şey:

  * içerik-hash KATALOĞU  -> değişim takibi (NEW / CHANGED / SAME)
  * "SAME" sayfalar tekrar yazılmaz (incremental: sadece değişeni işle)
  * opsiyonel Qdrant embedding (mevcut fabrikalar; host yoksa sessizce atlanır)

Hash, sayfanın GÖVDESİ üzerinden alınır (frontmatter'daki crawled_at tarihi
her gün değiştiği için dosya değil, içerik hash'lenir).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from dataprep.crawl.bank import engine
from dataprep.ledger import Ledger


def _ledger() -> Ledger:
    """Aktif bankanın işleme günlüğü (data/<bank>_site/_processing_log.jsonl)."""
    return Ledger(engine.OUT / "_processing_log.jsonl")

log = logging.getLogger("dataprep.crawl.store")

# AŞAMA 1'de metin temizleme YAPILMAZ (bkz. fetch_and_store). Eski davranış
# (crawl sırasında clean_page) CRAWL_CLEAN_INLINE=1 ile geri alınabilir.
_INLINE_CLEAN = os.environ.get("CRAWL_CLEAN_INLINE", "") == "1"


class Catalog:
    """URL -> {hash, path, kind, first_seen, last_seen, reason}. Değişim takibi."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, dict] = {}
        # PARALEL HASAT (2026-08-22): sayfalar artık eşzamanlı indiriliyor, bu
        # yüzden `data` sözlüğüne yazan/okuyan her yol KİLİTLİ. Aksi hâlde iki
        # coroutine aynı anda record() çağırdığında kayıt kaybolabilir ya da
        # save() yarı yazılmış bir sözlüğü serileştirebilir.
        self._lock = threading.RLock()
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))

    def status(self, url: str, digest: str) -> str:
        with self._lock:
            prev = self.data.get(url)
        if prev is None:
            return "NEW"
        return "SAME" if prev.get("hash") == digest else "CHANGED"

    def mark_removed(self, url: str) -> None:
        """URL bir sonraki keşifte artık bulunamadıysa (bkz. graph.py::mark_removed)
        SADECE bayrak eklenir — kayıt/DOSYA SİLİNMEZ, iz olarak kalır. Gerçek
        temizlik (fiziksel silme) kasıtlı olarak SONRAKİ bir aşamaya bırakıldı."""
        with self._lock:
            if url in self.data:
                self.data[url]["status"] = "removed"

    def touch(self, url: str) -> None:
        """Değişmemiş (SAME) sayfayı 'bugün görüldü' diye işaretle.

        Böylece yarın: last_seen<bugün olan kayıtlar = artık sitede yok (silinmiş)."""
        with self._lock:
            if url in self.data:
                self.data[url]["last_seen"] = date.today().isoformat()

    def record(self, url: str, digest: str, path: Path, kind: str, reason: str,
               parent: str = "", source_page: str = "", size_bytes: int = 0,
               images: list | None = None, relevance: str = "") -> None:
        today = date.today().isoformat()
        prev = self.data.get(url, {})
        rec = {
            "hash": digest,
            "path": str(path.relative_to(engine.OUT)) if path else "",
            "kind": kind,
            "parent": parent,                   # sayfa: bir üst bölüm; PDF: kaynak sayfa
            "first_seen": prev.get("first_seen", today),
            "last_seen": today,
            "reason": reason,
        }
        if kind == "pdf":                       # PDF provenance tek yerde
            rec["source_page"] = source_page
            rec["size_bytes"] = size_bytes
        if kind == "page":                      # item-VLM için sayfa görselleri
            rec["images"] = images or []
        # İÇERİK-DÜZEYİNDE gerekli/gereksiz etiketi (URL bazlı triage'dan çok
        # daha isabetli, LLM gerçek metni gördü) — 'gereksiz' ise content.py
        # bu URL'i işlemeyi TAMAMEN atlar (bkz. process_bank_images/pdf_text).
        # hash SABİT kaldığı sürece SAME sayılmaya devam eder, bir daha
        # LLM'e sorulmaz.
        if relevance:
            rec["relevance"] = relevance
        elif prev.get("relevance"):              # önceki karar varsa KORUNUR
            rec["relevance"] = prev["relevance"]
        self.data[url] = rec

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            icerik = json.dumps(self.data, ensure_ascii=False, indent=1)
        # ATOMİK YAZIM: paralel hasatta save() sık çağrılıyor; doğrudan
        # write_text bir çökme/kesinti anında katalogu YARIM bırakabilir
        # (bir sonraki koşu onu okuyamaz -> tüm değişim takibi sıfırlanır).
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(icerik, encoding="utf-8")
        tmp.replace(self.path)


def set_output(out: Path) -> None:
    """Motorun çıktı kökünü ayarla (write_doc/url_to_path bunu kullanır)."""
    engine.OUT = out
    out.mkdir(parents=True, exist_ok=True)


def _digest(body: str) -> str:
    return hashlib.sha256(body.strip().encode("utf-8")).hexdigest()


def _page_signature(body: str, images: list) -> str:
    """Değişim imzası = METİN + GÖRSEL. Metin aynı olsa da görsel eklenmiş/çıkmışsa
    imza değişir -> sayfa CHANGED sayılır ve yeniden alınır. İkisi de aynıysa SAME."""
    payload = body.strip() + "\n\x00IMG\x00\n" + "\n".join(sorted(images or []))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# PDF'ler metne ÇEVRİLMEZ; binary olarak diske iner (ayrı işlenecek).
# Aynı PDF birçok sayfada linkli olabilir -> URL bazında bir kez indir.
_PDF_SAVED: set[str] = set()


def _safe_segs(url: str) -> list[str]:
    return [re.sub(r"[^A-Za-z0-9._-]", "_", s) for s in urlparse(url).path.split("/") if s]


def _parent_url(url: str) -> str:
    """URL'nin bir üst bölümü (path'te bir seviye yukarı). Kök ise site tabanı."""
    s = urlparse(url)
    segs = [x for x in s.path.split("/") if x]
    base = f"{s.scheme}://{s.netloc}"
    return base if len(segs) <= 1 else f"{base}/" + "/".join(segs[:-1])


def _doc_path(url: str, source_page: str = "") -> Path:
    """Belge diskte KAYNAK SAYFANIN yolu altına gruplanır (parent-child görünür).

    .pdf -> pdfs/<kaynak-yolu>/...  (VLM hattı buradan okur)
    diğer belgeler -> docs/<kaynak-yolu>/...  (xlsx/docx/... ayrı işlenecek)"""
    name = (_safe_segs(url) or ["index"])[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    subdir = "pdfs" if ext == "pdf" else "docs"
    parent_segs = _safe_segs(source_page) if source_page else _safe_segs(url)[:-1]
    return engine.OUT / subdir / Path(*(parent_segs + [name]))


async def _download_pdf(client, url: str, catalog: Catalog, reason: str,
                        source_page: str = "") -> str:
    """Belgeyi (pdf/docx/xlsx/...) binary indir + kataloğa işle. Durum döner."""
    if url in _PDF_SAVED:
        return "SAME"
    r, err = await engine.fetch(client, url)
    if r is None:
        log.warning("  FAIL(doc) %s (%s)", url, err)
        _ledger().record("pdf", url, status="FAIL", decision="download_fail",
                         reason=str(err), source_page=source_page)
        return "FAIL"
    data = r.content
    digest = hashlib.sha256(data).hexdigest()
    status = catalog.status(url, digest)
    _PDF_SAVED.add(url)
    if status == "SAME":
        catalog.touch(url)          # bugün görüldü (silinmedi)
        _ledger().record("pdf", url, status="SAME", decision="download_skip",
                         reason="dosya değişmedi", source_page=source_page)
        return "SAME"
    path = _doc_path(url, source_page)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    kind = "pdf" if url.lower().split("?")[0].endswith(".pdf") else "doc"
    # provenance tek yerde: parent = kaynak sayfa
    catalog.record(url, digest, path, kind, reason, parent=source_page,
                   source_page=source_page, size_bytes=len(data))
    log.info("  %-5s(%s) %s", status, kind, url)
    _ledger().record("pdf", url, status=status, decision="downloaded",
                     reason=reason, source_page=source_page, kind=kind)
    return status


async def _build_body(client, url: str, r):
    """HTML sayfadan (kind, title, desc, body) üretir. PDF burada işlenmez."""
    ctype = r.headers.get("content-type", "").lower()
    if "html" not in ctype:
        return None
    import asyncio
    md = (await asyncio.to_thread(engine._extract_markdown_sync, r.text, url) or "").strip()
    raw = _html_to_text(r.text)                      # ham HTML'den tam metin
    # BİLGİ KAÇMASIN: trafilatura'yı yalnız BOŞ değil, ham metne göre İNCE kaldığında
    # da yedekle. (Ölçüldü: hesaplama-araclari trafilatura<250 ama ham~71k.)
    if len(md) < 400 or len(raw) > 3 * max(len(md), 1):
        if len(raw) > len(md):
            md = raw
    if len(md.strip()) < 40:
        return None
    title, desc = await asyncio.to_thread(engine._page_meta_sync, r.text)
    return "page", title, desc, md


_TAGSTRIP = re.compile(r"(?is)<(script|style|noscript|template)[^>]*>.*?</\1>")
_TAG = re.compile(r"(?s)<[^>]+>")


def _html_to_text(html: str) -> str:
    """trafilatura boş dönerse son çare: script/style at, etiketleri sıyır, metni topla."""
    from html import unescape
    t = _TAGSTRIP.sub(" ", html)
    t = _TAG.sub(" ", t)
    t = unescape(t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\s*\n\s*", "\n", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


# Yakalanacak TÜM belge türleri (PDF + office + veri). Bilgi kaçırmamak için geniş.
_DOC_EXTS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".rtf")
_DOC_RE = re.compile(r'href=["\']([^"\']+\.(?:pdf|docx?|xlsx?|pptx?|csv|rtf)(?:\?[^"\']*)?)["\']', re.I)


def _is_doc_url(u: str) -> bool:
    return u.lower().split("?")[0].endswith(_DOC_EXTS)


# Sayfa <img>'leri — item-VLM (görsel inceleme) için kaydedilir.
_IMG_RE = re.compile(r'<img\b[^>]*\bsrc=["\']([^"\']+)["\']', re.I)
# aşikar dekoratif/izleme görselleri (VLM'e gitmesin diye burada değil, aşamada
# dedup+VLM eler; sadece anlamsızları burada atıyoruz). AYRICA: analytics/ad-
# tracking pikselleri VE captcha gibi HER İSTEKTE FARKLI TOKEN taşıyan görseller
# atılır — bunlar sayfa hiç değişmese bile URL'leri her fetch'te değiştiği için
# değişim-imzasını (digest) sürekli bozup sahte CHANGED'e yol açardı (genel bir
# web-scraping problemi, tek bir siteye özel değil).
_IMG_SKIP = re.compile(
    r"transparent|spacer|1x1|pixel|blank|\.svg(\?|$)"
    r"|captcha|doubleclick\.net|google-analytics\.com|googletagmanager\.com"
    r"|googlesyndication\.com|facebook\.com/tr|mc\.yandex\.ru|hotjar\.com"
    r"|clarity\.ms|analytics", re.I)


def _content_img_urls(html: str, base: str) -> list[str]:
    """Sayfadaki <img> src'lerini mutlak URL + dedup olarak döndürür (item-VLM girdisi).

    data:/izleme-piksel/svg-sprite atılır; kalanların dekoratif mi içerik mi olduğuna
    görsel-VLM aşaması (hash-dedup cache) karar verir."""
    out, seen = [], set()
    for s in _IMG_RE.findall(html):
        if s.startswith("data:") or _IMG_SKIP.search(s):
            continue
        u = engine.clean_url(urljoin(base, s.strip()))
        if u.lower().startswith("http") and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _doc_links(html: str, base: str) -> list[str]:
    """Sayfanın HAM HTML'indeki TÜM belge linklerini döndürür (pdf/docx/xlsx/...).

    ÖNEMLİ: markdown'dan DEĞİL ham HTML'den taranır — trafilatura bazı linkleri
    (buton/gizli href) markdown'a taşımıyor. Dış domain dahil; LLM sonra seçer."""
    out, seen = [], set()
    for link in _DOC_RE.findall(html):
        u = engine.clean_url(urljoin(base, link.strip()))
        if u.lower().startswith("http") and u not in seen:
            seen.add(u)
            out.append(u)
    return out


_HREF_RE = re.compile(r'href=["\']([^"\'#]+)', re.I)


def _same_domain_page_links(html: str, base: str) -> set[str]:
    """Sayfa HTML'inden same-domain, indirmeye değer (wanted) link URL'leri."""
    out = set()
    for h in _HREF_RE.findall(html):
        u = engine.clean_url(urljoin(base, h.strip()))
        if engine.same_domain(u) and engine.wanted(u) and not _is_doc_url(u):
            out.add(u)
    return out


async def fetch_and_store(client, url: str, catalog: Catalog, *,
                          reason: str = "", embed=None, store_vec=None,
                          link_sink: set | None = None) -> str:
    """Bir sayfayı indir, (değiştiyse) kaydet, kataloğu güncelle. Durum döner.

    embed/store_vec verilirse ve içerik yeni/değişmişse Qdrant'a da eklenir.
    """
    # URL'in kendisi BELGE ise (pdf/docx/xlsx/...): metne çevirme, binary indir
    if _is_doc_url(url):
        return await _download_pdf(client, url, catalog, reason, source_page=url)

    r, err = await engine.fetch(client, url)
    if r is None:
        log.warning("  FAIL %s (%s)", url, err)
        _ledger().record("page", url, status="FAIL", decision="skip", reason=str(err))
        return "FAIL"
    ct = r.headers.get("content-type", "").lower()
    if ("application/pdf" in ct or "officedocument" in ct or "msword" in ct
            or "ms-excel" in ct or "spreadsheet" in ct):
        return await _download_pdf(client, url, catalog, reason, source_page=url)

    # incremental BFS: bu sayfadaki same-domain linkleri topla (yeni URL keşfi)
    if link_sink is not None:
        link_sink.update(_same_domain_page_links(r.text, url))

    built = await _build_body(client, url, r)
    if built is None:
        _ledger().record("page", url, status="EMPTY", decision="skip", reason="içerik boş")
        return "EMPTY"
    kind, title, desc, raw_body = built

    # İMZA (digest) ÖNCE, LLM'DEN ÖNCE hesaplanır — LLM temizleme pahalı ve
    # deterministik değil (temp>0'da aynı sayfa bile ufak farklı metin
    # üretebilir); SAME çıkacak bir sayfa için LLM'i hiç ÇAĞIRMAMAK için
    # sıralama böyle: önce ham imza + SAME kontrolü, İSE ancak DEĞİŞTİYSE
    # temizleme/indirme/kayıt.
    images = _content_img_urls(r.text, url)     # sayfadaki içerik görselleri (item-VLM)
    digest = _page_signature(raw_body, images)  # imza = HAM METİN + GÖRSEL
    status = catalog.status(url, digest)
    if status == "SAME":                        # hem metin hem görsel değişmemiş -> alma
        catalog.touch(url)                      # bugün görüldü (silinmedi)
        log.info("  SAME  %s", url)
        _ledger().record("page", url, status="SAME", decision="skip",
                         reason="metin+görsel değişmedi", images=len(images))
        return "SAME"

    # DOĞRULAMA: "değişti" derken ne olduğunu BİLMEDİĞİMİZ volatil içeriğe
    # (captcha/tracking dışında, henüz karşılaşmadığımız bir tanesine) kanmamak
    # için AYRI bir istemciyle (taze session/cookie — session'a sabitlenmiş
    # token'lar da yakalansın diye) İKİNCİ kez çekip imzayı DOĞRULARIZ. İki
    # taze fetch birbirini doğrularsa gerçek değişikliktir; doğrulamazsa
    # (kararsız/volatil) bu koşuda SAME sayılır, sonraki koşuda tekrar denenir
    # (kalıcı olarak sıkışmaz, kendi kendini düzeltir).
    async with httpx.AsyncClient(headers=engine.HEADERS, timeout=40, follow_redirects=True) as _confirm_client:
        r2, err2 = await engine.fetch(_confirm_client, url, retries=2)
    if r2 is not None:
        built2 = await _build_body(_confirm_client, url, r2)
        if built2 is not None:
            images2 = _content_img_urls(r2.text, url)
            digest2 = _page_signature(built2[3], images2)
            if digest2 != digest:
                # KRİTİK İSTİSNA (2026-08-22): bu koruma yalnız DAHA ÖNCE
                # KAYDEDİLMİŞ sayfalar için geçerlidir. Sayfa katalogda HİÇ
                # yoksa "SAME" demek, geri düşülecek eski bir sürüm olmadığı
                # için içeriği SESSİZCE ÇÖPE ATMAK demektir — sayfa ne diske
                # yazılır ne failures'a girer, verify onu sonsuza dek
                # "AÇIKLANAMAYAN" sayar. Canlı kanıt (emlakkatilim): 
                # tedarikci-finansmani ve web-servis-bakiye-izleme HTTP 200
                # dönmesine rağmen bu dalda kaybolmuştu.
                # Volatil olsa bile İLK sürümü kaydetmek, hiç kaydetmemekten
                # her zaman iyidir (sonraki koşu nasılsa güncelleyecek).
                if url in catalog.data:
                    log.info("  (değişim imzası kararsız/volatil — bu koşuda SAME sayılıyor) %s", url)
                    catalog.touch(url)
                    _ledger().record("page", url, status="SAME", decision="skip",
                                     reason="değişim imzası doğrulanamadı (volatil içerik)",
                                     images=len(images))
                    return "SAME"
                log.info("  (imza volatil ama sayfa YENİ — yine de kaydediliyor) %s", url)

    # AŞAMA AYRIMI (kullanıcı kararı 2026-08-22): AŞAMA 1 artık SAF İNDİRME —
    # URL budama (triage) + sayfa/PDF'i hızla diske almak. Metin temizleme,
    # tarih ve gerekli/gereksiz etiketleme AŞAMA 4'e taşındı
    # (pages.retag_bank; `_raw/` altındaki ham metinden çalışır, siteyi
    # TEKRAR TARAMADAN).
    #
    # NEDEN: sayfa başına LLM İKİ KEZ çalışıyordu — bir kez burada
    # (crawl-time clean_page), bir kez sonraki etiketleme/denetim
    # geçişlerinde. Sonda bir kez çalıştırmak yeterli; bu hem GPU israfını
    # yarıya indirir hem crawl'ı ağ hızına çıkarır (ölçüm: seri clean_page'li
    # hasat ~1 sayfa/90sn idi).
    #
    # VERİ KAYBI YOK: ham metin `_raw/` altına ZATEN yazılıyor (aşağıda) ve
    # aşama 4 tam olarak oradan besleniyor. Eski davranışa dönmek için:
    # CRAWL_CLEAN_INLINE=1
    from dataprep import pages
    if _INLINE_CLEAN:
        cleaned, page_dates = pages.clean_page(raw_body, url, title)
        body = cleaned if cleaned is not None else raw_body
        relevance = page_dates.get("relevance") or "gerekli"
    else:
        cleaned, page_dates = None, {}
        body = raw_body                       # ham metin diske; temizlik aşama 4'te
        relevance = "gerekli"                 # eleme kararı da aşama 4'ün

    # sayfadaki PDF'leri binary indir — HAM HTML'den (trafilatura'nın attıkları dahil)
    for doc_url in _doc_links(r.text, url):
        await _download_pdf(client, doc_url, catalog, f"linked from {url}",
                            source_page=url)

    path = engine.url_to_path(url)
    if relevance == "gereksiz":
        # İÇERİK-DÜZEYİNDE 'gereksiz' (URL bazlı ön-elemeden çok daha isabetli,
        # LLM gerçek metni gördü) -> HİÇBİR DOSYA yazılmaz (ham/temiz metin
        # lokalde durmaz, "URL'i yeterli"). Sadece kataloğa hash+relevance
        # kaydedilir: bu, gelecekteki koşularda digest AYNI kaldığı sürece
        # SAME sayılmaya devam etmesini sağlar (bir daha LLM'e sorulmaz, bir
        # daha dosya üretilmez) — CHANGED/NEW tuzağına asla düşmez.
        catalog.record(url, digest, path, kind, reason, parent=_parent_url(url),
                       images=images, relevance="gereksiz")
        log.info("  gereksiz %s (içerik atıldı, sadece URL+hash tutuluyor)", url)
        _ledger().record("page", url, status=status, decision="excluded_gereksiz",
                         reason=reason, images=len(images))
        return status

    # engine.write_doc: sayfa metni frontmatter formatı BİREBİR aynı
    engine.write_doc(url, title, desc, kind, body)
    # HAM metin, LLM-temizlenmişten AYRI bir yerde saklanır (PDF'in ham binary
    # + _pdf_clean/ ayrımıyla TUTARLI) — değişim tespiti zaten raw_body'den
    # yapılıyor, bu da o karşılaştırmayı geriye dönük denetlenebilir kılar ve
    # ihtiyaç halinde metni LLM'siz kaynağından yeniden üretmeyi sağlar.
    try:
        raw_path = engine.OUT / "_raw" / path.relative_to(engine.OUT)
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(raw_body.strip() + "\n", encoding="utf-8")
    except Exception as _exc:
        # AŞAMA 4'ÜN TEK GİRDİSİ BU DOSYA: inline temizleme kapalıyken
        # (varsayılan) `_raw/` yazılamazsa o sayfa bir daha ASLA
        # temizlenemez/etiketlenemez — sessiz veri kaybı olurdu. Bu yüzden
        # katalogda "işlendi" diye işaretlenmesin: hata FAIL sayılır, sayfa
        # failures.txt'e düşer ve sonraki koşuda tekrar denenir.
        log.warning("  (HAM METİN KAYDEDİLEMEDİ) %s: %s", url, _exc)
        if not _INLINE_CLEAN:
            _ledger().record("page", url, status="FAIL", decision="skip",
                             reason=f"_raw yazılamadı: {_exc}")
            return "FAIL"
    # Gemma'nın bu güncelleme sırasında gördüğü kampanya tarihini VE gerekli/
    # gereksiz etiketini frontmatter'a işle (ayrı bir geçiş gerekmez; relevance
    # cleaned!=None olduğu sürece HER ZAMAN dolu olduğu için bu blok artık
    # pratikte her başarılı temizlemede tetiklenir).
    if cleaned is not None and (page_dates.get("start") or page_dates.get("end")
                                or page_dates.get("relevance")):
        try:
            from pathlib import Path as _P
            _pth = _P(path)
            _front, _body = pages._split_front(_pth.read_text(encoding="utf-8"))
            _front = pages._set_front_dates(_front, page_dates)
            _pth.write_text((_front + "\n\n" + _body).rstrip() + "\n", encoding="utf-8")
        except Exception as _exc:
            log.warning("  (tarih/etiket frontmatter atlandı: %s)", _exc)
    catalog.record(url, digest, path, kind, reason, parent=_parent_url(url),
                   images=images, relevance=relevance)   # metin+görsel değişti -> yeniden kaydet
    log.info("  %-5s %s", status, url)
    _ledger().record("page", url, status=status, decision="saved",
                     reason=reason, images=len(images))

    if embed is not None and store_vec is not None:
        try:
            from langchain_core.documents import Document
            store_vec.add_documents([Document(
                page_content=body,
                metadata={"url": url, "title": title, "bank": engine.CONFIG["NAME"],
                          "kind": kind, "status": status},
            )])
        except Exception as exc:  # host yoksa boru hattını durdurma
            log.warning("  (qdrant atlandı: %s)", exc)

    return status
