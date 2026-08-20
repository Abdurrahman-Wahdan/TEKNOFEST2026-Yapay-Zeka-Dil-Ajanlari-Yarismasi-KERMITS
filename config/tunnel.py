"""Refresh the vLLM reverse-proxy URL only after a failed request."""

from __future__ import annotations

import time

import httpx

from .settings import settings

_GIST_URL = (
    "https://gist.githubusercontent.com/dijitalkariyermerkezi/"
    "e91ef0ddbc60b3e241c6b3e602cad5c8/raw/tunnel_url.txt"
)


def refresh_if_needed() -> bool:
    """Read the current tunnel URL and update settings when it changed.

    This function is intentionally not called during normal model construction.
    The timestamp defeats GitHub's CDN cache, but it is paid only after an LLM
    request has already failed.
    """
    response = httpx.get(
        f"{_GIST_URL}?t={int(time.time())}", timeout=10, follow_redirects=True
    )
    response.raise_for_status()
    current = response.text.strip().rstrip("/")
    if not current.startswith(("http://", "https://")):
        raise ValueError("Tunnel gist did not contain a valid HTTP(S) URL.")
    if current == settings.VLLM_BASE_URL.rstrip("/"):
        return False
    settings.VLLM_BASE_URL = current
    return True
