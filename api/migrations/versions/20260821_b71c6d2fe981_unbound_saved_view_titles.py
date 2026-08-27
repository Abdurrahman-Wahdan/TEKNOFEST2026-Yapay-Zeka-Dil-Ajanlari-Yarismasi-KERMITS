"""store saved-view titles as unbounded text

Revision ID: b71c6d2fe981
Revises: a60746d6de22
Create Date: 2026-08-21 12:00:00+03:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b71c6d2fe981"
down_revision: Union[str, None] = "a60746d6de22"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "saved_views",
        "title",
        existing_type=sa.String(length=160),
        type_=sa.Text(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "saved_views",
        "title",
        existing_type=sa.Text(),
        type_=sa.String(length=160),
        existing_nullable=False,
        postgresql_using="left(title, 160)",
    )
