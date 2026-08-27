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
       "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

_STEALTH_JS = """
// Anti-bot & stealth bypass
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['tr-TR', 'tr', 'en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });

// Chrome runtime mock
window.chrome = {
    runtime: {},
    app: { isInstalled: false },
    csi: () => {},
    loadTimes: () => {}
};

// WebGL Vendor & Renderer mock
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(parameter) {
    if (parameter === 37445) return 'Google Inc. (Apple)';
    if (parameter === 37446) return 'ANGLE (Apple, Apple M1, OpenGL 4.1)';
    return getParameter.apply(this, arguments);
};
"""


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
        self.browser = await self._pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-infobars",
                "--disable-dev-shm-usage",
            ]
        )
        self.ctx = await self.browser.new_context(
            user_agent=_UA,
            locale="tr-TR",
            timezone_id="Europe/Istanbul",
            viewport={"width": 1440, "height": 900},
            extra_http_headers={
                "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
                "Sec-Ch-Ua": '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"macOS"',
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            }
        )
        await self.ctx.add_init_script(_STEALTH_JS)
        log.info("render: Chromium + Stealth başlatıldı")

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
                # İnsan taklidi: mikro bekleme ve hafif sayfa kaydırma (scroll)
                import random
                await asyncio.sleep(random.uniform(0.3, 0.7))
                await page.evaluate("window.scrollBy({top: 350, behavior: 'smooth'})")
                await page.wait_for_timeout(self.settle_ms)
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
