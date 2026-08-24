"""LLM factory: role resolution, provider selection, and error messages.

No network — nothing here calls a model.
"""

import pytest
import httpx
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from openai import APIConnectionError, APITimeoutError, NotFoundError

from config.settings import settings
from config import tunnel
from llm import get_llm, list_models, resolve_model_key
from llm.providers import MODELS, get_provider
from llm.providers.vllm_provider import THINKING_OFF, THINKING_ON, TunnelAwareChatOpenAI

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


def test_openai_wrapped_connection_failures_trigger_tunnel_recovery():
    """LangChain surfaces OpenAI errors, not the wrapped httpx exception."""
    request = httpx.Request("POST", "https://stale-tunnel.example/gemma/v1/chat/completions")

    assert TunnelAwareChatOpenAI._is_tunnel_failure(
        APIConnectionError(request=request)
    )
    assert TunnelAwareChatOpenAI._is_tunnel_failure(
        APITimeoutError(request=request)
    )


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
    # Not one shared number. gemma is served with a 131072 window -- read back
    # from the host's own /v1/models, which is the only place the truth lives:
    # this constant said 65536 and was half the real figure.
    assert models["gemma"].context_window == 131072
    assert models["qwen"].context_window == 65536
    assert models["gpt"].context_window == 65536


def test_thinking_can_be_turned_on_for_a_model_that_does_not_reason_by_default():
    """gemma reasons only when asked, and the flag is what asks.

    Measured against the running host: enable_thinking=true came back with a
    populated `reasoning` field and 159 completion tokens, false with 4 and none.
    Before this, the "on" half was never sent and the switch could not reach it.
    """
    assert MODELS["gemma"].thinking_by_default is False
    assert MODELS["gemma"].supports_thinking is True
    assert get_llm("gemma", thinking=True).extra_body == THINKING_ON
    # Still left alone when nothing is asked for.
    assert get_llm("gemma").extra_body is None


def test_thinking_is_not_offered_where_the_model_ignores_it():
    """gpt discards enable_thinking, so nothing is sent in either direction."""
    assert MODELS["gpt"].supports_thinking is False
    assert get_llm("gpt", thinking=True).extra_body is None
    assert get_llm("gpt").extra_body is None


# --- the embeddings client survives a tunnel rotation -------------------------
class TestTunnelAwareEmbeddings:
    """The embeddings client is reached through the same rotating reverse proxy
    as the chat models, and used to be the only thing that could not follow it.

    It pinned `VLLM_BASE_URL` at construction and was then cached for the life
    of the process, so the first rotation did not cost one request -- it broke
    every embedding until a restart: search, indexing, table lookup, all 404.
    """

    @staticmethod
    def _embeddings(monkeypatch, responses):
        """A client whose inner embed_documents plays `responses` in order."""
        from embeddings.providers import remote_provider

        calls = []

        class FakeClient:
            def __init__(self, base_url):
                self.base_url = base_url

            def embed_documents(self, texts):
                calls.append(self.base_url)
                outcome = responses.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

            def embed_query(self, text):
                return self.embed_documents([text])[0]

        emb = remote_provider.TunnelAwareEmbeddings.__new__(
            remote_provider.TunnelAwareEmbeddings
        )
        emb._model = "m"
        emb._client_kwargs = {}
        emb._base_url = "https://stale.example"
        monkeypatch.setattr(emb, "_build", lambda: FakeClient(emb._base_url), raising=False)
        emb._client = emb._build()
        # No real sleeping between attempts.
        monkeypatch.setattr(remote_provider.time, "sleep", lambda _s: None)
        return emb, calls

    def test_a_rotated_tunnel_is_refreshed_and_the_call_retried(self, monkeypatch):
        from config import settings as settings_module
        from config import tunnel
        from embeddings.providers import remote_provider

        # The exception the OpenAI SDK actually raises for a rotated tunnel --
        # ngrok answers 404 for an address that no longer exists. Not
        # httpx.HTTPStatusError: that one carries its code on `.response`, and
        # the SDK never surfaces it to LangChain anyway.
        request = httpx.Request("POST", "https://stale.example/embed/v1/embeddings")
        stale = NotFoundError(
            "ERR_NGROK_3200",
            response=httpx.Response(404, request=request),
            body=None,
        )
        emb, calls = self._embeddings(monkeypatch, [stale, [[0.1, 0.2]]])

        def fake_refresh(failed):
            settings_module.settings.VLLM_BASE_URL = "https://fresh.example"
            return True

        monkeypatch.setattr(tunnel, "refresh_after_failure", fake_refresh)
        monkeypatch.setattr(remote_provider.tunnel, "refresh_after_failure", fake_refresh)

        assert emb.embed_documents(["x"]) == [[0.1, 0.2]]
        # First attempt against the stale URL, retry against the refreshed one.
        assert calls == ["https://stale.example", "https://fresh.example"]
        assert emb._base_url == "https://fresh.example"

    def test_an_application_error_is_not_treated_as_a_rotation(self, monkeypatch):
        """A 400 is the request being wrong, not the URL having moved. Fetching
        the Gist for it would turn every bad payload into a tunnel round-trip."""
        from config import tunnel
        from embeddings.providers import remote_provider

        boom = ValueError("bad input")
        emb, calls = self._embeddings(monkeypatch, [boom])

        called = []
        monkeypatch.setattr(
            remote_provider.tunnel, "refresh_after_failure",
            lambda failed: called.append(failed),
        )
        with pytest.raises(ValueError):
            emb.embed_documents(["x"])
        assert called == []
        assert calls == ["https://stale.example"]

    def test_the_inner_client_never_retries_on_its_own(self):
        """SDK retries would spend the whole budget re-hitting the stale URL
        before the refresh that fixes it ever runs."""
        import inspect

        from embeddings.providers.remote_provider import TunnelAwareEmbeddings

        assert "max_retries=0" in inspect.getsource(TunnelAwareEmbeddings._build)

    def test_the_cached_instance_follows_the_new_url(self, monkeypatch):
        """The cache holds the wrapper, not the pinned client, so every existing
        holder follows a rotation without knowing it happened."""
        from embeddings.providers import remote_provider

        remote_provider.clear_remote_cache()
        monkeypatch.setattr(
            remote_provider.TunnelAwareEmbeddings, "_build", lambda self: object()
        )
        a = remote_provider.RemoteProvider().create("m")
        b = remote_provider.RemoteProvider().create("m")
        assert a is b
        assert isinstance(a, remote_provider.TunnelAwareEmbeddings)
        remote_provider.clear_remote_cache()


def test_both_clients_share_one_tunnel_failure_detector():
    """Two copies of the retryable-error list would drift, and the half that
    fell behind would stop recovering."""
    from config import tunnel
    from llm.providers.vllm_provider import TunnelAwareChatOpenAI

    assert TunnelAwareChatOpenAI._is_tunnel_failure is tunnel.is_tunnel_failure


def test_refreshing_clients_does_not_close_a_shared_connection_pool():
    """The old client is only closed when the new one is not standing on it.

    `ChatOpenAI` instances sharing a base URL share one underlying httpx
    client, so closing the old one unconditionally shut the pool every other
    model object in the process was using -- one tunnel hiccup in a background
    call, and the next chat answer died on "the client has been closed".
    """
    model = get_llm("gemma")
    shared = model.root_client._client

    model._refresh_clients()

    assert model.root_client._client is shared
    assert not shared.is_closed
