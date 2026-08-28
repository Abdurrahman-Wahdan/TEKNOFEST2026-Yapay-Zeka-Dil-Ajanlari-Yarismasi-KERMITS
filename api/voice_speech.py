"""Streaming client for the remote Trendyol speech service.

The speech server exposes ``POST /speech`` and returns raw signed 16-bit PCM
at 48 kHz, mono. The response must be consumed through ``Client.stream``:
``httpx.post`` buffers the whole response and removes low-latency playback.
"""

from __future__ import annotations

import logging
import re
import threading
from collections import OrderedDict

import httpx

from config.settings import settings

logger = logging.getLogger(__name__)

_INFERENCE_LOCK = threading.Lock()
_ACTIVE_CLIENT_LOCK = threading.Lock()
_ACTIVE_CLIENT: httpx.Client | None = None
_AUDIO_CACHE_LOCK = threading.Lock()
_AUDIO_CACHE: OrderedDict[str, tuple[bytes, ...]] = OrderedDict()
_AUDIO_CACHE_MAX_ENTRIES = 2
_AUDIO_CACHE_MAX_BYTES = 8 * 1024 * 1024
_CHUNK_BYTES = 8192
_BUSY_RETRIES = 3


class VoiceSpeechUnavailable(RuntimeError):
    """The remote speech service cannot be reached or is not configured."""


class VoiceSpeechFailed(RuntimeError):
    """The remote speech service rejected or interrupted the reading."""


_TERMINATOR = re.compile(r"[.!?…]+")


def sample_rate() -> int:
    return settings.SPEECH_SAMPLE_RATE


def prepare() -> int:
    """Return the remote stream's PCM rate without making a model request."""
    if not settings.SPEECH_REMOTE_URL.strip():
        raise VoiceSpeechUnavailable("SPEECH_REMOTE_URL is not configured.")
    return sample_rate()


def _sentences(text: str) -> list[str]:
    pieces: list[str] = []
    start = 0
    for match in _TERMINATOR.finditer(text):
        end = match.end()
        following = text[end : end + 1]
        if following and not following.isspace():
            continue
        pieces.append(text[start:end])
        start = end
    if start < len(text):
        pieces.append(text[start:])
    return pieces


def segments(text: str, budget: int | None = None) -> list[str]:
    """Keep long answers within the remote service's practical request size."""
    limit = budget or settings.SPEECH_REMOTE_SEGMENT_CHARS
    pieces: list[str] = []
    current = ""
    for sentence in _sentences(text):
        candidate = f"{current}{sentence}" if current else sentence
        if current and len(candidate.strip()) > limit:
            pieces.append(current.strip())
            current = sentence
            continue
        current = candidate
    if current.strip():
        pieces.append(current.strip())
    return pieces


def speak(text: str):
    """Yield remote PCM bytes as the server generates them.

    The stream contexts live inside this generator, so closing it on browser
    Stop closes the upstream response and releases the remote connection.
    """
    url = settings.SPEECH_REMOTE_URL.strip()
    if not url:
        raise VoiceSpeechUnavailable("SPEECH_REMOTE_URL is not configured.")

    timeout = httpx.Timeout(
        connect=settings.SPEECH_REMOTE_CONNECT_TIMEOUT_SECONDS,
        read=settings.SPEECH_REMOTE_READ_TIMEOUT_SECONDS,
        write=settings.SPEECH_REMOTE_WRITE_TIMEOUT_SECONDS,
        pool=settings.SPEECH_REMOTE_CONNECT_TIMEOUT_SECONDS,
    )
    try:
        # A fresh client prevents a stopped request from reusing a stale
        # keep-alive connection against the remote single-worker server.
        with httpx.Client(timeout=timeout, headers={"Connection": "close"}) as client:
            with _ACTIVE_CLIENT_LOCK:
                global _ACTIVE_CLIENT
                _ACTIVE_CLIENT = client
            try:
                for attempt in range(_BUSY_RETRIES + 1):
                    with client.stream("POST", url, json={"text": text}) as response:
                        if response.status_code == 503 and attempt < _BUSY_RETRIES:
                            detail = response.read()[:200].decode("utf-8", "replace")
                            logger.info(
                                "Remote TTS is busy; retrying immediately (%d/%d): %s",
                                attempt + 1,
                                _BUSY_RETRIES,
                                detail,
                            )
                            continue
                        if response.status_code >= 400:
                            detail = response.read()[:200].decode("utf-8", "replace")
                            raise VoiceSpeechFailed(
                                f"Remote TTS returned HTTP {response.status_code}: {detail}"
                            )
                        yield from response.iter_bytes(_CHUNK_BYTES)
                        return
            finally:
                with _ACTIVE_CLIENT_LOCK:
                    if _ACTIVE_CLIENT is client:
                        _ACTIVE_CLIENT = None
    except VoiceSpeechFailed:
        raise
    except httpx.HTTPError as exc:
        raise VoiceSpeechUnavailable(f"Remote TTS is unreachable: {exc}") from exc
    finally:
        logger.info("remote_speech_closed chars=%d", len(text))


def cached_audio(text: str) -> tuple[bytes, ...] | None:
    with _AUDIO_CACHE_LOCK:
        chunks = _AUDIO_CACHE.get(text)
        if chunks is not None:
            _AUDIO_CACHE.move_to_end(text)
        return chunks


def remember_audio(text: str, chunks: list[bytes]) -> None:
    frozen = tuple(chunks)
    size = sum(len(chunk) for chunk in frozen)
    if not frozen or size > _AUDIO_CACHE_MAX_BYTES:
        return
    with _AUDIO_CACHE_LOCK:
        previous = _AUDIO_CACHE.pop(text, None)
        if previous is not None:
            size -= sum(len(chunk) for chunk in previous)
        _AUDIO_CACHE[text] = frozen
        while (
            len(_AUDIO_CACHE) > _AUDIO_CACHE_MAX_ENTRIES
            or sum(sum(len(chunk) for chunk in value) for value in _AUDIO_CACHE.values())
            > _AUDIO_CACHE_MAX_BYTES
        ):
            _AUDIO_CACHE.popitem(last=False)


def clear_audio_cache() -> None:
    with _AUDIO_CACHE_LOCK:
        _AUDIO_CACHE.clear()


def audio_cache_max_bytes() -> int:
    return _AUDIO_CACHE_MAX_BYTES


def cancel_active() -> None:
    """Close the previous remote reading so a new voice turn can take over."""
    with _ACTIVE_CLIENT_LOCK:
        client = _ACTIVE_CLIENT
    if client is not None:
        logger.info("Cancelling previous remote speech connection")
        client.close()


def acquire(timeout: float | None = None) -> bool:
    return _INFERENCE_LOCK.acquire(
        timeout=settings.SPEECH_QUEUE_TIMEOUT_SECONDS if timeout is None else timeout
    )


def release() -> None:
    try:
        _INFERENCE_LOCK.release()
    except RuntimeError:
        logger.warning("Speech lock released twice")
