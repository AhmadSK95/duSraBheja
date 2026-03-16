"""Canonical library records and owner-verified secret vault.

Revision ID: 008
Revises: 007
Create Date: 2026-03-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evidence_records",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("source_ref", sa.String(), nullable=False),
        sa.Column("artifact_id", UUID(as_uuid=True), nullable=True),
        sa.Column("source_item_id", UUID(as_uuid=True), nullable=True),
        sa.Column("project_note_id", UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("content_kind", sa.String(), nullable=False, server_default="text"),
        sa.Column("source_type", sa.String(), nullable=False, server_default="unknown"),
        sa.Column("provenance_kind", sa.String(), nullable=False, server_default="direct_sync"),
        sa.Column("retention_class", sa.String(), nullable=False, server_default="warm"),
        sa.Column("content_hash", sa.String(), nullable=True),
        sa.Column("is_sensitive", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("metadata", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_item_id"], ["source_items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_note_id"], ["notes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_kind", "source_ref"),
    )
    op.create_index("idx_evidence_source", "evidence_records", ["source_type"])
    op.create_index("idx_evidence_event_time", "evidence_records", ["event_time"])
    op.create_index("idx_evidence_project", "evidence_records", ["project_note_id"])

    op.create_table(
        "thread_records",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("source_ref", sa.String(), nullable=False),
        sa.Column("thread_type", sa.String(), nullable=False, server_default="topic"),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("priority", sa.String(), nullable=False, server_default="medium"),
        sa.Column("provenance_kind", sa.String(), nullable=False, server_default="direct_sync"),
        sa.Column("retention_class", sa.String(), nullable=False, server_default="hot"),
        sa.Column("project_note_id", UUID(as_uuid=True), nullable=True),
        sa.Column("subject_ref", sa.String(), nullable=True),
        sa.Column("aliases", JSONB(), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("metadata", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_note_id"], ["notes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_kind", "source_ref"),
    )
    op.create_index("idx_threads_type", "thread_records", ["thread_type"])
    op.create_index("idx_threads_status", "thread_records", ["status"])
    op.create_index("idx_threads_last_event", "thread_records", ["last_event_at"])
    op.create_index("idx_threads_project", "thread_records", ["project_note_id"])

    op.create_table(
        "entity_records",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("source_ref", sa.String(), nullable=False),
        sa.Column("entity_type", sa.String(), nullable=False, server_default="topic"),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("normalized_name", sa.String(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("aliases", JSONB(), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("thread_ids", JSONB(), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("metadata", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_kind", "source_ref"),
    )
    op.create_index("idx_entities_type", "entity_records", ["entity_type"])
    op.create_index("idx_entities_name", "entity_records", ["normalized_name"])
    op.create_index("idx_entities_last_seen", "entity_records", ["last_seen_at"])

    op.create_table(
        "observation_records",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("source_ref", sa.String(), nullable=False),
        sa.Column("project_note_id", UUID(as_uuid=True), nullable=True),
        sa.Column("observation_type", sa.String(), nullable=False, server_default="fact"),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("certainty", sa.Float(), nullable=False, server_default="0.7"),
        sa.Column("provenance_kind", sa.String(), nullable=False, server_default="direct_sync"),
        sa.Column("retention_class", sa.String(), nullable=False, server_default="hot"),
        sa.Column("actor", sa.String(), nullable=True),
        sa.Column("thread_ids", JSONB(), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("entity_ids", JSONB(), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("evidence_ids", JSONB(), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("metadata", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_note_id"], ["notes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_kind", "source_ref"),
    )
    op.create_index("idx_observations_type", "observation_records", ["observation_type"])
    op.create_index("idx_observations_event_time", "observation_records", ["event_time"])
    op.create_index("idx_observations_project", "observation_records", ["project_note_id"])

    op.create_table(
        "episode_records",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("source_ref", sa.String(), nullable=False),
        sa.Column("project_note_id", UUID(as_uuid=True), nullable=True),
        sa.Column("episode_type", sa.String(), nullable=False, server_default="session"),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("provenance_kind", sa.String(), nullable=False, server_default="direct_sync"),
        sa.Column("retention_class", sa.String(), nullable=False, server_default="hot"),
        sa.Column("participants", JSONB(), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("thread_ids", JSONB(), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("entity_ids", JSONB(), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("observation_ids", JSONB(), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("source_refs", JSONB(), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("metadata", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("coverage_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("coverage_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_note_id"], ["notes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_kind", "source_ref"),
    )
    op.create_index("idx_episodes_type", "episode_records", ["episode_type"])
    op.create_index("idx_episodes_coverage_start", "episode_records", ["coverage_start"])
    op.create_index("idx_episodes_project", "episode_records", ["project_note_id"])

    op.create_table(
        "synthesis_records",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("source_ref", sa.String(), nullable=False),
        sa.Column("project_note_id", UUID(as_uuid=True), nullable=True),
        sa.Column("synthesis_type", sa.String(), nullable=False, server_default="replay"),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("certainty_class", sa.String(), nullable=False, server_default="grounded_observation"),
        sa.Column("provenance_kind", sa.String(), nullable=False, server_default="derived_system"),
        sa.Column("thread_ids", JSONB(), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("entity_ids", JSONB(), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("source_refs", JSONB(), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("metadata", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_note_id"], ["notes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_kind", "source_ref"),
    )
    op.create_index("idx_syntheses_type", "synthesis_records", ["synthesis_type"])
    op.create_index("idx_syntheses_event_time", "synthesis_records", ["event_time"])
    op.create_index("idx_syntheses_project", "synthesis_records", ["project_note_id"])

    op.create_table(
        "capability_records",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("capability_key", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("protocol", sa.String(), nullable=False, server_default="http"),
        sa.Column("visibility", sa.String(), nullable=False, server_default="private"),
        sa.Column("payload", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("metadata", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("capability_key"),
    )
    op.create_index("idx_capabilities_protocol", "capability_records", ["protocol"])

    op.create_table(
        "secret_records",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("source_ref", sa.String(), nullable=False),
        sa.Column("secret_type", sa.String(), nullable=False, server_default="credential"),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("nonce", sa.String(), nullable=False),
        sa.Column("checksum", sa.String(), nullable=False),
        sa.Column("masked_preview", sa.String(), nullable=False),
        sa.Column("owner_scope", sa.String(), nullable=False, server_default="owner"),
        sa.Column("thread_refs", JSONB(), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("entity_refs", JSONB(), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("source_refs", JSONB(), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("rotation_metadata", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("metadata", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_kind", "source_ref"),
    )
    op.create_index("idx_secret_type", "secret_records", ["secret_type"])
    op.create_index("idx_secret_label", "secret_records", ["label"])

    op.create_table(
        "secret_alias_records",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("secret_id", UUID(as_uuid=True), nullable=False),
        sa.Column("alias", sa.String(), nullable=False),
        sa.Column("normalized_alias", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["secret_id"], ["secret_records.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_alias"),
    )
    op.create_index("idx_secret_alias_secret", "secret_alias_records", ["secret_id"])

    op.create_table(
        "secret_access_challenges",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("secret_id", UUID(as_uuid=True), nullable=True),
        sa.Column("requester", sa.String(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("challenge_hash", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["secret_id"], ["secret_records.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_secret_challenge_secret", "secret_access_challenges", ["secret_id"])
    op.create_index("idx_secret_challenge_status", "secret_access_challenges", ["status"])
    op.create_index("idx_secret_challenge_expires", "secret_access_challenges", ["expires_at"])

    op.create_table(
        "secret_access_grants",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("secret_id", UUID(as_uuid=True), nullable=False),
        sa.Column("challenge_id", UUID(as_uuid=True), nullable=False),
        sa.Column("requester", sa.String(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("grant_hash", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["secret_id"], ["secret_records.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["challenge_id"], ["secret_access_challenges.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_secret_grant_secret", "secret_access_grants", ["secret_id"])
    op.create_index("idx_secret_grant_status", "secret_access_grants", ["status"])
    op.create_index("idx_secret_grant_expires", "secret_access_grants", ["expires_at"])

    op.create_table(
        "secret_audit_entries",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("secret_id", UUID(as_uuid=True), nullable=True),
        sa.Column("challenge_id", UUID(as_uuid=True), nullable=True),
        sa.Column("grant_id", UUID(as_uuid=True), nullable=True),
        sa.Column("requester", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="ok"),
        sa.Column("metadata", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["secret_id"], ["secret_records.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["challenge_id"], ["secret_access_challenges.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["grant_id"], ["secret_access_grants.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_secret_audit_secret", "secret_audit_entries", ["secret_id"])
    op.create_index("idx_secret_audit_created", "secret_audit_entries", ["created_at"])
    op.create_index("idx_secret_audit_action", "secret_audit_entries", ["action"])


def downgrade() -> None:
    op.drop_index("idx_secret_audit_action", table_name="secret_audit_entries")
    op.drop_index("idx_secret_audit_created", table_name="secret_audit_entries")
    op.drop_index("idx_secret_audit_secret", table_name="secret_audit_entries")
    op.drop_table("secret_audit_entries")

    op.drop_index("idx_secret_grant_expires", table_name="secret_access_grants")
    op.drop_index("idx_secret_grant_status", table_name="secret_access_grants")
    op.drop_index("idx_secret_grant_secret", table_name="secret_access_grants")
    op.drop_table("secret_access_grants")

    op.drop_index("idx_secret_challenge_expires", table_name="secret_access_challenges")
    op.drop_index("idx_secret_challenge_status", table_name="secret_access_challenges")
    op.drop_index("idx_secret_challenge_secret", table_name="secret_access_challenges")
    op.drop_table("secret_access_challenges")

    op.drop_index("idx_secret_alias_secret", table_name="secret_alias_records")
    op.drop_table("secret_alias_records")

    op.drop_index("idx_secret_label", table_name="secret_records")
    op.drop_index("idx_secret_type", table_name="secret_records")
    op.drop_table("secret_records")

    op.drop_index("idx_capabilities_protocol", table_name="capability_records")
    op.drop_table("capability_records")

    op.drop_index("idx_syntheses_project", table_name="synthesis_records")
    op.drop_index("idx_syntheses_event_time", table_name="synthesis_records")
    op.drop_index("idx_syntheses_type", table_name="synthesis_records")
    op.drop_table("synthesis_records")

    op.drop_index("idx_episodes_project", table_name="episode_records")
    op.drop_index("idx_episodes_coverage_start", table_name="episode_records")
    op.drop_index("idx_episodes_type", table_name="episode_records")
    op.drop_table("episode_records")

    op.drop_index("idx_observations_project", table_name="observation_records")
    op.drop_index("idx_observations_event_time", table_name="observation_records")
    op.drop_index("idx_observations_type", table_name="observation_records")
    op.drop_table("observation_records")

    op.drop_index("idx_entities_last_seen", table_name="entity_records")
    op.drop_index("idx_entities_name", table_name="entity_records")
    op.drop_index("idx_entities_type", table_name="entity_records")
    op.drop_table("entity_records")

    op.drop_index("idx_threads_project", table_name="thread_records")
    op.drop_index("idx_threads_last_event", table_name="thread_records")
    op.drop_index("idx_threads_status", table_name="thread_records")
    op.drop_index("idx_threads_type", table_name="thread_records")
    op.drop_table("thread_records")

    op.drop_index("idx_evidence_project", table_name="evidence_records")
    op.drop_index("idx_evidence_event_time", table_name="evidence_records")
    op.drop_index("idx_evidence_source", table_name="evidence_records")
    op.drop_table("evidence_records")
