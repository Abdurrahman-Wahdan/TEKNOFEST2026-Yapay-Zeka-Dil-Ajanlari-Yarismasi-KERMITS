"""Validated output of the table-metadata agent."""

from pydantic import BaseModel, Field, field_validator


class TableMetadata(BaseModel):
    """Enough context for a saved table to make sense in a future chat."""

    title: str = Field(
        description=(
            "A self-contained title naming the table's subject, scope, and key "
            "scenario parameters when present."
        ),
    )
    description: str = Field(
        description=(
            "A factual handoff explaining the user's objective, what the table "
            "contains, its important inputs and caveats, and where the chat left off."
        ),
    )

    @field_validator("title", "description", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value
