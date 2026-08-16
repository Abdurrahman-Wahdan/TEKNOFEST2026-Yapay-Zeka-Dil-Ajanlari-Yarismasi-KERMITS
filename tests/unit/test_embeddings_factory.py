"""Embedding provider selection. No model is loaded here."""

import pytest

from embeddings.providers import PROVIDERS, get_provider

pytestmark = pytest.mark.unit


def test_providers_are_registered_remote_first():
    """Remote never auto-matches (opt-in only), so it must come first --
    otherwise it would shadow local's fallback-matches-anything behaviour."""
    assert [p.provider_name for p in PROVIDERS] == ["remote", "local"]


def test_local_matches_anything_as_the_fallback():
    """Local is last in the list, so it must accept any unclaimed model."""
    assert get_provider("BAAI/bge-m3").provider_name == "local"
    assert get_provider("some/other-model").provider_name == "local"


def test_remote_is_opt_in_only():
    """Remote never wins auto-detection; it only comes back when named."""
    assert get_provider("Qwen/Qwen3-Embedding-0.6B").provider_name == "local"
    assert get_provider("Qwen/Qwen3-Embedding-0.6B", provider_name="remote").provider_name == "remote"


def test_unknown_provider_lists_the_valid_ones():
    with pytest.raises(ValueError) as exc:
        get_provider("BAAI/bge-m3", provider_name="gemini")
    assert "gemini" in str(exc.value)
    assert "local" in str(exc.value)
    assert "remote" in str(exc.value)
