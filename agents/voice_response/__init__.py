"""One-shot adaptation of a completed answer for speech synthesis."""

from .agent import format_voice_response
from .models import VoiceResponse

__all__ = ["VoiceResponse", "format_voice_response"]
