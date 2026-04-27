"""satellite state

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "satellites",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("location", sa.String, nullable=True),
        sa.Column("mode", sa.String, nullable=False, server_default="home_assistant"),
        sa.Column(
            "active_chat_id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            sa.ForeignKey("chats.id"),
            nullable=True,
        ),
        sa.Column("mode_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("satellites")
