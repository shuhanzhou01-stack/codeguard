"""persist deterministic evidence IDs and resolved metadata

Revision ID: c8e4a2d91f0b
Revises: b7c8d9e0f1a2
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c8e4a2d91f0b"
down_revision: str | Sequence[str] | None = "b7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("review_findings", sa.Column("evidence_ids", sa.JSON(), nullable=True))
    op.add_column(
        "review_findings", sa.Column("resolved_evidence", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("review_findings", "resolved_evidence")
    op.drop_column("review_findings", "evidence_ids")
