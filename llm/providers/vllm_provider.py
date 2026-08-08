"""The three models served by the local vLLM host.

Capabilities in MODELS were measured against the running servers, not taken
from documentation. See docs/FINDINGS.md sections 1-4. Re-measure before
changing them.
"""

import logging
from dataclasses import dataclass

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from config.settings import settings

from .base import BaseLLMProvider

logger = logging.getLogger(__name__)

# Turns off chain-of-thought for models that emit it into `content`.
THINKING_OFF = {"chat_template_kwargs": {"enable_thinking": False}}


@dataclass(frozen=True)
class ModelSpec:
    """One model and the behaviour we measured from it."""

    model_id: str
    route: str
    context_window: int

    # True when the model reasons by default and mixes that reasoning into
    # `content`. Measured on qwen: turning it off cut 433 output tokens to 36.
    thinking_by_default: bool

    # Below this, reasoning can consume the whole budget and `content` comes
    # back empty with finish_reason='length' and no exception.
    min_max_tokens: int = 0

    notes: str = ""


MODELS: dict[str, ModelSpec] = {
    "gemma": ModelSpec(
        model_id="google/gemma-4-31B-it",
        route="/gemma/v1",
        context_window=65536,
        thinking_by_default=False,
        notes="Fastest and cleanest Turkish. Thinking is off by default and "
        "should stay off: when enabled the answer is concatenated onto the "
        "reasoning with no delimiter.",
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
        if not thinking and spec.thinking_by_default:
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

        return ChatOpenAI(
            model=spec.model_id,
            base_url=settings.VLLM_BASE_URL.rstrip("/") + spec.route,
            api_key=settings.VLLM_API_KEY,
            timeout=settings.LLM_TIMEOUT,
            max_retries=settings.LLM_MAX_RETRIES,
            extra_body=extra_body or None,
            **kwargs,
        )

    def list_models(self) -> list[str]:
        return list(MODELS)
