"""Allow condition alerts on fixed clock schedules.

Revision ID: b7f2916a0c42
Revises: a63d9c7e21b4
Create Date: 2026-08-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b7f2916a0c42"
down_revision: str | Sequence[str] | None = "a63d9c7e21b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_automations_schedule_and_condition", "automations", type_="check"
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
        "(kind = 'condition_alert' AND condition <> '{}'::jsonb))",
    )


def downgrade() -> None:
    # Existing fixed-time alerts need a valid interval before restoring the old
    # constraint. Sixty minutes is the historical alert default.
    op.execute(
        "UPDATE automations SET interval_minutes = 60 "
        "WHERE kind = 'condition_alert' AND interval_minutes IS NULL"
    )
    op.drop_constraint(
        "ck_automations_schedule_and_condition", "automations", type_="check"
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
