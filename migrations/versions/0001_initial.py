"""Начальная схема: users, printers, sessions, queue

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIVE_SESSION = "status IN ('printing', 'done_wait')"
ACTIVE_QUEUE = "status IN ('waiting', 'offered')"


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("tg_chat_id", sa.BigInteger, nullable=False, unique=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("pin_hash", sa.String(128), nullable=False),
        sa.Column("is_admin", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "printers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'free'")),
        sa.Column("note", sa.Text),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('free', 'printing', 'done_wait', 'broken')", name="printers_status_valid"
        ),
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("printer_id", sa.Integer, sa.ForeignKey("printers.id"), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("eta_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("freed_by_user_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('printing', 'done_wait', 'completed', 'cancelled')",
            name="sessions_status_valid",
        ),
    )

    # Правило 1: одна активная сессия на принтер.
    # Правило 2: одна активная сессия на человека.
    # Индексы держат эти правила на уровне БД, поэтому две одновременные
    # попытки занять принтер не могут пройти обе.
    op.create_index(
        "one_active_session_per_printer",
        "sessions",
        ["printer_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_SESSION),
    )
    op.create_index(
        "one_active_session_per_user",
        "sessions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_SESSION),
    )

    op.create_table(
        "queue",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("offered_printer_id", sa.Integer, sa.ForeignKey("printers.id")),
        sa.Column("offered_at", sa.DateTime(timezone=True)),
        sa.Column("offer_expires_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.CheckConstraint(
            "status IN ('waiting', 'offered', 'taken', 'expired', 'left')",
            name="queue_status_valid",
        ),
    )

    # Правило 2: одно место в очереди на человека.
    op.create_index(
        "one_queue_entry_per_user",
        "queue",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_QUEUE),
    )
    op.create_index("queue_active_order", "queue", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("queue_active_order", table_name="queue")
    op.drop_index("one_queue_entry_per_user", table_name="queue")
    op.drop_table("queue")
    op.drop_index("one_active_session_per_user", table_name="sessions")
    op.drop_index("one_active_session_per_printer", table_name="sessions")
    op.drop_table("sessions")
    op.drop_table("printers")
    op.drop_table("users")
