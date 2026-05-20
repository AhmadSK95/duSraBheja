"""Switch chunk embeddings to 1024 dimensions (NVIDIA NIM nv-embedqa-e5-v5).

Revision ID: 013
Revises: 012
Create Date: 2026-05-20

The free-tier NVIDIA NIM embedding model is 1024-dim. We drop the existing
pgvector column (the old 1536-dim vectors are unusable) and recreate it at
1024-dim. Also adds an `embedding_model` column on chunks so the reindex
script can be idempotent across model swaps. After this migration, run
`scripts/reindex_embeddings.py` to repopulate vectors.
"""

import sqlalchemy as sa

from alembic import op

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("chunks", "embedding")
    op.execute("ALTER TABLE chunks ADD COLUMN embedding vector(1024)")
    op.add_column(
        "chunks",
        sa.Column("embedding_model", sa.String, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chunks", "embedding_model")
    op.drop_column("chunks", "embedding")
    op.execute("ALTER TABLE chunks ADD COLUMN embedding vector(1536)")
