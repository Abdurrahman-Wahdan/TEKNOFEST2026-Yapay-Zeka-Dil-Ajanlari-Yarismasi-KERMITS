"""The stable browser contract for local speech-to-text."""

from pydantic import BaseModel, Field


class VoiceTranscriptionOut(BaseModel):
    """A completed Turkish transcription."""

    text: str
    language: str = Field(min_length=2, max_length=5)
    processing_ms: int = Field(ge=0)
