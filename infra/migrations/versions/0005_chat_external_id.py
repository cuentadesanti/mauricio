"""chat external_id for WhatsApp etc.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "chats",
        sa.Column("external_id", sa.String, nullable=True),
    )
    op.create_index(
        "ix_chats_channel_external_id",
        "chats",
        ["channel", "external_id"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_chats_channel_external_id", "chats")
    op.drop_column("chats", "external_id")
