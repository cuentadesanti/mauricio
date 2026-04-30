"""schedules table for cron-like one-shot/recurring jobs

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "schedules",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column(
            "payload",
            sa.dialects.postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="pending"),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Partial index covers the scheduler's only hot query: pending rows
    # whose run_at <= now(). Ignoring completed/failed rows keeps the
    # index tiny even after thousands of completed jobs.
    op.create_index(
        "ix_schedules_due",
        "schedules",
        ["run_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index("ix_schedules_user_id", "schedules", ["user_id"])


def downgrade():
    op.drop_index("ix_schedules_user_id", "schedules")
    op.drop_index("ix_schedules_due", "schedules")
    op.drop_table("schedules")
