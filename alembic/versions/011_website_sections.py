"""Website sections table for brain-owned site builder.

Revision ID: 011
Revises: 010
Create Date: 2026-03-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "website_sections",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("page", sa.String, nullable=False),
        sa.Column("section_key", sa.String, nullable=False),
        sa.Column("section_type", sa.String, nullable=False),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("title", sa.String, nullable=True),
        sa.Column("content", JSONB, nullable=False, server_default="{}"),
        sa.Column("style_hints", JSONB, nullable=False, server_default="{}"),
        sa.Column("visible", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_by", sa.String, nullable=False, server_default="'seed'"),
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
        sa.Column("metadata", JSONB, server_default="{}"),
        sa.UniqueConstraint("page", "section_key"),
    )
    op.create_index("idx_website_sections_page", "website_sections", ["page"])
    op.create_index("idx_website_sections_visible", "website_sections", ["visible"])


def downgrade() -> None:
    op.drop_index("idx_website_sections_visible")
    op.drop_index("idx_website_sections_page")
    op.drop_table("website_sections")
