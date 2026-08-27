"""Authenticated, local Turkish speech-to-text."""

import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from config.settings import settings

from ..deps import CurrentUser
from ..schemas.voice import VoiceTranscriptionOut
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
