"""RENDER — JS ile render edilen (SPA) bankalar için başsız tarayıcı katmanı.

Statik indirici (httpx+trafilatura) JS-render sayfalarda boş içerik alır. Bu
modül Playwright/Chromium ile sayfayı gerçekten açıp JS'i çalıştırır, render
edilmiş HTML'i döner. Yalnızca --render verilen bankalarda devreye girer.

Entegrasyon: graph, aktif banka motorunun `fetch` fonksiyonunu bu modülün
render-tabanlı fetch'iyle DEĞİŞTİRİR (monkeypatch). Böylece frontier/store/policy
hiç değişmeden render edilmiş HTML üstünde çalışır. PDF istekleri yine binary
(httpx) yoluyla iner — render edilmez.
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("dataprep.crawl.render")

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


class _ShimResponse:
    """httpx.Response'un crawl kodunun kullandığı arayüzünü taklit eder."""

    def __init__(self, url: str, html: str):
        self.url = url
        self.text = html
        self.content = html.encode("utf-8", "ignore")
        self.status_code = 200
        self.headers = {"content-type": "text/html; charset=utf-8"}

    def raise_for_status(self):
        return None


class RenderClient:
    """Tek Chromium örneği; sayfaları sırayla render eder (kilitle)."""

    def __init__(self, timeout_ms: int = 25000, settle_ms: int = 1500):
        self.timeout_ms = timeout_ms
        self.settle_ms = settle_ms
        self._lock = asyncio.Lock()
        self._pw = self.browser = self.ctx = None

    async def start(self):
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        self.browser = await self._pw.chromium.launch(headless=True)
        self.ctx = await self.browser.new_context(user_agent=_UA, locale="tr-TR")
        log.info("render: Chromium başlatıldı")

    async def stop(self):
        try:
            if self.ctx:
                await self.ctx.close()
            if self.browser:
                await self.browser.close()
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass

    async def get_html(self, url: str) -> str | None:
        async with self._lock:
            page = await self.ctx.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                await page.wait_for_timeout(self.settle_ms)   # JS'in render'ı için bekle
                return await page.content()
            except Exception as exc:
                log.warning("  render fail %s (%s)", url, type(exc).__name__)
                return None
            finally:
                await page.close()


def install(engine, render_client: RenderClient):
    """engine.fetch'i render-tabanlı sürümle değiştir (PDF'ler yine httpx binary)."""
    orig_fetch = engine.fetch

    async def render_fetch(client, url, retries: int = 3):
        # PDF ve diğer binary'ler: render etme, orijinal httpx ile indir
        if engine.is_pdf(url):
            return await orig_fetch(client, url, retries)
        for attempt in range(1, retries + 1):
            html = await render_client.get_html(url)
            if html:
                return _ShimResponse(url, html), None
            if attempt < retries:
                await asyncio.sleep(1.0 * attempt)
        return None, "render başarısız"

    engine.fetch = render_fetch
    return orig_fetch
