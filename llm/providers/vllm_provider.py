"""The three models served by the local vLLM host.

Capabilities in MODELS were measured against the running servers, not taken
from documentation. See docs/FINDINGS.md sections 1-4. Re-measure before
changing them.
"""

import logging
import threading
from dataclasses import dataclass

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from config.settings import settings

from .base import BaseLLMProvider

logger = logging.getLogger(__name__)

# --- BAĞLANTI HAVUZU (dataprep/vlm.py ile AYNI politika) --------------------
# SORUN (ölçüldü 2026-08-22): get_llm() her çağrıda YENİ bir ChatOpenAI
# üretiyor ama openai SDK'sı altta TEK bir GLOBAL httpx.Client paylaşıyor
# (kanıt: iki ayrı ChatOpenAI -> root_client farklı, ama `_client` AYNI nesne).
# Yani tünel soketi bayatladığında ya da sunucu bağlantıları kapattığında,
# bank_agent/classify_agent/dedup'ın SINIRSIZ retry'ı hep AYNI ÖLÜ havuza
# yazıyordu — vlm.py'de kanıtlanmış 14 dakikalık takılmanın aynısı, ama bu
# katmanda hiçbir kurtarma mekanizması YOKTU.
#
# ÇÖZÜM (kullanıcı kararı 2026-08-22, önceki aşamalarla AYNI olsun):
# httpx.Client'ı BİZ veriyoruz ve HERHANGİ bir hatada reset_http_pool() ile
# TAMAMEN kapatıp tazesini açıyoruz — bir sonraki deneme garantili olarak
# YENİ bir bağlantıyla gider, ölü sokete geri dönülmez.
#
# keepalive_expiry: bayat soket havuzda yeniden kullanılmadan atılsın diye
# tünelin soket ömründen KISA tutulur (vlm.py::_KEEPALIVE_EXPIRY ile aynı
# gerekçe, nginx "400 / 0 byte" kanıtı).
_KEEPALIVE_EXPIRY = float(getattr(settings, "VLM_KEEPALIVE_EXPIRY", 20.0))
_POOL = int(getattr(settings, "VLM_POOL", 64))
# Eski havuzu ANINDA değil, bu kadar bekleyip kapat: aynı havuzda BAŞKA
# thread'lerin GERÇEKTEN süren sağlıklı istekleri olabilir, onları koparmak
# sunucuda 499 üretir (vlm.py::_CLOSE_GRACE ile aynı gerekçe ve tavan).
_CLOSE_GRACE = 120.0

_http_lock = threading.Lock()
_http_gen = 0


def _new_http_client() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(connect=10.0, read=settings.LLM_TIMEOUT,
                              write=30.0, pool=30.0),
        limits=httpx.Limits(max_connections=_POOL,
                            max_keepalive_connections=_POOL,
                            keepalive_expiry=_KEEPALIVE_EXPIRY))


_http_client = _new_http_client()


def get_http_client() -> httpx.Client:
    """O anki (canlı) havuz. create() her ChatOpenAI'ye bunu verir."""
    with _http_lock:
        return _http_client


def reset_http_pool(reason: str = "") -> int:
    """Havuzu SIFIRDAN yenile — eski istemci kapatılır, tazesi açılır.

    Çağıranlar (bank_agent/classify_agent/dedup/_invoke_resilient) HER
    başarısızlıkta çağırır: hata aldığımız bağlantıya güvenilmez, yeniden
    kullanmanın hiçbir faydası yok. Eski istemcinin kapatılması _CLOSE_GRACE
    kadar ertelenir ki hâlâ süren sağlıklı istekler koparılmasın.

    Yeni havuz nesil numarasını döndürür (loglama için)."""
    global _http_client, _http_gen
    with _http_lock:
        old = _http_client
        _http_client = _new_http_client()
        _http_gen += 1
        gen = _http_gen

    def _gec_kapat():
        import time
        time.sleep(_CLOSE_GRACE)
        try:
            old.close()
        except Exception:
            pass

    threading.Thread(target=_gec_kapat, daemon=True).start()
    logger.warning("    [LLM_HAVUZ_YENİLENDİ] %s — eski bağlantılar kapatılıyor, "
                   "yeni havuz açıldı (nesil #%d)", reason or "istek başarısız", gen)
    return gen

# Turns off chain-of-thought for models that emit it into `content`.
THINKING_OFF = {"chat_template_kwargs": {"enable_thinking": False}}


@dataclass(frozen=True)
class ModelSpec:
    """One model and the behaviour we measured from it."""

    model_id: str
    route: str
    context_window: int

    # True when the model reasons by default and mixes that reasoning into
    # `content`. Measured on qwen: turning it off cut 433 output tokens to 36.
    thinking_by_default: bool

    # Below this, reasoning can consume the whole budget and `content` comes
    # back empty with finish_reason='length' and no exception.
    min_max_tokens: int = 0

    notes: str = ""


MODELS: dict[str, ModelSpec] = {
    "gemma": ModelSpec(
        model_id="google/gemma-4-31B-it",
        route="/gemma/v1",
        context_window=65536,
        thinking_by_default=False,
        notes="Fastest and cleanest Turkish. Thinking is off by default and "
        "should stay off: when enabled the answer is concatenated onto the "
        "reasoning with no delimiter.",
    ),
    "qwen": ModelSpec(
        model_id="Qwen/Qwen3.6-27B",
        route="/qwen/v1",
        context_window=65536,
        thinking_by_default=True,
        notes="Best structured output. Reasoning lands in `content` with no "
        "reliable tag, so it is disabled unless asked for.",
    ),
    "gpt": ModelSpec(
        model_id="openai/gpt-oss-20b",
        route="/gpt/v1",
        context_window=65536,
        thinking_by_default=False,
        min_max_tokens=300,
        notes="Ignores enable_thinking and writes reasoning to the `reasoning` "
        "field, which LangChain drops. Returns empty content under 300 tokens.",
    ),
}


class VLLMProvider(BaseLLMProvider):
    """Local vLLM host. Every model is OpenAI-compatible on its own route."""

    provider_name = "vllm"

    @staticmethod
    def matches(model_key: str) -> bool:
        return model_key in MODELS

    def create(self, model_key: str, **kwargs) -> BaseChatModel:
        spec = MODELS[model_key]

        thinking = kwargs.pop("thinking", False)
        extra_body = dict(kwargs.pop("extra_body", None) or {})
        if not thinking and spec.thinking_by_default:
            extra_body.update(THINKING_OFF)

        max_tokens = kwargs.pop("max_tokens", None)
        if max_tokens is not None and max_tokens < spec.min_max_tokens:
            raise ValueError(
                f"{model_key} returns empty content below "
                f"{spec.min_max_tokens} max_tokens (reasoning consumes the "
                f"budget first). Got {max_tokens}."
            )
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        kwargs.setdefault("temperature", settings.LLM_TEMPERATURE)
        # STREAMING VARSAYILAN AÇIK (dataprep/vlm.py::_stream_once ile AYNI
        # gerekçe, kanıtlı): stream OLMAYAN bir istekte üretim boyunca
        # bağlantıdan HİÇ bayt akmaz — aradaki tünel (lhr.life) ~120 saniyelik
        # IDLE zaman aşımı uyguladığı için uzun süren üretimler koparılıyordu
        # (sunucu access log'unda 499, bizde RemoteProtocolError; canlı ölçüldü:
        # kopmaların süresi 120-125s'de kümelendi). Streaming'de her token anında
        # aktığı için bağlantı ASLA boşta kalmaz. LangChain akışı kendi içinde
        # birleştirir — çağıranlar (bank_agent/classify_agent/dedup) .content
        # okumaya devam eder, tool-calling dahil davranış AYNI kalır.
        from config import tunnel
        # Yedek adres SABİT YAZILMAZ: tünel URL'i değişken olduğu için elle
        # gömülen bir adres kısa sürede bayatlar ve sessizce ölü bir hedefe
        # istek atılmasına yol açar. settings.VLLM_BASE_URL yoksa boş kalır;
        # doğru adres her hâlükârda config/tunnel.py tarafından gist'ten
        # tazelenir (bkz. refresh_if_needed).
        live_base = getattr(settings, "VLLM_BASE_URL", "") or ""
        cached = getattr(tunnel, "_LIVE_BASE_CACHE", None)
        if cached:
            live_base = cached

        # http_client: SDK'nın global/paylaşılan havuzu yerine BİZİM
        # yönettiğimiz havuz (bkz. reset_http_pool) — hatada tamamen kapatılıp
        # tazesi açılabilsin diye.
        return ChatOpenAI(
            model=spec.model_id,
            base_url=live_base.rstrip("/") + spec.route,
            api_key=settings.VLLM_API_KEY,
            timeout=settings.LLM_TIMEOUT,
            max_retries=settings.LLM_MAX_RETRIES,
            extra_body=extra_body or None,
            http_client=get_http_client(),
            **kwargs,
        )

    def list_models(self) -> list[str]:
        return list(MODELS)
