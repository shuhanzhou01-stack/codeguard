"""add reliability and validation fields

Revision ID: f6a1b2c3d4e5
Revises: d34db33f7a1c
Create Date: 2026-08-25
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f6a1b2c3d4e5"
down_revision: str | Sequence[str] | None = "d34db33f7a1c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("analysis_runs", sa.Column("base_sha", sa.String(64)))
    op.add_column("analysis_runs", sa.Column("head_sha", sa.String(64)))
    op.add_column(
        "analysis_runs",
        sa.Column(
            "trigger_source",
            sa.String(50),
            nullable=False,
            server_default="manual_api",
        ),
    )
    op.add_column(
        "analysis_runs", sa.Column("webhook_delivery_id", sa.Integer())
    )
    op.create_foreign_key(
        "fk_analysis_runs_webhook_delivery",
        "analysis_runs",
        "webhook_deliveries",
        ["webhook_delivery_id"],
        ["id"],
    )
    op.add_column(
        "test_executions", sa.Column("revision_role", sa.String(20))
    )
    op.add_column("test_executions", sa.Column("commit_sha", sa.String(64)))
    op.add_column(
        "review_findings",
        sa.Column(
            "grounding_status",
            sa.String(30),
            nullable=False,
            server_default="ungrounded",
        ),
    )
    op.add_column(
        "review_findings",
        sa.Column(
            "grounding_notes", sa.Text(), nullable=False, server_default=""
        ),
    )


def downgrade() -> None:
    op.drop_column("review_findings", "grounding_notes")
    op.drop_column("review_findings", "grounding_status")
    op.drop_column("test_executions", "commit_sha")
    op.drop_column("test_executions", "revision_role")
    op.drop_constraint(
        "fk_analysis_runs_webhook_delivery", "analysis_runs", type_="foreignkey"
    )
    op.drop_column("analysis_runs", "webhook_delivery_id")
    op.drop_column("analysis_runs", "trigger_source")
    op.drop_column("analysis_runs", "head_sha")
    op.drop_column("analysis_runs", "base_sha")
