"""Embedding model factory.

    from embeddings import get_embedding

    get_embedding().embed_query("kâr payı oranı")
"""

import logging

from langchain_core.embeddings import Embeddings

from config.settings import settings

from .providers import clear_local_cache, get_provider

logger = logging.getLogger(__name__)


def get_embedding(
    model: str | None = None,
    provider: str | None = None,
    **kwargs,
) -> Embeddings:
    """Build an embedding model.

    Args:
        model: Model name. Defaults to settings.EMBEDDING_MODEL.
        provider: Force a provider instead of auto-detecting.
        **kwargs: Passed to the provider (device, batch_size, ...).

    Returns:
        A LangChain Embeddings, usable directly by the vector store.
    """
    model = model or settings.EMBEDDING_MODEL
    chosen = get_provider(model, provider_name=provider or settings.EMBEDDING_PROVIDER)
    logger.debug("Creating embedding %s via %s", model, chosen.provider_name)
    return chosen.create(model, **kwargs)


def clear_embedding_cache() -> None:
    """Drop every cached embedding model."""
    clear_local_cache()
