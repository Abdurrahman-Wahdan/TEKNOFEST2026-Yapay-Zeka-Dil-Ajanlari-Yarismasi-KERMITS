"""Give chat_feedback the timestamp defaults its model already assumes.

Revision ID: d4e8b1c07a35
Revises: c91e5a7d4b20
Create Date: 2026-08-27

`c91e5a7d4b20` created `created_at` and `updated_at` as `NOT NULL` but without a
`server_default`, while `TimestampMixin` declares `server_default=func.now()` on
both. That combination is not a cosmetic mismatch: because the default is the
server's, SQLAlchemy omits the two columns from the INSERT and reads them back
with `RETURNING`, so every attempt to save a note failed with

    NotNullViolation: null value in column "created_at" of relation
    "chat_feedback" violates not-null constraint

and the endpoint returned 500 for the whole life of the feature -- the table
never held a single row.

Fixed forward rather than by editing `c91e5a7d4b20`, which is already applied:
rewriting an applied revision leaves every existing database broken while
looking correct in the source. No backfill: the columns are `NOT NULL`, so any
row that does exist already has a value.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e8b1c07a35"
down_revision: str | Sequence[str] | None = "c91e5a7d4b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for column in ("created_at", "updated_at"):
        op.alter_column(
            "chat_feedback",
            column,
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
            server_default=sa.text("now()"),
        )


def downgrade() -> None:
    for column in ("created_at", "updated_at"):
        op.alter_column(
            "chat_feedback",
            column,
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
            server_default=None,
        )
