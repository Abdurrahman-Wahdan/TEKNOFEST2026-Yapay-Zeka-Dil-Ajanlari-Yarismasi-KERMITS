"""Configurable local or OpenAI-compatible voice models."""

from .errors import (
    VoiceSpeechFailed,
    VoiceSpeechUnavailable,
    VoiceTranscriptionFailed,
    VoiceTranscriptionUnavailable,
)
from .factory import get_synthesizer, get_transcriber
from .providers import close_voice_providers

__all__ = [
    "VoiceSpeechFailed",
    "VoiceSpeechUnavailable",
    "VoiceTranscriptionFailed",
    "VoiceTranscriptionUnavailable",
    "close_voice_providers",
    "get_synthesizer",
    "get_transcriber",
]
