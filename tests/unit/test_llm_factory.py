"""LLM factory: role resolution, provider selection, and error messages.

No network — nothing here calls a model.
"""

import pytest

from config.settings import settings
from config import tunnel
from llm import get_llm, list_models, resolve_model_key
from llm.providers import MODELS, get_provider
from llm.providers.vllm_provider import THINKING_OFF, TunnelAwareChatOpenAI

pytestmark = pytest.mark.unit


def test_roles_resolve_through_settings():
    assert resolve_model_key("extractor") == settings.EXTRACTOR_MODEL
    assert resolve_model_key("chat") == settings.CHAT_MODEL
    assert resolve_model_key(None) == settings.DEFAULT_MODEL


def test_model_keys_pass_through():
    assert resolve_model_key("gemma") == "gemma"


def test_every_model_is_served_by_a_provider():
    for key in MODELS:
        assert get_provider(key).provider_name == "vllm"


def test_unknown_model_lists_the_valid_ones():
    with pytest.raises(ValueError) as exc:
        get_provider("llama")
    message = str(exc.value)
    assert "llama" in message
    for key in MODELS:
        assert key in message


def test_each_model_gets_its_own_route():
    """A shared base URL would silently send every request to one model."""
    routes = {key: get_llm(key).openai_api_base for key in MODELS}
    assert len(set(routes.values())) == len(MODELS)
    for key, base in routes.items():
        assert str(base).endswith(MODELS[key].route)


def test_factory_returns_a_tunnel_aware_model_with_sdk_retries_disabled():
    model = get_llm("gemma")
    assert isinstance(model, TunnelAwareChatOpenAI)
    assert model.max_retries == 0


def test_tunnel_is_checked_only_after_a_model_error(monkeypatch):
    model = get_llm("gemma")
    refreshed: list[bool] = []
    monkeypatch.setattr(tunnel, "refresh_if_needed", lambda: refreshed.append(True) or False)
    monkeypatch.setattr(model, "_refresh_clients", lambda: None)

    assert model._retry_after_tunnel_refresh(lambda: "ok") == "ok"
    assert refreshed == []

    attempts = 0

    def fails_once():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("stale tunnel")
        return "recovered"

    assert model._retry_after_tunnel_refresh(fails_once) == "recovered"
    assert attempts == 2
    assert refreshed == [True]


def test_tunnel_refresh_strips_whitespace_and_changes_only_when_needed(monkeypatch):
    before = settings.VLLM_BASE_URL

    class _Response:
        text = "  https://new-tunnel.example/\n"

        def raise_for_status(self):
            pass

    try:
        monkeypatch.setattr(tunnel.httpx, "get", lambda *args, **kwargs: _Response())
        assert tunnel.refresh_if_needed() is True
        assert settings.VLLM_BASE_URL == "https://new-tunnel.example"
        assert tunnel.refresh_if_needed() is False
    finally:
        settings.VLLM_BASE_URL = before


def test_thinking_is_disabled_where_it_pollutes_content():
    """qwen reasons into `content` by default; that must be off unless asked."""
    assert MODELS["qwen"].thinking_by_default is True
    assert get_llm("qwen").extra_body == THINKING_OFF
    assert get_llm("qwen", thinking=True).extra_body is None


def test_models_without_default_thinking_are_left_alone():
    assert get_llm("gemma").extra_body is None


def test_max_tokens_below_the_model_minimum_is_refused():
    """gpt-oss returns empty content, not an error, below ~300 tokens."""
    with pytest.raises(ValueError, match="empty content"):
        get_llm("gpt", max_tokens=100)

    assert get_llm("gpt", max_tokens=500).max_tokens == 500


def test_explicit_arguments_beat_settings():
    assert get_llm("gemma", temperature=0.9).temperature == 0.9
    assert get_llm("gemma").temperature == settings.LLM_TEMPERATURE


def test_list_models_reports_measured_capabilities():
    models = list_models()
    assert set(models) == set(MODELS)
    assert all(spec.context_window == 65536 for spec in models.values())
