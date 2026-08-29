"""Add the superadmin role and promote existing administrators.

Revision ID: 0013_superadmins
Revises: 0012_text_overrides
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_superadmins"
down_revision: str | None = "0012_text_overrides"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_superadmin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # Whoever administered the installation before this role existed keeps
    # full authority. Newly appointed admins are ordinary admins by default.
    op.execute("UPDATE users SET is_superadmin = true WHERE is_admin = true")
    op.create_check_constraint(
        "users_superadmin_is_admin",
        "users",
        "is_superadmin = false OR is_admin = true",
    )


def downgrade() -> None:
    op.drop_constraint("users_superadmin_is_admin", "users", type_="check")
    op.drop_column("users", "is_superadmin")
