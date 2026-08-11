"""Причина снятия печати и время закрытия записи в очереди

Журнал событий в админке собирается из `sessions` и `queue`, а не из отдельной
таблицы: так он не может разойтись с реальностью. Но двух отметок для этого не
хватало — почему админ снял чужую печать и когда запись в очереди закрылась.

Revision ID: 0004_admin_audit
Revises: 0003_reminder_flags
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_admin_audit"
down_revision: str | None = "0003_reminder_flags"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("cancel_reason", sa.Text()))
    op.add_column("queue", sa.Column("resolved_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("queue", "resolved_at")
    op.drop_column("sessions", "cancel_reason")
