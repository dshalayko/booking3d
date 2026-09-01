"""Add persistent switches for experimental features.

Revision ID: 0014_feature_flags
Revises: 0013_superadmins
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_feature_flags"
down_revision: str | None = "0013_superadmins"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feature_flags",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "slicer_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("id = 1", name="feature_flags_singleton"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Сохраняем текущее поведение после обновления: функция уже видна админам.
    op.execute("INSERT INTO feature_flags (id, slicer_enabled) VALUES (1, true)")


def downgrade() -> None:
    op.drop_table("feature_flags")
