"""Brain hardening schema additions.

Revision ID: 004
Revises: 003
Create Date: 2026-03-12
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_sessions",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("source_item_id", UUID(as_uuid=True), nullable=False),
        sa.Column("project_note_id", UUID(as_uuid=True), nullable=True),
        sa.Column("agent_kind", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("parent_session_id", sa.String(), nullable=True),
        sa.Column("cwd", sa.String(), nullable=True),
        sa.Column("title_hint", sa.String(), nullable=True),
        sa.Column("transcript_blob_ref", sa.String(), nullable=True),
        sa.Column("transcript_excerpt", sa.Text(), nullable=True),
        sa.Column("participants", ARRAY(sa.String()), nullable=True),
        sa.Column("turn_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_note_id"], ["notes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_item_id"], ["source_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_item_id"),
    )
    op.create_index("idx_conversation_sessions_project", "conversation_sessions", ["project_note_id"])
    op.create_index("idx_conversation_sessions_agent", "conversation_sessions", ["agent_kind"])
    op.create_index("idx_conversation_sessions_ended", "conversation_sessions", ["ended_at"])

    op.create_table(
        "project_state_snapshots",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("project_note_id", UUID(as_uuid=True), nullable=False),
        sa.Column("active_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(), nullable=False, server_default="uncertain"),
        sa.Column("manual_state", sa.String(), nullable=False, server_default="normal"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("implemented", sa.Text(), nullable=True),
        sa.Column("remaining", sa.Text(), nullable=True),
        sa.Column("blockers", JSONB(), nullable=True),
        sa.Column("risks", JSONB(), nullable=True),
        sa.Column("holes", JSONB(), nullable=True),
        sa.Column("what_changed", sa.Text(), nullable=True),
        sa.Column("why_active", sa.Text(), nullable=True),
        sa.Column("why_not_active", sa.Text(), nullable=True),
        sa.Column("last_signal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("feature_scores", JSONB(), nullable=True),
        sa.Column("metadata", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_note_id"], ["notes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_note_id"),
    )
    op.create_index("idx_project_snapshots_status", "project_state_snapshots", ["status"])
    op.create_index("idx_project_snapshots_score", "project_state_snapshots", ["active_score"])
    op.create_index("idx_project_snapshots_signal", "project_state_snapshots", ["last_signal_at"])

    op.create_table(
        "story_connections",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("source_ref", sa.String(), nullable=False),
        sa.Column("target_ref", sa.String(), nullable=False),
        sa.Column("relation", sa.String(), nullable=False, server_default="co_signal"),
        sa.Column("source_project_note_id", UUID(as_uuid=True), nullable=True),
        sa.Column("target_project_note_id", UUID(as_uuid=True), nullable=True),
        sa.Column("weight", sa.Float(), nullable=False, server_default="0"),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata", JSONB(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_project_note_id"], ["notes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["target_project_note_id"], ["notes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_ref", "target_ref", "relation"),
    )
    op.create_index("idx_story_connections_source", "story_connections", ["source_ref"])
    op.create_index("idx_story_connections_target", "story_connections", ["target_ref"])
    op.create_index("idx_story_connections_weight", "story_connections", ["weight"])

    op.create_table(
        "reminders",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("note_id", UUID(as_uuid=True), nullable=True),
        sa.Column("project_note_id", UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("timezone", sa.String(), nullable=False, server_default="America/New_York"),
        sa.Column("recurrence_kind", sa.String(), nullable=False, server_default="once"),
        sa.Column("recurrence_rule", JSONB(), nullable=True),
        sa.Column("next_fire_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_channel", sa.String(), nullable=False, server_default="discord"),
        sa.Column("discord_channel_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("metadata", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["note_id"], ["notes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_note_id"], ["notes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_reminders_next_fire", "reminders", ["next_fire_at"])
    op.create_index("idx_reminders_status", "reminders", ["status"])
    op.create_index("idx_reminders_project", "reminders", ["project_note_id"])

    op.create_table(
        "digest_preferences",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("profile_name", sa.String(), nullable=False),
        sa.Column("timezone", sa.String(), nullable=False, server_default="America/New_York"),
        sa.Column("sections", JSONB(), nullable=True),
        sa.Column("metadata", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profile_name"),
    )
    op.create_index("idx_digest_preferences_profile", "digest_preferences", ["profile_name"])


def downgrade() -> None:
    op.drop_index("idx_digest_preferences_profile", table_name="digest_preferences")
    op.drop_table("digest_preferences")

    op.drop_index("idx_reminders_project", table_name="reminders")
    op.drop_index("idx_reminders_status", table_name="reminders")
    op.drop_index("idx_reminders_next_fire", table_name="reminders")
    op.drop_table("reminders")

    op.drop_index("idx_story_connections_weight", table_name="story_connections")
    op.drop_index("idx_story_connections_target", table_name="story_connections")
    op.drop_index("idx_story_connections_source", table_name="story_connections")
    op.drop_table("story_connections")

    op.drop_index("idx_project_snapshots_signal", table_name="project_state_snapshots")
    op.drop_index("idx_project_snapshots_score", table_name="project_state_snapshots")
    op.drop_index("idx_project_snapshots_status", table_name="project_state_snapshots")
    op.drop_table("project_state_snapshots")

    op.drop_index("idx_conversation_sessions_ended", table_name="conversation_sessions")
    op.drop_index("idx_conversation_sessions_agent", table_name="conversation_sessions")
    op.drop_index("idx_conversation_sessions_project", table_name="conversation_sessions")
    op.drop_table("conversation_sessions")
