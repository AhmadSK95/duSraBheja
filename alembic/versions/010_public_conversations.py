"""Public conversation tables for multi-turn chatbot.

Revision ID: 010
Revises: 009
Create Date: 2026-03-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "public_conversations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("conversation_id", sa.String, nullable=False),
        sa.Column("remote_ip", sa.String, nullable=True),
        sa.Column("user_agent", sa.String, nullable=True),
        sa.Column("topic_summary", sa.String, nullable=True),
        sa.Column("turn_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("intent", sa.String, nullable=True),
        sa.Column("persona_hash", sa.String, nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata", JSONB, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("conversation_id"),
    )
    op.create_index("idx_public_conversations_expires", "public_conversations", ["expires_at"])
    op.create_index("idx_public_conversations_ip", "public_conversations", ["remote_ip"])

    op.create_table(
        "public_conversation_turns",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "conversation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("intent", sa.String, nullable=True),
        sa.Column("model_used", sa.String, nullable=True),
        sa.Column("input_tokens", sa.Integer, nullable=True),
        sa.Column("output_tokens", sa.Integer, nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_public_conversation_turns_conv", "public_conversation_turns", ["conversation_id"])
    op.create_index("idx_public_conversation_turns_created", "public_conversation_turns", ["created_at"])


def downgrade() -> None:
    op.drop_table("public_conversation_turns")
    op.drop_table("public_conversations")
