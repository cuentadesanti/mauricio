"""memory and knowledge

Revision ID: 0002
Revises: 0001
"""

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    # ---- memories ----
    op.create_table(
        "memories",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("metadata", sa.dialects.postgresql.JSONB, server_default="{}"),
        sa.Column(
            "source_chat_id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            sa.ForeignKey("chats.id"),
            nullable=True,
        ),
        sa.Column(
            "source_message_id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            sa.ForeignKey("messages.id"),
            nullable=True,
        ),
        sa.Column("embedding", Vector(1536)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_memories_user_kind", "memories", ["user_id", "kind"])
    op.execute(
        "CREATE INDEX ix_memories_embedding ON memories "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    # ---- chat_summaries ----
    op.create_table(
        "chat_summaries",
        sa.Column(
            "chat_id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            sa.ForeignKey("chats.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column(
            "up_to_message_id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            sa.ForeignKey("messages.id"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ---- knowledge_docs ----
    op.create_table(
        "knowledge_docs",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("s3_key", sa.String, nullable=False),
        sa.Column("title", sa.String),
        sa.Column("content_hash", sa.String, nullable=False),
        sa.Column("metadata", sa.dialects.postgresql.JSONB, server_default="{}"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "s3_key", name="uq_knowledge_docs_user_key"),
    )

    # ---- knowledge_chunks ----
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "doc_id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            sa.ForeignKey("knowledge_docs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("embedding", Vector(1536)),
        sa.UniqueConstraint("doc_id", "chunk_index", name="uq_chunks_doc_idx"),
    )
    op.execute(
        "CREATE INDEX ix_chunks_embedding ON knowledge_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade():
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_docs")
    op.drop_table("chat_summaries")
    op.drop_table("memories")
