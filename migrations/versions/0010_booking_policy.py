"""Добавить переключаемый лимит нескольких машин на пользователя.

В обычном режиме приложение сохраняет прежнее правило «одно дело вообще».
В расширенном режиме разрешены две задачи на принтерах и одна на гравировщике.
Частичные уникальные индексы по пользователю несовместимы с переключателем;
гонки теперь целиком сериализуются блокировкой строки пользователя.

Revision ID: 0010_booking_policy
Revises: 0009_global_user_limits
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_booking_policy"
down_revision: str | None = "0009_global_user_limits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIVE_SESSION = "status IN ('printing', 'done_wait')"
ACTIVE_RESERVATION = "status = 'booked'"


def upgrade() -> None:
    op.create_table(
        "booking_policy",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "multi_machine_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("id = 1", name="booking_policy_singleton"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        "INSERT INTO booking_policy (id, multi_machine_enabled) VALUES (1, false)"
    )
    op.drop_index("one_active_session_per_user", table_name="sessions")
    op.drop_index("one_active_reservation_per_user", table_name="reservations")
    op.create_index("sessions_user_active", "sessions", ["user_id", "status"])
    op.create_index(
        "reservations_user_active",
        "reservations",
        ["user_id", "status", "starts_at"],
    )


def downgrade() -> None:
    # В расширенном режиме могли появиться несколько активных записей. Молча
    # удалять их нельзя: останавливаем откат и просим сначала освободить лишнее.
    op.execute(
        "DO $$ BEGIN IF EXISTS ("
        "SELECT user_id FROM sessions WHERE " + ACTIVE_SESSION + " "
        "GROUP BY user_id HAVING count(*) > 1"
        ") THEN RAISE EXCEPTION "
        "'у пользователя несколько активных работ; завершите лишние перед откатом'; "
        "END IF; END $$"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS ("
        "SELECT user_id FROM reservations WHERE " + ACTIVE_RESERVATION + " "
        "GROUP BY user_id HAVING count(*) > 1"
        ") THEN RAISE EXCEPTION "
        "'у пользователя несколько активных броней; отмените лишние перед откатом'; "
        "END IF; END $$"
    )
    op.drop_index("reservations_user_active", table_name="reservations")
    op.drop_index("sessions_user_active", table_name="sessions")
    op.create_index(
        "one_active_reservation_per_user",
        "reservations",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_RESERVATION),
    )
    op.create_index(
        "one_active_session_per_user",
        "sessions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_SESSION),
    )
    op.drop_table("booking_policy")
