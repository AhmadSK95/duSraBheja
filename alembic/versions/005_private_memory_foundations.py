"""Private memory foundations: aliases, protected content, voice profiles.

Revision ID: 005
Revises: 004
Create Date: 2026-03-12
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_aliases",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("project_note_id", UUID(as_uuid=True), nullable=False),
        sa.Column("alias", sa.String(), nullable=False),
        sa.Column("normalized_alias", sa.String(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=True),
        sa.Column("source_ref", sa.String(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.8"),
        sa.Column("is_manual", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("metadata", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_note_id"], ["notes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_alias"),
    )
    op.create_index("idx_project_alias_project", "project_aliases", ["project_note_id"])
    op.create_index("idx_project_alias_source", "project_aliases", ["source_type", "source_ref"])

    op.create_table(
        "protected_contents",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("source_ref", sa.String(), nullable=False),
        sa.Column("content_kind", sa.String(), nullable=False, server_default="body"),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("nonce", sa.String(), nullable=False),
        sa.Column("checksum", sa.String(), nullable=False),
        sa.Column("preview_text", sa.Text(), nullable=True),
        sa.Column("metadata", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_type", "source_ref", "content_kind"),
    )
    op.create_index("idx_protected_content_source", "protected_contents", ["source_type", "source_ref"])

    op.create_table(
        "voice_profiles",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("profile_name", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("traits", JSONB(), nullable=True),
        sa.Column("style_anchors", JSONB(), nullable=True),
        sa.Column("source_refs", JSONB(), nullable=True),
        sa.Column("metadata", JSONB(), nullable=True),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profile_name"),
    )
    op.create_index("idx_voice_profiles_name", "voice_profiles", ["profile_name"])


def downgrade() -> None:
    op.drop_index("idx_voice_profiles_name", table_name="voice_profiles")
    op.drop_table("voice_profiles")

    op.drop_index("idx_protected_content_source", table_name="protected_contents")
    op.drop_table("protected_contents")

    op.drop_index("idx_project_alias_source", table_name="project_aliases")
    op.drop_index("idx_project_alias_project", table_name="project_aliases")
    op.drop_table("project_aliases")
