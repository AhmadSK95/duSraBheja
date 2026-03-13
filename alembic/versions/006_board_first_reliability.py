"""Board-first reliability schema.

Revision ID: 006
Revises: 005
Create Date: 2026-03-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "classifications",
        sa.Column("capture_intent", sa.String(), nullable=False, server_default="thought"),
    )
    op.add_column(
        "classifications",
        sa.Column("intent_confidence", sa.Float(), nullable=False, server_default="0.5"),
    )
    op.add_column(
        "classifications",
        sa.Column("validation_status", sa.String(), nullable=False, server_default="validated"),
    )
    op.add_column(
        "classifications",
        sa.Column("quality_issues", JSONB(), nullable=True, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "classifications",
        sa.Column("eligible_for_boards", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "classifications",
        sa.Column("eligible_for_project_state", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.add_column(
        "review_queue",
        sa.Column("review_kind", sa.String(), nullable=False, server_default="moderation"),
    )
    op.add_column("review_queue", sa.Column("resolution", sa.Text(), nullable=True))
    op.add_column("review_queue", sa.Column("moderation_notes", sa.Text(), nullable=True))
    op.add_column("review_queue", sa.Column("resolved_by", sa.String(), nullable=True))

    op.create_table(
        "boards",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("board_type", sa.String(), nullable=False),
        sa.Column("coverage_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("coverage_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generated_for_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="ready"),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("source_artifact_ids", JSONB(), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("excluded_artifact_ids", JSONB(), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("discord_channel_name", sa.String(), nullable=True),
        sa.Column("discord_message_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("board_type", "coverage_start", "coverage_end"),
    )
    op.create_index("idx_boards_type_date", "boards", ["board_type", "generated_for_date"])
    op.create_index("idx_boards_status", "boards", ["status"])


def downgrade() -> None:
    op.drop_index("idx_boards_status", table_name="boards")
    op.drop_index("idx_boards_type_date", table_name="boards")
    op.drop_table("boards")

    op.drop_column("review_queue", "resolved_by")
    op.drop_column("review_queue", "moderation_notes")
    op.drop_column("review_queue", "resolution")
    op.drop_column("review_queue", "review_kind")

    op.drop_column("classifications", "eligible_for_project_state")
    op.drop_column("classifications", "eligible_for_boards")
    op.drop_column("classifications", "quality_issues")
    op.drop_column("classifications", "validation_status")
    op.drop_column("classifications", "intent_confidence")
    op.drop_column("classifications", "capture_intent")
