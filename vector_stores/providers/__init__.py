"""Vector store provider registry.

To add a provider (Chroma, pgvector, ...): write one module here and append an
instance to PROVIDERS.
"""

from .base import BaseVectorStoreProvider
from .qdrant_provider import QdrantProvider

PROVIDERS: list[BaseVectorStoreProvider] = [
    QdrantProvider(),
]


def get_provider(name: str) -> BaseVectorStoreProvider:
    """Find a provider by name.

    Raises:
        ValueError: if unknown, listing what is available.
    """
    for provider in PROVIDERS:
        if provider.provider_name == name.lower():
            return provider

    available = ", ".join(p.provider_name for p in PROVIDERS)
    raise ValueError(f"Unknown vector store: {name!r}. Available: {available}")


__all__ = ["PROVIDERS", "BaseVectorStoreProvider", "QdrantProvider", "get_provider"]
