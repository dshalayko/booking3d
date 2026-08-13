"""Часы работы мастерской: таблица `work_hours`.

До этой миграции расписание было круглосуточным: сетка рисовала все 24 часа,
и забронировать можно было любой из них, включая четыре утра. Столбец из 24
клеток на телефоне — это экран, который нужно листать, чтобы найти рабочий день
внутри ночи.

Таблица, а не переменная в .env: часы меняет тот, кто отвечает за мастерскую, а
не тот, у кого есть ssh на сервер.

Строка ровно одна — это держит `work_hours_singleton` (id = 1). Значения по
умолчанию, 08:00–20:00, вписываются здесь же: приложение не должно уметь жить
без часов работы, иначе на каждый запрос сетки нужна была бы ветка «а если
строки нет».

Время местное и без зоны: 08:00 — это то, что написано на двери, а в какой
момент UTC оно случится, считает services/schedule.py по `TZ`.

Revision ID: 0007_work_hours
Revises: 0006_reservations
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_work_hours"
down_revision: str | None = "0006_reservations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "work_hours",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("opens_at", sa.Time, nullable=False),
        sa.Column("closes_at", sa.Time, nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("id = 1", name="work_hours_singleton"),
        # Закрытие раньше открытия — это ночная смена, которой у нас нет, и
        # сетка из отрицательного числа часов. 00:00 разрешено: так
        # записывается «до полуночи» у круглосуточной мастерской.
        sa.CheckConstraint(
            "closes_at > opens_at OR closes_at = '00:00'", name="work_hours_valid"
        ),
    )
    op.execute("INSERT INTO work_hours (id, opens_at, closes_at) VALUES (1, '08:00', '20:00')")


def downgrade() -> None:
    op.drop_table("work_hours")
