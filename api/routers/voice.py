"""Authenticated local STT and remote streaming TTS."""

import asyncio
import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from agents.voice_response import shape_for_speech
from config.settings import settings

from .. import voice_speech
from ..deps import CurrentUser
from ..schemas.voice import (
    VoiceResponseOut,
    VoiceResponseRequest,
    VoiceSpeechRequest,
    VoiceTranscriptionOut,
)
from ..voice_speech import VoiceSpeechFailed, VoiceSpeechUnavailable
from ..voice_transcription import (
    VoiceTranscriptionFailed,
    VoiceTranscriptionUnavailable,
    transcribe_voice,
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


@router.post("/transcriptions", response_model=VoiceTranscriptionOut)
def create_voice_transcription(
    user: CurrentUser,
    file: UploadFile = File(description="One complete browser voice recording."),
) -> VoiceTranscriptionOut:
    """Create a Turkish transcript without sending audio off the machine."""
    del user  # Authentication is the dependency; the transcript is not persisted.
    content_type = (file.content_type or "").split(";", 1)[0].lower()
    suffix = _CONTENT_SUFFIX.get(content_type)
    if suffix is None:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Use WebM, Ogg, MP4, MP3, or WAV audio.",
        )

    maximum = settings.VOICE_MAX_UPLOAD_MB * 1024 * 1024
    payload = file.file.read(maximum + 1)
    if not payload:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The audio is empty.")
    if len(payload) > maximum:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"Voice recordings are limited to {settings.VOICE_MAX_UPLOAD_MB} MB.",
        )

    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="tf26-voice-", suffix=suffix, delete=False
        ) as out:
            out.write(payload)
            temporary = Path(out.name)
        text, processing_ms = transcribe_voice(temporary)
    except VoiceTranscriptionUnavailable as exc:
        logger.warning("Voice transcription unavailable: %s", exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except VoiceTranscriptionFailed as exc:
        logger.info("Voice transcription rejected: %s", exc)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

    return VoiceTranscriptionOut(
        text=text,
        language=settings.VOICE_LANGUAGE,
        processing_ms=processing_ms,
    )


@router.post("/response", response_model=VoiceResponseOut)
def create_voice_response(
    body: VoiceResponseRequest, user: CurrentUser
) -> VoiceResponseOut:
    """Rewrite one finished answer as prose, for a caller that will speak it.

    Text in, text out -- deliberately not wired to `/speech` below. Voice mode
    posts the result there itself, which keeps the queue, the cache and the
    503-when-busy contract on one route instead of two, and leaves this one
    usable on its own.

    Every failure is a 503, including a model that answers with nothing usable.
    The browser reads that as "shape it yourself" and falls back to the
    deterministic converter, so the answer is still spoken -- less smoothly, and
    without any chance of a rewritten figure. That is the same posture the
    output guard takes when it cannot run: the turn continues.
    """
    del user  # Authentication is the boundary; nothing here is persisted.

    text = body.text.strip()
    if not text:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "There is no answer to say."
        )
    if len(text) > settings.VOICE_RESPONSE_MAX_INPUT_CHARS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Answers are rewritten up to {settings.VOICE_RESPONSE_MAX_INPUT_CHARS}"
            " characters.",
        )

    try:
        shaped = shape_for_speech(text, question=body.question)
    except Exception as exc:
        logger.exception("Voice response shaping failed")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The answer could not be prepared for speech.",
        ) from exc
    return VoiceResponseOut(speech=shaped.speech)


@router.post(
    "/speech",
    responses={200: {"content": {"audio/L16": {}}}},
    response_class=StreamingResponse,
)
def create_speech(
    body: VoiceSpeechRequest,
    user: CurrentUser,
    request: Request,
) -> StreamingResponse:
    """Read a passage aloud. Returns raw 16-bit PCM as it is generated.

    **Streamed over plain HTTP, not a WebSocket.** The client sends text once
    and receives audio, so a regular HTTP stream is sufficient. The browser can
    also abort a fetch when the user presses stop, which closes the response and
    releases the remote reading cleanly.

    **Raw PCM, no container.** The browser schedules the generated pieces into
    an `AudioContext`, which wants samples rather than a decoded file. `audio/L16`
    is the registered type for this raw 16-bit audio.
    """
    del user  # Authentication is the dependency; nothing about the user is read.

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

    cached = voice_speech.cached_audio(text)
    if cached is not None:
        return StreamingResponse(
            iter(cached),
            media_type="audio/L16",
            headers={
                "X-Sample-Rate": str(voice_speech.sample_rate()),
                "X-Channels": "1",
                "X-Speech-Cache": "hit",
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
            },
        )

    # A new voice turn replaces an interrupted one. Closing the previous HTTP
    # client releases the remote connection instead of leaving the new V press
    # waiting behind a stale stream.
    voice_speech.cancel_active()
    if not voice_speech.acquire():
        voice_speech.cancel_active()
        if voice_speech.acquire(timeout=5):
            pass
        else:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "The assistant is reading something else. Try again in a moment.",
            )

    # Loaded before the response starts, so "the speech extra is not installed"
    # and "the checkpoint will not load" arrive as a 503 the browser can report.
    # Discovered inside the generator they would follow a 200 status line, and a
    # failed reading would be indistinguishable from a silent one.
    try:
        rate = voice_speech.prepare()
    except VoiceSpeechUnavailable as exc:
        voice_speech.release()
        logger.warning("Speech unavailable: %s", exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except Exception:
        voice_speech.release()
        raise

    async def frames():
        chunks: list[bytes] = []
        cached_bytes = 0
        cache_limit = voice_speech.audio_cache_max_bytes()
        cacheable = True
        speech = iter(voice_speech.speak(text))

        def next_chunk():
            try:
                return False, next(speech)
            except StopIteration:
                return True, b""

        try:
            while True:
                if await request.is_disconnected():
                    break
                done, chunk = await run_in_threadpool(next_chunk)
                if done:
                    break
                if await request.is_disconnected():
                    break
                if cacheable:
                    if cached_bytes + len(chunk) <= cache_limit:
                        chunks.append(chunk)
                        cached_bytes += len(chunk)
                    else:
                        chunks.clear()
                        cacheable = False
                yield chunk
            if cacheable:
                voice_speech.remember_audio(text, chunks)
        except asyncio.CancelledError:
            logger.info("Speech client disconnected; closing remote stream")
            raise
        except VoiceSpeechFailed as exc:
            logger.info("Speech failed: %s", exc)
        except Exception:
            logger.exception("Speech stream failed")
        finally:
            speech.close()
            voice_speech.release()

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
            "X-Accel-Buffering": "no",
        },
    )
