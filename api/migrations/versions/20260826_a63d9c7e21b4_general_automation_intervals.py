"""general interval schedules and optional daily windows

Revision ID: a63d9c7e21b4
Revises: f4a82c1d9e70
Create Date: 2026-08-26 22:20:00+03:00

Intervals are schedule properties, not alert properties. Existing fixed-time
reports and interval alerts keep their behavior; new reports may also recur by
minute, optionally inside a local-time window.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a63d9c7e21b4"
down_revision: Union[str, None] = "f4a82c1d9e70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_automations_condition_shape", "automations", type_="check"
    )
    op.add_column(
        "automations", sa.Column("window_start_minute", sa.Integer(), nullable=True)
    )
    op.add_column(
        "automations", sa.Column("window_end_minute", sa.Integer(), nullable=True)
    )
    op.create_check_constraint(
        "ck_automations_schedule_and_condition",
        "automations",
        "(interval_minutes IS NULL OR interval_minutes BETWEEN 5 AND 10080) AND "
        "((window_start_minute IS NULL AND window_end_minute IS NULL) OR "
        "(interval_minutes IS NOT NULL AND window_start_minute BETWEEN 0 AND 1439 "
        "AND window_end_minute BETWEEN 0 AND 1439 "
        "AND window_start_minute <> window_end_minute)) AND "
        "((kind = 'scheduled_report' AND condition = '{}'::jsonb) OR "
        "(kind = 'condition_alert' AND interval_minutes IS NOT NULL "
        "AND condition <> '{}'::jsonb))",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_automations_schedule_and_condition", "automations", type_="check"
    )
    # A report interval cannot be represented by the previous schema. Convert
    # it to its existing visible clock fields before restoring the old check.
    op.execute(
        "UPDATE automations SET interval_minutes = NULL "
        "WHERE kind = 'scheduled_report'"
    )
    op.drop_column("automations", "window_end_minute")
    op.drop_column("automations", "window_start_minute")
    op.create_check_constraint(
        "ck_automations_condition_shape",
        "automations",
        "(kind = 'scheduled_report' AND interval_minutes IS NULL) OR "
        "(kind = 'condition_alert' AND interval_minutes BETWEEN 15 AND 10080 "
        "AND condition <> '{}'::jsonb)",
    )
