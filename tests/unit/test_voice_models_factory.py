"""Voice provider selection and OpenAI-compatible remote contracts."""

from __future__ import annotations

import json
import struct
import wave
from io import BytesIO

import httpx
import pytest
from fastapi import HTTPException

from api.routers import voice as voice_router
from api.schemas.voice import VoiceSpeechRequest
from config.settings import settings
from voice_models import get_synthesizer, get_transcriber
from voice_models.errors import (
    VoiceSpeechFailed,
    VoiceSpeechUnavailable,
    VoiceTranscriptionFailed,
)
from voice_models.providers.remote import (
    RemoteSpeechProvider,
    RemoteTranscriptionProvider,
    _pcm_from_wav,
)

pytestmark = pytest.mark.unit


def _wav(samples: bytes, rate: int = 48_000) -> bytes:
    output = BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(samples)
    return output.getvalue()


def test_factories_default_to_remote_and_allow_local_override():
    assert get_transcriber().provider_name == "remote"
    assert get_synthesizer().provider_name == "remote"
    assert get_transcriber("local").provider_name == "local"
    assert get_synthesizer("local").provider_name == "local"


def test_unknown_provider_lists_available_choices():
    with pytest.raises(ValueError, match="remote, local"):
        get_transcriber("missing")
    with pytest.raises(ValueError, match="remote, local"):
        get_synthesizer("missing")


def test_remote_stt_uses_shared_tunnel_and_openai_multipart_contract(
    tmp_path, monkeypatch
):
    seen: dict[str, object] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        seen["body"] = request.read()
        return httpx.Response(200, json={"text": "  Merhaba dünya.  "})

    monkeypatch.setattr(settings, "VOICE_REMOTE_BASE_URL", "")
    monkeypatch.setattr(settings, "VLLM_BASE_URL", "https://models.example")
    recording = tmp_path / "voice.webm"
    recording.write_bytes(b"webm-audio")
    provider = RemoteTranscriptionProvider(httpx.Client(transport=httpx.MockTransport(handle)))

    text, elapsed = provider.transcribe(recording)

    body = seen["body"]
    assert text == "Merhaba dünya."
    assert elapsed >= 0
    assert seen["url"] == (
        "https://models.example/whisper/v1/audio/transcriptions"
    )
    assert seen["authorization"] == "Bearer none"
    assert b'name="model"' in body and b"whisper-1" in body
    assert b'name="language"' in body and b"tr" in body
    assert b'name="response_format"' in body and b"json" in body
    assert b'name="file"; filename="voice.webm"' in body
    assert b"webm-audio" in body


def test_remote_stt_maps_a_bad_recording_to_a_transcription_failure(tmp_path):
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid audio"})

    recording = tmp_path / "voice.wav"
    recording.write_bytes(b"bad")
    provider = RemoteTranscriptionProvider(httpx.Client(transport=httpx.MockTransport(handle)))

    with pytest.raises(VoiceTranscriptionFailed, match="400"):
        provider.transcribe(recording)


def test_remote_tts_uses_openai_json_contract_and_streams_pcm(monkeypatch):
    samples = struct.pack("<hhh", 0, 1234, -1234)
    seen: dict[str, object] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        seen["json"] = json.loads(request.read())
        return httpx.Response(200, content=_wav(samples))

    monkeypatch.setattr(settings, "VOICE_REMOTE_BASE_URL", "https://voice.example")
    provider = RemoteSpeechProvider(httpx.Client(transport=httpx.MockTransport(handle)))

    result = b"".join(provider.speak("Merhaba."))

    assert result == samples
    assert seen["url"] == "https://voice.example/tts/v1/audio/speech"
    assert seen["authorization"] == "Bearer none"
    assert seen["json"] == {
        "input": "Merhaba.",
        "model": "tts-1",
        "voice": "default",
        "response_format": "wav",
        "cfg_value": 2.0,
        "inference_timesteps": 16,
    }


def test_wav_parser_handles_headers_split_across_network_chunks():
    samples = struct.pack("<hhhh", 0, 1, -1, 32767)
    payload = _wav(samples)

    chunks = (payload[i : i + 1] for i in range(len(payload)))
    assert b"".join(_pcm_from_wav(chunks, 48_000)) == samples


def test_wav_parser_streams_unknown_length_wav_data_until_eof():
    samples = struct.pack("<hhhh", 0, 1, -1, 32767)
    payload = bytearray(_wav(samples))
    # Streaming WAV headers cannot know the final RIFF/data size up front.
    struct.pack_into("<I", payload, 4, 0x7FFFFFDB)
    struct.pack_into("<I", payload, 40, 0x7FFFFFDB)

    chunks = (bytes(payload[i : i + 7]) for i in range(0, len(payload), 7))
    assert b"".join(_pcm_from_wav(chunks, 48_000)) == samples


def test_wav_parser_refuses_audio_that_would_play_at_the_wrong_pitch():
    with pytest.raises(VoiceSpeechFailed, match="expected 48000"):
        list(_pcm_from_wav([_wav(b"\x00\x00", rate=24_000)], 48_000))


@pytest.mark.asyncio
async def test_existing_speech_route_streams_the_configured_provider(monkeypatch):
    class FakeSynthesizer:
        released = False

        def cached_audio(self, text):
            return None

        def acquire(self):
            return True

        def prepare(self):
            return 48_000

        def audio_cache_max_bytes(self):
            return 0

        def speak(self, text):
            assert text == "Merhaba."
            return iter((b"\x01\x00", b"\x02\x00"))

        def remember_audio(self, text, chunks):
            raise AssertionError("remote speech is not cached in the API process")

        def release(self):
            self.released = True

    provider = FakeSynthesizer()
    monkeypatch.setattr(voice_router, "get_synthesizer", lambda: provider)

    response = voice_router.create_speech(
        VoiceSpeechRequest(text="Merhaba."), object()
    )
    body = b"".join([chunk async for chunk in response.body_iterator])

    assert body == b"\x01\x00\x02\x00"
    assert response.headers["x-sample-rate"] == "48000"
    assert provider.released is True


def test_speech_route_reports_remote_failure_before_sending_200(monkeypatch):
    class UnavailableSynthesizer:
        released = False

        def cached_audio(self, text):
            return None

        def acquire(self):
            return True

        def prepare(self):
            return 48_000

        def speak(self, text):
            def failed_stream():
                raise VoiceSpeechUnavailable("remote TTS is offline")
                yield b""  # pragma: no cover - marks this as a generator

            return failed_stream()

        def release(self):
            self.released = True

    provider = UnavailableSynthesizer()
    monkeypatch.setattr(voice_router, "get_synthesizer", lambda: provider)

    with pytest.raises(HTTPException) as raised:
        voice_router.create_speech(VoiceSpeechRequest(text="Merhaba."), object())

    assert raised.value.status_code == 503
    assert provider.released is True
