"""Брони на будущее: таблица `reservations` и связь с работой.

До этой миграции машину можно было занять только «с этой секунды»: очередь
отвечала на вопрос «кто следующий», но не на вопрос «мне нужно к утру
четверга». Бронь — это право занять конкретную машину в конкретное окно,
выданное заранее.

Непересечение брон несёт EXCLUDE-ограничение по `tstzrange`, а не проверка в
коде: два человека, жмущие «забронировать» на один час, — обычная гонка, и
выиграть её должен один. Для этого нужен `btree_gist` (сравнение `machine_id`
через `=` внутри gist-индекса); расширение входит в образ postgres:16-alpine, а
`POSTGRES_USER` в обоих compose-файлах — владелец базы, так что `CREATE
EXTENSION` ему разрешён.

Ограничение живёт только для `status = 'booked'`: закрытые брони (человек
пришёл, не пришёл, отменил) — это журнал, и пересекаться им никто не мешает.

`sessions.reservation_id` заводится здесь же: без него нельзя отличить работу,
начатую по брони, от обычного занятия, и журнал админки не покажет, зачем
машина стояла придержанной полдня.

Revision ID: 0006_reservations
Revises: 0005_machines
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_reservations"
down_revision: str | None = "0005_machines"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUS_VALID = "status IN ('booked', 'taken', 'expired', 'cancelled')"
ACTIVE_RESERVATION = "status = 'booked'"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.create_table(
        "reservations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("machine_id", sa.Integer, sa.ForeignKey("machines.id"), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_reason", sa.Text),
        sa.Column("cancelled_by_user_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("reminded_at", sa.DateTime(timezone=True)),
        sa.Column("started_notified_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(STATUS_VALID, name="reservations_status_valid"),
        # Бронь нулевой и отрицательной длины — это не бронь, а способ
        # заблокировать час пустой строкой.
        sa.CheckConstraint("ends_at > starts_at", name="reservations_window_valid"),
    )

    # Правило 13: одна активная бронь на человека — тем же способом, что правила
    # 1 и 2, то есть частичным уникальным индексом, а не проверкой в коде.
    op.create_index(
        "one_active_reservation_per_user",
        "reservations",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_RESERVATION),
    )
    op.create_index("reservations_machine_time", "reservations", ["machine_id", "starts_at"])

    # Правило 12: два окна на одной машине не пересекаются. `[)` — правая
    # граница открыта: бронь 14:00–16:00 и бронь 16:00–18:00 стыкуются, а не
    # конфликтуют.
    op.execute(
        "ALTER TABLE reservations ADD CONSTRAINT reservations_no_overlap "
        "EXCLUDE USING gist ("
        "  machine_id WITH =, "
        "  tstzrange(starts_at, ends_at, '[)') WITH &&"
        f") WHERE ({ACTIVE_RESERVATION})"
    )

    op.add_column(
        "sessions",
        sa.Column("reservation_id", sa.Integer, sa.ForeignKey("reservations.id")),
    )


def downgrade() -> None:
    op.drop_column("sessions", "reservation_id")
    op.drop_table("reservations")
    # `btree_gist` не удаляем: расширение могло стоять в базе и до нас, а его
    # удаление уронило бы чужие индексы.
