import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.config import Settings, load
from app.enums import PrinterStatus, QueueStatus, SessionStatus
from app.models import Printer, PrintSession, QueueEntry, User
from app.services import printers as svc
from app.services import queue as queue_svc
from app.services.errors import (
    DomainError,
    InvalidDuration,
    NotAdmin,
    PrinterNotAvailable,
    PrinterReserved,
    UserBusy,
)

NOON = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


async def test_occupy_starts_session(db, printers, make_user):
    user = await make_user()

    result = await svc.occupy(db, user, printers[0].id, 120, now=NOON)

    assert result.printer_name == "P2S #1"
    assert result.eta_at == NOON + timedelta(minutes=120)
    assert result.from_offer is False

    session = await db.get(PrintSession, result.session_id)
    assert session.status == SessionStatus.PRINTING
    assert (await db.get(Printer, printers[0].id)).status == PrinterStatus.PRINTING


async def test_eta_ignores_night_pause(db, printers, make_user):
    """Печать идёт и ночью: ночная пауза относится только к окну очереди."""
    user = await make_user()
    late = datetime(2026, 8, 10, 19, 50, tzinfo=UTC)  # 22:50 по Никосии

    result = await svc.occupy(db, user, printers[0].id, 240, now=late)

    assert result.eta_at == late + timedelta(minutes=240)


async def test_occupy_rejects_busy_printer(db, printers, make_user):
    first = await make_user()
    second = await make_user()
    await svc.occupy(db, first, printers[0].id, 60, now=NOON)

    with pytest.raises(PrinterNotAvailable, match="уже занят"):
        await svc.occupy(db, second, printers[0].id, 60, now=NOON)


async def test_occupy_rejects_broken_printer(db, printers, make_user):
    admin = await make_user(is_admin=True)
    user = await make_user()
    await svc.set_broken(db, admin, printers[0].id, note="сопло", now=NOON)

    with pytest.raises(PrinterNotAvailable, match="обслуживании"):
        await svc.occupy(db, user, printers[0].id, 60, now=NOON)


async def test_occupy_rejects_second_session_of_same_user(db, printers, make_user):
    """Правило 2: иначе один человек забирает весь парк из двух машин."""
    user = await make_user()
    await svc.occupy(db, user, printers[0].id, 60, now=NOON)

    with pytest.raises(UserBusy):
        await svc.occupy(db, user, printers[1].id, 60, now=NOON)


@pytest.mark.parametrize("minutes", [0, 5, 14, 48 * 60 + 1])
async def test_occupy_rejects_bad_duration(db, printers, make_user, minutes):
    user = await make_user()

    with pytest.raises(InvalidDuration):
        await svc.occupy(db, user, printers[0].id, minutes, now=NOON)


async def test_release_from_printing_cancels_session(db, printers, make_user):
    user = await make_user()
    occupied = await svc.occupy(db, user, printers[0].id, 60, now=NOON)

    result = await svc.release(db, user, printers[0].id, now=NOON + timedelta(minutes=10))

    assert result.session_status == SessionStatus.CANCELLED
    assert (await db.get(Printer, printers[0].id)).status == PrinterStatus.FREE
    session = await db.get(PrintSession, occupied.session_id)
    assert session.ended_at is not None


async def test_release_from_done_wait_completes_session(db, printers, make_user):
    user = await make_user()
    occupied = await svc.occupy(db, user, printers[0].id, 60, now=NOON)
    await svc.mark_done_wait(db, printers[0].id, now=NOON + timedelta(minutes=60))

    result = await svc.release(db, user, printers[0].id, now=NOON + timedelta(minutes=70))

    assert result.session_status == SessionStatus.COMPLETED
    assert (await db.get(PrintSession, occupied.session_id)).status == SessionStatus.COMPLETED


async def test_anyone_can_release_and_it_is_logged(db, printers, make_user):
    """Правило 9: стол пустой — принтер свободен, даже если владелец уехал."""
    owner = await make_user()
    stranger = await make_user()
    occupied = await svc.occupy(db, owner, printers[0].id, 60, now=NOON)
    await svc.mark_done_wait(db, printers[0].id, now=NOON + timedelta(minutes=60))

    result = await svc.release(db, stranger, printers[0].id, now=NOON + timedelta(minutes=90))

    assert result.owner_user_id == owner.id
    session = await db.get(PrintSession, occupied.session_id)
    assert session.freed_by_user_id == stranger.id


async def test_mark_done_wait_does_not_free_printer(db, printers, make_user):
    """Правило 8: таймер врёт, поэтому освобождать вслепую нельзя."""
    user = await make_user()
    await svc.occupy(db, user, printers[0].id, 60, now=NOON)

    result = await svc.mark_done_wait(db, printers[0].id, now=NOON + timedelta(minutes=60))

    assert result.owner_user_id == user.id
    assert (await db.get(Printer, printers[0].id)).status == PrinterStatus.DONE_WAIT
    # принтер всё ещё занят чужой деталью — другой человек занять его не может
    other = await make_user()
    with pytest.raises(PrinterNotAvailable):
        await svc.occupy(db, other, printers[0].id, 60, now=NOON + timedelta(minutes=61))


async def test_mark_done_wait_rejects_idle_printer(db, printers):
    with pytest.raises(PrinterNotAvailable, match="не печатает"):
        await svc.mark_done_wait(db, printers[0].id, now=NOON)


async def test_set_broken_cancels_active_print(db, printers, make_user):
    admin = await make_user(is_admin=True)
    owner = await make_user()
    occupied = await svc.occupy(db, owner, printers[0].id, 600, now=NOON)

    result = await svc.set_broken(db, admin, printers[0].id, note="полетел хотэнд", now=NOON)

    assert result.cancelled_session_id == occupied.session_id
    assert result.owner_user_id == owner.id
    printer = await db.get(Printer, printers[0].id)
    assert printer.status == PrinterStatus.BROKEN
    assert printer.note == "полетел хотэнд"


async def test_set_broken_requires_admin(db, printers, make_user):
    user = await make_user()

    with pytest.raises(NotAdmin):
        await svc.set_broken(db, user, printers[0].id, now=NOON)


async def test_clear_broken_offers_printer_to_queue(db, printers, make_user):
    admin = await make_user(is_admin=True)
    waiting = await make_user()
    await svc.set_broken(db, admin, printers[0].id, now=NOON)
    await svc.set_broken(db, admin, printers[1].id, now=NOON)
    await queue_svc.join(db, waiting.id, now=NOON)

    result = await svc.clear_broken(db, admin, printers[0].id, now=NOON)

    assert [offer.user_id for offer in result.offers] == [waiting.id]


async def test_release_offers_printer_to_first_in_queue(db, printers, make_user):
    owner = await make_user()
    first = await make_user()
    second = await make_user()
    await svc.occupy(db, owner, printers[0].id, 60, now=NOON)
    await svc.occupy(db, first, printers[1].id, 60, now=NOON)
    await db.commit()

    # оба принтера заняты, двое встают в очередь
    third = await make_user()
    await queue_svc.join(db, second.id, now=NOON + timedelta(minutes=1))
    await queue_svc.join(db, third.id, now=NOON + timedelta(minutes=2))

    result = await svc.release(db, owner, printers[0].id, now=NOON + timedelta(minutes=30))

    assert [offer.user_id for offer in result.offers] == [second.id]


async def test_concurrent_occupy_lets_exactly_one_win(db, printers, sessions, make_user):
    """Правило 1 под гонкой: два тапа «Занять» на один принтер одновременно."""
    first = await make_user()
    second = await make_user()
    await db.commit()
    printer_id = printers[0].id

    async def attempt(user_id: int) -> str:
        async with sessions() as session:
            user = await session.get(User, user_id)
            try:
                await svc.occupy(session, user, printer_id, 60, now=NOON)
                await session.commit()
                return "ok"
            except DomainError:
                await session.rollback()
                return "fail"

    outcomes = await asyncio.gather(attempt(first.id), attempt(second.id))

    assert sorted(outcomes) == ["fail", "ok"]
    active = (
        await db.scalars(
            select(PrintSession).where(PrintSession.status == SessionStatus.PRINTING)
        )
    ).all()
    assert len(active) == 1


async def test_offer_is_marked_taken_when_used(db, printers, make_user):
    owner = await make_user()
    waiting = await make_user()
    await svc.occupy(db, owner, printers[0].id, 60, now=NOON)
    await svc.occupy(db, await make_user(), printers[1].id, 60, now=NOON)
    join = await queue_svc.join(db, waiting.id, now=NOON)

    await svc.release(db, owner, printers[0].id, now=NOON + timedelta(minutes=30))
    result = await svc.occupy(db, waiting, printers[0].id, 60, now=NOON + timedelta(minutes=31))

    assert result.from_offer is True
    entry = await db.get(QueueEntry, join.entry_id)
    assert entry.status == QueueStatus.TAKEN


async def test_admin_can_bypass_queue(db, printers, make_user):
    """Исключение из правила 7: админ чинит застрявшие ситуации руками."""
    owner = await make_user()
    waiting = await make_user()
    admin = await make_user(is_admin=True)
    await svc.occupy(db, owner, printers[0].id, 60, now=NOON)
    await svc.occupy(db, await make_user(), printers[1].id, 60, now=NOON)
    await queue_svc.join(db, waiting.id, now=NOON)
    await svc.release(db, owner, printers[0].id, now=NOON + timedelta(minutes=30))

    result = await svc.occupy(db, admin, printers[0].id, 60, now=NOON + timedelta(minutes=31))

    assert result.from_offer is False


async def test_non_offered_user_cannot_jump_the_queue(db, printers, make_user):
    """Правило 7: подошедший к киоску всегда быстрее того, кто едет из дома."""
    owner = await make_user()
    waiting = await make_user()
    outsider = await make_user()
    await svc.occupy(db, owner, printers[0].id, 60, now=NOON)
    await svc.occupy(db, await make_user(), printers[1].id, 60, now=NOON)
    await queue_svc.join(db, waiting.id, now=NOON)
    await svc.release(db, owner, printers[0].id, now=NOON + timedelta(minutes=30))

    with pytest.raises(PrinterReserved):
        await svc.occupy(db, outsider, printers[0].id, 60, now=NOON + timedelta(minutes=31))


class TestPrinterNames:
    """Парк объявлен в PRINTER_NAMES, а переименование идёт по строке в БД.

    Обе половины легко ломаются молча: `#` в незакавыченном .env съедает конец
    строки, а переименование мимо строки БД оторвало бы историю печатей от
    принтера.
    """

    @staticmethod
    def park(raw: str) -> tuple[str, ...]:
        return Settings(database_url="x", printer_names=raw).printers

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("P2S #1,P2S #2", ("P2S #1", "P2S #2")),
            (" P2S #1 , P2S #2 ", ("P2S #1", "P2S #2")),  # пробелы вокруг запятых
            ("P2S #1,,P2S #2,", ("P2S #1", "P2S #2")),  # пустые куски
            ("Bambu X1", ("Bambu X1",)),  # один принтер — тоже парк
        ],
    )
    def test_names_are_parsed(self, raw, expected):
        assert self.park(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", ",,"])
    def test_empty_park_is_refused(self, raw):
        with pytest.raises(RuntimeError, match="PRINTER_NAMES"):
            self.park(raw)

    def test_duplicate_names_are_refused(self):
        """Два принтера с одним именем на стене не различить."""
        with pytest.raises(RuntimeError, match="повторяются"):
            self.park("P2S #1,P2S #1")

    def test_missing_variable_names_itself(self, monkeypatch):
        """Дефолта нет, поэтому отказ обязан объяснять, какую строку дописать.

        Сообщение читает тот, у кого не поднялся контейнер: в нём должно быть
        имя переменной как в .env, а не имя поля в нижнем регистре.
        """
        monkeypatch.delenv("PRINTER_NAMES", raising=False)

        with pytest.raises(RuntimeError) as error:
            load(_env_file=None, database_url="x")

        assert "PRINTER_NAMES" in str(error.value)
        assert ".env" in str(error.value)

    def test_park_is_not_guessed_from_code(self):
        """Дефолт вернулся бы незаметно — тест держит имена в одном месте."""
        assert Settings.model_fields["printer_names"].is_required()

    async def test_rename_keeps_history_on_the_same_printer(self, db, printers, make_user):
        user = await make_user()
        await svc.occupy(db, user, printers[0].id, 60, now=NOON)
        printer_id = printers[0].id

        printer = await db.get(Printer, printer_id)
        printer.name = "Bambu X1"
        await db.commit()

        session = await db.scalar(
            select(PrintSession).where(PrintSession.printer_id == printer_id)
        )
        assert session is not None, "переименование не должно осиротить печать"
        result = await svc.release(db, user, printer_id, now=NOON + timedelta(minutes=10))
        assert result.printer_name == "Bambu X1"
