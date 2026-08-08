"""Base class for embedding providers."""

from abc import ABC, abstractmethod

from langchain_core.embeddings import Embeddings


class BaseEmbeddingProvider(ABC):
    """A source of embedding models.

    Add a provider by writing one module here and appending an instance to
    PROVIDERS in __init__.py.
    """

    provider_name: str = ""

    @staticmethod
    @abstractmethod
    def matches(model: str) -> bool:
        """Whether this provider serves the given model name."""

    @abstractmethod
    def create(self, model: str, **kwargs) -> Embeddings:
        """Build an embedding model. Returns a plain LangChain Embeddings."""
