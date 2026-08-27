"""Validated output of the voice-response formatter."""

from pydantic import BaseModel, Field, field_validator


class VoiceResponse(BaseModel):
    """A completed assistant answer rewritten only for natural speech."""

    text: str = Field(
        min_length=1,
        description=(
            "Natural spoken prose containing every material fact, number, caveat, "
            "and conclusion from the source answer, with no markdown or URLs."
        ),
    )

    @field_validator("text", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value
