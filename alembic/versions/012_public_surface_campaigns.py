"""Public surface refresh runs, reviews, and autonomous campaign tracking.

Revision ID: 012
Revises: 011
Create Date: 2026-03-23
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "public_surface_refresh_runs",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("run_key", sa.String, nullable=False),
        sa.Column("trigger", sa.String, nullable=False, server_default="'manual'"),
        sa.Column("status", sa.String, nullable=False, server_default="'running'"),
        sa.Column("touched_pages", JSONB, nullable=False, server_default="[]"),
        sa.Column("changed_projects", JSONB, nullable=False, server_default="[]"),
        sa.Column("published_dynamic_updates", JSONB, nullable=False, server_default="[]"),
        sa.Column("staged_reviews", JSONB, nullable=False, server_default="[]"),
        sa.Column("evidence_refs", JSONB, nullable=False, server_default="[]"),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("deployment_wave_link", sa.String, nullable=True),
        sa.Column("failure_detail", sa.Text, nullable=True),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("run_key"),
    )
    op.create_index(
        "idx_public_surface_refresh_runs_status",
        "public_surface_refresh_runs",
        ["status"],
    )
    op.create_index(
        "idx_public_surface_refresh_runs_started",
        "public_surface_refresh_runs",
        ["started_at"],
    )

    op.create_table(
        "public_surface_reviews",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("review_key", sa.String, nullable=False),
        sa.Column("subject_type", sa.String, nullable=False, server_default="'project'"),
        sa.Column("subject_slug", sa.String, nullable=False),
        sa.Column("diff_summary", sa.Text, nullable=True),
        sa.Column("before_excerpt", sa.Text, nullable=True),
        sa.Column("after_excerpt", sa.Text, nullable=True),
        sa.Column("staged_payload", JSONB, nullable=False, server_default="{}"),
        sa.Column("evidence_refs", JSONB, nullable=False, server_default="[]"),
        sa.Column("status", sa.String, nullable=False, server_default="'staged'"),
        sa.Column("auto_advance_policy", sa.String, nullable=False, server_default="'wave-gate'"),
        sa.Column("resolution_notes", sa.Text, nullable=True),
        sa.Column("discord_message_id", sa.String, nullable=True),
        sa.Column("discord_thread_id", sa.String, nullable=True),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("review_key"),
    )
    op.create_index(
        "idx_public_surface_reviews_status",
        "public_surface_reviews",
        ["status"],
    )
    op.create_index(
        "idx_public_surface_reviews_subject",
        "public_surface_reviews",
        ["subject_type", "subject_slug"],
    )
    op.create_index(
        "idx_public_surface_reviews_thread",
        "public_surface_reviews",
        ["discord_thread_id"],
    )

    op.create_table(
        "improvement_opportunities",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("slug", sa.String, nullable=False),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("severity", sa.String, nullable=False, server_default="'medium'"),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="'open'"),
        sa.Column("payload", JSONB, nullable=False, server_default="{}"),
        sa.Column("source_refs", JSONB, nullable=False, server_default="[]"),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
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
        sa.UniqueConstraint("slug"),
    )
    op.create_index(
        "idx_improvement_opportunities_status",
        "improvement_opportunities",
        ["status"],
    )
    op.create_index(
        "idx_improvement_opportunities_severity",
        "improvement_opportunities",
        ["severity"],
    )

    op.create_table(
        "product_improvement_campaigns",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("campaign_key", sa.String, nullable=False),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="'active'"),
        sa.Column("target_cycles", sa.Integer, nullable=False, server_default="20"),
        sa.Column("completed_cycles", sa.Integer, nullable=False, server_default="0"),
        sa.Column("wave_size", sa.Integer, nullable=False, server_default="5"),
        sa.Column("latest_wave", sa.Integer, nullable=False, server_default="0"),
        sa.Column("deploy_mode", sa.String, nullable=False, server_default="'wave'"),
        sa.Column("autonomous", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("review_non_blocking", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("campaign_key"),
    )
    op.create_index(
        "idx_product_improvement_campaigns_status",
        "product_improvement_campaigns",
        ["status"],
    )

    op.create_table(
        "improvement_cycle_runs",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "campaign_id",
            UUID(as_uuid=True),
            sa.ForeignKey("product_improvement_campaigns.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("cycle_number", sa.Integer, nullable=False, server_default="1"),
        sa.Column("trigger", sa.String, nullable=False, server_default="'manual'"),
        sa.Column("status", sa.String, nullable=False, server_default="'running'"),
        sa.Column("pm_findings", JSONB, nullable=False, server_default="[]"),
        sa.Column("chosen_plan", JSONB, nullable=False, server_default="{}"),
        sa.Column("implementation_summary", sa.Text, nullable=True),
        sa.Column("qa_results", JSONB, nullable=False, server_default="[]"),
        sa.Column("uat_results", JSONB, nullable=False, server_default="[]"),
        sa.Column("regressions_fixed", JSONB, nullable=False, server_default="[]"),
        sa.Column("deployed_wave", sa.Integer, nullable=True),
        sa.Column("residual_risks", JSONB, nullable=False, server_default="[]"),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
    op.create_index(
        "idx_improvement_cycle_runs_campaign",
        "improvement_cycle_runs",
        ["campaign_id"],
    )
    op.create_index(
        "idx_improvement_cycle_runs_status",
        "improvement_cycle_runs",
        ["status"],
    )
    op.create_index(
        "idx_improvement_cycle_runs_started",
        "improvement_cycle_runs",
        ["started_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_improvement_cycle_runs_started")
    op.drop_index("idx_improvement_cycle_runs_status")
    op.drop_index("idx_improvement_cycle_runs_campaign")
    op.drop_table("improvement_cycle_runs")

    op.drop_index("idx_product_improvement_campaigns_status")
    op.drop_table("product_improvement_campaigns")

    op.drop_index("idx_improvement_opportunities_severity")
    op.drop_index("idx_improvement_opportunities_status")
    op.drop_table("improvement_opportunities")

    op.drop_index("idx_public_surface_reviews_thread")
    op.drop_index("idx_public_surface_reviews_subject")
    op.drop_index("idx_public_surface_reviews_status")
    op.drop_table("public_surface_reviews")

    op.drop_index("idx_public_surface_refresh_runs_started")
    op.drop_index("idx_public_surface_refresh_runs_status")
    op.drop_table("public_surface_refresh_runs")
