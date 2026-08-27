"""What the output check returns: a verdict, and what is wrong when it fails."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class RuleCheck(BaseModel):
    """One rule, judged. All of them come back from a single review."""

    rule_id: str = Field(min_length=1)
    passed: bool
    problem: str = Field(
        default="",
        max_length=300,
        description="Why this rule failed. Empty when it passed.",
    )


class GuardVerdict(BaseModel):
    """Pass, or fail with the problem to hand back to the assistant.

    There is no patch, no rewrite and no replacement text: the guard reads the
    answer and says yes or no. Repairing it is the assistant's work, because the
    assistant is the one holding the tools and the conversation.
    """

    checks: list[RuleCheck] = Field(min_length=1)
    passed: bool
    problem: str = Field(
        default="",
        max_length=800,
        description=(
            "What the assistant must fix, addressed to it. One or two plain "
            "sentences. Empty when the answer passed."
        ),
    )

    @model_validator(mode="after")
    def consistent(self):
        rule_ids = [row.rule_id for row in self.checks]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("Each rule must appear exactly once.")
        failed = [row for row in self.checks if not row.passed]
        if self.passed and failed:
            raise ValueError(
                "Cannot pass while these rules failed: "
                + ", ".join(row.rule_id for row in failed)
            )
        if not self.passed:
            if not failed:
                raise ValueError("A failure must name the rule that failed.")
            if not self.problem.strip():
                raise ValueError("A failure must say what the assistant should fix.")
        return self
