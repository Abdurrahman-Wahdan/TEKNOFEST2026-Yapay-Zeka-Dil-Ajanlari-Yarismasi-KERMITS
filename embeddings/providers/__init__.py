"""Embedding provider registry.

To add a provider (Gemini, OpenAI, ...): write one module here and insert an
instance BEFORE LocalProvider, which matches everything as a fallback.
"""

from .base import BaseEmbeddingProvider
from .local_provider import LocalProvider, clear_local_cache

# Order matters: the first provider whose matches() returns True wins.
PROVIDERS: list[BaseEmbeddingProvider] = [
    LocalProvider(),
]


def get_provider(model: str, provider_name: str | None = None) -> BaseEmbeddingProvider:
    """Find the provider for a model, or the one named explicitly.

    Raises:
        ValueError: if the named provider is unknown, or nothing matches.
    """
    if provider_name:
        for provider in PROVIDERS:
            if provider.provider_name == provider_name.lower():
                return provider
        available = ", ".join(p.provider_name for p in PROVIDERS)
        raise ValueError(
            f"Unknown embedding provider: {provider_name!r}. Available: {available}"
        )

    for provider in PROVIDERS:
        if provider.matches(model):
            return provider

    available = ", ".join(p.provider_name for p in PROVIDERS)
    raise ValueError(
        f"No provider handles embedding model {model!r}. Available: {available}"
    )


__all__ = [
    "PROVIDERS",
    "BaseEmbeddingProvider",
    "LocalProvider",
    "clear_local_cache",
    "get_provider",
]
