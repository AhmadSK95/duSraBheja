"""Vault tables — secret storage with asymmetric envelope encryption.

Revision ID: 015
Revises: 014
Create Date: 2026-06-02

Five new tables for the vault subsystem:

- ``vault_material`` — singleton row with the at-rest material needed to
  unlock the vault (salt, encrypted vault private key, vault public key).
- ``vault_unlock_sessions`` — per-device unlock metadata + TTL. The actual
  unwrapped private key lives only in process memory; this table records
  intent + audit info.
- ``vault_secrets`` — owner-confirmed secrets. Envelope stored as JSONB.
- ``vault_secret_candidates`` — pre-classifier or retro-scan hits awaiting owner
  approval. Already encrypted with the public key (safe at rest).
- ``vault_reveal_audits`` — append-only log of every reveal attempt,
  successful or denied.

See ``src/lib/vault_crypto.py`` for the encryption layer and ``src/models.py``
for the SQLAlchemy ORM definitions.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from alembic import op

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vault_material",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("singleton", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("salt", sa.LargeBinary, nullable=False),
        sa.Column("kdf_params", JSONB, nullable=False),
        sa.Column("vault_public_key", sa.LargeBinary, nullable=False),
        sa.Column("encrypted_vault_private_key", sa.LargeBinary, nullable=False),
        sa.Column("private_key_nonce", sa.LargeBinary, nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("singleton", name="uq_vault_material_singleton"),
    )

    op.create_table(
        "vault_unlock_sessions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("session_id", sa.String, nullable=False, unique=True),
        sa.Column("device_label", sa.String, nullable=True),
        sa.Column("device_fingerprint", sa.String, nullable=False),
        sa.Column("ip", sa.String, nullable=True),
        sa.Column(
            "unlocked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "last_active_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("idx_vault_unlock_session", "vault_unlock_sessions", ["session_id"])
    op.create_index("idx_vault_unlock_expires", "vault_unlock_sessions", ["expires_at"])

    op.create_table(
        "vault_secrets",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("label", sa.String, nullable=False),
        sa.Column("envelope", JSONB, nullable=False),
        sa.Column("preview_text", sa.String, nullable=True),
        sa.Column("source_ref", sa.String, nullable=True),
        sa.Column("project_slug", sa.String, nullable=True),
        sa.Column(
            "tags",
            ARRAY(sa.String),
            nullable=False,
            server_default=sa.text("ARRAY[]::text[]"),
        ),
        sa.Column("metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("last_revealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reveal_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("idx_vault_secrets_label", "vault_secrets", ["label"])
    op.create_index("idx_vault_secrets_project", "vault_secrets", ["project_slug"])
    op.create_index("idx_vault_secrets_created", "vault_secrets", ["created_at"])

    op.create_table(
        "vault_secret_candidates",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("envelope", JSONB, nullable=False),
        sa.Column("detection", JSONB, nullable=False),
        sa.Column("source_artifact_type", sa.String, nullable=False),
        sa.Column("source_artifact_id", UUID(as_uuid=True), nullable=False),
        sa.Column("suggested_label", sa.String, nullable=True),
        sa.Column("confidence_tier", sa.String, nullable=False, server_default="medium"),
        sa.Column("status", sa.String, nullable=False, server_default="pending"),
        sa.Column(
            "promoted_to_secret_id",
            UUID(as_uuid=True),
            sa.ForeignKey("vault_secrets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reviewer_session", sa.String, nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "source_artifact_type",
            "source_artifact_id",
            "suggested_label",
            name="uq_vault_secret_candidates_source_label",
        ),
    )
    op.create_index("idx_vault_secret_candidates_status", "vault_secret_candidates", ["status"])
    op.create_index(
        "idx_vault_secret_candidates_source",
        "vault_secret_candidates",
        ["source_artifact_type", "source_artifact_id"],
    )
    op.create_index(
        "idx_vault_secret_candidates_confidence",
        "vault_secret_candidates",
        ["confidence_tier"],
    )

    op.create_table(
        "vault_reveal_audits",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "secret_id",
            UUID(as_uuid=True),
            sa.ForeignKey("vault_secrets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("session_id", sa.String, nullable=True),
        sa.Column("ip", sa.String, nullable=True),
        sa.Column("user_agent", sa.String, nullable=True),
        sa.Column("request_source", sa.String, nullable=False, server_default="dashboard"),
        sa.Column("otp_method", sa.String, nullable=True),
        sa.Column("outcome", sa.String, nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("idx_vault_reveal_audit_secret", "vault_reveal_audits", ["secret_id"])
    op.create_index(
        "idx_vault_reveal_audit_requested",
        "vault_reveal_audits",
        ["requested_at"],
    )
    op.create_index("idx_vault_reveal_audit_outcome", "vault_reveal_audits", ["outcome"])


def downgrade() -> None:
    # Drop in reverse dependency order: tables with FKs to vault_secrets
    # come first.
    op.drop_index("idx_vault_reveal_audit_outcome", table_name="vault_reveal_audits")
    op.drop_index("idx_vault_reveal_audit_requested", table_name="vault_reveal_audits")
    op.drop_index("idx_vault_reveal_audit_secret", table_name="vault_reveal_audits")
    op.drop_table("vault_reveal_audits")

    op.drop_index("idx_vault_secret_candidates_confidence", table_name="vault_secret_candidates")
    op.drop_index("idx_vault_secret_candidates_source", table_name="vault_secret_candidates")
    op.drop_index("idx_vault_secret_candidates_status", table_name="vault_secret_candidates")
    op.drop_table("vault_secret_candidates")

    op.drop_index("idx_vault_secrets_created", table_name="vault_secrets")
    op.drop_index("idx_vault_secrets_project", table_name="vault_secrets")
    op.drop_index("idx_vault_secrets_label", table_name="vault_secrets")
    op.drop_table("vault_secrets")

    op.drop_index("idx_vault_unlock_expires", table_name="vault_unlock_sessions")
    op.drop_index("idx_vault_unlock_session", table_name="vault_unlock_sessions")
    op.drop_table("vault_unlock_sessions")

    op.drop_table("vault_material")
