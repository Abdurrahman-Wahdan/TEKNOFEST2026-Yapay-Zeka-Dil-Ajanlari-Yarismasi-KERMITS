"""Vector store factory.

    from vector_stores import ensure_collection, get_vector_store
    from embeddings import get_embedding

    emb = get_embedding()
    ensure_collection("campaigns")
    store = get_vector_store("campaigns", emb)
"""

import logging

from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore

from config.settings import settings

from .providers import get_provider

logger = logging.getLogger(__name__)


def get_vector_store(
    collection: str,
    embedding: Embeddings,
    provider: str | None = None,
    **kwargs,
) -> VectorStore:
    """Open a collection as a LangChain VectorStore.

    The collection must already exist; call ensure_collection() first.

    Args:
        collection: Collection name.
        embedding: The embedding model to index and query with.
        provider: Defaults to settings.VECTOR_STORE.
    """
    chosen = get_provider(provider or settings.VECTOR_STORE)
    logger.debug("Opening %s/%s", chosen.provider_name, collection)
    return chosen.create(collection, embedding, **kwargs)


def ensure_collection(
    collection: str,
    dimensions: int | None = None,
    provider: str | None = None,
) -> bool:
    """Create the collection if it does not exist.

    Args:
        collection: Collection name.
        dimensions: Vector size. Defaults to settings.EMBEDDING_DIMENSIONS.
        provider: Defaults to settings.VECTOR_STORE.

    Returns:
        True if created, False if it already existed.

    Raises:
        ValueError: if it exists with a different vector size.
    """
    chosen = get_provider(provider or settings.VECTOR_STORE)
    return chosen.ensure_collection(
        collection, dimensions or settings.EMBEDDING_DIMENSIONS
    )
