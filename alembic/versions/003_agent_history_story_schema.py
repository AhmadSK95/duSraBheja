"""Agent-history storyteller schema additions.

Revision ID: 003
Revises: 002
Create Date: 2026-03-12
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "journal_entries",
        sa.Column("subject_type", sa.String(), nullable=False, server_default="topic"),
    )
    op.add_column("journal_entries", sa.Column("subject_ref", sa.String(), nullable=True))
    op.add_column("journal_entries", sa.Column("decision", sa.Text(), nullable=True))
    op.add_column("journal_entries", sa.Column("rationale", sa.Text(), nullable=True))
    op.add_column("journal_entries", sa.Column("constraint", sa.Text(), nullable=True))
    op.add_column("journal_entries", sa.Column("outcome", sa.Text(), nullable=True))
    op.add_column("journal_entries", sa.Column("impact", sa.Text(), nullable=True))
    op.add_column("journal_entries", sa.Column("open_question", sa.Text(), nullable=True))
    op.add_column(
        "journal_entries",
        sa.Column("evidence_refs", JSONB(), nullable=False, server_default="[]"),
    )
    op.create_index("idx_journal_subject", "journal_entries", ["subject_type", "subject_ref"])

    op.alter_column("journal_entries", "subject_type", server_default=None)
    op.alter_column("journal_entries", "evidence_refs", server_default=None)


def downgrade() -> None:
    op.drop_index("idx_journal_subject", table_name="journal_entries")
    op.drop_column("journal_entries", "evidence_refs")
    op.drop_column("journal_entries", "open_question")
    op.drop_column("journal_entries", "impact")
    op.drop_column("journal_entries", "outcome")
    op.drop_column("journal_entries", "constraint")
    op.drop_column("journal_entries", "rationale")
    op.drop_column("journal_entries", "decision")
    op.drop_column("journal_entries", "subject_ref")
    op.drop_column("journal_entries", "subject_type")
