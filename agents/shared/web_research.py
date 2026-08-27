"""Bank-bound live web research tools for one specialist.

The supervisor never imports these tools.  ``build_bank_web_tools`` closes over
one bank's :class:`corpus.models.Site`, so neither tool exposes a bank or domain
argument to the model:

``search_bank_web`` discovers pages through the local SearXNG service and drops
every result outside that bank's root domain.  ``read_bank_source`` opens an
exact page/PDF URL from a table, a retrieved chunk, or a search result.  The
reader validates the original URL and every redirect, which prevents a bank
specialist from being redirected to another bank or to an internal address.

Both tools return JSON evidence envelopes.  The envelope is deliberate: the
specialist's prose is private and only its final response reaches the
supervisor, so the model needs exact URLs, timestamps, source types, and failure
states in a shape it can carry forward without guessing.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import logging
import socket
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from banks import clock
from config.settings import settings
from corpus import extract as corpus_extract
from corpus.models import Site
from corpus.fetch import TRUSTED_PDF_HOSTS
from corpus.sites import get_site
from corpus.urls import is_pdf, same_site

logger = logging.getLogger(__name__)


class WebSearchInput(BaseModel):
    query: str = Field(
        min_length=2,
        max_length=500,
        description=(
            "A focused Turkish search query about this bank. Do not put a URL "
            "here; use read_bank_source for an exact URL."
        ),
    )


class ReadSourceInput(BaseModel):
    url: str = Field(
        min_length=8,
        max_length=4096,
        description=(
            "An exact source URL belonging to this bank, copied from an "
            "attachment, retrieval result, or search result."
        ),
    )


_SEARCH_CACHE: dict[tuple[str, str], tuple[float, str]] = {}
_READ_CACHE: dict[tuple[str, str], tuple[float, str]] = {}
_CACHE_LOCK = threading.Lock()


def clear_web_research_cache() -> None:
    """Forget process-local results; used by tests and operational refreshes."""
    with _CACHE_LOCK:
        _SEARCH_CACHE.clear()
        _READ_CACHE.clear()


def _cached(cache: dict, key: tuple[str, str]) -> str | None:
    with _CACHE_LOCK:
        found = cache.get(key)
        if found and time.monotonic() - found[0] <= settings.WEB_RESEARCH_CACHE_SECONDS:
            return found[1]
        if found:
            cache.pop(key, None)
    return None


def _remember(cache: dict, key: tuple[str, str], value: str) -> str:
    with _CACHE_LOCK:
        cache[key] = (time.monotonic(), value)
    return value


def _json(**payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _allowed_url(url: str, site: Site) -> bool:
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return False
    if parts.username or parts.password:
        return False
    try:
        port = parts.port
    except ValueError:
        return False
    if port not in (None, 80, 443):
        return False
    if same_site(url, site.root_domain):
        return True
    host = parts.hostname.lower()
    return is_pdf(url) and any(
        host == trusted or host.endswith("." + trusted)
        for trusted in TRUSTED_PDF_HOSTS
    )


def _assert_public_host(url: str) -> None:
    """Reject DNS answers that could turn a public URL into an SSRF target."""
    host = urlsplit(url).hostname or ""
    try:
        answers = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"The source host could not be resolved: {host}") from exc
    if not answers:
        raise ValueError(f"The source host could not be resolved: {host}")
    for answer in answers:
        address = ipaddress.ip_address(answer[4][0])
        if not address.is_global:
            raise ValueError("The source resolved to a non-public network address.")


def _validate_url(url: str, site: Site) -> None:
    if not _allowed_url(url, site):
        raise ValueError(
            f"This specialist may only open {site.root_domain}, its subdomains, "
            "or a PDF on the approved Turkish financial-authority list."
        )
    _assert_public_host(url)


def _bank_search_query(site: Site, query: str) -> str:
    """Bias discovery toward one bank without relying on ``site:`` syntax.

    SearXNG passes query syntax through to each upstream engine, and not every
    engine implements Google's ``site:`` operator consistently. Results are
    already constrained by :func:`_allowed_url`, so a plain bank-name query has
    better recall without weakening the specialist's bank boundary.
    """
    focused = " ".join(query.split())
    # Every registered Turkish bank has its distinctive public brand in the
    # first two words ("Kuveyt Türk", "Vakıf Katılım", "Türkiye Finans", ...).
    brand = " ".join(site.display_name.split()[:2])
    if (
        brand.casefold() in focused.casefold()
        or site.root_domain.casefold() in focused.casefold()
    ):
        return focused
    return f"{brand} {focused}"


def _search(site: Site, query: str) -> str:
    key = (site.slug, " ".join(query.split()).casefold())
    if cached := _cached(_SEARCH_CACHE, key):
        logger.info(
            "web_tool cache_hit tool=search_bank_web bank=%s query_chars=%d",
            site.slug,
            len(query),
        )
        return cached

    logger.info(
        "web_tool invoked tool=search_bank_web bank=%s query_chars=%d",
        site.slug,
        len(query),
    )

    outbound_query = _bank_search_query(site, query)
    params = {
        "q": outbound_query,
        "format": "json",
        "language": "tr-TR",
        "categories": "general",
        "safesearch": 0,
    }
    try:
        response = httpx.get(
            settings.WEB_SEARCH_URL.rstrip("/") + "/search",
            params=params,
            timeout=settings.WEB_SEARCH_TIMEOUT,
            trust_env=False,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 - one search failure is evidence, not a crash
        logger.warning(
            "web_tool failed tool=search_bank_web bank=%s error=%s",
            site.slug,
            type(exc).__name__,
        )
        return _json(
            bank=site.slug,
            tool="search_bank_web",
            source_type="web_search",
            retrieved_at=clock.stamp_tr(),
            status="error",
            query=query,
            message=f"SearXNG search failed ({type(exc).__name__}).",
            results=[],
        )

    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in payload.get("results") or []:
        url = str(item.get("url") or "").strip()
        if not _allowed_url(url, site) or url in seen:
            continue
        seen.add(url)
        results.append({
            "title": " ".join(str(item.get("title") or "").split())[:500],
            "url": url,
            "snippet": " ".join(str(item.get("content") or "").split())[:1200],
        })
        if len(results) >= settings.WEB_SEARCH_MAX_RESULTS:
            break

    upstream_results = payload.get("results") or []
    unresponsive_engines = (payload.get("unresponsive_engines") or [])[:12]
    if results:
        status = "ok"
    elif not upstream_results and unresponsive_engines:
        status = "search_unavailable"
    else:
        status = "no_results"

    value = _json(
        bank=site.slug,
        tool="search_bank_web",
        source_type="web_search",
        retrieved_at=clock.stamp_tr(),
        status=status,
        query=query,
        searched_query=outbound_query,
        results=results,
        unresponsive_engines=unresponsive_engines,
        message=(
            "Every selected upstream search engine was unavailable for this request."
            if status == "search_unavailable"
            else ""
        ),
        note=(
            "Search snippets are discovery hints, not evidence. "
            + (
                "Open a result with read_bank_source before using its claims."
                if settings.WEB_READ_SOURCE_ENABLED
                else "The direct reader is disabled, so page claims cannot be verified."
            )
        ),
    )
    logger.info(
        "web_tool completed tool=search_bank_web bank=%s status=%s results=%d unresponsive_engines=%d",
        site.slug,
        status,
        len(results),
        len(payload.get("unresponsive_engines") or []),
    )
    # Cache only positive discoveries. A CAPTCHA, rate limit, or temporary
    # upstream outage must not become a five-minute false negative.
    return _remember(_SEARCH_CACHE, key, value) if status == "ok" else value


def _download(site: Site, requested_url: str) -> tuple[bytes, str, str, int]:
    """Download with a byte ceiling and validate every redirect target."""
    url = requested_url.strip()
    _validate_url(url, site)
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.5",
        "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.5",
        "User-Agent": settings.WEB_RESEARCH_USER_AGENT,
    }
    with httpx.Client(
        headers=headers,
        timeout=settings.WEB_READ_TIMEOUT,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        for _ in range(settings.WEB_READ_MAX_REDIRECTS + 1):
            with client.stream("GET", url) as response:
                if response.is_redirect:
                    target = urljoin(url, response.headers.get("location", ""))
                    _validate_url(target, site)
                    url = target
                    continue
                status = response.status_code
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                body = bytearray()
                for piece in response.iter_bytes():
                    body.extend(piece)
                    if len(body) > settings.WEB_READ_MAX_BYTES:
                        raise ValueError(
                            f"The source exceeds the {settings.WEB_READ_MAX_BYTES} byte safety limit."
                        )
                return bytes(body), url, content_type, status
    raise ValueError("The source exceeded the redirect limit.")


def _pdf_text(data: bytes) -> tuple[str, str]:
    """Read a PDF text layer with Poppler first, then pypdf as a fallback."""
    try:
        from corpus.pdftools import PdfToolError, text_pages

        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "source.pdf"
            path.write_bytes(data)
            pages = text_pages(path)
        text = "\n\n".join(
            f"<!-- page {number} -->\n{page.strip()}"
            for number, page in enumerate(pages, 1)
            if page.strip()
        )
        if text.strip():
            return text, "pdftotext"
    except (ImportError, PdfToolError, OSError):
        pass

    from io import BytesIO
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data))
    parts = []
    for number, page in enumerate(reader.pages[: settings.WEB_READ_MAX_PDF_PAGES], 1):
        parts.append(f"<!-- page {number} -->\n{page.extract_text() or ''}")
    return "\n\n".join(parts), "pypdf"


def _image_text(data: bytes, content_type: str) -> tuple[str, str]:
    """Transcribe a bank-hosted source image with the configured vision model."""
    from langchain_core.messages import HumanMessage
    from llm import get_llm

    prompt = (
        "Transcribe and describe this Turkish participation-bank source image. "
        "Preserve every visible heading, condition, date, amount, rate, table "
        "cell, footnote and disclaimer exactly. Do not infer missing text and do "
        "not translate. Return concise markdown."
    )
    encoded = base64.b64encode(data).decode()
    response = get_llm("chat", disable_streaming=True).invoke([
        HumanMessage(content=[
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {
                "url": f"data:{content_type};base64,{encoded}"
            }},
        ])
    ])
    content = response.content
    return (content if isinstance(content, str) else str(content)), "vision_model"


def _looks_like_error_page(status: int, final_url: str, title: str, text: str) -> bool:
    if status >= 400:
        return True
    path = urlsplit(final_url).path.rstrip("/").lower()
    head = f"{title}\n{text[:700]}".casefold()
    error_path = path.endswith(("/401", "/403", "/404", "/500"))
    signals = ("sayfa bulunamadı", "page not found", "erişim reddedildi", "yetkisiz erişim")
    return error_path or any(signal in head for signal in signals)


def _read(site: Site, url: str) -> str:
    key = (site.slug, url.strip())
    if cached := _cached(_READ_CACHE, key):
        logger.info(
            "web_tool cache_hit tool=read_bank_source bank=%s host=%s",
            site.slug,
            urlsplit(url).hostname or "invalid",
        )
        return cached
    logger.info(
        "web_tool invoked tool=read_bank_source bank=%s host=%s",
        site.slug,
        urlsplit(url).hostname or "invalid",
    )
    try:
        data, final_url, content_type, status = _download(site, url)
        is_pdf = content_type == "application/pdf" or data.startswith(b"%PDF")
        is_image = content_type.startswith("image/")
        if is_pdf:
            text, engine = _pdf_text(data)
            title = Path(urlsplit(final_url).path).name or "PDF"
            source_type = "live_web_pdf"
        elif is_image:
            text, engine = _image_text(data, content_type)
            title = Path(urlsplit(final_url).path).name or "Image"
            source_type = "live_web_image"
        else:
            html = data.decode("utf-8", "replace")
            extracted = corpus_extract.extract(html, final_url, site)
            text = extracted.get("text") or ""
            title = extracted.get("title") or ""
            engine = "trafilatura"
            source_type = "live_web_page"

        text = text.strip()
        if _looks_like_error_page(status, final_url, title, text):
            result_status = "unavailable"
            message = "The bank returned an error or not-found page for this URL."
        elif not text:
            result_status = "unreadable"
            message = "The source downloaded but yielded no extractable text."
        else:
            result_status = "ok"
            message = ""

        truncated = len(text) > settings.WEB_READ_MAX_CHARS
        value = _json(
            bank=site.slug,
            tool="read_bank_source",
            source_type=source_type,
            retrieved_at=clock.stamp_tr(),
            status=result_status,
            requested_url=url,
            url=final_url,
            http_status=status,
            content_type=content_type,
            title=title,
            extraction_engine=engine,
            text=text[: settings.WEB_READ_MAX_CHARS],
            truncated=truncated,
            message=message,
        )
    except Exception as exc:  # noqa: BLE001 - a bad source must not end the bank turn
        logger.warning(
            "web_tool failed tool=read_bank_source bank=%s host=%s error=%s",
            site.slug,
            urlsplit(url).hostname or "invalid",
            type(exc).__name__,
        )
        return _json(
            bank=site.slug,
            tool="read_bank_source",
            source_type="live_web_source",
            retrieved_at=clock.stamp_tr(),
            status="error",
            requested_url=url,
            message=str(exc)[:1000],
        )
    logger.info(
        "web_tool completed tool=read_bank_source bank=%s host=%s status=%s",
        site.slug,
        urlsplit(url).hostname or "invalid",
        result_status,
    )
    return _remember(_READ_CACHE, key, value)


async def _asearch(site: Site, query: str) -> str:
    return await asyncio.to_thread(_search, site, query)


async def _aread(site: Site, url: str) -> str:
    return await asyncio.to_thread(_read, site, url)


def build_bank_web_tools(
    bank_name: str,
    *,
    include_reader: bool | None = None,
) -> list[BaseTool]:
    """Build the optional web surface for exactly one bank.

    ``include_reader=False`` exists for controlled search-quality assessments.
    It removes the tool from the schema entirely; prompting alone would not
    prove that the model had no direct-reader path available.
    """
    site = get_site(bank_name)
    if include_reader is None:
        include_reader = settings.WEB_READ_SOURCE_ENABLED
    tools: list[BaseTool] = [
        StructuredTool.from_function(
            func=lambda query: _search(site, query),
            coroutine=lambda query: _asearch(site, query),
            name="search_bank_web",
            description=(
                f"Search the current public web presence of {site.display_name} only. "
                "Use this to discover relevant pages on this bank's domain. "
                "Results outside the bank's domain are removed. Search snippets are not "
                "evidence; "
                + (
                    "open the chosen URL with read_bank_source."
                    if include_reader
                    else "the reader is disabled, so report them as unverified leads."
                )
            ),
            args_schema=WebSearchInput,
        ),
    ]
    if include_reader:
        tools.append(StructuredTool.from_function(
            func=lambda url: _read(site, url),
            coroutine=lambda url: _aread(site, url),
            name="read_bank_source",
            description=(
                f"Open and extract an exact current HTML, PDF, or image source URL for "
                f"{site.display_name}. Use it directly for URLs supplied in attached "
                "tables, retrieved chunks, or search results. It cannot open another "
                "bank's domain (plus bank-linked regulator PDFs) and returns citable "
                "URL, retrieval time, and extracted source text."
            ),
            args_schema=ReadSourceInput,
        ))
    return tools
