"""Local Qdrant, running in Docker."""

import logging

from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore

from vector_stores.client import get_qdrant_client

from .base import BaseVectorStoreProvider

logger = logging.getLogger(__name__)


class QdrantProvider(BaseVectorStoreProvider):
    """Qdrant vector store."""

    provider_name = "qdrant"

    def create(self, collection: str, embedding: Embeddings, **kwargs) -> VectorStore:
        try:
            from langchain_qdrant import QdrantVectorStore
        except ImportError as exc:
            raise RuntimeError(
                "Qdrant support needs langchain-qdrant. Install with: "
                "pip install langchain-qdrant qdrant-client"
            ) from exc

        return QdrantVectorStore(
            client=get_qdrant_client(),
            collection_name=collection,
            embedding=embedding,
            **kwargs,
        )

    def ensure_collection(self, collection: str, dimensions: int) -> bool:
        """Create the collection if it does not exist.

        Raises:
            ValueError: if it exists with a different vector size. Writing
                vectors of the wrong size fails late and confusingly, so this
                is checked up front.
        """
        from qdrant_client.models import Distance, VectorParams

        client = get_qdrant_client()

        if client.collection_exists(collection):
            info = client.get_collection(collection)
            existing = info.config.params.vectors.size
            if existing != dimensions:
                raise ValueError(
                    f"Collection {collection!r} stores {existing}-dim vectors but "
                    f"EMBEDDING_DIMENSIONS is {dimensions}. Use a matching embedding "
                    f"model, or delete the collection and re-index."
                )
            return False

        client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=dimensions, distance=Distance.COSINE),
        )
        logger.info("Created collection %s (%d dims)", collection, dimensions)
        return True
