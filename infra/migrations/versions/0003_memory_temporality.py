"""memory temporality: valid_from, valid_until, supersession, confidence

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "memories",
        sa.Column("valid_from", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.add_column(
        "memories",
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "memories",
        sa.Column(
            "superseded_by",
            sa.dialects.postgresql.UUID(as_uuid=False),
            sa.ForeignKey("memories.id"),
            nullable=True,
        ),
    )
    op.add_column(
        "memories",
        sa.Column("confidence", sa.Float, server_default="1.0"),
    )
    op.execute(
        "CREATE INDEX ix_memories_active ON memories (user_id, kind) WHERE valid_until IS NULL"
    )


def downgrade():
    op.drop_index("ix_memories_active", "memories")
    op.drop_column("memories", "confidence")
    op.drop_column("memories", "superseded_by")
    op.drop_column("memories", "valid_until")
    op.drop_column("memories", "valid_from")
