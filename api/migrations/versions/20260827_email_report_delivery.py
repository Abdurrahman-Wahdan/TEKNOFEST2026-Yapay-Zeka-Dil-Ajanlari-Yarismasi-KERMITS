"""Add per-user email destinations and per-automation report delivery."""

from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = "email_report_delivery"
down_revision: str | None = "d4e8b1c07a35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("notification_email", sa.String(length=320), nullable=True))
    op.add_column("automations", sa.Column("email_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("automations", sa.Column("email_format", sa.String(length=8), nullable=False, server_default="pdf"))


def downgrade() -> None:
    op.drop_column("automations", "email_format")
    op.drop_column("automations", "email_enabled")
    op.drop_column("users", "notification_email")
