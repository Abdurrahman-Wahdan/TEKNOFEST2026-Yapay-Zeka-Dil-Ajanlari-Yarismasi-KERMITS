"""The voice boundary validates bytes and never leaves recordings on disk."""

from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from api.routers import voice
from api.voice_transcription import VoiceTranscriptionUnavailable, _require_model


def _upload(payload: bytes, content_type: str = "audio/webm") -> UploadFile:
    return UploadFile(
        file=BytesIO(payload),
        filename="voice.webm",
        headers=Headers({"content-type": content_type}),
    )


def test_transcription_returns_text_and_removes_temporary_audio(monkeypatch):
    observed = {}

    def transcribe(path):
        observed["path"] = path
        observed["exists_during_inference"] = path.is_file()
        return "Katılım bankalarını karşılaştır.", 321

    monkeypatch.setattr(voice, "transcribe_voice", transcribe)

    result = voice.create_voice_transcription(object(), _upload(b"valid-audio"))

    assert result.text == "Katılım bankalarını karşılaştır."
    assert result.language == "tr"
    assert result.processing_ms == 321
    assert observed["exists_during_inference"] is True
    assert observed["path"].exists() is False


def test_transcription_rejects_unknown_media_type():
    with pytest.raises(HTTPException) as raised:
        voice.create_voice_transcription(object(), _upload(b"text", "text/plain"))

    assert raised.value.status_code == 415


def test_transcription_rejects_empty_audio():
    with pytest.raises(HTTPException) as raised:
        voice.create_voice_transcription(object(), _upload(b""))

    assert raised.value.status_code == 422


def test_missing_checkpoint_is_reported_before_runtime_import(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "api.voice_transcription.settings.VOICE_MODEL_PATH", str(tmp_path)
    )

    with pytest.raises(VoiceTranscriptionUnavailable, match="incomplete"):
        _require_model()
