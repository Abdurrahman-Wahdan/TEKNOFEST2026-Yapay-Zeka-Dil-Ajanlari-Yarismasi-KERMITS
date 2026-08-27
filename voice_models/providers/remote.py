"""OpenAI-compatible voice providers served through the shared model tunnel."""

from __future__ import annotations

import logging
import struct
import threading
import time
from contextlib import contextmanager
from collections.abc import Iterable, Iterator
from pathlib import Path

import httpx

from config import tunnel
from config.settings import settings
from voice_models.errors import (
    VoiceSpeechFailed,
    VoiceSpeechUnavailable,
    VoiceTranscriptionFailed,
    VoiceTranscriptionUnavailable,
)

from .base import BaseSpeechProvider, BaseTranscriptionProvider

logger = logging.getLogger(__name__)


def _base_url() -> str:
    """The voice host, falling back to the same rotating URL as LLM/embed."""
    return (settings.VOICE_REMOTE_BASE_URL or tunnel.current_base_url()).rstrip("/")


def _url(route: str, operation: str) -> str:
    return f"{_base_url()}/{route.strip('/')}/{operation.lstrip('/')}"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.VOICE_REMOTE_API_KEY}",
        "Connection": "close",
    }


# The inverse of the router's `_CONTENT_SUFFIX`, and it has to be spelled out
# rather than left to `mimetypes.guess_type`. That returns **video/webm** for a
# `.webm` name -- which is what a browser recording is saved as, so the dominant
# case (Chrome/Edge record `audio/webm;codecs=opus`) was uploading a *video*
# content type for an audio part. It also answers `audio/mp4a-latm` for `.m4a`,
# a name no OpenAI-compatible server matches. The upload already told us what it
# was; the suffix is only how it reached disk.
_AUDIO_TYPES = {
    ".webm": "audio/webm",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
}


def _audio_type(path: Path) -> str:
    return _AUDIO_TYPES.get(path.suffix.lower(), "application/octet-stream")


# The known-good client measured through this tunnel (`tts_client.py`) splits its
# budget across the four phases instead of handing one number to all of them,
# and that split is load-bearing: `httpx.Timeout(300.0)` sets *connect* to 300 s
# as well, so a tunnel that completes the TCP handshake and then goes quiet holds
# a FastAPI threadpool thread for five minutes. Connecting is either quick or
# never; only reading a long answer is legitimately slow.
_CONNECT_TIMEOUT = 10.0
_WRITE_TIMEOUT = 30.0
_POOL_TIMEOUT = 10.0


def _timeout(read: float) -> httpx.Timeout:
    return httpx.Timeout(
        connect=_CONNECT_TIMEOUT, read=read, write=_WRITE_TIMEOUT, pool=_POOL_TIMEOUT
    )


class _TunnelClient:
    """One pooled client per provider, thrown away whenever the tunnel fails.

    `TunnelAwareEmbeddings._recover` already does this on the embedding side and
    its log line names the reason -- the client is rebuilt *with a fresh
    connection*. Voice kept a single client for the life of the process, so its
    keep-alive pool outlived the tunnel that opened it. A pooled socket to a
    tunnel that has since been torn down is the worst case available: it does not
    refuse, it accepts and never answers, so the request burns the whole read
    timeout instead of failing in the time it takes to open a new connection.

    This is also what separates us from the standalone script that works. That
    one builds a client per call inside a `with`, so it can never reuse a socket
    the tunnel has forgotten about. A long-lived server has to reach the same
    place deliberately.
    """

    def __init__(self, read_timeout: float, client: httpx.Client | None = None) -> None:
        self._read_timeout = read_timeout
        # An injected client belongs to the caller (tests): keep it, and never
        # replace it, so a stubbed transport survives a simulated rotation.
        self._owned = client is None
        self._client = client or self._build()

    def _build(self, read_timeout: float | None = None) -> httpx.Client:
        return httpx.Client(
            timeout=_timeout(read_timeout or self._read_timeout),
            # NO KEEP-ALIVE, and this is the whole point of the class.
            #
            # Measured against the live tunnel: a reading and a transcription
            # interfere even when they are run one after the other. They share
            # one tunnel with a small connection budget, and a pool that keeps
            # eight sockets warm holds that budget after the work is finished --
            # so TTS finishes, its sockets stay open, and the next STT upload has
            # nowhere to land and stalls until its read timeout.
            #
            # `max_keepalive_connections=0` makes httpx close each connection as
            # soon as its response is done, which is exactly what the standalone
            # client that works does by building a client per call inside a
            # `with`. Reconnecting costs one handshake; not reconnecting cost us
            # the other half of the voice feature.
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=0),
        )

    @property
    def client(self) -> httpx.Client:
        return self._client

    def rebuild(self) -> None:
        """Drop every pooled connection and open the next request on a new one."""
        if not self._owned:
            return
        try:
            self._client.close()
        except Exception:
            logger.debug("Could not close the previous voice client", exc_info=True)
        self._client = self._build()

    @contextmanager
    def request_client(self, read_timeout: float | None = None) -> Iterator[httpx.Client]:
        """Yield a fresh client for each remote request.

        The working standalone client creates and closes ``httpx.Client`` around
        every synthesis. A long-lived client can retain transport state across
        a rotating tunnel even when keep-alive is disabled, so the provider
        follows the same lifecycle for production requests. Injected clients
        remain available for deterministic tests.
        """
        if not self._owned:
            yield self._client
            return
        with self._build(read_timeout) as client:
            yield client

    def close(self) -> None:
        if self._owned:
            self._client.close()


def _refresh_after(exc: Exception, failed_base: str) -> bool:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if not tunnel.is_tunnel_failure(exc) and status_code not in {404, 502, 503, 504}:
        return False
    try:
        tunnel.refresh_after_failure(failed_base)
    except Exception:
        logger.warning("Could not refresh the voice tunnel URL", exc_info=True)
    return True


class RemoteTranscriptionProvider(BaseTranscriptionProvider):
    """Multipart Whisper client for ``/v1/audio/transcriptions``."""

    provider_name = "remote"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._pool = _TunnelClient(settings.VOICE_REMOTE_TIMEOUT_SECONDS, client)
        # The remote Whisper process has one inference worker. Without a local
        # gate, an abandoned/timed-out recording keeps running remotely while
        # later recordings pile up behind it and all appear broken. Serialize
        # calls in this API process so only one retry sequence reaches that
        # worker at a time.
        self._inference_lock = threading.Lock()

    def close(self) -> None:
        self._pool.close()

    def warm(self) -> None:
        """Make the server load Whisper before the first person holds the button.

        Remote providers were treated as needing no warm-up, and that assumption
        is what made transcription look random. Measured against the live server:
        the first request after an idle period takes 60-70 s because large-v3 is
        reloaded, while the next one returns in about a second. Our own timed-out
        attempt is what triggered that load, so the failure always landed on a
        user and the retry always looked fine -- which reads as "STT works
        sometimes" rather than "STT has a cold start".

        One second of silence is enough to force the load, and it is thrown away.
        Failures here are deliberately swallowed: startup must not depend on the
        voice server being up, exactly as the local checkpoint path does not.
        """
        silence = b"\x00" * (16_000 * 2)
        wav = (
            b"RIFF" + (36 + len(silence)).to_bytes(4, "little") + b"WAVEfmt "
            + (16).to_bytes(4, "little") + (1).to_bytes(2, "little")
            + (1).to_bytes(2, "little") + (16_000).to_bytes(4, "little")
            + (32_000).to_bytes(4, "little") + (2).to_bytes(2, "little")
            + (16).to_bytes(2, "little")
            + b"data" + len(silence).to_bytes(4, "little") + silence
        )
        try:
            with self._pool.request_client() as client:
                response = client.post(
                    _url(settings.VOICE_REMOTE_STT_ROUTE, "audio/transcriptions"),
                    headers=_headers(),
                    files={"file": ("warm.wav", wav, "audio/wav")},
                    data={"model": settings.VOICE_REMOTE_STT_MODEL,
                          "language": settings.VOICE_LANGUAGE,
                          "response_format": "json"},
                    timeout=_timeout(settings.VOICE_REMOTE_WARM_SECONDS),
                )
                response.raise_for_status()
            logger.info("Remote STT warm")
        except Exception:
            logger.warning("Could not warm the remote STT model", exc_info=True)

    def transcribe(self, audio_path: Path) -> tuple[str, int]:
        acquired = self._inference_lock.acquire(
            timeout=settings.VOICE_REMOTE_RETRY_SECONDS
        )
        if not acquired:
            raise VoiceTranscriptionUnavailable(
                "The remote transcription worker is still processing another recording."
            )
        try:
            return self._transcribe_locked(audio_path)
        finally:
            self._inference_lock.release()

    def _transcribe_locked(self, audio_path: Path) -> tuple[str, int]:
        started = time.perf_counter()
        last_error: Exception | None = None
        deadline = time.monotonic() + settings.VOICE_REMOTE_RETRY_SECONDS
        delay = 1.0
        attempt = 1
        while True:
            base = _base_url()
            endpoint = _url(settings.VOICE_REMOTE_STT_ROUTE, "audio/transcriptions")
            content_type = _audio_type(audio_path)
            logger.info("Remote STT attempt=%d base=%s bytes=%d type=%s", attempt, base, audio_path.stat().st_size, content_type)
            try:
                with audio_path.open("rb") as audio:
                    with self._pool.request_client() as client:
                        response = client.post(
                            endpoint,
                            headers=_headers(),
                            files={"file": (audio_path.name, audio, content_type)},
                            data={
                                "model": settings.VOICE_REMOTE_STT_MODEL,
                                "language": settings.VOICE_LANGUAGE,
                                "response_format": "json",
                            },
                        )
                if response.status_code >= 400:
                    logger.warning("Remote STT attempt=%d status=%d elapsed_ms=%d", attempt, response.status_code, round((time.perf_counter() - started) * 1000))
                    if response.status_code in {404, 502, 503, 504}:
                        response.raise_for_status()
                    # Carry the server's own words. A 400 here is almost always
                    # "this container/codec is not supported" or a missing
                    # field, and discarding the body turned every one of those
                    # into the same unactionable status code in the log.
                    detail = response.text.strip()[:300]
                    raise VoiceTranscriptionFailed(
                        f"The transcription server rejected the audio "
                        f"({response.status_code}): {detail}"
                        if detail
                        else f"The transcription server rejected the audio ({response.status_code})."
                    )
                text = str(response.json().get("text", "")).strip()
                logger.info("Remote STT success attempt=%d text_chars=%d elapsed_ms=%d", attempt, len(text), round((time.perf_counter() - started) * 1000))
                return text, round((time.perf_counter() - started) * 1000)
            except VoiceTranscriptionFailed:
                raise
            except (httpx.HTTPError, OSError, ValueError) as exc:
                last_error = exc
                logger.warning("Remote STT exception attempt=%d type=%s elapsed_ms=%d", attempt, type(exc).__name__, round((time.perf_counter() - started) * 1000))
                if not _refresh_after(exc, base):
                    break
                # Whatever the tunnel did, this pool's sockets were opened
                # against the old one. Retrying on them re-runs the same hang.
                self._pool.rebuild()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(delay, remaining))
                delay = min(delay * 2, settings.LLM_RETRY_MAX_DELAY_SECONDS)
                attempt += 1
        raise VoiceTranscriptionUnavailable("The remote transcription service is unavailable.") from last_error


def _pcm_from_wav(chunks: Iterable[bytes], expected_rate: int) -> Iterator[bytes]:
    """Strip a streamed RIFF/WAVE container and yield its PCM data chunk.

    RIFF chunks may cross arbitrary network boundaries, and non-audio metadata
    may appear before ``data``. Buffer only the header chunks; once ``data`` is
    found, pass payload through without holding the complete reading in memory.
    """
    source = iter(chunks)
    buffer = bytearray()

    def fill(size: int) -> bool:
        while len(buffer) < size:
            try:
                buffer.extend(next(source))
            except StopIteration:
                return False
        return True

    if not fill(12) or bytes(buffer[:4]) != b"RIFF" or bytes(buffer[8:12]) != b"WAVE":
        raise VoiceSpeechFailed("The speech server returned an invalid WAV stream.")
    del buffer[:12]

    fmt_seen = False
    data_remaining: int | None = None
    data_until_eof = False
    while data_remaining is None:
        if not fill(8):
            raise VoiceSpeechFailed("The speech WAV stream ended before its data chunk.")
        chunk_id = bytes(buffer[:4])
        chunk_size = struct.unpack_from("<I", buffer, 4)[0]
        del buffer[:8]
        if chunk_id == b"data":
            if not fmt_seen:
                raise VoiceSpeechFailed("The speech WAV stream has no format chunk.")
            # A streaming WAV cannot know its final size when the header is
            # written. Trendyol-TTS uses 0x7fffffff for that case instead of
            # a real byte count; consume the data until the HTTP stream ends.
            if chunk_size in {0x7FFFFFFF, 0xFFFFFFFF} or chunk_size >= 0x7FFFF000:
                data_until_eof = True
            else:
                data_remaining = chunk_size
            break
        padded = chunk_size + (chunk_size % 2)
        if not fill(padded):
            raise VoiceSpeechFailed("The speech WAV header is truncated.")
        if chunk_id == b"fmt ":
            if chunk_size < 16:
                raise VoiceSpeechFailed("The speech WAV format chunk is invalid.")
            audio_format, channels, sample_rate, _, _, sample_width = struct.unpack_from(
                "<HHIIHH", buffer, 0
            )
            if audio_format != 1 or channels != 1 or sample_width != 16:
                raise VoiceSpeechFailed(
                    "The speech server must return mono 16-bit PCM WAV audio."
                )
            if sample_rate != expected_rate:
                raise VoiceSpeechFailed(
                    f"The speech server returned {sample_rate} Hz; expected {expected_rate} Hz."
                )
            fmt_seen = True
        del buffer[:padded]

    if data_until_eof:
        if buffer:
            yield bytes(buffer)
            buffer.clear()
        for payload in source:
            if payload:
                yield payload
        return

    while data_remaining > 0:
        if buffer:
            take = min(len(buffer), data_remaining)
            payload = bytes(buffer[:take])
            del buffer[:take]
        else:
            try:
                payload = next(source)
            except StopIteration as exc:
                raise VoiceSpeechFailed("The speech WAV data is truncated.") from exc
            if len(payload) > data_remaining:
                payload = payload[:data_remaining]
        data_remaining -= len(payload)
        if payload:
            yield payload


def _text_chunks(text: str, limit: int) -> list[str]:
    """Split text losslessly so each tunnel response stays bounded."""
    remaining = text.strip()
    chunks: list[str] = []
    while len(remaining) > limit:
        cut = remaining.rfind(" ", 0, limit + 1)
        if cut < max(1, limit // 2):
            cut = limit
        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


class RemoteSpeechProvider(BaseSpeechProvider):
    """Trendyol-TTS client for ``/v1/audio/speech``."""

    provider_name = "remote"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._pool = _TunnelClient(settings.SPEECH_REMOTE_TIMEOUT_SECONDS, client)
        # The remote Trendyol-TTS process is a single-reader model. Multiple
        # HTTP streams can all receive 200 headers, then compete for inference
        # and leave one another silent. Hold the reservation for the whole
        # streamed response, not just until its headers arrive.
        self._inference_lock = threading.Lock()

    def close(self) -> None:
        self._pool.close()

    def warm(self) -> None:
        """Force the speech model to load before the first reader waits on it."""
        try:
            with self._pool.request_client() as client:
                with client.stream(
                    "POST",
                    _url(settings.VOICE_REMOTE_TTS_ROUTE, "audio/speech"),
                    headers=_headers(),
                    json={"input": "Merhaba.", "model": settings.VOICE_REMOTE_TTS_MODEL,
                          "voice": "default", "response_format": "wav",
                          "cfg_value": settings.SPEECH_REMOTE_CFG_VALUE,
                          "inference_timesteps": settings.SPEECH_REMOTE_TIMESTEPS},
                    timeout=_timeout(settings.VOICE_REMOTE_WARM_SECONDS),
                ) as response:
                    response.raise_for_status()
                    for _ in response.iter_bytes(8192):
                        pass
            logger.info("Remote TTS warm")
        except Exception:
            logger.warning("Could not warm the remote speech model", exc_info=True)

    def prepare(self) -> int:
        # The remote call starts inside the response iterator. The configured
        # rate is the contract and is checked against the WAV header before PCM
        # is yielded, so the browser never receives incorrectly pitched audio.
        return settings.SPEECH_SAMPLE_RATE

    def acquire(self) -> bool:
        return self._inference_lock.acquire(
            timeout=settings.SPEECH_QUEUE_TIMEOUT_SECONDS
        )

    def release(self) -> None:
        if self._inference_lock.locked():
            self._inference_lock.release()

    def speak(self, text: str) -> Iterable[bytes]:
        def stream() -> Iterator[bytes]:
            parts = _text_chunks(text, settings.SPEECH_REMOTE_SEGMENT_CHARS)
            logger.info("Remote TTS segmented chars=%d parts=%d", len(text), len(parts))
            for part_index, part in enumerate(parts, start=1):
                last_error: Exception | None = None
                started = time.monotonic()
                deadline = started + settings.VOICE_REMOTE_RETRY_SECONDS
                delay = 1.0
                attempt = 1
                while True:
                    base = _base_url()
                    endpoint = _url(settings.VOICE_REMOTE_TTS_ROUTE, "audio/speech")
                    emitted = False
                    logger.info(
                        "Remote TTS part=%d/%d attempt=%d base=%s chars=%d",
                        part_index, len(parts), attempt, base, len(part),
                    )
                    try:
                        with self._pool.request_client(
                            read_timeout=min(30.0, settings.SPEECH_REMOTE_TIMEOUT_SECONDS)
                        ) as client:
                            with client.stream(
                                "POST",
                                endpoint,
                                headers={**_headers(), "Content-Type": "application/json"},
                                json={
                                    "input": part,
                                    "model": settings.VOICE_REMOTE_TTS_MODEL,
                                    "voice": "default",
                                    "response_format": "wav",
                                    "cfg_value": settings.SPEECH_REMOTE_CFG_VALUE,
                                    "inference_timesteps": settings.SPEECH_REMOTE_TIMESTEPS,
                                },
                            ) as response:
                                if response.status_code >= 400:
                                    logger.warning(
                                        "Remote TTS part=%d attempt=%d status=%d elapsed_ms=%d",
                                        part_index, attempt, response.status_code,
                                        round((time.monotonic() - started) * 1000),
                                    )
                                    if response.status_code in {404, 502, 503, 504}:
                                        response.raise_for_status()
                                    raise VoiceSpeechFailed(
                                        f"The speech server rejected the text ({response.status_code})."
                                    )
                                for chunk in _pcm_from_wav(
                                    response.iter_bytes(8192), settings.SPEECH_SAMPLE_RATE
                                ):
                                    emitted = True
                                    yield chunk
                                logger.info(
                                    "Remote TTS part=%d/%d complete elapsed_ms=%d",
                                    part_index, len(parts),
                                    round((time.monotonic() - started) * 1000),
                                )
                                break
                    except VoiceSpeechFailed:
                        raise
                    except httpx.HTTPError as exc:
                        last_error = exc
                        logger.warning(
                            "Remote TTS part=%d attempt=%d exception=%s",
                            part_index, attempt, type(exc).__name__,
                        )
                        if not emitted and _refresh_after(exc, base):
                            remaining = deadline - time.monotonic()
                            if remaining > 0:
                                time.sleep(min(delay, remaining))
                                delay = min(
                                    delay * 2, settings.LLM_RETRY_MAX_DELAY_SECONDS
                                )
                                attempt += 1
                                continue
                        raise VoiceSpeechUnavailable(
                            "The remote speech service is unavailable."
                        ) from last_error

        return stream()
