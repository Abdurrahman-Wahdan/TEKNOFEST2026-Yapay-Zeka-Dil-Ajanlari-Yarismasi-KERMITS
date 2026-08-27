"""Factories for speech-to-text and text-to-speech providers."""

from config.settings import settings

from .providers import (
    BaseSpeechProvider,
    BaseTranscriptionProvider,
    get_speech_provider as _get_speech_provider,
    get_transcription_provider as _get_transcription_provider,
)


def get_transcriber(provider: str | None = None) -> BaseTranscriptionProvider:
    """Return the configured STT provider."""
    return _get_transcription_provider(provider or settings.VOICE_PROVIDER)


def get_synthesizer(provider: str | None = None) -> BaseSpeechProvider:
    """Return the configured TTS provider."""
    return _get_speech_provider(provider or settings.SPEECH_PROVIDER)
