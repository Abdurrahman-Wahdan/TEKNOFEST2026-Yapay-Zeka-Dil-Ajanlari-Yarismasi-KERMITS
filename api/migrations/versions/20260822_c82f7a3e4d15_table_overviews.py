"""cache the overview agent's read of each comparison table

Revision ID: c82f7a3e4d15
Revises: b71c6d2fe981
Create Date: 2026-08-22 19:55:00+03:00

One row per (table, language), holding what the overview agent said about one
table in the offline pool. Cached because generating it costs a model call, and
safe to cache because the pool is produced offline: `source_hash` is a digest of
the exact table the agent read, so a rerun of the producer stops matching and
the row is regenerated rather than served.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "c82f7a3e4d15"
down_revision: Union[str, None] = "b71c6d2fe981"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "table_overviews",
        sa.Column("table_id", sa.Text(), nullable=False),
        sa.Column("locale", sa.String(length=5), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        # Model *and* prompt version, e.g. "gemma@v4": both change what comes
        # back, and only one of them is visible in the table hash.
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("table_id", "locale", name="uq_table_overviews_table_locale"),
    )
    op.create_index(
        op.f("ix_table_overviews_table_id"), "table_overviews", ["table_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_table_overviews_table_id"), table_name="table_overviews")
    op.drop_table("table_overviews")
