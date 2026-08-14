"""Команды парка.

Проверяются два барьера, потому что оба уже ломались вживую:

* `seed_printers` при смене имени в `PRINTER_NAMES` завёл второй парк рядом с
  первым — на стене стало четыре плитки вместо двух, а история печатей осталась
  на старых строках;
* `remove_machine` не должен уносить машину, на которую ссылается журнал.

Третий барьер появился вместе с гравировщиками: `seed_printers` смотрит только
на машины типа `printer`. Иначе первый же гравировщик выглядел бы для неё
«лишним принтером не из .env» и блокировал бы добавление нового принтера.

Принтеры сид заводит в первое помещение: оно то самое, которое подразумевалось,
пока помещений в системе не было вовсе.

Команды ходят в БД через `app.cli.SessionLocal`, поэтому его подменяем на
тестовую фабрику — иначе они пойдут в настоящую базу разработчика.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import cli
from app.config import settings
from app.enums import MachineKind, SessionStatus
from app.models import Machine, MachineSession

NOON = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def cli_db(monkeypatch, sessions):
    monkeypatch.setattr(cli, "SessionLocal", sessions)


@pytest.fixture
def park(monkeypatch):
    """Подменить PRINTER_NAMES. `printers` — cached_property, кладём прямо в кэш."""

    def declare(*names: str) -> None:
        monkeypatch.setitem(settings.__dict__, "printers", names)
        monkeypatch.setitem(settings.__dict__, "printer_names", ",".join(names))

    return declare


async def names_in(db) -> list[str]:
    return list((await db.scalars(select(Machine.name).order_by(Machine.id))).all())


class TestSeed:
    async def test_creates_declared_park(self, db, room, park):
        park("P2S #1", "P2S #2")
        await cli.seed_printers()
        assert await names_in(db) == ["P2S #1", "P2S #2"]

    async def test_adds_only_the_missing_one(self, db, printers, park):
        park("P2S #1", "P2S #2", "Prusa MK4")
        await cli.seed_printers()
        assert await names_in(db) == ["P2S #1", "P2S #2", "Prusa MK4"]

    async def test_renamed_park_is_refused(self, db, printers, park, capsys):
        """Тот самый случай: поменяли имена в .env и запустили сид."""
        park("Bambu Lab P2S #1", "Bambu Lab P2S #2")

        await cli.seed_printers()

        assert await names_in(db) == ["P2S #1", "P2S #2"], "сид завёл второй парк"
        output = capsys.readouterr().out
        assert "переименование" in output
        assert "rename_machine" in output, "отказ должен подсказать, что делать"

    async def test_extra_printer_is_kept(self, db, printers, park, capsys):
        """Принтер увезли и убрали из .env — строку не трогаем, на ней журнал."""
        park("P2S #1")

        await cli.seed_printers()

        assert await names_in(db) == ["P2S #1", "P2S #2"]
        assert "P2S #2" in capsys.readouterr().out

    async def test_engravers_are_none_of_its_business(self, db, printers, engravers, park):
        """Гравировщик — не «лишний принтер»: сид не должен из-за него вставать."""
        park("P2S #1", "P2S #2", "Prusa MK4")

        await cli.seed_printers()

        assert await names_in(db) == ["P2S #1", "P2S #2", "Гравёр #1", "Prusa MK4"]


class TestAdd:
    async def test_adds_an_engraver(self, db, printers, make_user, park, capsys):
        park("P2S #1", "P2S #2")
        await make_user(is_admin=True)
        await db.commit()

        await cli.add_machine("Гравёр #1", MachineKind.ENGRAVER.value, None)

        machine = await db.scalar(select(Machine).where(Machine.name == "Гравёр #1"))
        assert machine.kind == MachineKind.ENGRAVER
        # про PRINTER_NAMES напоминаем только про принтеры: гравировщиков там нет
        assert "PRINTER_NAMES" not in capsys.readouterr().out

    async def test_taken_name_is_refused(self, db, printers, make_user, park, capsys):
        park("P2S #1", "P2S #2")
        await make_user(is_admin=True)
        await db.commit()

        await cli.add_machine("P2S #1", MachineKind.ENGRAVER.value, None)

        assert "занято" in capsys.readouterr().out
        assert await names_in(db) == ["P2S #1", "P2S #2"]


class TestRemove:
    async def test_removes_machine_without_history(self, db, printers, make_user, park):
        park("P2S #1")
        await make_user(is_admin=True)
        await db.commit()

        await cli.remove_machine(str(printers[1].id))

        assert await names_in(db) == ["P2S #1"]

    async def test_keeps_machine_with_history(self, db, printers, park, make_user, capsys):
        park("P2S #1")
        await make_user(is_admin=True)
        user = await make_user()
        db.add(
            MachineSession(
                machine_id=printers[1].id,
                room_id=printers[1].room_id,
                user_id=user.id,
                started_at=NOON,
                eta_at=NOON + timedelta(hours=1),
                status=SessionStatus.PRINTING,
            )
        )
        await db.commit()

        await cli.remove_machine(str(printers[1].id))

        assert await names_in(db) == ["P2S #1", "P2S #2"], "журнал оторвался от машины"
        assert "не удалить" in capsys.readouterr().out


class TestRename:
    async def test_rename_keeps_the_same_row(self, db, printers, make_user, park):
        park("Bambu X1", "P2S #2")
        await make_user(is_admin=True)
        await db.commit()

        await cli.rename_machine("P2S #1", "Bambu X1")

        machine = await db.get(Machine, printers[0].id)
        await db.refresh(machine)
        assert machine.name == "Bambu X1"

    async def test_taken_name_is_refused(self, db, printers, make_user, park, capsys):
        park("P2S #1", "P2S #2")
        await make_user(is_admin=True)
        await db.commit()

        await cli.rename_machine("P2S #1", "P2S #2")

        assert "занято" in capsys.readouterr().out
        assert await names_in(db) == ["P2S #1", "P2S #2"]
