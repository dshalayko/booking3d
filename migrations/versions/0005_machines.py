"""Парк перестал быть только принтерами: printers → machines, у машин и мест
в очереди появился тип.

Переименование, а не новые таблицы: строки те же самые, на них ссылаются
`sessions` и `queue`, и история печатей должна пережить эту миграцию целиком.
Всё существующее считается принтерами — отсюда server_default 'printer' у обеих
новых колонок. Дефолт после заполнения снимается: тип новой машины выбирают
явно в админке, и молчаливое «ну пусть будет принтер» здесь скрыло бы ошибку.

Значения статусов не трогаем: `printing` на гравировщике звучит странно, но это
внутренняя строка, на неё завязаны CHECK и два частичных уникальных индекса, а
человеку слово подставляют тексты по типу машины. См. app/enums.py.

Revision ID: 0005_machines
Revises: 0004_admin_audit
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_machines"
down_revision: str | None = "0004_admin_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIVE_SESSION = "status IN ('printing', 'done_wait')"
KIND_VALID = "kind IN ('printer', 'engraver')"


def upgrade() -> None:
    op.rename_table("printers", "machines")
    op.execute(
        "ALTER TABLE machines RENAME CONSTRAINT printers_status_valid TO machines_status_valid"
    )

    op.add_column(
        "machines",
        sa.Column("kind", sa.String(16), nullable=False, server_default=sa.text("'printer'")),
    )
    op.create_check_constraint("machines_kind_valid", "machines", KIND_VALID)
    op.alter_column("machines", "kind", server_default=None)

    op.alter_column("sessions", "printer_id", new_column_name="machine_id")
    # Индекс правила 1 переименовать нельзя молча: имя встречается в моделях и
    # в 0001, и расхождение всплыло бы только при следующем автогенерённом diff.
    op.drop_index("one_active_session_per_printer", table_name="sessions")
    op.create_index(
        "one_active_session_per_machine",
        "sessions",
        ["machine_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_SESSION),
    )

    op.alter_column("queue", "offered_printer_id", new_column_name="offered_machine_id")
    op.add_column(
        "queue",
        sa.Column("kind", sa.String(16), nullable=False, server_default=sa.text("'printer'")),
    )
    op.create_check_constraint("queue_kind_valid", "queue", KIND_VALID)
    op.alter_column("queue", "kind", server_default=None)

    # Очередь выбирается по типу, поэтому он идёт первым в индексе порядка.
    op.drop_index("queue_active_order", table_name="queue")
    op.create_index("queue_active_order", "queue", ["kind", "status", "created_at"])


def downgrade() -> None:
    # Гравировщики и ожидания в их очереди в старую схему не помещаются: там
    # весь парк — принтеры. Удалять их молча нельзя (на них ссылается история),
    # поэтому откат требует сначала убрать их руками.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM machines WHERE kind <> 'printer') THEN "
        "RAISE EXCEPTION 'в парке есть не-принтеры: откат потеряет их тип, "
        "удали или переведи их сначала'; END IF; END $$"
    )

    op.drop_index("queue_active_order", table_name="queue")
    op.create_index("queue_active_order", "queue", ["status", "created_at"])
    op.drop_constraint("queue_kind_valid", "queue", type_="check")
    op.drop_column("queue", "kind")
    op.alter_column("queue", "offered_machine_id", new_column_name="offered_printer_id")

    op.drop_index("one_active_session_per_machine", table_name="sessions")
    op.alter_column("sessions", "machine_id", new_column_name="printer_id")
    op.create_index(
        "one_active_session_per_printer",
        "sessions",
        ["printer_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_SESSION),
    )

    op.drop_constraint("machines_kind_valid", "machines", type_="check")
    op.drop_column("machines", "kind")
    op.execute(
        "ALTER TABLE machines RENAME CONSTRAINT machines_status_valid TO printers_status_valid"
    )
    op.rename_table("machines", "printers")
