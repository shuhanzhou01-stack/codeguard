"""add correctness hardening fields

Revision ID: b7c8d9e0f1a2
Revises: f6a1b2c3d4e5
Create Date: 2026-08-25
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7c8d9e0f1a2"
down_revision: str | Sequence[str] | None = "f6a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for column_name in (
        "passed_tests",
        "failed_tests",
        "error_tests",
        "skipped_tests",
    ):
        op.add_column(
            "test_executions",
            sa.Column(
                column_name,
                sa.JSON(),
                nullable=False,
                server_default="[]",
            ),
        )
    op.add_column("review_reports", sa.Column("raw_model_summary", sa.Text()))
    op.add_column(
        "review_reports", sa.Column("raw_model_risk", sa.String(20))
    )


def downgrade() -> None:
    op.drop_column("review_reports", "raw_model_risk")
    op.drop_column("review_reports", "raw_model_summary")
    op.drop_column("test_executions", "skipped_tests")
    op.drop_column("test_executions", "error_tests")
    op.drop_column("test_executions", "failed_tests")
    op.drop_column("test_executions", "passed_tests")
