"""TOM Katılım'a özel düzeltmeler.

Tespit:
  * İçerik uzantısız yolda yaşıyor (/urunlerimiz, /vadeli-hesap ...).
  * Aynı içeriğin '.html' biçimi (/urunlerimiz.html) SUNUCUDA YOK ama
    HTTP 200 + gövdede "Server Error 404" (soft-404) döndürüyor.
  => .html biçimini uzantısız kanonik biçime çevir + soft-404'leri ele.
"""
from __future__ import annotations


def rewrite_url(url: str) -> str:
    # ".html" ölü biçim -> çalışan uzantısız biçim (ana sayfa /index hariç değil, o da çalışıyor)
    if url.lower().endswith(".html"):
        return url[:-5]
    return url


def soft_404(text: str) -> bool:
    head = text[:2000]
    return "Server Error 404" in head or "File or directory not found" in head
