"""LLM provider registry.

To add a provider (Gemini, OpenAI, ...): write one module here and append an
instance to PROVIDERS. Nothing else changes.
"""

from .base import BaseLLMProvider
from .vllm_provider import MODELS, ModelSpec, VLLMProvider

# Order matters: the first provider whose matches() returns True wins.
PROVIDERS: list[BaseLLMProvider] = [
    VLLMProvider(),
]


def get_provider(model_key: str) -> BaseLLMProvider:
    """Find the provider serving a model key.

    Raises:
        ValueError: if no provider serves it, listing what is available.
    """
    for provider in PROVIDERS:
        if provider.matches(model_key):
            return provider

    known = sorted(m for p in PROVIDERS for m in p.list_models())
    raise ValueError(
        f"Unknown model: {model_key!r}. Available models: {', '.join(known)}"
    )


__all__ = [
    "MODELS",
    "PROVIDERS",
    "BaseLLMProvider",
    "ModelSpec",
    "VLLMProvider",
    "get_provider",
]
