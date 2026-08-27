"""Adapters around the existing in-process MLX Whisper and Trendyol-TTS."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .base import BaseSpeechProvider, BaseTranscriptionProvider


class LocalTranscriptionProvider(BaseTranscriptionProvider):
    provider_name = "local"

    def warm(self) -> None:
        from api.voice_transcription import warm_voice_model

        warm_voice_model()

    def transcribe(self, audio_path: Path) -> tuple[str, int]:
        from api.voice_transcription import transcribe_voice

        return transcribe_voice(audio_path)


class LocalSpeechProvider(BaseSpeechProvider):
    provider_name = "local"

    def warm(self) -> None:
        from api.voice_speech import warm_speech_model

        warm_speech_model()

    def cached_audio(self, text: str) -> tuple[bytes, ...] | None:
        from api.voice_speech import cached_audio

        return cached_audio(text)

    def remember_audio(self, text: str, chunks: list[bytes]) -> None:
        from api.voice_speech import remember_audio

        remember_audio(text, chunks)

    def audio_cache_max_bytes(self) -> int:
        from api.voice_speech import audio_cache_max_bytes

        return audio_cache_max_bytes()

    def acquire(self) -> bool:
        from api.voice_speech import acquire

        return acquire()

    def release(self) -> None:
        from api.voice_speech import release

        release()

    def prepare(self) -> int:
        from api.voice_speech import prepare

        return prepare()

    def speak(self, text: str) -> Iterable[bytes]:
        from api.voice_speech import speak

        return speak(text)
