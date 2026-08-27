"""automations, and the reports they produce

Revision ID: d93b8f5a1c62
Revises: c82f7a3e4d15
Create Date: 2026-08-25 18:20:00+03:00

A recurring question the user asked the assistant to run for them, and one row
per answer it produced. An unread report is the notification: there is no
separate notifications table, because a notification here carries nothing the
report itself does not.

`automation_reports.automation_id` is ON DELETE SET NULL rather than CASCADE --
cancelling tomorrow's report must not delete yesterday's -- which is why the
report keeps its own snapshot of the title.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "d93b8f5a1c62"
down_revision: Union[str, None] = "c82f7a3e4d15"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "automations",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        # Wall clock in Europe/Istanbul, not UTC: "every morning at nine" means
        # nine where the user is.
        sa.Column("hour", sa.Integer(), nullable=False),
        sa.Column("minute", sa.Integer(), nullable=False),
        # 0=Monday .. 6=Sunday, matching datetime.weekday(). Empty means daily.
        sa.Column("weekdays", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("web_search", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_automations_user_id"), "automations", ["user_id"], unique=False
    )
    # The runner's only query. Partial on `enabled`, because a disabled
    # automation is never a candidate and this index is scanned every poll.
    op.create_index(
        "ix_automations_due",
        "automations",
        ["next_run_at"],
        unique=False,
        postgresql_where=sa.text("enabled"),
    )

    op.create_table(
        "automation_reports",
        sa.Column("automation_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("citations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        # NULL is what the notification bell counts.
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["automation_id"], ["automations.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_automation_reports_automation_id"),
        "automation_reports",
        ["automation_id"],
        unique=False,
    )
    # The badge count.
    op.create_index(
        "ix_automation_reports_unread",
        "automation_reports",
        ["user_id", "read_at"],
        unique=False,
    )
    # The Reports tab, newest first.
    op.create_index(
        "ix_automation_reports_user_created",
        "automation_reports",
        ["user_id", sa.text("created_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_automation_reports_user_created", table_name="automation_reports"
    )
    op.drop_index("ix_automation_reports_unread", table_name="automation_reports")
    op.drop_index(
        op.f("ix_automation_reports_automation_id"), table_name="automation_reports"
    )
    op.drop_table("automation_reports")
    op.drop_index("ix_automations_due", table_name="automations")
    op.drop_index(op.f("ix_automations_user_id"), table_name="automations")
    op.drop_table("automations")
