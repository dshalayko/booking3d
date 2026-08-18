"""Закрыть старую очередь и сделать пользовательские лимиты глобальными.

Очередь больше не участвует в пользовательских сценариях: свободную машину
занимают сразу, нужное время выбирают в календаре. Исторические строки остаются
для журнала, а незавершённые ожидания закрываются как добровольный выход.

Одновременно лимит активных работ и броней перестаёт зависеть от помещения.
Приложение сериализует действия блокировкой строки пользователя, а частичные
индексы остаются последней защитой от параллельных записей одного типа.

Revision ID: 0009_global_user_limits
Revises: 0008_rooms
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_global_user_limits"
down_revision: str | None = "0008_rooms"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIVE_SESSION = "status IN ('printing', 'done_wait')"
ACTIVE_RESERVATION = "status = 'booked'"


def upgrade() -> None:
    # После удаления интерфейсов очереди активная запись уже никогда не могла
    # бы завершиться. Закрываем её, сохраняя саму строку и все ссылки журнала.
    op.execute(
        "UPDATE queue SET status = 'left', "
        "resolved_at = COALESCE(resolved_at, CURRENT_TIMESTAMP) "
        "WHERE status IN ('waiting', 'offered')"
    )

    # Если до обновления один человек успел занять несколько помещений или
    # создать в них несколько броней, выбор записи для автоматической отмены
    # был бы опасным. Останавливаем миграцию с понятной диагностикой.
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS ("
        "SELECT user_id FROM sessions WHERE " + ACTIVE_SESSION + " "
        "GROUP BY user_id HAVING count(*) > 1"
        ") THEN RAISE EXCEPTION "
        "'у пользователя несколько активных работ; завершите лишние перед миграцией'; "
        "END IF; END $$"
    )
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS ("
        "SELECT user_id FROM reservations WHERE " + ACTIVE_RESERVATION + " "
        "GROUP BY user_id HAVING count(*) > 1"
        ") THEN RAISE EXCEPTION "
        "'у пользователя несколько активных броней; отмените лишние перед миграцией'; "
        "END IF; END $$"
    )

    op.drop_index("one_active_session_per_user", table_name="sessions")
    op.create_index(
        "one_active_session_per_user",
        "sessions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_SESSION),
    )

    op.drop_index("one_active_reservation_per_user", table_name="reservations")
    op.create_index(
        "one_active_reservation_per_user",
        "reservations",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_RESERVATION),
    )


def downgrade() -> None:
    # Закрытые ожидания намеренно не открываем: после обновления неизвестно,
    # какие из них пользователь всё ещё хотел бы продолжить.
    op.drop_index("one_active_reservation_per_user", table_name="reservations")
    op.create_index(
        "one_active_reservation_per_user",
        "reservations",
        ["user_id", "room_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_RESERVATION),
    )

    op.drop_index("one_active_session_per_user", table_name="sessions")
    op.create_index(
        "one_active_session_per_user",
        "sessions",
        ["user_id", "room_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_SESSION),
    )
