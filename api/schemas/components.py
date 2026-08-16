"""AI-produced page components on the wire.

A page's RAG content is a list of `{type, props}` -- the same envelope the
profile layer already stores in `SavedView.components`, reused deliberately so a
topic page and an AI Overview page render through one pipeline instead of two.

`props` stays unvalidated here, for the reason `profile.Component` already
gives: the catalog and every component's prop schema live in TypeScript, and
duplicating them in Python guarantees the two drift. The frontend validates and
renders an unreadable component visibly rather than silently dropping it.

What this module *does* pin down is the envelope around them, because that is
the part the producer and the consumer must agree on before either exists.
"""

from pydantic import BaseModel, Field

from .profile import Component


class ComponentsResponse(BaseModel):
    """Everything a topic page needs to render, in one response.

    `components` is ordered, and the order is meaningful -- it is the producer's
    argument, not an arbitrary set. The frontend preserves it and computes
    widths itself.
    """

    category: str = Field(description="A key from GET /api/components.")
    generated_at: str = Field(
        default="",
        description="When the producer built these, ISO-8601. Empty if unknown.",
    )
    source: str = Field(
        default="fixture",
        description=(
            "fixture | agent. Until the RAG agent lands these are hand-written "
            "development fixtures; the UI marks them so nobody reads placeholder "
            "content as bank data."
        ),
    )
    components: list[Component] = Field(default_factory=list)


class CategoryOut(BaseModel):
    """One topic page, and whether a producer has filled it yet."""

    key: str
    label: str
    has_components: bool
