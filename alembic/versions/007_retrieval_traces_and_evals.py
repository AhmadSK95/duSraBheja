"""Retrieval traces and evaluation storage.

Revision ID: 007
Revises: 006
Create Date: 2026-03-15
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "retrieval_traces",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("resolved_mode", sa.String(), nullable=False, server_default="answer"),
        sa.Column("resolved_intent", sa.String(), nullable=False, server_default="general_answer"),
        sa.Column("failure_stage", sa.String(), nullable=True),
        sa.Column("evidence_quality", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("used_exact_match", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("used_project_snapshot", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("used_vector_search", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("used_web", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("payload", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_retrieval_traces_created", "retrieval_traces", ["created_at"])
    op.create_index("idx_retrieval_traces_mode", "retrieval_traces", ["resolved_mode"])
    op.create_index("idx_retrieval_traces_failure", "retrieval_traces", ["failure_stage"])

    op.create_table(
        "eval_runs",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("run_name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="running"),
        sa.Column("summary", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("metadata", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_eval_runs_created", "eval_runs", ["created_at"])
    op.create_index("idx_eval_runs_status", "eval_runs", ["status"])

    op.create_table(
        "eval_case_results",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("eval_run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("case_name", sa.String(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("expected", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("actual", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["eval_run_id"], ["eval_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_eval_case_results_run", "eval_case_results", ["eval_run_id"])
    op.create_index("idx_eval_case_results_status", "eval_case_results", ["status"])


def downgrade() -> None:
    op.drop_index("idx_eval_case_results_status", table_name="eval_case_results")
    op.drop_index("idx_eval_case_results_run", table_name="eval_case_results")
    op.drop_table("eval_case_results")

    op.drop_index("idx_eval_runs_status", table_name="eval_runs")
    op.drop_index("idx_eval_runs_created", table_name="eval_runs")
    op.drop_table("eval_runs")

    op.drop_index("idx_retrieval_traces_failure", table_name="retrieval_traces")
    op.drop_index("idx_retrieval_traces_mode", table_name="retrieval_traces")
    op.drop_index("idx_retrieval_traces_created", table_name="retrieval_traces")
    op.drop_table("retrieval_traces")
