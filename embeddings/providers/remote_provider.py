"""Embeddings served by the local vLLM host, over the same ngrok tunnel as the
chat models (see llm/providers/vllm_provider.py) — one more path (/embed/v1)
on the same base URL, same "no auth but the client wants a key" story.
"""

import logging

from langchain_core.embeddings import Embeddings

from config.settings import settings

from .base import BaseEmbeddingProvider

logger = logging.getLogger(__name__)

# Loading a client is cheap, but reuse it anyway (same pattern as LocalProvider)
# so repeated get_embedding() calls don't rebuild it.
_CACHE: dict[tuple, Embeddings] = {}


class RemoteProvider(BaseEmbeddingProvider):
    """OpenAI-compatible embeddings endpoint on the vLLM host."""

    provider_name = "remote"

    @staticmethod
    def matches(model: str) -> bool:
        # Only picked when EMBEDDING_PROVIDER="remote" is set explicitly
        # (registered before LocalProvider, but never matches on its own).
        return False

    def create(self, model: str, **kwargs) -> Embeddings:
        key = (model,)
        cached = _CACHE.get(key)
        if cached is not None:
            return cached

        from langchain_openai import OpenAIEmbeddings

        instance = OpenAIEmbeddings(
            model=model,
            base_url=settings.VLLM_BASE_URL.rstrip("/") + settings.EMBEDDING_ROUTE,
            api_key=settings.VLLM_API_KEY,
            timeout=settings.LLM_TIMEOUT,
            max_retries=settings.LLM_MAX_RETRIES,
            # The server's tokenizer is Qwen3's, not OpenAI's -- tiktoken-based
            # pre-checks/truncation would be wrong for it. Send plain text.
            check_embedding_ctx_length=False,
            **kwargs,
        )
        _CACHE[key] = instance
        return instance


def clear_remote_cache() -> None:
    """Drop cached clients. Frees memory and lets tests start clean."""
    count = len(_CACHE)
    _CACHE.clear()
    logger.info("Cleared %d cached remote embedding client(s)", count)
