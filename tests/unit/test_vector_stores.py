"""Vector store provider selection and client caching. No Qdrant needed."""

import pytest

from vector_stores import clear_client_cache
from vector_stores.providers import PROVIDERS, get_provider

pytestmark = pytest.mark.unit


def test_qdrant_is_registered():
    assert [p.provider_name for p in PROVIDERS] == ["qdrant"]


def test_provider_lookup_is_case_insensitive():
    assert get_provider("QDRANT").provider_name == "qdrant"


def test_unknown_store_lists_the_valid_ones():
    with pytest.raises(ValueError) as exc:
        get_provider("chroma")
    assert "chroma" in str(exc.value)
    assert "qdrant" in str(exc.value)


def test_client_is_reused_for_the_same_connection():
    """A client per call would leak sockets and re-handshake every time."""
    from vector_stores.client import get_qdrant_client

    first = get_qdrant_client()
    second = get_qdrant_client()
    assert first is second

    clear_client_cache()
    assert get_qdrant_client() is not first


def test_missing_url_is_reported_clearly():
    from vector_stores.client import get_qdrant_client

    with pytest.raises(ValueError, match="QDRANT_URL"):
        get_qdrant_client(url="")
