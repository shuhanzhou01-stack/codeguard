"""add review pipeline tables

Revision ID: d34db33f7a1c
Revises: 544f6cea83db
Create Date: 2026-08-24
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d34db33f7a1c"
down_revision: str | Sequence[str] | None = "544f6cea83db"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("analysis_runs", sa.Column("failed_stage", sa.String(100)))
    op.add_column("analysis_runs", sa.Column("error_summary", sa.Text()))
    op.add_column(
        "test_executions",
        sa.Column("timed_out", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("test_executions", sa.Column("duration_ms", sa.Integer()))
    op.add_column("test_executions", sa.Column("backend", sa.String(100)))

    op.create_table(
        "review_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "analysis_run_id",
            sa.Integer(),
            sa.ForeignKey("analysis_runs.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.String(20), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("prompt_version", sa.String(100), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("estimated_cost", sa.Float()),
        sa.Column("test_summary", sa.JSON(), nullable=False),
        sa.Column("static_summary", sa.JSON(), nullable=False),
        sa.Column(
            "publish_status", sa.String(50), nullable=False, server_default="disabled"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "review_findings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "review_report_id",
            sa.Integer(),
            sa.ForeignKey("review_reports.id"),
            nullable=False,
        ),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("file_path", sa.String(1000)),
        sa.Column("line_start", sa.Integer()),
        sa.Column("line_end", sa.Integer()),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("suggestion", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "review_report_id", "fingerprint", name="uq_review_finding_fingerprint"
        ),
    )
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("delivery_id", sa.String(255), nullable=False, unique=True),
        sa.Column("event", sa.String(100), nullable=False),
        sa.Column("action", sa.String(100)),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("error_summary", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("webhook_deliveries")
    op.drop_table("review_findings")
    op.drop_table("review_reports")
    op.drop_column("test_executions", "backend")
    op.drop_column("test_executions", "duration_ms")
    op.drop_column("test_executions", "timed_out")
    op.drop_column("analysis_runs", "error_summary")
    op.drop_column("analysis_runs", "failed_stage")
