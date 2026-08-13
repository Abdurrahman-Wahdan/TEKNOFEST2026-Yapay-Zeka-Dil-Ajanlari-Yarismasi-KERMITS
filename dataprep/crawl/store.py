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
import re
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

from dataprep.crawl.bank import engine
from dataprep.ledger import Ledger


def _ledger() -> Ledger:
    """Aktif bankanın işleme günlüğü (data/<bank>_site/_processing_log.jsonl)."""
    return Ledger(engine.OUT / "_processing_log.jsonl")

log = logging.getLogger("dataprep.crawl.store")


class Catalog:
    """URL -> {hash, path, kind, first_seen, last_seen, reason}. Değişim takibi."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, dict] = {}
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))

    def status(self, url: str, digest: str) -> str:
        prev = self.data.get(url)
        if prev is None:
            return "NEW"
        return "SAME" if prev.get("hash") == digest else "CHANGED"

    def touch(self, url: str) -> None:
        """Değişmemiş (SAME) sayfayı 'bugün görüldü' diye işaretle.

        Böylece yarın: last_seen<bugün olan kayıtlar = artık sitede yok (silinmiş)."""
        if url in self.data:
            self.data[url]["last_seen"] = date.today().isoformat()

    def record(self, url: str, digest: str, path: Path, kind: str, reason: str,
               parent: str = "", source_page: str = "", size_bytes: int = 0,
               images: list | None = None) -> None:
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
        self.data[url] = rec

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1),
                             encoding="utf-8")


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
# dedup+VLM eler; sadece anlamsızları burada atıyoruz)
_IMG_SKIP = re.compile(r"transparent|spacer|1x1|pixel|blank|\.svg(\?|$)", re.I)


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
    kind, title, desc, body = built

    # LLM İÇERİK TEMİZLEME (kural yok; görsel/PDF ile tutarlı): nav/footer/duyuru gibi
    # tekrarlayan site öğelerini at, sayfaya özgü gerçek içeriği bırak. LLM yoksa ham kalır.
    from dataprep import pages
    cleaned = pages.clean_page(body, url)
    if cleaned is not None:
        body = cleaned

    # sayfadaki PDF'leri binary indir — HAM HTML'den (trafilatura'nın attıkları dahil)
    for doc_url in _doc_links(r.text, url):
        await _download_pdf(client, doc_url, catalog, f"linked from {url}",
                            source_page=url)

    images = _content_img_urls(r.text, url)     # sayfadaki içerik görselleri (item-VLM)
    digest = _page_signature(body, images)      # imza = METİN + GÖRSEL
    status = catalog.status(url, digest)
    if status == "SAME":                        # hem metin hem görsel değişmemiş -> alma
        catalog.touch(url)                      # bugün görüldü (silinmedi)
        log.info("  SAME  %s", url)
        _ledger().record("page", url, status="SAME", decision="skip",
                         reason="metin+görsel değişmedi", images=len(images))
        return "SAME"

    # engine.write_doc: sayfa metni frontmatter formatı BİREBİR aynı
    engine.write_doc(url, title, desc, kind, body)
    path = engine.url_to_path(url)
    catalog.record(url, digest, path, kind, reason, parent=_parent_url(url),
                   images=images)               # metin+görsel değişti -> yeniden kaydet
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
