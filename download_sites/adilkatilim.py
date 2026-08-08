#!/usr/bin/env python3
"""Adil Katılım (https://www.adilkatilim.com.tr) için RAG amaçlı, Türkçe-only site indirici.

Profesyonel, kendi kendine yeten tek-dosya crawler. Ayrıntı için aşağıdaki
ENGINE bölümünün başındaki kullanım notlarına bakın.
"""
SLUG = 'adilkatilim'
CONFIG = {'NAME': 'Adil Katılım', 'BASE': 'https://www.adilkatilim.com.tr', 'ROOT_DOMAIN': 'adilkatilim.com.tr', 'MODE': 'recursive', 'SITEMAPS': [], 'INCLUDE_PREFIXES': [], 'EXTRA_SEEDS': []}

# ---------------------------------------------------------------------------
#  RAG CRAWLER MOTORU  (tüm banka dosyalarında ortak, kendi kendine yeter)
#
#  Gereksinimler:  pip install httpx trafilatura pypdf
#
#  Kullanım:
#     python <banka>.py                    # tüm siteyi indir
#     python <banka>.py --limit 20         # hızlı test (ilk 20 sayfa)
#     python <banka>.py --concurrency 8 --delay 0.1
#
#  Üretilen çıktı (./<banka>_site/):
#     - URL yapısını yansıtan .md ağacı (RAG'e hazır, YAML frontmatter'lı)
#     - urls.txt      : indirilen tüm sayfa URL'leri
#     - INDEX.md      : tıklanabilir içindekiler
#     - failures.txt  : birkaç denemeden sonra vazgeçilen URL'ler + neden
# ---------------------------------------------------------------------------
import argparse
import asyncio
import io
import logging
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

try:
    import httpx
    import trafilatura
except ImportError as exc:  # pragma: no cover
    sys.exit(f"Eksik bağımlılık: {exc}. Kurulum: pip install httpx trafilatura pypdf")

try:
    import pypdf
    HAVE_PDF = True
except ImportError:
    HAVE_PDF = False

# --- sabitler --------------------------------------------------------------
EXCLUDE_LANGS = {"en", "ar", "de", "ru", "fr", "es", "kmr", "az", "it", "nl",
                 "en-us", "en-gb", "ar-sa"}
ASSET_EXT = {".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
             ".ico", ".bmp", ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".mp3",
             ".avi", ".mov", ".zip", ".rar", ".gz", ".json", ".xml", ".rss",
             ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".css.map"}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; RAGCrawler/1.0; +educational)",
    "Accept-Language": "tr-TR,tr;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

log = logging.getLogger("crawler")

# --- global durum ----------------------------------------------------------
RESULTS: list[tuple[str, Path]] = []   # (url, kaydedilen dosya)
FAILED: list[tuple[str, str]] = []     # (url, hata)
_SAVED_URLS: set[str] = set()
# Aynı PDF birçok sayfada linkli olabilir -> URL bazında bir kez ayrıştır (bilgi kaybı yok).
_PDF_CACHE: dict[str, str] = {}
_PDF_LOCK = asyncio.Lock()


# --- yardımcılar -----------------------------------------------------------
def clean_url(u: str) -> str:
    u, _ = urldefrag(u)
    return u.rstrip("/") or u


def same_domain(u: str) -> bool:
    host = (urlparse(u).hostname or "").lower()   # portu (:443) yok sayar
    return host.endswith(CONFIG["ROOT_DOMAIN"])


def is_turkish(u: str) -> bool:
    """Sadece Türkçe içerik: dil öneki filtresi + varsa INCLUDE_PREFIXES."""
    path = urlparse(u).path
    inc = CONFIG.get("INCLUDE_PREFIXES") or []
    if inc and path not in ("", "/"):
        if not any(path.lower().startswith(p) for p in inc):
            return False
    segs = [s for s in path.split("/") if s]
    if segs and segs[0].lower() in EXCLUDE_LANGS:
        return False
    return True


def is_asset(u: str) -> bool:
    return Path(urlparse(u).path).suffix.lower() in ASSET_EXT


def is_pdf(u: str) -> bool:
    return urlparse(u).path.lower().endswith(".pdf")


def wanted(u: str) -> bool:
    if not u.lower().startswith("http"):
        return False
    if not same_domain(u) or not is_turkish(u):
        return False
    return is_pdf(u) or not is_asset(u)


def collapse_ws(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def yaml_escape(s: str) -> str:
    return (s or "").replace("\\", " ").replace('"', "'").replace("\n", " ").strip()


def frontmatter(url: str, title: str, desc: str, kind: str) -> str:
    lines = ["---", f'url: "{url}"']
    if title:
        lines.append(f'title: "{yaml_escape(title)}"')
    if desc:
        lines.append(f'description: "{yaml_escape(desc)}"')
    lines += [f'bank: "{CONFIG["NAME"]}"', f"type: {kind}",
              f"crawled_at: {date.today().isoformat()}", "---", ""]
    hdr = f"# {yaml_escape(title)}\n\n" if title else ""
    return "\n".join(lines) + "\n" + hdr


def url_to_path(url: str) -> Path:
    parts = [s for s in urlparse(url).path.split("/") if s]
    if not parts:
        return OUT / "index.md"
    *dirs, last = parts
    stem = Path(last).stem if Path(last).suffix else last
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", stem) or "index"
    return OUT.joinpath(*[re.sub(r"[^A-Za-z0-9._-]", "_", d) for d in dirs], stem + ".md")


def pdf_to_text(data: bytes) -> str:
    if not HAVE_PDF:
        return ""
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        return collapse_ws("\n\n".join(pg.extract_text() or "" for pg in reader.pages))
    except Exception:
        return ""


# --- ağ katmanı (retry'li) -------------------------------------------------
async def fetch(client: "httpx.AsyncClient", url: str, retries: int = 3):
    """Geçici hatalarda üstel geri çekilmeyle birkaç kez dener, sonra vazgeçer."""
    delay, last = 1.0, "bilinmeyen hata"
    for attempt in range(1, retries + 1):
        try:
            r = await client.get(url)
            if r.status_code in (400, 401, 403, 404, 410):
                return None, f"HTTP {r.status_code}"   # kalıcı: tekrar deneme
            r.raise_for_status()
            return r, None
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                await asyncio.sleep(delay)
                delay *= 2
    return None, last


# --- içerik çıkarımı + kayıt ----------------------------------------------
# NOT: trafilatura ve pypdf CPU-bound + senkron. Bunları asyncio.to_thread ile
# ayrı thread'e alıyoruz ki ağ indirmesi (I/O) ile çıkarım (CPU) ÖRTÜŞSÜN.
# Çıkarım mantığı birebir aynı -> hiçbir bilgi kaybı yok, sadece paralellik artar.
def _extract_markdown_sync(html: str, url: str) -> str | None:
    md = trafilatura.extract(
        html, url=url, output_format="markdown",
        include_links=True, include_images=False,
        include_tables=True, include_formatting=True, favor_recall=True,
    )
    if not md or len(md.strip()) < 40:
        return None
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md)   # görselleri at (RAG odağı: metin)
    return collapse_ws(md)


def _page_meta_sync(html: str) -> tuple[str, str]:
    meta = trafilatura.extract_metadata(html)
    if not meta:
        return "", ""
    return (meta.title or "").strip(), (meta.description or "").strip()


async def pdf_text_cached(client: "httpx.AsyncClient", url: str,
                          data: bytes | None = None) -> str:
    """PDF metnini URL bazında önbellekler: aynı PDF yalnızca BİR kez ayrıştırılır."""
    async with _PDF_LOCK:
        if url in _PDF_CACHE:
            return _PDF_CACHE[url]
    if data is None:
        pr, _ = await fetch(client, url, retries=2)
        data = pr.content if pr is not None else b""
    txt = await asyncio.to_thread(pdf_to_text, data) if data else ""
    async with _PDF_LOCK:
        _PDF_CACHE[url] = txt
    return txt


def write_doc(url: str, title: str, desc: str, kind: str, body: str) -> None:
    if url in _SAVED_URLS:
        return
    path = url_to_path(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(frontmatter(url, title, desc, kind) + body.strip() + "\n",
                    encoding="utf-8")
    _SAVED_URLS.add(url)
    RESULTS.append((url, path))


async def process_url(client: "httpx.AsyncClient", url: str) -> list[str]:
    """Tek URL: indir, kaydet, (recursive için) sayfadaki linkleri döndür."""
    r, err = await fetch(client, url)
    if r is None:
        FAILED.append((url, err))
        return []
    ctype = r.headers.get("content-type", "").lower()
    if is_pdf(url) or "application/pdf" in ctype:
        txt = await pdf_text_cached(client, url, data=r.content)
        if len(txt.strip()) >= 40:
            write_doc(url, Path(urlparse(url).path).name, "", "pdf", txt)
        return []
    if "html" not in ctype:
        return []

    md = await asyncio.to_thread(_extract_markdown_sync, r.text, url)
    if md:
        if HAVE_PDF:   # sayfa içindeki PDF'lerin metnini de göm (önbellekli)
            seen_pdf = set()
            for link in re.findall(r"\]\(([^)]+\.pdf[^)]*)\)", md, re.I):
                pdf_url = clean_url(urljoin(url, link.strip()))
                if pdf_url in seen_pdf or not same_domain(pdf_url):
                    continue
                seen_pdf.add(pdf_url)
                txt = await pdf_text_cached(client, pdf_url)
                if txt:
                    md += f"\n\n## Ek belge: {Path(urlparse(pdf_url).path).name}\n\n{txt}"
        title, desc = await asyncio.to_thread(_page_meta_sync, r.text)
        write_doc(url, title, desc, "page", md)

    return [clean_url(urljoin(url, h))
            for h in re.findall(r'href=["\']([^"\'#]+)["\']', r.text)]


# --- keşif: sitemap --------------------------------------------------------
async def discover_from_sitemaps(client: "httpx.AsyncClient") -> set[str]:
    urls: set[str] = set()
    queue = list(CONFIG.get("SITEMAPS") or [])
    # robots.txt'teki Sitemap satırlarını da ekle
    rob, _ = await fetch(client, CONFIG["BASE"].rstrip("/") + "/robots.txt", retries=2)
    if rob is not None:
        queue += re.findall(r"(?i)Sitemap:\s*(\S+)", rob.text)
    seen: set[str] = set()
    while queue:
        sm = queue.pop()
        if sm in seen:
            continue
        seen.add(sm)
        r, _ = await fetch(client, sm, retries=2)
        if r is None:
            continue
        for loc in re.findall(r"<loc>\s*(.*?)\s*</loc>", r.text, re.I | re.S):
            loc = loc.strip()
            # iç içe sitemap: yol .xml ile bitiyor (query'li olabilir: sitemap.xml?page=2)
            # veya URL 'sitemap' içeriyor
            path_l = urlparse(loc).path.lower()
            if path_l.endswith(".xml") or "sitemap" in loc.lower():
                if same_domain(loc) and loc not in seen:
                    queue.append(loc)
            else:
                cu = clean_url(loc)
                if wanted(cu):
                    urls.add(cu)
    return urls


# --- yürütücüler -----------------------------------------------------------
async def run_batch(client: "httpx.AsyncClient", urls: list[str]) -> None:
    sem = asyncio.Semaphore(ARGS.concurrency)

    async def worker(u: str) -> None:
        async with sem:
            await process_url(client, u)
            await asyncio.sleep(ARGS.delay)
            done = len(RESULTS) + len(FAILED)
            if done % 100 == 0:
                log.info("  %d / %d işlendi", done, len(urls))

    await asyncio.gather(*(worker(u) for u in urls))


async def run_recursive(client: "httpx.AsyncClient") -> None:
    """Sürekli worker-havuzu: ortak kuyruk, boru hattı hep dolu (batch beklemesi yok)."""
    seeds = [clean_url(CONFIG["BASE"])] + [clean_url(s) for s in CONFIG.get("EXTRA_SEEDS", [])]
    seen: set[str] = {s for s in seeds if wanted(s) or s == clean_url(CONFIG["BASE"])}
    cap = ARGS.limit or ARGS.max_pages   # --limit test için recursive'i de sınırlar
    queue: "asyncio.Queue[str]" = asyncio.Queue()
    for s in seen:
        queue.put_nowait(s)
    lock = asyncio.Lock()

    async def worker() -> None:
        while True:
            u = await queue.get()
            try:
                if len(_SAVED_URLS) < cap:
                    links = await process_url(client, u)
                    async with lock:
                        for link in links:
                            if link not in seen and wanted(link) and len(seen) < cap * 4:
                                seen.add(link)
                                queue.put_nowait(link)
                    if len(_SAVED_URLS) % 100 < ARGS.concurrency:
                        log.info("  %d sayfa kaydedildi (kuyruk: %d)",
                                 len(_SAVED_URLS), queue.qsize())
            finally:
                queue.task_done()
            await asyncio.sleep(ARGS.delay)

    workers = [asyncio.create_task(worker()) for _ in range(ARGS.concurrency)]
    await queue.join()
    for w in workers:
        w.cancel()


# --- çıktı -----------------------------------------------------------------
def finalize() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    saved = sorted(set(RESULTS), key=lambda x: x[0])
    (OUT / "urls.txt").write_text(
        "\n".join(u for u, _ in saved) + "\n", encoding="utf-8")

    lines = [f"# {CONFIG['NAME']} — Site İçeriği", "",
             f"Toplam {len(saved)} sayfa. Tarih: {date.today().isoformat()}", ""]
    for u, p in saved:
        lines.append(f"- [{u}]({p.relative_to(OUT)})")
    (OUT / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    if FAILED:
        fl = [f"{u}\t{err}" for u, err in sorted(set(FAILED))]
        (OUT / "failures.txt").write_text("\n".join(fl) + "\n", encoding="utf-8")

    log.info("BİTTİ: %d sayfa kaydedildi, %d başarısız -> %s",
             len(saved), len(set(FAILED)), OUT)


async def crawl() -> None:
    limits = httpx.Limits(max_connections=ARGS.concurrency + 4)
    async with httpx.AsyncClient(
        headers=HEADERS, timeout=ARGS.timeout, follow_redirects=True, limits=limits
    ) as client:
        urls: set[str] = set()
        if CONFIG["MODE"] in ("sitemap", "auto"):
            log.info("sitemap keşfi...")
            urls = await discover_from_sitemaps(client)
            log.info("sitemap'ten %d Türkçe URL", len(urls))

        if urls:
            ordered = sorted(urls)
            if ARGS.limit:
                ordered = ordered[:ARGS.limit]
            await run_batch(client, ordered)
        elif CONFIG["MODE"] in ("recursive", "auto"):
            log.info("sitemap yok/boş -> recursive tarama başlıyor")
            await run_recursive(client)
        else:
            log.warning("keşfedilecek URL yok.")
    finalize()


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=f"{CONFIG['NAME']} RAG crawler")
    ap.add_argument("--out", default=None, help="çıktı klasörü (varsayılan: <banka>_site)")
    ap.add_argument("--concurrency", type=int, default=12)
    ap.add_argument("--delay", type=float, default=0.05, help="istekler arası bekleme (sn)")
    ap.add_argument("--timeout", type=float, default=40.0)
    ap.add_argument("--max-pages", type=int, default=8000, help="recursive mod üst sınırı")
    ap.add_argument("--limit", type=int, default=0, help="test için ilk N sayfa")
    return ap.parse_args()


def main() -> None:
    global ARGS, OUT
    ARGS = parse_args()
    OUT = Path(ARGS.out) if ARGS.out else Path(__file__).parent / f"{SLUG}_site"
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    log.info("=== %s taranıyor (%s) ===", CONFIG["NAME"], CONFIG["BASE"])
    try:
        asyncio.run(crawl())
    except KeyboardInterrupt:
        log.warning("kullanıcı iptali — ara sonuçlar yazılıyor")
        finalize()


if __name__ == "__main__":
    main()
