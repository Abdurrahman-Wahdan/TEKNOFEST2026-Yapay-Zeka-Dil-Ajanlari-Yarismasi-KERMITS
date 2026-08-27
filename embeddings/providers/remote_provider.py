import json
import logging
import time
import requests
from requests.adapters import HTTPAdapter
from langchain_core.embeddings import Embeddings
from config.settings import settings
from .base import BaseEmbeddingProvider

logger = logging.getLogger(__name__)

# Kalıcı HTTP Session (Genişletilmiş 35 bağlantılık havuz — paralel iş parçacıkları için)
_SESSION = requests.Session()
_adapter = HTTPAdapter(pool_connections=35, pool_maxsize=35)
_SESSION.mount("https://", _adapter)
_SESSION.mount("http://", _adapter)
_SESSION.headers.update({
    "Content-Type": "application/json",
    "Authorization": "Bearer EMPTY"
})


_LIVE_BASE_CACHE = settings.VLLM_BASE_URL

def _get_live_base(force: bool = False) -> str:
    global _LIVE_BASE_CACHE
    if force or _LIVE_BASE_CACHE is None:
        from config import tunnel
        live = tunnel._fetch_live_url()
        if live:
            _LIVE_BASE_CACHE = live
    return _LIVE_BASE_CACHE or settings.VLLM_BASE_URL


class DynamicRemoteEmbeddings(Embeddings):
    """Her istekte ve her retry'da canlı Gist tünel URL'ini tazeleyen,
    asla ölü tünelde takılı kalmayan, kalıcı session kullanan dinamik embedding istemcisi."""

    def __init__(self, model: str, **kwargs):
        self.model = model
        self.kwargs = kwargs

    def _post_embed(self, texts: list[str]) -> list[list[float]]:
        delay = 0.5
        attempt = 0
        while True:
            attempt += 1
            live_base = _get_live_base(force=(attempt > 1))
            url = live_base.rstrip("/") + settings.EMBEDDING_ROUTE.rstrip("/") + "/embeddings"
            payload = {
                "model": self.model,
                "input": texts
            }
            try:
                r = _SESSION.post(url, json=payload, timeout=60)
                if r.status_code == 200:
                    data = r.json()
                    sorted_data = sorted(data["data"], key=lambda x: x.get("index", 0))
                    return [item["embedding"] for item in sorted_data]
                else:
                    logger.warning("  Embedding HTTP %d (%s) — %.1fs sonra tekrar",
                                   r.status_code, url, delay)
            except Exception as exc:
                logger.warning("  Embedding isteği hatası (deneme %d, %s): %s — %.1fs sonra tekrar",
                               attempt, url, exc, delay)
            time.sleep(delay)
            delay = min(delay * 1.5, 4.0)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        from concurrent.futures import ThreadPoolExecutor
        # Teker teker ve yüksek paralellikte (20 paralel worker) gönder
        workers = min(len(texts), 20)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(self._post_embed, [t]) for t in texts]
            results = []
            for f in futures:
                res = f.result()
                results.append(res[0] if res else [])
            return results

    def embed_query(self, text: str) -> list[float]:
        res = self._post_embed([text])
        return res[0] if res else []


class RemoteProvider(BaseEmbeddingProvider):
    """OpenAI-compatible embeddings endpoint on the vLLM host with dynamic tunnel support."""

    provider_name = "remote"

    @staticmethod
    def matches(model: str) -> bool:
        return False

    def create(self, model: str, **kwargs) -> Embeddings:
        return DynamicRemoteEmbeddings(model=model, **kwargs)


def clear_remote_cache() -> None:
    pass
