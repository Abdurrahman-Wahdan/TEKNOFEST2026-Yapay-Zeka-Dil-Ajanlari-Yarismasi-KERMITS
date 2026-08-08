"""Base class for vector store providers."""

from abc import ABC, abstractmethod

from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore


class BaseVectorStoreProvider(ABC):
    """A source of vector stores.

    Add a provider by writing one module here and appending an instance to
    PROVIDERS in __init__.py.
    """

    provider_name: str = ""

    @abstractmethod
    def create(self, collection: str, embedding: Embeddings, **kwargs) -> VectorStore:
        """Open a collection as a LangChain VectorStore."""

    @abstractmethod
    def ensure_collection(self, collection: str, dimensions: int) -> bool:
        """Create the collection if absent. Returns True if it was created.

        Must raise if the collection exists with a different vector size.
        """
