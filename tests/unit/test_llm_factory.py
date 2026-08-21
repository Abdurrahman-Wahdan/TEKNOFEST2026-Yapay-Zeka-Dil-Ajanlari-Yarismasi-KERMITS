"""LLM factory: role resolution, provider selection, and error messages.

No network — nothing here calls a model.
"""

import pytest
import httpx
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

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


def test_recovery_replaces_the_failed_http_client():
    model = get_llm("gemma")
    old_root_client = model.root_client

    model._refresh_clients()

    assert model.root_client is not old_root_client


def test_empty_stream_bootstrap_does_not_disable_retry(monkeypatch):
    model = get_llm("gemma")
    empty = ChatGenerationChunk(message=AIMessageChunk(content=""))
    answer = ChatGenerationChunk(message=AIMessageChunk(content="recovered"))
    streams = iter(
        [
            iter([empty]),
            iter([answer]),
        ]
    )

    def open_stream(*args, **kwargs):
        stream = next(streams)
        for chunk in stream:
            yield chunk
        if chunk is empty:
            raise httpx.ReadTimeout("empty stream timed out")

    monkeypatch.setattr(model, "_open_stream", open_stream)
    monkeypatch.setattr(model, "_recover_tunnel", lambda exc: None)
    monkeypatch.setattr(model, "_wait_before_retry", lambda delay, deadline: delay * 2)

    chunks = list(model._stream([]))

    assert [chunk.message.content for chunk in chunks] == ["", "recovered"]


def test_text_or_tool_call_disables_stream_replay():
    text = ChatGenerationChunk(message=AIMessageChunk(content="partial"))
    tool = ChatGenerationChunk(
        message=AIMessageChunk(
            content="",
            tool_call_chunks=[{"name": "bank", "args": "{}", "id": "1", "index": 0}],
        )
    )

    assert TunnelAwareChatOpenAI._chunk_has_meaningful_output(text) is True
    assert TunnelAwareChatOpenAI._chunk_has_meaningful_output(tool) is True


def test_tunnel_is_checked_only_after_a_model_error(monkeypatch):
    model = get_llm("gemma")
    refreshed: list[bool] = []
    monkeypatch.setattr(
        tunnel,
        "refresh_after_failure",
        lambda failed_url: refreshed.append(failed_url) or False,
    )
    monkeypatch.setattr(model, "_refresh_clients", lambda: None)
    monkeypatch.setattr("llm.providers.vllm_provider.time.sleep", lambda _: None)

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
    assert refreshed == [settings.VLLM_BASE_URL.rstrip("/")]


def test_retry_backoff_doubles_then_caps_at_sixty_seconds(monkeypatch):
    model = get_llm("gemma")
    waits: list[float] = []
    refreshes: list[str] = []
    before_window = settings.LLM_TIMEOUT
    before_cap = settings.LLM_RETRY_MAX_DELAY_SECONDS
    try:
        settings.LLM_TIMEOUT = 1800
        settings.LLM_RETRY_MAX_DELAY_SECONDS = 60
        monkeypatch.setattr(
            tunnel,
            "refresh_after_failure",
            lambda failed_url: refreshes.append(failed_url) or False,
        )
        monkeypatch.setattr(model, "_refresh_clients", lambda: None)
        monkeypatch.setattr(
            "llm.providers.vllm_provider.time.sleep", lambda seconds: waits.append(seconds)
        )

        attempts = 0

        def fails_then_recovers():
            nonlocal attempts
            attempts += 1
            if attempts <= 7:
                raise ConnectionError("tunnel unavailable")
            return "recovered"

        assert model._retry_after_tunnel_refresh(fails_then_recovers) == "recovered"
        assert waits == [1, 2, 4, 8, 16, 32, 60]
        assert len(refreshes) == 7
    finally:
        settings.LLM_TIMEOUT = before_window
        settings.LLM_RETRY_MAX_DELAY_SECONDS = before_cap


def test_non_tunnel_error_does_not_fetch_the_gist(monkeypatch):
    model = get_llm("gemma")
    refreshed: list[str] = []
    monkeypatch.setattr(
        tunnel,
        "refresh_after_failure",
        lambda failed_url: refreshed.append(failed_url) or False,
    )

    class BadRequest(Exception):
        status_code = 400

    with pytest.raises(BadRequest):
        model._retry_after_tunnel_refresh(lambda: (_ for _ in ()).throw(BadRequest()))

    assert refreshed == []


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


def test_tunnel_refresh_reuses_a_url_another_request_already_refreshed(monkeypatch):
    before = settings.VLLM_BASE_URL
    try:
        settings.VLLM_BASE_URL = "https://fresh-tunnel.example"
        called = False

        def unexpected_get(*args, **kwargs):
            nonlocal called
            called = True
            raise AssertionError("the Gist must not be fetched again")

        monkeypatch.setattr(tunnel.httpx, "get", unexpected_get)
        assert tunnel.refresh_after_failure("https://stale-tunnel.example") is True
        assert called is False
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
