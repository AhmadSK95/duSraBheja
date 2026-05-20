"""Dashboard view state (last-seen tracker) and BrainCounter table.

Revision ID: 014
Revises: 013
Create Date: 2026-05-20

`dashboard_view_state` powers the "What's New since I last looked" page in the
lean Atlas dashboard. `brain_counters` is a tiny key-value table used to count
librarian merges since the last on-demand cognition run.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dashboard_view_state",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("username", sa.String, nullable=False, unique=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_table(
        "brain_counters",
        sa.Column("key", sa.String, primary_key=True),
        sa.Column("value", sa.Integer, nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("brain_counters")
    op.drop_table("dashboard_view_state")
