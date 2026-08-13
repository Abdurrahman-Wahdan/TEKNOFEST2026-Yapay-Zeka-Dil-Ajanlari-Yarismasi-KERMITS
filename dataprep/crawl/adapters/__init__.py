"""Banka-BAŞINA spesifik düzeltmeler (adaptörler).

Felsefe: tek "her siteye uyan" genel kod yerine, her bankanın kendi tuhaflığı
için kendi dosyası olsun (crawl/adapters/<slug>.py). Bir bankanın dosyası yoksa
varsayılan (hiçbir şey yapmayan) adaptör kullanılır.

Bir adaptör şu isteğe bağlı kancaları tanımlayabilir:
  * rewrite_url(url) -> url   : URL'yi çalışan kanonik biçime çevir
                               (ör. tombank'ta ölü '.html' -> uzantısız)
  * soft_404(text) -> bool   : HTTP 200 dönen ama gövdesi '404' olan sahte
                               sayfaları yakala (kaydetme/keşfe alma)

install(engine, slug): aktif banka motorunun clean_url + fetch'ini adaptörün
kancalarıyla sarar. Böylece hem keşif hem indirme otomatik faydalanır; crawl
çekirdeği (frontier/store/policy) hiç değişmez.
"""
from __future__ import annotations

import importlib
import logging

log = logging.getLogger("dataprep.crawl.adapters")


class _Default:
    slug = "_default"

    @staticmethod
    def rewrite_url(url: str) -> str:
        return url

    @staticmethod
    def soft_404(text: str) -> bool:
        return False


def get(slug: str):
    try:
        return importlib.import_module(f"crawl.adapters.{slug}")
    except ModuleNotFoundError:
        return _Default


def install(engine, slug: str):
    """engine.clean_url + engine.fetch'i bankaya özel kancalarla sar."""
    ad = get(slug)
    has_rewrite = hasattr(ad, "rewrite_url")
    has_soft404 = hasattr(ad, "soft_404")
    if not (has_rewrite or has_soft404):
        return ad                       # bu banka için özel kod yok -> dokunma

    if has_rewrite:
        orig_clean = engine.clean_url
        def clean_url(u: str) -> str:
            return ad.rewrite_url(orig_clean(u))
        engine.clean_url = clean_url

    if has_soft404:
        orig_fetch = engine.fetch
        async def fetch(client, url, retries: int = 3):
            r, err = await orig_fetch(client, url, retries)
            if r is not None and "html" in r.headers.get("content-type", "").lower():
                try:
                    if ad.soft_404(r.text):
                        return None, "soft-404"
                except Exception:
                    pass
            return r, err
        engine.fetch = fetch

    log.info("adapter: %s özel düzeltmeleri aktif", slug)
    return ad
