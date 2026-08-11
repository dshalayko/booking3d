"""Отметки об отправленных напоминаниях

Планировщик не хранит задания, а каждую минуту сверяет состояние с часами
(см. app/services/reminders.py). Чтобы одно и то же напоминание не ушло дважды,
факт отправки записывается рядом с сессией.

Заодно это журнал: видно, предупредили ли человека и когда.

Revision ID: 0003_reminder_flags
Revises: 0002_pin_digest
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_reminder_flags"
down_revision: str | None = "0002_pin_digest"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("warned_at", sa.DateTime(timezone=True)))
    op.add_column("sessions", sa.Column("finished_notified_at", sa.DateTime(timezone=True)))
    op.add_column("sessions", sa.Column("unclaimed_notified_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("sessions", "unclaimed_notified_at")
    op.drop_column("sessions", "finished_notified_at")
    op.drop_column("sessions", "warned_at")
