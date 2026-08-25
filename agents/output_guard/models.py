"""Validated contracts for surgical output review."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class PolicyCheck(BaseModel):
    """One checklist row returned by the guard model."""

    policy_id: str = Field(min_length=1)
    status: Literal["pass", "violation"]
    note: str = Field(
        default="",
        max_length=240,
        description="A short finding, never hidden chain-of-thought.",
    )


class TextPatch(BaseModel):
    """Replace one immutable draft segment, and nothing around it."""

    policy_ids: list[str] = Field(
        min_length=1,
        description="Every violated policy this one local edit corrects.",
    )
    segment_id: str = Field(pattern=r"^S[1-9][0-9]*$")
    replacement: str = Field(
        description="The corrected segment body. May be empty to delete the segment.",
    )


class GuardReview(BaseModel):
    """One complete checklist and its minimal edits."""

    checks: list[PolicyCheck] = Field(min_length=1)
    patches: list[TextPatch] = Field(default_factory=list, max_length=6)
    safe_after_patches: bool = Field(
        description="True only when applying every patch makes the draft publishable.",
    )

    @model_validator(mode="after")
    def unique_rows(self):
        check_ids = [row.policy_id for row in self.checks]
        if len(check_ids) != len(set(check_ids)):
            raise ValueError("Each policy must appear exactly once in checks.")
        segment_ids = [patch.segment_id for patch in self.patches]
        if len(segment_ids) != len(set(segment_ids)):
            raise ValueError("A draft segment may be patched only once.")
        if any(len(ids) != len(set(ids)) for ids in (p.policy_ids for p in self.patches)):
            raise ValueError("Patch policy ids must be unique.")
        return self


class GuardedOutput(BaseModel):
    """The accepted answer and a private audit summary."""

    text: str
    changed: bool
    checks: list[PolicyCheck]
    patches: list[TextPatch]
    passes: int = Field(ge=1, le=2)
