"""Validated output of the conversation recommendation agent."""

from pydantic import BaseModel, Field, field_validator


class ConversationRecommendation(BaseModel):
    """One message the user can accept directly into the composer."""

    text: str = Field(
        min_length=1,
        max_length=240,
        description="A concise, context-aware next user message without quotes or a prefix.",
    )

    @field_validator("text", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value
