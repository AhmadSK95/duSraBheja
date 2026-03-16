"""Public surface allowlist and versioned secret vault records.

Revision ID: 009
Revises: 008
Create Date: 2026-03-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "secret_identities",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("normalized_label", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False, server_default="credential"),
        sa.Column("owner_scope", sa.String(), nullable=False, server_default="owner"),
        sa.Column("current_version_id", UUID(as_uuid=True), nullable=True),
        sa.Column("shadow_secret_id", UUID(as_uuid=True), nullable=True),
        sa.Column("project_note_id", UUID(as_uuid=True), nullable=True),
        sa.Column("aliases", JSONB(), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("thread_refs", JSONB(), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("entity_refs", JSONB(), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("metadata", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_note_id"], ["notes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["shadow_secret_id"], ["secret_records.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_label"),
    )
    op.create_index("idx_secret_identities_label", "secret_identities", ["normalized_label"])
    op.create_index("idx_secret_identities_scope", "secret_identities", ["owner_scope"])

    op.create_table(
        "secret_versions",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("identity_id", UUID(as_uuid=True), nullable=False),
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("source_ref", sa.String(), nullable=False),
        sa.Column("secret_type", sa.String(), nullable=False, server_default="credential"),
        sa.Column("username", sa.String(), nullable=True),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("nonce", sa.String(), nullable=False),
        sa.Column("checksum", sa.String(), nullable=False),
        sa.Column("masked_preview", sa.String(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_refs", JSONB(), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("metadata", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["identity_id"], ["secret_identities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_kind", "source_ref"),
    )
    op.create_index("idx_secret_versions_identity", "secret_versions", ["identity_id"])
    op.create_index("idx_secret_versions_current", "secret_versions", ["is_current"])
    op.create_index("idx_secret_versions_created", "secret_versions", ["created_at"])

    op.create_foreign_key(
        "fk_secret_identities_current_version",
        "secret_identities",
        "secret_versions",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "secret_access_audits",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("identity_id", UUID(as_uuid=True), nullable=True),
        sa.Column("version_id", UUID(as_uuid=True), nullable=True),
        sa.Column("requester", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="ok"),
        sa.Column("metadata", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["identity_id"], ["secret_identities.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["version_id"], ["secret_versions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_secret_access_audits_identity", "secret_access_audits", ["identity_id"])
    op.create_index("idx_secret_access_audits_version", "secret_access_audits", ["version_id"])
    op.create_index("idx_secret_access_audits_created", "secret_access_audits", ["created_at"])

    op.create_table(
        "public_fact_records",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("fact_key", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("fact_type", sa.String(), nullable=False, server_default="profile_fact"),
        sa.Column("facet", sa.String(), nullable=False, server_default="about"),
        sa.Column("visibility", sa.String(), nullable=False, server_default="public"),
        sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("refresh_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("project_slug", sa.String(), nullable=True),
        sa.Column("source_kind", sa.String(), nullable=False, server_default="manual"),
        sa.Column("source_ref", sa.String(), nullable=False),
        sa.Column("source_refs", JSONB(), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("tags", JSONB(), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fact_key"),
    )
    op.create_index("idx_public_facts_type", "public_fact_records", ["fact_type"])
    op.create_index("idx_public_facts_facet", "public_fact_records", ["facet"])
    op.create_index("idx_public_facts_project", "public_fact_records", ["project_slug"])
    op.create_index("idx_public_facts_approved", "public_fact_records", ["approved"])

    op.create_table(
        "public_profile_snapshots",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_key", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("payload", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("source_refs", JSONB(), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("metadata", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_key"),
    )
    op.create_index(
        "idx_public_profile_snapshots_refreshed",
        "public_profile_snapshots",
        ["refreshed_at"],
    )

    op.create_table(
        "public_project_snapshots",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("payload", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("source_refs", JSONB(), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("metadata", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index(
        "idx_public_project_snapshots_refreshed",
        "public_project_snapshots",
        ["refreshed_at"],
    )

    op.create_table(
        "public_faq_snapshots",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("question_key", sa.String(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("source_refs", JSONB(), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("metadata", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("question_key"),
    )
    op.create_index("idx_public_faq_snapshots_refreshed", "public_faq_snapshots", ["refreshed_at"])

    op.create_table(
        "public_answer_policies",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("policy_key", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("allowed_topics", JSONB(), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("disallowed_topics", JSONB(), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("payload", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("metadata", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("policy_key"),
    )
    op.create_index(
        "idx_public_answer_policies_active",
        "public_answer_policies",
        ["is_active"],
    )

    op.execute(
        sa.text(
            """
            WITH ranked_secrets AS (
                SELECT
                    sr.id AS secret_id,
                    sr.label AS label,
                    NULLIF(TRIM(BOTH '-' FROM LOWER(REGEXP_REPLACE(sr.label, '[^a-zA-Z0-9]+', '-', 'g'))), '') AS base_label,
                    ROW_NUMBER() OVER (
                        PARTITION BY LOWER(REGEXP_REPLACE(sr.label, '[^a-zA-Z0-9]+', '-', 'g'))
                        ORDER BY sr.created_at, sr.id
                    ) AS ordinal
                FROM secret_records sr
            ),
            inserted_identities AS (
                INSERT INTO secret_identities (
                    id,
                    label,
                    normalized_label,
                    category,
                    owner_scope,
                    current_version_id,
                    shadow_secret_id,
                    project_note_id,
                    aliases,
                    thread_refs,
                    entity_refs,
                    metadata,
                    created_at,
                    updated_at
                )
                SELECT
                    uuid_generate_v4(),
                    sr.label,
                    CASE
                        WHEN ranked.base_label IS NULL THEN CONCAT('secret-', SUBSTRING(sr.id::text, 1, 8))
                        WHEN ranked.ordinal = 1 THEN ranked.base_label
                        ELSE CONCAT(ranked.base_label, '-', ranked.ordinal)
                    END,
                    sr.secret_type,
                    sr.owner_scope,
                    NULL,
                    sr.id,
                    NULL,
                    COALESCE(
                        (
                            SELECT JSONB_AGG(alias_record.alias ORDER BY alias_record.created_at)
                            FROM secret_alias_records alias_record
                            WHERE alias_record.secret_id = sr.id
                        ),
                        '[]'::jsonb
                    ),
                    COALESCE(sr.thread_refs, '[]'::jsonb),
                    COALESCE(sr.entity_refs, '[]'::jsonb),
                    COALESCE(sr.metadata, '{}'::jsonb),
                    sr.created_at,
                    sr.updated_at
                FROM secret_records sr
                JOIN ranked_secrets ranked
                  ON ranked.secret_id = sr.id
                RETURNING id, shadow_secret_id
            ),
            inserted_versions AS (
                INSERT INTO secret_versions (
                    id,
                    identity_id,
                    source_kind,
                    source_ref,
                    secret_type,
                    username,
                    ciphertext,
                    nonce,
                    checksum,
                    masked_preview,
                    is_current,
                    superseded_at,
                    source_refs,
                    notes,
                    metadata,
                    created_at,
                    updated_at
                )
                SELECT
                    uuid_generate_v4(),
                    identity.id,
                    secret_record.source_kind,
                    secret_record.source_ref,
                    secret_record.secret_type,
                    NULL,
                    secret_record.ciphertext,
                    secret_record.nonce,
                    secret_record.checksum,
                    secret_record.masked_preview,
                    TRUE,
                    NULL,
                    COALESCE(secret_record.source_refs, '[]'::jsonb),
                    NULL,
                    COALESCE(secret_record.metadata, '{}'::jsonb),
                    secret_record.created_at,
                    secret_record.updated_at
                FROM inserted_identities identity
                JOIN secret_records secret_record
                  ON secret_record.id = identity.shadow_secret_id
                RETURNING id, identity_id
            )
            UPDATE secret_identities identity
            SET current_version_id = version.id
            FROM inserted_versions version
            WHERE version.identity_id = identity.id
            """
        )
    )


def downgrade() -> None:
    op.drop_index("idx_public_answer_policies_active", table_name="public_answer_policies")
    op.drop_table("public_answer_policies")

    op.drop_index("idx_public_faq_snapshots_refreshed", table_name="public_faq_snapshots")
    op.drop_table("public_faq_snapshots")

    op.drop_index(
        "idx_public_project_snapshots_refreshed",
        table_name="public_project_snapshots",
    )
    op.drop_table("public_project_snapshots")

    op.drop_index(
        "idx_public_profile_snapshots_refreshed",
        table_name="public_profile_snapshots",
    )
    op.drop_table("public_profile_snapshots")

    op.drop_index("idx_public_facts_approved", table_name="public_fact_records")
    op.drop_index("idx_public_facts_project", table_name="public_fact_records")
    op.drop_index("idx_public_facts_facet", table_name="public_fact_records")
    op.drop_index("idx_public_facts_type", table_name="public_fact_records")
    op.drop_table("public_fact_records")

    op.drop_index("idx_secret_access_audits_created", table_name="secret_access_audits")
    op.drop_index("idx_secret_access_audits_version", table_name="secret_access_audits")
    op.drop_index("idx_secret_access_audits_identity", table_name="secret_access_audits")
    op.drop_table("secret_access_audits")

    op.drop_constraint("fk_secret_identities_current_version", "secret_identities", type_="foreignkey")
    op.drop_index("idx_secret_versions_created", table_name="secret_versions")
    op.drop_index("idx_secret_versions_current", table_name="secret_versions")
    op.drop_index("idx_secret_versions_identity", table_name="secret_versions")
    op.drop_table("secret_versions")

    op.drop_index("idx_secret_identities_scope", table_name="secret_identities")
    op.drop_index("idx_secret_identities_label", table_name="secret_identities")
    op.drop_table("secret_identities")
