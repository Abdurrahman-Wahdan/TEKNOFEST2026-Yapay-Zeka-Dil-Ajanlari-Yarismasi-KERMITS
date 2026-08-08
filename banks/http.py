"""Shared HTTP clients for the bank endpoints.

One client per transport, reused across the process. Creating a client per call
leaks sockets and costs a handshake each time.

Three transports, not one. Most banks answer plain httpx; Vakıf and Dünya add a
per-page anti-forgery token (see csrf_token below); and Albaraka and Emlak sit
behind an F5 WAF that fingerprints the TLS handshake and rejects httpx and curl
outright, whatever the headers, cookies or HTTP version, where curl_cffi
impersonating Chrome passes unchanged. Providers declare which they need through
BaseBank.transport and are otherwise unaware.

    from banks.http import request_json

    request_json("GET", url, headers={"referer": page})
    request_json("GET", url, headers=..., impersonate="chrome124")
"""

import logging
import re
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
                    "The Albaraka and Emlak WAFs reject httpx, so curl_cffi is "
                    "required. Install with: pip install curl_cffi"
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
    data: dict | None = None,
    impersonate: str | None = None,
):
    """Call an endpoint and return the raw response.

    Retries settings.BANK_HTTP_RETRIES times on a transport error.

    Args:
        json: A JSON body.
        data: A form-encoded body. Vakıf and Dünya put their parameters in the
            query string and their anti-forgery token in the form body.

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
                method, url, headers=headers, params=params, json=json, data=data
            )
        except Exception as exc:  # noqa: BLE001 - retried, then re-raised below
            last_exc = exc
            logger.debug("%s %s failed (attempt %d): %s", method, url, attempt + 1, exc)
            continue

        if response.status_code >= 400:
            message = _message_of(response)
            # The URL carries the bank's opaque calculator hash and belongs in
            # the log, not in an answer a user may read.
            logger.warning("%s %s returned %s", method, url, response.status_code)
            raise ValueError(
                f"The bank returned {response.status_code}"
                + (f": {message}" if message else " with no explanation.")
            )
        return response

    logger.warning("%s %s failed: %s", method, url, last_exc)
    raise ValueError(
        f"The bank could not be reached: {type(last_exc).__name__}: {last_exc}"
    )


def _decode(response, url: str) -> Any:
    """Decode a JSON body, or None when the bank sent nothing.

    A zero-length 200 is how Vakıf says "this term is not offered for this
    currency". Decoding it raises a JSONDecodeError that reads like a broken
    endpoint, so it becomes None and the provider decides what it means.
    """
    if not (response.text or "").strip():
        logger.debug("Empty 200 body from %s", url)
        return None
    try:
        return response.json()
    except ValueError as exc:
        raise ValueError(
            f"{url} returned a body that is not JSON: {response.text[:120]!r}"
        ) from exc


def request_json(
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    params: dict | None = None,
    json: Any = None,
    data: dict | None = None,
    impersonate: str | None = None,
    retry_if: Callable[[Any], bool] | None = None,
) -> Any:
    """Call an endpoint and return the decoded JSON, or None for an empty body.

    Args:
        retry_if: Called with the decoded payload. When it returns True the
            call is repeated, up to settings.BANK_HTTP_RETRIES times. Kuveyt
            Türk's finance endpoint intermittently answers 200 with an empty
            Meta, and one retry separates that from a genuinely bad product.
    """
    def call():
        return _decode(
            request(
                method, url, headers=headers, params=params, json=json, data=data,
                impersonate=impersonate,
            ),
            url,
        )

    payload = call()
    if retry_if is None:
        return payload

    for _ in range(settings.BANK_HTTP_RETRIES):
        if not retry_if(payload):
            return payload
        logger.debug("Retrying %s %s: response looked empty", method, url)
        payload = call()

    return payload


def request_text(
    url: str,
    *,
    headers: dict | None = None,
    impersonate: str | None = None,
) -> str:
    """Fetch a page as text. Albaraka's product catalogue is HTML, not an API."""
    return request("GET", url, headers=headers, impersonate=impersonate).text


_TOKEN = re.compile(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"')
_TOKENS: dict[str, str] = {}


def csrf_token(
    page_url: str,
    *,
    headers: dict | None = None,
    impersonate: str | None = None,
    refresh: bool = False,
) -> str:
    """The ASP.NET anti-forgery token embedded in a calculator page.

    Vakıf and Dünya guard every plugin with one. It belongs to the page, so the
    page you are about to call is the one to read it from. Cached, because
    fetching a 200 KB page per quote is most of the latency; pass refresh=True
    to replace one the server has stopped accepting.
    """
    if not refresh:
        cached = _TOKENS.get(page_url)
        if cached is not None:
            return cached

    html = request_text(page_url, headers=headers, impersonate=impersonate)
    found = _TOKEN.search(html)
    if not found:
        raise ValueError(
            "The calculator page carried no anti-forgery token. Its layout may "
            "have changed, or the response was not the calculator page."
        )
    with _LOCK:
        _TOKENS[page_url] = found.group(1)
    return found.group(1)


def clear_http_cache() -> None:
    """Close and drop every cached client and anti-forgery token."""
    with _LOCK:
        for client in _CACHE.values():
            try:
                client.close()
            except Exception:  # noqa: BLE001 - closing must never raise
                logger.debug("Ignoring error while closing bank client", exc_info=True)
        count = len(_CACHE)
        _CACHE.clear()
        _TOKENS.clear()
    logger.debug("Closed %d bank HTTP client(s)", count)
