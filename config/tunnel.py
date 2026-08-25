"""Refresh the vLLM reverse-proxy URL only after a failed request."""

from __future__ import annotations

import time
import logging
from threading import Lock

import httpx

from .settings import settings

logger = logging.getLogger(__name__)

_GIST_URL = (
    "https://gist.githubusercontent.com/dijitalkariyermerkezi/"
    "e91ef0ddbc60b3e241c6b3e602cad5c8/raw/tunnel_url.txt"
)
_refresh_lock = Lock()


def is_tunnel_failure(exc: BaseException) -> bool:
    """Whether retrying against a newly published tunnel can help.

    Do not fetch the Gist for validation/authentication errors: those are
    application failures, not evidence that the reverse-proxy URL rotated. A
    stale tunnel is observed as 404; reverse-proxy outages use the listed
    gateway statuses or a transport failure.

    Lives here rather than on the chat model because it is about the *tunnel*,
    not about chat: everything reached through this reverse proxy fails the same
    way when it rotates, and the embeddings client needs the identical answer.
    Two copies of this list would drift, and the half that fell behind would
    stop recovering.
    """
    # The OpenAI SDK wraps httpx connection/reset/timeout exceptions in its own
    # APIConnectionError hierarchy. Looking only for httpx exceptions therefore
    # misses the exact error LangChain gives us and skips tunnel refresh
    # entirely. Walk the exception chain as well so this remains correct if
    # another client wrapper adds one more layer later.
    from openai import APIConnectionError, APITimeoutError

    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(
            current,
            (
                APIConnectionError,
                APITimeoutError,
                ConnectionError,
                TimeoutError,
                OSError,
                httpx.TransportError,
            ),
        ):
            return True
        if getattr(current, "status_code", None) in {404, 502, 503, 504}:
            return True
        current = current.__cause__ or current.__context__
    return False


def refresh_after_failure(failed_base_url: str | None = None) -> bool:
    """Refresh a failed tunnel URL once and share the result with other callers.

    This function is intentionally not called during normal model construction.
    The timestamp defeats GitHub's CDN cache, but it is paid only after an LLM
    request has already failed.  ``failed_base_url`` prevents a thundering herd:
    if another request has already replaced that URL, this caller adopts the
    replacement without fetching the Gist a second time.
    """
    failed = failed_base_url.rstrip("/") if failed_base_url else None
    with _refresh_lock:
        configured = settings.VLLM_BASE_URL.rstrip("/")
        if failed is not None and configured != failed:
            logger.info(
                "Tunnel already refreshed by another request old=%s current=%s",
                failed,
                configured,
            )
            return True

        response = httpx.get(
            f"{_GIST_URL}?t={int(time.time())}", timeout=10, follow_redirects=True
        )
        response.raise_for_status()
        current = response.text.strip().rstrip("/")
        if not current.startswith(("http://", "https://")):
            raise ValueError("Tunnel gist did not contain a valid HTTP(S) URL.")
        if current == configured:
            logger.info("Tunnel Gist URL is unchanged url=%s", current)
            return False
        settings.VLLM_BASE_URL = current
        logger.info("Tunnel URL updated old=%s new=%s", configured, current)
        return True


def refresh_if_needed() -> bool:
    """Backward-compatible alias for callers without a failed URL."""
    return refresh_after_failure()
