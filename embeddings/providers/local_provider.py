"""Embeddings that run in-process via sentence-transformers.

Everything stays on this machine, which is what şartname 5.9 (on-premise)
requires. Any sentence-transformers model works; pick it in .env.
"""

import logging

from langchain_core.embeddings import Embeddings

from config.settings import settings

from .base import BaseEmbeddingProvider

logger = logging.getLogger(__name__)

# Loading a model takes seconds and hundreds of MB, so instances are reused.
# Keyed on everything that changes the model's behaviour.
_CACHE: dict[tuple, Embeddings] = {}


class LocalProvider(BaseEmbeddingProvider):
    """sentence-transformers running locally."""

    provider_name = "local"

    @staticmethod
    def matches(model: str) -> bool:
        # Local is the fallback: anything not claimed by an API provider runs
        # here. When an API provider is added, it is listed before this one.
        return True

    def create(self, model: str, **kwargs) -> Embeddings:
        device = kwargs.pop("device", settings.EMBEDDING_DEVICE)
        batch_size = kwargs.pop("batch_size", settings.EMBEDDING_BATCH_SIZE)
        normalize = kwargs.pop("normalize_embeddings", True)

        key = (model, device, batch_size, normalize)
        cached = _CACHE.get(key)
        if cached is not None:
            return cached

        try:
            from langchain_huggingface import HuggingFaceEmbeddings
        except ImportError as exc:
            raise RuntimeError(
                "Local embeddings need sentence-transformers. Install with: "
                "pip install langchain-huggingface sentence-transformers"
            ) from exc

        logger.info("Loading embedding model %s on %s (first load is slow)", model, device)
        instance = HuggingFaceEmbeddings(
            model_name=model,
            model_kwargs={"device": device},
            encode_kwargs={"batch_size": batch_size, "normalize_embeddings": normalize},
            **kwargs,
        )
        _CACHE[key] = instance
        return instance


def clear_local_cache() -> None:
    """Drop cached models. Frees memory and lets tests start clean."""
    count = len(_CACHE)
    _CACHE.clear()
    logger.info("Cleared %d cached embedding model(s)", count)
