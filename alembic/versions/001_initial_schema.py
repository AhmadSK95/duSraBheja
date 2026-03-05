"""Initial schema — all brain tables.

Revision ID: 001
Revises:
Create Date: 2026-03-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from pgvector.sqlalchemy import Vector

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Extensions
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    # artifacts
    op.create_table(
        "artifacts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("discord_message_id", sa.String, unique=True, nullable=True),
        sa.Column("discord_channel_id", sa.String, nullable=True),
        sa.Column("discord_thread_id", sa.String, nullable=True),
        sa.Column("content_type", sa.String, nullable=False),
        sa.Column("raw_text", sa.Text, nullable=True),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("blob_ref", sa.String, nullable=True),
        sa.Column("blob_hash", sa.String, nullable=True),
        sa.Column("blob_mime", sa.String, nullable=True),
        sa.Column("blob_size_bytes", sa.Integer, nullable=True),
        sa.Column("metadata", JSONB, server_default="{}"),
        sa.Column("source", sa.String, nullable=False, server_default="discord"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("idx_artifacts_created", "artifacts", ["created_at"])
    op.create_index("idx_artifacts_content_type", "artifacts", ["content_type"])

    # classifications
    op.create_table(
        "classifications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("artifact_id", UUID(as_uuid=True), sa.ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("entities", JSONB, server_default="[]"),
        sa.Column("tags", ARRAY(sa.String), server_default="{}"),
        sa.Column("priority", sa.String, server_default="medium"),
        sa.Column("suggested_action", sa.Text, nullable=True),
        sa.Column("model_used", sa.String, nullable=False),
        sa.Column("tokens_used", sa.Integer, nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("is_final", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("idx_class_artifact", "classifications", ["artifact_id"])
    op.create_index("idx_class_category", "classifications", ["category"])
    op.create_index("idx_class_confidence", "classifications", ["confidence"])

    # notes
    op.create_table(
        "notes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("category", sa.String, nullable=False),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("content", sa.Text, nullable=True),
        sa.Column("status", sa.String, nullable=False, server_default="active"),
        sa.Column("priority", sa.String, server_default="medium"),
        sa.Column("tags", ARRAY(sa.String), server_default="{}"),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("remind_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", JSONB, server_default="{}"),
        sa.Column("discord_channel_id", sa.String, nullable=True),
        sa.Column("discord_message_id", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("idx_notes_category", "notes", ["category"])
    op.create_index("idx_notes_status", "notes", ["status"])
    op.create_index("idx_notes_tags", "notes", ["tags"], postgresql_using="gin")

    # chunks
    op.create_table(
        "chunks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("artifact_id", UUID(as_uuid=True), sa.ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("note_id", UUID(as_uuid=True), sa.ForeignKey("notes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("chunk_index", sa.Integer, nullable=False, server_default="0"),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("token_count", sa.Integer, nullable=True),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("metadata", JSONB, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("idx_chunks_artifact", "chunks", ["artifact_id"])
    op.create_index("idx_chunks_note", "chunks", ["note_id"])
    # HNSW index for vector search
    op.execute(
        "CREATE INDEX idx_chunks_embedding ON chunks "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )

    # links
    op.create_table(
        "links",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("source_type", sa.String, nullable=False),
        sa.Column("source_id", UUID(as_uuid=True), nullable=False),
        sa.Column("target_type", sa.String, nullable=False),
        sa.Column("target_id", UUID(as_uuid=True), nullable=False),
        sa.Column("relation", sa.String, nullable=False),
        sa.Column("confidence", sa.Float, server_default="1.0"),
        sa.Column("metadata", JSONB, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("source_type", "source_id", "target_type", "target_id", "relation"),
    )
    op.create_index("idx_links_source", "links", ["source_type", "source_id"])
    op.create_index("idx_links_target", "links", ["target_type", "target_id"])

    # review_queue
    op.create_table(
        "review_queue",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("artifact_id", UUID(as_uuid=True), sa.ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("classification_id", UUID(as_uuid=True), sa.ForeignKey("classifications.id"), nullable=True),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("answer", sa.Text, nullable=True),
        sa.Column("discord_thread_id", sa.String, nullable=True),
        sa.Column("status", sa.String, nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_review_status", "review_queue", ["status"])
    op.create_index("idx_review_artifact", "review_queue", ["artifact_id"])

    # audit_log
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("trace_id", UUID(as_uuid=True), nullable=False, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("agent", sa.String, nullable=False),
        sa.Column("action", sa.String, nullable=False),
        sa.Column("model_used", sa.String, nullable=True),
        sa.Column("input_summary", sa.Text, nullable=True),
        sa.Column("output_summary", sa.Text, nullable=True),
        sa.Column("tokens_used", sa.Integer, nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("metadata", JSONB, server_default="{}"),
    )
    op.create_index("idx_audit_timestamp", "audit_log", ["timestamp"])
    op.create_index("idx_audit_agent", "audit_log", ["agent"])
    op.create_index("idx_audit_trace", "audit_log", ["trace_id"])

    # digests
    op.create_table(
        "digests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("digest_date", sa.Date, nullable=False, unique=True),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("discord_message_id", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("idx_digests_date", "digests", ["digest_date"])


def downgrade() -> None:
    op.drop_table("digests")
    op.drop_table("audit_log")
    op.drop_table("review_queue")
    op.drop_table("links")
    op.drop_table("chunks")
    op.drop_table("notes")
    op.drop_table("classifications")
    op.drop_table("artifacts")
