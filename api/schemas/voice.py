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


class VoiceResponseRequest(BaseModel):
    """A finished assistant answer, in markdown, to be rewritten for speech.

    The mirror image of `VoiceSpeechRequest`, and the reason that one can keep
    insisting on prose. The browser strips marks well enough for a speaker
    button, but voice mode is *only* listened to -- there is no answer on screen
    to fall back on -- so the whole passage goes through a model first, and what
    comes back is what gets posted to `/voice/speech`.
    """

    text: str = Field(
        min_length=1,
        description=(
            "The answer as written, markdown and all. Bounded by "
            "VOICE_RESPONSE_MAX_INPUT_CHARS and refused rather than truncated: "
            "half an answer spoken confidently is worse than none."
        ),
    )
    question: str = Field(
        default="",
        description="What was asked. Context for the rewrite, never an instruction.",
    )


class VoiceResponseOut(BaseModel):
    """The same answer as prose, ready to hand straight to `/voice/speech`."""

    speech: str
