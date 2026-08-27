"""Voice provider registry."""

from .base import BaseSpeechProvider, BaseTranscriptionProvider
from .local import LocalSpeechProvider, LocalTranscriptionProvider
from .remote import RemoteSpeechProvider, RemoteTranscriptionProvider

TRANSCRIPTION_PROVIDERS: dict[str, type[BaseTranscriptionProvider]] = {
    "remote": RemoteTranscriptionProvider,
    "local": LocalTranscriptionProvider,
}

SPEECH_PROVIDERS: dict[str, type[BaseSpeechProvider]] = {
    "remote": RemoteSpeechProvider,
    "local": LocalSpeechProvider,
}

_TRANSCRIPTION_CACHE: dict[str, BaseTranscriptionProvider] = {}
_SPEECH_CACHE: dict[str, BaseSpeechProvider] = {}


def get_transcription_provider(name: str) -> BaseTranscriptionProvider:
    key = name.lower()
    try:
        provider_type = TRANSCRIPTION_PROVIDERS[key]
    except KeyError as exc:
        available = ", ".join(TRANSCRIPTION_PROVIDERS)
        raise ValueError(
            f"Unknown transcription provider: {name!r}. Available: {available}"
        ) from exc
    cached = _TRANSCRIPTION_CACHE.get(key)
    if cached is None:
        cached = provider_type()
        _TRANSCRIPTION_CACHE[key] = cached
    return cached


def get_speech_provider(name: str) -> BaseSpeechProvider:
    key = name.lower()
    try:
        provider_type = SPEECH_PROVIDERS[key]
    except KeyError as exc:
        available = ", ".join(SPEECH_PROVIDERS)
        raise ValueError(
            f"Unknown speech provider: {name!r}. Available: {available}"
        ) from exc
    cached = _SPEECH_CACHE.get(key)
    if cached is None:
        cached = provider_type()
        _SPEECH_CACHE[key] = cached
    return cached


def close_voice_providers() -> None:
    """Close remote connection pools during application shutdown."""
    for provider in (*_TRANSCRIPTION_CACHE.values(), *_SPEECH_CACHE.values()):
        provider.close()
    _TRANSCRIPTION_CACHE.clear()
    _SPEECH_CACHE.clear()


__all__ = [
    "BaseSpeechProvider",
    "BaseTranscriptionProvider",
    "LocalSpeechProvider",
    "LocalTranscriptionProvider",
    "RemoteSpeechProvider",
    "RemoteTranscriptionProvider",
    "SPEECH_PROVIDERS",
    "TRANSCRIPTION_PROVIDERS",
    "close_voice_providers",
    "get_speech_provider",
    "get_transcription_provider",
]
