"""Embeddings served by the local vLLM host, over the same ngrok tunnel as the
chat models (see llm/providers/vllm_provider.py) — one more path (/embed/v1)
on the same base URL, same "no auth but the client wants a key" story.

**Same URL means the same rotation problem.** The tunnel's address changes, and
the chat models have survived that since `TunnelAwareChatOpenAI`: fetch the
newly published URL from the Gist after a request fails, rebuild the client
against it, retry. This client did not, and the shape of the failure was worse
than a single dropped request — the instance was built with the URL that was
current at construction and then cached for the life of the process, so the
first rotation broke embeddings *permanently*, until a restart. Every search,
every index write, every table lookup, all 404 with nothing in the logs
suggesting the reason is a URL that moved.
"""

import logging
import time

from langchain_core.embeddings import Embeddings

from config import tunnel
from config.settings import settings

from .base import BaseEmbeddingProvider

logger = logging.getLogger(__name__)

# Loading a client is cheap, but reuse it anyway (same pattern as LocalProvider)
# so repeated get_embedding() calls don't rebuild it. Caching the *wrapper* is
# what makes the cache safe across a rotation: it swaps its inner client in
# place, so every holder of a cached instance follows the new URL without
# knowing anything happened.
_CACHE: dict[tuple, Embeddings] = {}


class TunnelAwareEmbeddings(Embeddings):
    """Retry tunnel failures until the configured request window expires.

    The chat counterpart subclasses `ChatOpenAI` and mutates itself through
    `object.__setattr__` because LangChain's model classes are pydantic and the
    streaming path has to stay inside the class. Nothing here needs either:
    `Embeddings` is a two-method interface, so the client is held as a plain
    attribute and replaced outright on refresh.

    There is no streaming equivalent to guard, which removes the one genuinely
    subtle rule on the chat side — never replay a stream that already emitted
    content. An embedding call either returns every vector or none, so a retry
    can never duplicate half an answer.
    """

    def __init__(self, model: str, **client_kwargs) -> None:
        self._model = model
        self._client_kwargs = client_kwargs
        self._base_url = settings.VLLM_BASE_URL.rstrip("/")
        self._client = self._build()

    def _build(self):
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(
            model=self._model,
            base_url=self._base_url + settings.EMBEDDING_ROUTE,
            api_key=settings.VLLM_API_KEY,
            timeout=settings.LLM_TIMEOUT,
            # SDK retries would keep using a stale tunnel -- the same reason
            # `vllm_provider.py` sets this to 0. It used to be
            # settings.LLM_MAX_RETRIES here, which meant a rotation was paid for
            # several times over at the wrong URL before the call gave up, and
            # the refresh that would have fixed it never ran.
            max_retries=0,
            # The server's tokenizer is Qwen3's, not OpenAI's -- tiktoken-based
            # pre-checks/truncation would be wrong for it. Send plain text.
            check_embedding_ctx_length=False,
            **self._client_kwargs,
        )

    def _recover(self, exc: Exception) -> None:
        """Refresh the tunnel and rebuild, or re-raise if this is not the tunnel."""
        if not tunnel.is_tunnel_failure(exc):
            raise exc
        logger.warning(
            "Embedding request failed; checking tunnel before retry type=%s status=%s base=%s",
            type(exc).__name__,
            getattr(exc, "status_code", None),
            self._base_url,
        )
        try:
            tunnel.refresh_after_failure(self._base_url)
        except Exception:
            # The Gist can be unavailable at the same time as the tunnel. Keep
            # the existing base URL and let the bounded retry schedule recover
            # once either service is back, instead of failing at once.
            logger.warning("Could not refresh the tunnel URL; retrying current URL", exc_info=True)
        self._base_url = settings.VLLM_BASE_URL.rstrip("/")
        self._client = self._build()
        logger.info("Embedding client rebuilt with fresh connection base=%s", self._base_url)

    def _call(self, method: str, payload):
        deadline = time.monotonic() + settings.LLM_TIMEOUT
        delay = 1.0
        attempt = 1
        while True:
            try:
                return getattr(self._client, method)(payload)
            except Exception as exc:
                self._recover(exc)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                wait_for = min(delay, remaining)
                logger.warning("Retrying embedding request in %.0fs", wait_for)
                time.sleep(wait_for)
                delay = min(delay * 2, settings.LLM_RETRY_MAX_DELAY_SECONDS)
                attempt += 1

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._call("embed_documents", texts)

    def embed_query(self, text: str) -> list[float]:
        return self._call("embed_query", text)


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

        instance = TunnelAwareEmbeddings(model, **kwargs)
        _CACHE[key] = instance
        return instance


def clear_remote_cache() -> None:
    """Drop cached clients. Frees memory and lets tests start clean."""
    count = len(_CACHE)
    _CACHE.clear()
    logger.info("Cleared %d cached remote embedding client(s)", count)
