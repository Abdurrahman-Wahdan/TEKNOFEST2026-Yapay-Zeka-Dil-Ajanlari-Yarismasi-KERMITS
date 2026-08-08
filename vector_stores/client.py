"""Shared Qdrant client.

One client per distinct connection, reused across the process. Creating a
client per call leaks sockets and costs a handshake each time.
"""

import logging
import threading

from qdrant_client import QdrantClient

from config.settings import settings

logger = logging.getLogger(__name__)

_CACHE: dict[tuple, QdrantClient] = {}
_LOCK = threading.Lock()


def get_qdrant_client(url: str | None = None, timeout: int | None = None) -> QdrantClient:
    """Get or create the client for a connection.

    Synchronous on purpose: langchain-qdrant expects a sync client.

    Raises:
        ValueError: if no URL is configured.
    """
    # `is None` rather than `or`: an explicitly empty url is a configuration
    # error worth reporting, not something to silently replace with the default.
    url = settings.QDRANT_URL if url is None else url
    timeout = settings.QDRANT_TIMEOUT if timeout is None else timeout

    if not url:
        raise ValueError("Qdrant URL is not set. Set QDRANT_URL in .env or pass url.")

    key = (url, timeout)

    # Fast path without the lock for the common case.
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    # Double-check under the lock so concurrent callers share one client.
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            return cached
        logger.info("Connecting to Qdrant at %s", url)
        # check_compatibility=False keeps construction offline; without it the
        # client calls the server immediately, so building one requires Qdrant
        # to be up even when nothing is queried.
        client = QdrantClient(url=url, timeout=timeout, check_compatibility=False)
        _CACHE[key] = client
        return client


def clear_client_cache() -> None:
    """Close and drop every cached client."""
    with _LOCK:
        for client in _CACHE.values():
            try:
                client.close()
            except Exception:  # noqa: BLE001 - closing must never raise
                logger.debug("Ignoring error while closing Qdrant client", exc_info=True)
        count = len(_CACHE)
        _CACHE.clear()
    logger.info("Closed %d Qdrant client(s)", count)
