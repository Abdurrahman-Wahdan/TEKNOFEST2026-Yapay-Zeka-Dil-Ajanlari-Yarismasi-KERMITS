"""How much context a model actually has, asked of the server that serves it.

`ModelSpec.context_window` is a measurement someone wrote down. It was wrong:
gemma is served with 131072 and the constant said 65536, half the real figure.
Nothing warns when a constant like that drifts -- the model is restarted with a
longer window, the number here stays, and every decision built on it is quietly
made against the wrong denominator.

So the window is read from vLLM's own `/v1/models`, which reports `max_model_len`
per served model. That is the only place the truth lives.

Two windows matter, and they are not the same number:

    reported_context_window()   what the model accepts, total
    usable_context_window()     what the *conversation* can occupy

The difference is what every request carries and no summary can remove: the
system prompt, and the JSON schema of every tool bound to the agent. Measured on
the supervisor, that is ~2000 tokens on every call. A compaction threshold set
against the reported window silently overspends by that much; set against the
usable window it means what it says.
"""

from __future__ import annotations

import logging
from threading import Lock

import httpx
from langchain_core.messages import SystemMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool

from config import tunnel
from config.settings import settings

from .providers.vllm_provider import MODELS

logger = logging.getLogger(__name__)

# Keyed by (model_key, base_url) so a rotated tunnel re-probes instead of
# serving a window read from a host that is no longer the one answering.
_cache: dict[tuple[str, str], int] = {}
_lock = Lock()


class ContextWindowUnavailable(RuntimeError):
    """The server did not report a window for this model.

    Raised rather than falling back to the value in `MODELS`. A fallback here
    would reintroduce exactly the stale constant this module exists to replace,
    and would do it silently, at the one moment the number matters.
    """


def _probe(model_key: str, base_url: str) -> int:
    """Ask the host what window it serves this model with."""
    spec = MODELS[model_key]
    url = base_url.rstrip("/") + spec.route + "/models"
    response = httpx.get(url, timeout=settings.QDRANT_TIMEOUT)
    response.raise_for_status()
    cards = response.json().get("data") or []

    # Matched by id, not taken as the first card. A route serving more than one
    # model would otherwise hand back whichever happened to be listed first.
    for card in cards:
        if card.get("id") == spec.model_id:
            window = card.get("max_model_len")
            if isinstance(window, int) and window > 0:
                return window
            raise ContextWindowUnavailable(
                f"{spec.model_id} is served without a usable max_model_len "
                f"({window!r}). vLLM reports it; a proxy in front of it may not."
            )

    served = ", ".join(str(card.get("id")) for card in cards) or "nothing"
    raise ContextWindowUnavailable(
        f"{url} serves {served}, not {spec.model_id}."
    )


def reported_context_window(model_key: str) -> int:
    """The window the host serves `model_key` with, cached per tunnel URL.

    A failed probe refreshes the tunnel once and retries, for the same reason
    `TunnelAwareChatOpenAI` does: the published URL rotates, and the first
    request after a rotation is the one that discovers it.
    """
    base_url = settings.VLLM_BASE_URL.rstrip("/")
    key = (model_key, base_url)
    with _lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached

    try:
        window = _probe(model_key, base_url)
    except ContextWindowUnavailable:
        raise
    except Exception as first_error:
        logger.warning(
            "Context-window probe failed for %s; refreshing the tunnel", model_key
        )
        try:
            tunnel.refresh_after_failure(base_url)
        except Exception:
            logger.warning("Could not refresh the tunnel URL", exc_info=True)
        refreshed = settings.VLLM_BASE_URL.rstrip("/")
        try:
            window = _probe(model_key, refreshed)
        except Exception as second_error:
            raise ContextWindowUnavailable(
                f"Could not read {model_key}'s window from {base_url} "
                f"({first_error!r}) or {refreshed} ({second_error!r})."
            ) from second_error
        key = (model_key, refreshed)

    with _lock:
        _cache[key] = window
    logger.info("%s serves a %d-token window", model_key, window)
    return window


def fixed_overhead(system_prompt: str, tools: list[BaseTool] | None = None) -> int:
    """Tokens every call carries before a single message is added.

    The system prompt and each tool's JSON schema are re-sent on every request,
    so no amount of summarising reduces them. Counted with the same approximate
    counter the compaction trigger uses, deliberately: the two numbers are
    subtracted from one another, and mixing an exact count with an approximate
    one would leave a residue that looks like drift.
    """
    tool_dicts = [convert_to_openai_tool(tool) for tool in (tools or [])]
    return count_tokens_approximately(
        [SystemMessage(system_prompt)],
        tools=tool_dicts or None,
    )


def usable_context_window(
    model_key: str,
    system_prompt: str,
    tools: list[BaseTool] | None = None,
) -> int:
    """What the conversation itself can occupy, after the unremovable part.

    This is the number a compaction fraction should be taken of, and the number
    a "how full is it" indicator should divide by. Using the reported window for
    either makes 100% a level the conversation can never reach: the call would
    already have failed by then, short by exactly this overhead.
    """
    reported = reported_context_window(model_key)
    overhead = fixed_overhead(system_prompt, tools)
    usable = reported - overhead
    if usable <= 0:
        raise ContextWindowUnavailable(
            f"{model_key}'s system prompt and tools need {overhead} tokens of a "
            f"{reported}-token window, leaving nothing for the conversation."
        )
    return usable


def clear_cache() -> None:
    """Forget probed windows. For tests, and for a deliberate re-probe."""
    with _lock:
        _cache.clear()
