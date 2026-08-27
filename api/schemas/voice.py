"""The stable browser contract for local speech, in both directions."""

from pydantic import BaseModel, Field


class VoiceTranscriptionOut(BaseModel):
    """A completed Turkish transcription."""

    text: str
    language: str = Field(min_length=2, max_length=5)
    processing_ms: int = Field(ge=0)


class VoiceSpeechRequest(BaseModel):
    """Text to read aloud.

    Prose, not markdown. The browser strips the marks before sending, because it
    is the side that knows a table should be read "column: value" and that a code
    block should not be read at all -- see `UI/src/lib/chat/speech-text.ts`.
    Sending markdown here would have the model pronounce the asterisks.
    """

    text: str = Field(
        min_length=1,
        description=(
            "The passage to speak. Bounded by SPEECH_MAX_CHARS and refused "
            "rather than truncated when over it: a reading that stops halfway "
            "is indistinguishable from a crash, so the caller is told."
        ),
    )
