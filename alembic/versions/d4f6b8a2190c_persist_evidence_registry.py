"""persist complete per-run evidence registry

Revision ID: d4f6b8a2190c
Revises: c8e4a2d91f0b
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4f6b8a2190c"
down_revision: str | Sequence[str] | None = "c8e4a2d91f0b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "review_reports", sa.Column("evidence_registry", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("review_reports", "evidence_registry")
