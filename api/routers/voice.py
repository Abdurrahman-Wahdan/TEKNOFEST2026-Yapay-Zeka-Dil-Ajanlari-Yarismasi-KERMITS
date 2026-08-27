"""Authenticated Turkish speech, backed by configurable local or remote models."""

import logging
import tempfile
import time
import uuid
from itertools import chain
from pathlib import Path

from fastapi import APIRouter, File, Header, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse

from agents.voice_response import format_voice_response
from config.settings import settings
from voice_models import (
    VoiceSpeechFailed,
    VoiceSpeechUnavailable,
    VoiceTranscriptionFailed,
    VoiceTranscriptionUnavailable,
    get_synthesizer,
    get_transcriber,
)

from ..deps import CurrentUser
from ..schemas.voice import (
    VoiceResponseOut,
    VoiceResponseRequest,
    VoiceSpeechRequest,
    VoiceTranscriptionOut,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/voice", tags=["voice"])

_CONTENT_SUFFIX = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}


def transcribe_voice(audio_path: Path) -> tuple[str, int]:
    """Compatibility seam kept small so route tests can replace inference."""
    return get_transcriber().transcribe(audio_path)


@router.post("/transcriptions", response_model=VoiceTranscriptionOut)
def create_voice_transcription(
    user: CurrentUser,
    file: UploadFile = File(description="One complete browser voice recording."),
) -> VoiceTranscriptionOut:
    """Create a Turkish transcript through the configured STT provider."""
    del user  # Authentication is the dependency; the transcript is not persisted.
    request_id = uuid.uuid4().hex[:12]
    started = time.perf_counter()
    content_type = (file.content_type or "").split(";", 1)[0].lower()
    logger.info("STT[%s] received type=%s filename=%s", request_id, content_type or "<missing>", file.filename or "<missing>")
    suffix = _CONTENT_SUFFIX.get(content_type)
    if suffix is None:
        logger.warning("STT[%s] unsupported content type=%s", request_id, content_type or "<missing>")
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Use WebM, Ogg, MP4, MP3, or WAV audio.",
        )

    maximum = settings.VOICE_MAX_UPLOAD_MB * 1024 * 1024
    payload = file.file.read(maximum + 1)
    logger.info("STT[%s] upload bytes=%d suffix=%s", request_id, len(payload), suffix)
    if not payload:
        logger.warning("STT[%s] empty upload", request_id)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The audio is empty.")
    if len(payload) > maximum:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"Voice recordings are limited to {settings.VOICE_MAX_UPLOAD_MB} MB.",
        )

    temporary: Path | None = None
    try:
        logger.info("STT[%s] provider start", request_id)
        with tempfile.NamedTemporaryFile(
            prefix="tf26-voice-", suffix=suffix, delete=False
        ) as out:
            out.write(payload)
            temporary = Path(out.name)
        text, processing_ms = transcribe_voice(temporary)
        logger.info("STT[%s] provider success text_chars=%d provider_ms=%d total_ms=%d", request_id, len(text), processing_ms, round((time.perf_counter() - started) * 1000))
    except VoiceTranscriptionUnavailable as exc:
        logger.warning("STT[%s] unavailable: %s", request_id, exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except VoiceTranscriptionFailed as exc:
        logger.info("STT[%s] rejected: %s", request_id, exc)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

    return VoiceTranscriptionOut(
        text=text,
        language=settings.VOICE_LANGUAGE,
        processing_ms=processing_ms,
    )


@router.post("/format-response", response_model=VoiceResponseOut)
def create_voice_response(
    body: VoiceResponseRequest, user: CurrentUser
) -> VoiceResponseOut:
    """Turn a completed assistant answer into natural, speakable prose."""
    del user  # Authentication is the boundary; the formatter stores no user data.
    try:
        result = format_voice_response(
            body.answer,
            question=body.question,
            locale=body.locale,
        )
    except Exception as exc:
        logger.exception("Voice response formatting failed")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The voice response could not be prepared.",
        ) from exc
    return VoiceResponseOut(text=result.text)


@router.post(
    "/speech",
    responses={200: {"content": {"audio/L16": {}}}},
    response_class=StreamingResponse,
)
def create_speech(
    body: VoiceSpeechRequest,
    user: CurrentUser,
    voice_request_id: str | None = Header(default=None, alias="X-Voice-Request-ID"),
) -> StreamingResponse:
    """Read a passage aloud. Returns raw 16-bit PCM as it is generated.

    **Streamed over plain HTTP, not a WebSocket.** `TTS_ENTEGRASYON.md` shows a
    WebSocket, and it works, but nothing here needs one: the client sends text
    once and receives audio, which is the one-directional case, and it is the
    same argument `chat.py` already makes for SSE over WebSockets. Two concrete
    gains for this endpoint in particular -- the browser's WebSocket API cannot
    set an `Authorization` header, so a socket would push this account's bearer
    token into a query string where proxies log it; and aborting a `fetch` is
    already how the client stops a stream, so pressing stop closes the response
    and the generator below is closed with it.

    **Raw PCM, no container.** The audio is generated in ~160 ms pieces and the
    browser schedules them into an `AudioContext`, which wants samples rather
    than a decoded file. Wrapping each piece as WAV would put a 44-byte header
    in the middle of the stream every 160 ms; wrapping the whole reading as one
    file would mean holding it to the end, which is what streaming exists to
    avoid. `audio/L16` is the registered type for exactly this.
    """
    del user  # Authentication is the dependency; nothing about the user is read.
    request_id = voice_request_id or uuid.uuid4().hex[:12]
    started = time.perf_counter()
    logger.info("TTS[%s] received text_chars=%d", request_id, len(body.text))

    text = body.text.strip()
    if not text:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "There is nothing to read."
        )
    if len(text) > settings.SPEECH_MAX_CHARS:
        # Refused, not truncated. Half a reading sounds like a failure and gives
        # the listener no way to know the rest existed.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Readings are limited to {settings.SPEECH_MAX_CHARS} characters.",
        )

    synthesizer = get_synthesizer()
    cached = synthesizer.cached_audio(text)
    if cached is not None:
        logger.info("TTS[%s] cache hit", request_id)
        return StreamingResponse(
            iter(cached),
            media_type="audio/L16",
            headers={
                "X-Sample-Rate": str(synthesizer.prepare()),
                "X-Channels": "1",
                "X-Speech-Cache": "hit",
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
            },
        )

    # The model is not thread-safe, so readings queue. Claimed here rather than
    # inside the generator because this is the last moment a status code can
    # still be sent -- once the first bytes of a stream are out, a busy model has
    # no way left to say so.
    if not synthesizer.acquire():
        logger.warning("TTS[%s] rejected busy queue", request_id)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The assistant is reading something else. Try again in a moment.",
        )

    # Open the provider and obtain its first PCM chunk before the response starts.
    # This covers both local model load and the remote HTTP connection: failures
    # still become a useful 503/422 instead of following a 200 status line as a
    # silent, empty reading.
    try:
        logger.info("TTS[%s] provider start", request_id)
        rate = synthesizer.prepare()
        speech = iter(synthesizer.speak(text))
        first_chunk = next(speech)
        logger.info("TTS[%s] first_pcm_bytes=%d total_ms=%d", request_id, len(first_chunk), round((time.perf_counter() - started) * 1000))
    except VoiceSpeechUnavailable as exc:
        synthesizer.release()
        logger.warning("Speech unavailable: %s", exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except VoiceSpeechFailed as exc:
        synthesizer.release()
        logger.info("Speech rejected: %s", exc)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except StopIteration as exc:
        synthesizer.release()
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The speech provider returned no audio.",
        ) from exc
    except Exception:
        synthesizer.release()
        raise

    def frames():
        chunks: list[bytes] = []
        cached_bytes = 0
        cache_limit = synthesizer.audio_cache_max_bytes()
        cacheable = cache_limit > 0
        try:
            for chunk in chain((first_chunk,), speech):
                if cacheable:
                    if cached_bytes + len(chunk) <= cache_limit:
                        chunks.append(chunk)
                        cached_bytes += len(chunk)
                    else:
                        # Keep streaming the response, but do not retain a
                        # long reading just to populate the optional cache.
                        chunks.clear()
                        cacheable = False
                yield chunk
            if cacheable:
                synthesizer.remember_audio(text, chunks)
        except VoiceSpeechFailed as exc:
            # Too late for a status code -- the model loaded and then failed
            # part-way. Closing the stream is the only signal left, and the
            # client renders it as the reading having stopped.
            logger.info("Speech failed: %s", exc)
        except Exception:
            logger.exception("Speech stream failed")
        finally:
            close = getattr(speech, "close", None)
            if close is not None:
                close()
            synthesizer.release()

    return StreamingResponse(
        frames(),
        media_type="audio/L16",
        headers={
            # The browser builds its AudioContext from these two rather than
            # assuming: an AudioContext at the wrong rate does not fail, it plays
            # the answer at the wrong pitch.
            "X-Sample-Rate": str(rate),
            "X-Channels": "1",
            "Cache-Control": "no-store",
            # Tells nginx not to buffer. Without it a proxy holds every chunk
            # until the response finishes, which is the silence-then-everything
            # that streaming exists to avoid.
            "X-Accel-Buffering": "no",
        },
    )
