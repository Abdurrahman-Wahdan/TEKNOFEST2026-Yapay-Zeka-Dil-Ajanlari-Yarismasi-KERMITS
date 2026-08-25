"""The three models served by the local vLLM host.

Capabilities in MODELS were measured against the running servers, not taken
from documentation. See docs/FINDINGS.md sections 1-4. Re-measure before
changing them.
"""

import logging
import time
from dataclasses import dataclass
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from config.settings import settings
from config import tunnel

from .base import BaseLLMProvider

logger = logging.getLogger(__name__)

# Chain-of-thought, asked for explicitly in both directions. Sending only the
# "off" half meant a model that does not reason by default could never be turned
# *on*: the flag was accepted, matched no branch, and was dropped.
THINKING_OFF = {"chat_template_kwargs": {"enable_thinking": False}}
THINKING_ON = {"chat_template_kwargs": {"enable_thinking": True}}


class TunnelAwareChatOpenAI(ChatOpenAI):
    """Retry tunnel failures until the configured request window expires."""

    # The detector moved to `config/tunnel.py`: the embeddings client reached
    # through the same reverse proxy needs the identical answer, and two copies
    # of the list would drift until the half that fell behind stopped
    # recovering. Kept as a static method here because that is the name the
    # retry paths below and the tests both call.
    _is_tunnel_failure = staticmethod(tunnel.is_tunnel_failure)

    def _recover_tunnel(self, exc: Exception) -> None:
        if not self._is_tunnel_failure(exc):
            raise exc
        logger.warning(
            "LLM request failed; checking tunnel before retry type=%s status=%s base=%s",
            type(exc).__name__,
            getattr(exc, "status_code", None),
            self._tf26_base_url,
        )
        try:
            tunnel.refresh_after_failure(self._tf26_base_url)
        except Exception:
            # The Gist can be unavailable at the same time as the tunnel.  Keep
            # the existing base URL and let the bounded retry schedule recover
            # once either service is back, instead of failing the chat at once.
            logger.warning("Could not refresh the tunnel URL; retrying current URL", exc_info=True)
        self._refresh_clients()
        object.__setattr__(self, "_tf26_base_url", settings.VLLM_BASE_URL.rstrip("/"))
        logger.info("LLM client rebuilt with fresh connection base=%s", self._tf26_base_url)

    @staticmethod
    def _next_delay(delay: float) -> float:
        return min(delay * 2, settings.LLM_RETRY_MAX_DELAY_SECONDS)

    def _wait_before_retry(self, delay: float, deadline: float) -> float:
        """Sleep 1, 2, 4, ... seconds (capped) without exceeding the window."""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return 0.0
        wait_for = min(delay, remaining)
        logger.warning("Retrying LLM request in %.0fs", wait_for)
        time.sleep(wait_for)
        return self._next_delay(delay)

    def _refresh_clients(self) -> None:
        """Replace the failed HTTP client with a fresh connection for the retry."""
        old_root_client = self.root_client
        replacement = ChatOpenAI(
            model=self.model_name,
            base_url=settings.VLLM_BASE_URL.rstrip("/") + self._tf26_route,
            api_key=self.openai_api_key,
            timeout=self.request_timeout,
            max_retries=0,
            temperature=self.temperature,
            extra_body=self.extra_body,
        )
        for name in (
            "openai_api_base", "root_client", "client", "root_async_client", "async_client"
        ):
            object.__setattr__(self, name, getattr(replacement, name))
        # A stream that failed mid-connect can leave a dead pooled connection
        # behind, so the old client is closed after replacement -- but only if
        # the replacement is not standing on it.
        #
        # `ChatOpenAI` instances built with the same base URL and timeout share
        # one underlying httpx client (langchain-openai caches it), so a plain
        # `close()` here shut the connection pool that every *other* model
        # object in the process was using: one tunnel hiccup during a table
        # overview and the next chat answer died on "Cannot send a request, as
        # the client has been closed". Concurrent callers are what made it
        # visible; it was always wrong.
        old_http = getattr(old_root_client, "_client", None)
        new_http = getattr(self.root_client, "_client", None)
        close = getattr(old_root_client, "close", None)
        if callable(close) and old_http is not None and old_http is not new_http:
            close()

    def _retry_after_tunnel_refresh(self, operation, *args, **kwargs):
        deadline = time.monotonic() + settings.LLM_TIMEOUT
        delay = 1.0
        attempt = 1
        while True:
            try:
                logger.info("Starting LLM request attempt=%d base=%s", attempt, self._tf26_base_url)
                result = operation(*args, **kwargs)
                logger.info("LLM request completed attempt=%d base=%s", attempt, self._tf26_base_url)
                return result
            except Exception as exc:
                self._recover_tunnel(exc)
                delay = self._wait_before_retry(delay, deadline)
                if delay == 0.0:
                    raise
                attempt += 1

    def _generate(self, *args, **kwargs):
        return self._retry_after_tunnel_refresh(super()._generate, *args, **kwargs)

    @staticmethod
    def _chunk_has_meaningful_output(chunk: Any) -> bool:
        """Ignore OpenAI's empty role/bootstrap frame for retry safety."""
        message = getattr(chunk, "message", None)
        if message is None:
            return False
        if getattr(message, "content", None):
            return True
        if getattr(message, "tool_call_chunks", None):
            return True
        return bool((getattr(message, "additional_kwargs", None) or {}).get("tool_calls"))

    def _open_stream(self, *args, **kwargs):
        """One physical HTTP stream; separated so retry behavior is testable."""
        return super()._stream(*args, **kwargs)

    def _stream(self, *args, **kwargs):
        deadline = time.monotonic() + settings.LLM_TIMEOUT
        delay = 1.0
        attempt = 1
        while True:
            meaningful_output = False
            try:
                logger.info("Opening LLM stream attempt=%d base=%s", attempt, self._tf26_base_url)
                for chunk in self._open_stream(*args, **kwargs):
                    meaningful_output = meaningful_output or self._chunk_has_meaningful_output(chunk)
                    yield chunk
                logger.info("LLM stream completed attempt=%d base=%s", attempt, self._tf26_base_url)
                return
            except Exception as exc:
                # Retrying after meaningful content would duplicate prose or a
                # tool call. OpenAI's initial empty role frame is protocol
                # bookkeeping and must not suppress recovery.
                if meaningful_output:
                    logger.error(
                        "LLM stream failed after meaningful output; not replaying type=%s base=%s",
                        type(exc).__name__,
                        self._tf26_base_url,
                    )
                    raise
                self._recover_tunnel(exc)
                delay = self._wait_before_retry(delay, deadline)
                if delay == 0.0:
                    raise
                attempt += 1


@dataclass(frozen=True)
class ModelSpec:
    """One model and the behaviour we measured from it."""

    model_id: str
    route: str
    context_window: int

    # True when the model reasons by default and mixes that reasoning into
    # `content`. Measured on qwen: turning it off cut 433 output tokens to 36.
    thinking_by_default: bool

    # Whether `enable_thinking` does anything at all. Separate from the above:
    # gemma reasons only when asked, so it is False by default and True here,
    # and collapsing the two is what hid its thinking support entirely.
    supports_thinking: bool = True

    # Below this, reasoning can consume the whole budget and `content` comes
    # back empty with finish_reason='length' and no exception.
    min_max_tokens: int = 0

    notes: str = ""


MODELS: dict[str, ModelSpec] = {
    "gemma": ModelSpec(
        model_id="google/gemma-4-31B-it",
        route="/gemma/v1",
        context_window=131072,
        thinking_by_default=False,
        notes="Fastest and cleanest Turkish. Reasons only when asked, and the "
        "reasoning arrives in the response's `reasoning` field with `content` "
        "left clean -- measured both ways against the running server: "
        "enable_thinking=true returned 159 completion tokens with a populated "
        "`reasoning`, false returned 4 and none. An earlier note here said "
        "thinking concatenated the answer onto the reasoning; that is no longer "
        "how this model behaves.",
    ),
    "qwen": ModelSpec(
        model_id="Qwen/Qwen3.6-27B",
        route="/qwen/v1",
        context_window=65536,
        thinking_by_default=True,
        notes="Best structured output. Reasoning lands in `content` with no "
        "reliable tag, so it is disabled unless asked for.",
    ),
    "gpt": ModelSpec(
        model_id="openai/gpt-oss-20b",
        route="/gpt/v1",
        context_window=65536,
        thinking_by_default=False,
        min_max_tokens=300,
        supports_thinking=False,
        notes="Ignores enable_thinking and writes reasoning to the `reasoning` "
        "field, which LangChain drops. Returns empty content under 300 tokens.",
    ),
}


class VLLMProvider(BaseLLMProvider):
    """Local vLLM host. Every model is OpenAI-compatible on its own route."""

    provider_name = "vllm"

    @staticmethod
    def matches(model_key: str) -> bool:
        return model_key in MODELS

    def create(self, model_key: str, **kwargs) -> BaseChatModel:
        spec = MODELS[model_key]

        thinking = kwargs.pop("thinking", False)
        extra_body = dict(kwargs.pop("extra_body", None) or {})
        # Sent only when it changes the outcome, which is why this is not a plain
        # `enable_thinking = thinking`. A model already doing what was asked is
        # left alone deliberately: fewer chat_template_kwargs reach a template
        # that may not read them. So the flag goes out to turn a reasoning model
        # off, or to turn a non-reasoning one on -- never to restate a default.
        if thinking:
            if spec.supports_thinking and not spec.thinking_by_default:
                extra_body.update(THINKING_ON)
        elif spec.thinking_by_default:
            extra_body.update(THINKING_OFF)

        max_tokens = kwargs.pop("max_tokens", None)
        if max_tokens is not None and max_tokens < spec.min_max_tokens:
            raise ValueError(
                f"{model_key} returns empty content below "
                f"{spec.min_max_tokens} max_tokens (reasoning consumes the "
                f"budget first). Got {max_tokens}."
            )
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        kwargs.setdefault("temperature", settings.LLM_TEMPERATURE)

        model = TunnelAwareChatOpenAI(
            model=spec.model_id,
            base_url=settings.VLLM_BASE_URL.rstrip("/") + spec.route,
            api_key=settings.VLLM_API_KEY,
            timeout=settings.LLM_TIMEOUT,
            # SDK retries would keep using a stale tunnel. The wrapper refreshes
            # the Gist only after the first error and retries once with its URL.
            max_retries=0,
            extra_body=extra_body or None,
            **kwargs,
        )
        object.__setattr__(model, "_tf26_route", spec.route)
        object.__setattr__(model, "_tf26_base_url", settings.VLLM_BASE_URL.rstrip("/"))
        return model

    def list_models(self) -> list[str]:
        return list(MODELS)
