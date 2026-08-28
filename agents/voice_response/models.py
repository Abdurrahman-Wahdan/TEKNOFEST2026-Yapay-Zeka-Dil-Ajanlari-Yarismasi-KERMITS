"""What the spoken-answer writer returns: one passage, ready to be read aloud."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class VoiceResponse(BaseModel):
    """One finished answer, rewritten to be heard rather than read.

    No maximum length. A cap would turn a long but correct answer into a
    validation failure, which the route reports as a 503, which sends the
    browser to the deterministic converter -- a worse reading, for a limit that
    protects nothing. The *input* is bounded instead, at the route.
    """

    speech: str = Field(
        min_length=1,
        description=(
            "The answer as continuous prose: no markdown, no tables, no bullet "
            "marks, no code and no addresses. Every figure preserved exactly as "
            "it was written."
        ),
    )

    @field_validator("speech", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value
