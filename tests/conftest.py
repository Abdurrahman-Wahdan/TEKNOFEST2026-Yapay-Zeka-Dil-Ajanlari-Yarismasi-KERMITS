"""Shared fixtures."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def clear_caches():
    """Empty every module-level cache around each test.

    The factories cache clients and loaded models on purpose; without this,
    state leaks between tests and failures depend on ordering.
    """
    from banks import clear_catalogue_cache, clear_http_cache
    from embeddings import clear_embedding_cache
    from vector_stores import clear_client_cache

    def clear():
        clear_embedding_cache()
        clear_client_cache()
        clear_catalogue_cache()
        clear_http_cache()

    clear()
    yield
    clear()


@pytest.fixture
def qdrant_available() -> bool:
    """Whether a local Qdrant is reachable."""
    import httpx

    try:
        return httpx.get("http://localhost:6333/collections", timeout=3).status_code == 200
    except Exception:  # noqa: BLE001 - any failure means unavailable
        return False
