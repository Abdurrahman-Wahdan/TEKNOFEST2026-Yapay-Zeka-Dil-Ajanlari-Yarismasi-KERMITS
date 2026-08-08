"""Base class for LLM providers."""

from abc import ABC, abstractmethod

from langchain_core.language_models.chat_models import BaseChatModel


class BaseLLMProvider(ABC):
    """A source of chat models.

    Add a provider by writing one module here and appending an instance to
    PROVIDERS in __init__.py.
    """

    provider_name: str = ""

    @staticmethod
    @abstractmethod
    def matches(model_key: str) -> bool:
        """Whether this provider serves the given model key."""

    @abstractmethod
    def create(self, model_key: str, **kwargs) -> BaseChatModel:
        """Build a chat model. Returns a plain LangChain model, not a wrapper."""

    @abstractmethod
    def list_models(self) -> list[str]:
        """Model keys this provider serves."""
