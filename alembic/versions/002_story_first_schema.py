"""Story-first schema additions.

Revision ID: 002
Revises: 001
Create Date: 2026-03-11
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE notes SET category = 'daily_planner' WHERE category = 'planner'")
    op.execute("UPDATE classifications SET category = 'daily_planner' WHERE category = 'planner'")

    op.create_table(
        "sync_sources",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("source_type", sa.String, nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="active"),
        sa.Column("config", JSONB, server_default="{}"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("source_type", "name"),
    )
    op.create_index("idx_sync_sources_type", "sync_sources", ["source_type"])

    op.create_table(
        "sync_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("sync_source_id", UUID(as_uuid=True), sa.ForeignKey("sync_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mode", sa.String, nullable=False, server_default="sync"),
        sa.Column("status", sa.String, nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("items_seen", sa.Integer, nullable=False, server_default="0"),
        sa.Column("items_imported", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("metadata", JSONB, server_default="{}"),
    )
    op.create_index("idx_sync_runs_source", "sync_runs", ["sync_source_id"])
    op.create_index("idx_sync_runs_status", "sync_runs", ["status"])

    op.create_table(
        "project_repos",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("project_note_id", UUID(as_uuid=True), sa.ForeignKey("notes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("repo_owner", sa.String, nullable=True),
        sa.Column("repo_name", sa.String, nullable=False),
        sa.Column("repo_url", sa.String, nullable=True),
        sa.Column("branch", sa.String, nullable=True),
        sa.Column("local_path", sa.String, nullable=True),
        sa.Column("is_primary", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("project_note_id", "repo_name"),
    )
    op.create_index("idx_project_repos_note", "project_repos", ["project_note_id"])

    op.create_table(
        "source_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("sync_source_id", UUID(as_uuid=True), sa.ForeignKey("sync_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_note_id", UUID(as_uuid=True), sa.ForeignKey("notes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("artifact_id", UUID(as_uuid=True), sa.ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("external_id", sa.String, nullable=False),
        sa.Column("external_url", sa.String, nullable=True),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("payload", JSONB, server_default="{}"),
        sa.Column("content_hash", sa.String, nullable=True),
        sa.Column("happened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("sync_source_id", "external_id"),
    )
    op.create_index("idx_source_items_project", "source_items", ["project_note_id"])
    op.create_index("idx_source_items_hash", "source_items", ["content_hash"])

    op.create_table(
        "journal_entries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("artifact_id", UUID(as_uuid=True), sa.ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("project_note_id", UUID(as_uuid=True), sa.ForeignKey("notes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_item_id", UUID(as_uuid=True), sa.ForeignKey("source_items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("entry_type", sa.String, nullable=False, server_default="note"),
        sa.Column("actor_type", sa.String, nullable=False, server_default="human"),
        sa.Column("actor_name", sa.String, nullable=False, server_default="unknown"),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("body_markdown", sa.Text, nullable=True),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("tags", ARRAY(sa.String), server_default="{}"),
        sa.Column("source_links", JSONB, server_default="[]"),
        sa.Column("metadata", JSONB, server_default="{}"),
        sa.Column("happened_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("idx_journal_project", "journal_entries", ["project_note_id"])
    op.create_index("idx_journal_happened", "journal_entries", ["happened_at"])
    op.create_index("idx_journal_entry_type", "journal_entries", ["entry_type"])

    op.create_table(
        "oauth_credentials",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("provider", sa.String, nullable=False),
        sa.Column("account_email", sa.String, nullable=True),
        sa.Column("scopes", ARRAY(sa.String), server_default="{}"),
        sa.Column("encrypted_refresh_token", sa.Text, nullable=True),
        sa.Column("encrypted_access_token", sa.Text, nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", JSONB, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("idx_oauth_provider", "oauth_credentials", ["provider"])


def downgrade() -> None:
    op.drop_index("idx_oauth_provider", table_name="oauth_credentials")
    op.drop_table("oauth_credentials")

    op.drop_index("idx_journal_entry_type", table_name="journal_entries")
    op.drop_index("idx_journal_happened", table_name="journal_entries")
    op.drop_index("idx_journal_project", table_name="journal_entries")
    op.drop_table("journal_entries")

    op.drop_index("idx_source_items_hash", table_name="source_items")
    op.drop_index("idx_source_items_project", table_name="source_items")
    op.drop_table("source_items")

    op.drop_index("idx_project_repos_note", table_name="project_repos")
    op.drop_table("project_repos")

    op.drop_index("idx_sync_runs_status", table_name="sync_runs")
    op.drop_index("idx_sync_runs_source", table_name="sync_runs")
    op.drop_table("sync_runs")

    op.drop_index("idx_sync_sources_type", table_name="sync_sources")
    op.drop_table("sync_sources")
