"""Shared HTTP clients for the bank endpoints.

One client per transport, reused across the process. Creating a client per call
leaks sockets and costs a handshake each time.

Two transports, not one. Most banks answer plain httpx. Albaraka sits behind an
F5 WAF that fingerprints the TLS handshake and rejects httpx and curl outright,
whatever the headers, cookies or HTTP version; curl_cffi impersonating Chrome
passes unchanged. Callers pick with `impersonate` and are otherwise unaware.

    from banks.http import request_json

    request_json("GET", url, headers={"referer": page})
    request_json("GET", url, headers=..., impersonate="chrome124")
"""

import logging
import threading
from typing import Any, Callable

import httpx

from config.settings import settings

logger = logging.getLogger(__name__)

_CACHE: dict[str | None, Any] = {}
_LOCK = threading.Lock()


def get_client(impersonate: str | None = None):
    """Get or create the client for a transport.

    Args:
        impersonate: None for httpx. A curl_cffi browser target
            ("chrome124") for banks whose WAF checks the TLS fingerprint.

    Raises:
        RuntimeError: if curl_cffi is asked for and is not installed.
    """
    # Fast path without the lock for the common case.
    cached = _CACHE.get(impersonate)
    if cached is not None:
        return cached

    # Double-check under the lock so concurrent callers share one client.
    with _LOCK:
        cached = _CACHE.get(impersonate)
        if cached is not None:
            return cached

        if impersonate is None:
            logger.debug("Creating httpx client for bank endpoints")
            client = httpx.Client(
                timeout=settings.BANK_HTTP_TIMEOUT,
                headers={"user-agent": settings.BANK_USER_AGENT},
                follow_redirects=True,
            )
        else:
            try:
                from curl_cffi import requests as curl_requests
            except ImportError as exc:
                raise RuntimeError(
                    "Albaraka's WAF rejects httpx, so curl_cffi is required. "
                    "Install with: pip install curl_cffi"
                ) from exc
            logger.debug("Creating curl_cffi client impersonating %s", impersonate)
            client = curl_requests.Session(
                impersonate=impersonate,
                timeout=settings.BANK_HTTP_TIMEOUT,
            )

        _CACHE[impersonate] = client
        return client


def _message_of(response) -> str:
    """The bank's own error sentence, when it sends one.

    Kuveyt Türk answers an out-of-range term with 400 and
    {"Message":[{"ErrorMessage": "Lütfen 31 değerine eşit ya da..."}]}. That
    sentence is more useful to the user than the status code, so it is what the
    caller sees.
    """
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 - a non-JSON error body is normal
        return ""
    if isinstance(body, dict):
        messages = body.get("Message") or body.get("Errors") or []
        if isinstance(messages, list):
            texts = [
                m.get("ErrorMessage", "")
                for m in messages
                if isinstance(m, dict) and m.get("ErrorMessage")
            ]
            if texts:
                return " ".join(texts)
        if isinstance(body.get("Error"), str):
            return body["Error"]
    return ""


def request(
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    params: dict | None = None,
    json: Any = None,
    impersonate: str | None = None,
):
    """Call an endpoint and return the raw response.

    Retries settings.BANK_HTTP_RETRIES times on a transport error.

    Raises:
        ValueError: on any non-2xx, carrying the bank's own message when it
            sends one. These endpoints answer 4xx for out-of-range input, which
            is the case worth reporting.
    """
    client = get_client(impersonate)
    last_exc: Exception | None = None

    for attempt in range(settings.BANK_HTTP_RETRIES + 1):
        try:
            response = client.request(
                method, url, headers=headers, params=params, json=json
            )
        except Exception as exc:  # noqa: BLE001 - retried, then re-raised below
            last_exc = exc
            logger.debug("%s %s failed (attempt %d): %s", method, url, attempt + 1, exc)
            continue

        if response.status_code >= 400:
            message = _message_of(response)
            raise ValueError(
                f"{method} {url} returned {response.status_code}"
                + (f": {message}" if message else "")
            )
        return response

    raise ValueError(f"{method} {url} failed: {type(last_exc).__name__}: {last_exc}")


def request_json(
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    params: dict | None = None,
    json: Any = None,
    impersonate: str | None = None,
    retry_if: Callable[[Any], bool] | None = None,
) -> Any:
    """Call an endpoint and return the decoded JSON.

    Args:
        retry_if: Called with the decoded payload. When it returns True the
            call is repeated, up to settings.BANK_HTTP_RETRIES times. Kuveyt
            Türk's finance endpoint intermittently answers 200 with an empty
            Meta, and one retry separates that from a genuinely bad product.
    """
    payload = request(
        method, url, headers=headers, params=params, json=json,
        impersonate=impersonate,
    ).json()

    if retry_if is None:
        return payload

    for _ in range(settings.BANK_HTTP_RETRIES):
        if not retry_if(payload):
            return payload
        logger.debug("Retrying %s %s: response looked empty", method, url)
        payload = request(
            method, url, headers=headers, params=params, json=json,
            impersonate=impersonate,
        ).json()

    return payload


def request_text(
    url: str,
    *,
    headers: dict | None = None,
    impersonate: str | None = None,
) -> str:
    """Fetch a page as text. Albaraka's product catalogue is HTML, not an API."""
    return request("GET", url, headers=headers, impersonate=impersonate).text


def clear_http_cache() -> None:
    """Close and drop every cached client."""
    with _LOCK:
        for client in _CACHE.values():
            try:
                client.close()
            except Exception:  # noqa: BLE001 - closing must never raise
                logger.debug("Ignoring error while closing bank client", exc_info=True)
        count = len(_CACHE)
        _CACHE.clear()
    logger.debug("Closed %d bank HTTP client(s)", count)
