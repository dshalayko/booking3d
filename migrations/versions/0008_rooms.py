"""Помещения: система перестала быть системой одной комнаты.

До этой миграции «где стоит машина» нигде не хранилось: парк был один, и тип
оборудования отвечал сразу и за «что это», и за «где это». С появлением второй
мастерской и переговорной так больше нельзя — очередь на принтер в одном корпусе
не должна приглашать человека в другой.

Помещение — это граница, внутри которой считаются правила:

* правило 3 — очередь общая на машины одного типа *в этом помещении*;
* правила 2 и 13 — одна работа, одно место в очереди и одна бронь на человека
  тоже в пределах помещения. Поэтому `room_id` появляется не только у машин, но
  и у работ, ожиданий и брон: частичные уникальные индексы, которые эти правила
  несут, не умеют смотреть в соседнюю таблицу;
* правило 15 — часы работы у каждого помещения свои, и `work_hours` перестаёт
  быть одной строкой на всю базу.

Переговорная въезжает в `machines` строкой с типом `meeting_room`, а не своей
таблицей: занять, освободить, забронировать и попасть в журнал у комнаты и у
принтера — одно и то же действие, и вторая копия этой логики разошлась бы с
первой на первой же правке. См. app/enums.py.

Всё существующее переезжает в одно помещение — то самое, которое до сих пор
подразумевалось молча. Его имя потом правится в админке.

Revision ID: 0008_rooms
Revises: 0007_work_hours
Create Date: 2026-08-13
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_rooms"
down_revision: str | None = "0007_work_hours"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIVE_SESSION = "status IN ('printing', 'done_wait')"
ACTIVE_QUEUE = "status IN ('waiting', 'offered')"
ACTIVE_RESERVATION = "status = 'booked'"

ROOM_KIND_VALID = "kind IN ('workshop', 'meeting')"
# Переговорная — такой же занимаемый объект, как принтер (см. app/enums.py).
KIND_VALID = "kind IN ('printer', 'engraver', 'meeting_room')"
KIND_VALID_BEFORE = "kind IN ('printer', 'engraver')"

# Имя первого помещения человек увидит на планшете, поэтому оно на языке
# интерфейса. Не через `app.texts`: миграция должна накатываться и на базу, у
# которой рядом нет приложения, а `UI_LANG` в окружении есть всегда — по нему
# собирается и сам интерфейс (см. app/texts/__init__.py).
FIRST_ROOM_NAME = "Workshop" if os.environ.get("UI_LANG") == "en" else "Мастерская"

# Помещение, в которое переезжает всё существующее. Подзапросом, а не «1»:
# на свежей базе последовательность начинается с единицы, но полагаться на это
# в UPDATE, который нельзя переиграть, незачем.
FIRST_ROOM = "(SELECT id FROM rooms ORDER BY id LIMIT 1)"


def upgrade() -> None:
    op.create_table(
        "rooms",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("note", sa.Text),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(ROOM_KIND_VALID, name="rooms_kind_valid"),
    )
    op.execute(
        "INSERT INTO rooms (name, kind) VALUES "
        f"('{FIRST_ROOM_NAME}', 'workshop')"
    )

    # --- machines ------------------------------------------------------------
    #
    # Порядок один и тот же у всех четырёх таблиц: колонка nullable → заполнить
    # → NOT NULL. Сразу NOT NULL нельзя — в таблицах есть строки, а значения по
    # умолчанию у ссылки на помещение быть не должно: молчаливое «ну пусть будет
    # первое» однажды скрыло бы машину, заведённую не туда.
    op.add_column("machines", sa.Column("room_id", sa.Integer, nullable=True))
    op.execute(f"UPDATE machines SET room_id = {FIRST_ROOM}")
    op.alter_column("machines", "room_id", nullable=False)
    op.create_foreign_key("machines_room_id_fkey", "machines", "rooms", ["room_id"], ["id"])

    op.drop_constraint("machines_kind_valid", "machines", type_="check")
    op.create_check_constraint("machines_kind_valid", "machines", KIND_VALID)

    # --- sessions ------------------------------------------------------------
    op.add_column("sessions", sa.Column("room_id", sa.Integer, nullable=True))
    op.execute(
        "UPDATE sessions SET room_id = machines.room_id "
        "FROM machines WHERE machines.id = sessions.machine_id"
    )
    op.alter_column("sessions", "room_id", nullable=False)
    op.create_foreign_key("sessions_room_id_fkey", "sessions", "rooms", ["room_id"], ["id"])

    # Правило 2 теперь считается в пределах помещения: занятый принтер в
    # мастерской не мешает занять переговорную.
    op.drop_index("one_active_session_per_user", table_name="sessions")
    op.create_index(
        "one_active_session_per_user",
        "sessions",
        ["user_id", "room_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_SESSION),
    )

    # --- queue ---------------------------------------------------------------
    #
    # У ожидания своей машины может не быть вовсе (человек ждёт тип, а не
    # машину), поэтому помещение берём не через `offered_machine_id`, а то
    # единственное, которое было до этой миграции.
    op.add_column("queue", sa.Column("room_id", sa.Integer, nullable=True))
    op.execute(f"UPDATE queue SET room_id = {FIRST_ROOM}")
    op.alter_column("queue", "room_id", nullable=False)
    op.create_foreign_key("queue_room_id_fkey", "queue", "rooms", ["room_id"], ["id"])

    op.drop_constraint("queue_kind_valid", "queue", type_="check")
    op.create_check_constraint("queue_kind_valid", "queue", KIND_VALID)

    op.drop_index("one_queue_entry_per_user", table_name="queue")
    op.create_index(
        "one_queue_entry_per_user",
        "queue",
        ["user_id", "room_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_QUEUE),
    )

    # Очередь выбирается парой (помещение, тип) — оба поля идут впереди порядка.
    op.drop_index("queue_active_order", table_name="queue")
    op.create_index("queue_active_order", "queue", ["room_id", "kind", "status", "created_at"])

    # --- reservations --------------------------------------------------------
    op.add_column("reservations", sa.Column("room_id", sa.Integer, nullable=True))
    op.execute(
        "UPDATE reservations SET room_id = machines.room_id "
        "FROM machines WHERE machines.id = reservations.machine_id"
    )
    op.alter_column("reservations", "room_id", nullable=False)
    op.create_foreign_key(
        "reservations_room_id_fkey", "reservations", "rooms", ["room_id"], ["id"]
    )

    op.drop_index("one_active_reservation_per_user", table_name="reservations")
    op.create_index(
        "one_active_reservation_per_user",
        "reservations",
        ["user_id", "room_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_RESERVATION),
    )

    # EXCLUDE-ограничение `reservations_no_overlap` не трогаем: окна не
    # пересекаются на машине, а машина принадлежит одному помещению — добавлять
    # к ограничению помещение значило бы разрешить две брони на один принтер.

    # --- work_hours ----------------------------------------------------------
    #
    # Часы перестают быть одной строкой на всю базу: у переговорной свои, у
    # мастерской свои. Существующая строка достаётся первому помещению.
    op.drop_constraint("work_hours_singleton", "work_hours", type_="check")
    op.add_column("work_hours", sa.Column("room_id", sa.Integer, nullable=True))
    op.execute(f"UPDATE work_hours SET room_id = {FIRST_ROOM}")
    op.alter_column("work_hours", "room_id", nullable=False)
    op.create_unique_constraint("work_hours_room_unique", "work_hours", ["room_id"])
    # Удаление помещения забирает его часы с собой: иначе от удалённой комнаты
    # остались бы часы, которые уже никто не откроет, и следующая комната с тем
    # же id получила бы их в наследство.
    op.create_foreign_key(
        "work_hours_room_id_fkey",
        "work_hours",
        "rooms",
        ["room_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    # В старую схему помещается ровно одна комната: `room_id` там нет, и от
    # второй мастерской осталась бы неотличимая от первой каша — на её машины
    # при этом ссылается история. Удалять её молча нельзя, поэтому просим
    # сказать явно, что делать. Переговорные — тот же случай: в схеме без
    # помещений тип `meeting_room` не предусмотрен ни одним CHECK.
    op.execute(
        "DO $$ BEGIN IF (SELECT count(*) FROM rooms) > 1 THEN "
        "RAISE EXCEPTION 'помещений больше одного: откат потеряет, где что "
        "стоит — оставь одно помещение или откатывайся на копии базы'; "
        "END IF; END $$"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM machines WHERE kind = 'meeting_room') THEN "
        "RAISE EXCEPTION 'в парке есть переговорные: в схеме без помещений им "
        "нет места — удали их сначала'; END IF; END $$"
    )

    op.drop_constraint("work_hours_room_id_fkey", "work_hours", type_="foreignkey")
    op.drop_constraint("work_hours_room_unique", "work_hours", type_="unique")
    op.drop_column("work_hours", "room_id")
    # Строка снова одна на всю базу — но их и так одна, проверку выше прошли.
    op.execute("DELETE FROM work_hours WHERE id <> 1")
    op.create_check_constraint("work_hours_singleton", "work_hours", "id = 1")

    op.drop_index("one_active_reservation_per_user", table_name="reservations")
    op.create_index(
        "one_active_reservation_per_user",
        "reservations",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_RESERVATION),
    )
    op.drop_constraint("reservations_room_id_fkey", "reservations", type_="foreignkey")
    op.drop_column("reservations", "room_id")

    op.drop_index("queue_active_order", table_name="queue")
    op.create_index("queue_active_order", "queue", ["kind", "status", "created_at"])
    op.drop_index("one_queue_entry_per_user", table_name="queue")
    op.create_index(
        "one_queue_entry_per_user",
        "queue",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_QUEUE),
    )
    op.drop_constraint("queue_kind_valid", "queue", type_="check")
    op.create_check_constraint("queue_kind_valid", "queue", KIND_VALID_BEFORE)
    op.drop_constraint("queue_room_id_fkey", "queue", type_="foreignkey")
    op.drop_column("queue", "room_id")

    op.drop_index("one_active_session_per_user", table_name="sessions")
    op.create_index(
        "one_active_session_per_user",
        "sessions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_SESSION),
    )
    op.drop_constraint("sessions_room_id_fkey", "sessions", type_="foreignkey")
    op.drop_column("sessions", "room_id")

    op.drop_constraint("machines_kind_valid", "machines", type_="check")
    op.create_check_constraint("machines_kind_valid", "machines", KIND_VALID_BEFORE)
    op.drop_constraint("machines_room_id_fkey", "machines", type_="foreignkey")
    op.drop_column("machines", "room_id")

    op.drop_table("rooms")
