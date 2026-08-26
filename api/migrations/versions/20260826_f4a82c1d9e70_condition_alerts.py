"""typed conditional alerts on live banking metrics

Revision ID: f4a82c1d9e70
Revises: e1f4a7c92b08
Create Date: 2026-08-26 12:00:00+03:00

Existing rows remain scheduled reports. Alert rules are versioned JSON because
the operand union will grow, while the fields the due-row index needs stay
relational: kind, interval and next_run_at.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "f4a82c1d9e70"
down_revision: Union[str, None] = "e1f4a7c92b08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "automations",
        sa.Column(
            "kind",
            sa.String(length=24),
            nullable=False,
            server_default="scheduled_report",
        ),
    )
    op.add_column(
        "automations",
        sa.Column(
            "condition",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "automations", sa.Column("interval_minutes", sa.Integer(), nullable=True)
    )
    op.add_column(
        "automations", sa.Column("last_condition_met", sa.Boolean(), nullable=True)
    )
    op.add_column(
        "automations",
        sa.Column(
            "last_observation",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "automations",
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_automations_condition_shape",
        "automations",
        "(kind = 'scheduled_report' AND interval_minutes IS NULL) OR "
        "(kind = 'condition_alert' AND interval_minutes BETWEEN 15 AND 10080 "
        "AND condition <> '{}'::jsonb)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_automations_condition_shape", "automations", type_="check"
    )
    op.drop_column("automations", "last_triggered_at")
    op.drop_column("automations", "last_observation")
    op.drop_column("automations", "last_condition_met")
    op.drop_column("automations", "interval_minutes")
    op.drop_column("automations", "condition")
    op.drop_column("automations", "kind")
