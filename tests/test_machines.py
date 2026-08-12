import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.config import Settings, load
from app.enums import MachineKind, MachineStatus, QueueStatus, SessionStatus
from app.models import Machine, MachineSession, QueueEntry, User
from app.services import machines as svc
from app.services import queue as queue_svc
from app.services.errors import (
    DomainError,
    InvalidDuration,
    MachineHasHistory,
    MachineKindUnknown,
    MachineNameInvalid,
    MachineNameTaken,
    MachineNotAvailable,
    MachineReleaseForbidden,
    MachineReserved,
    NotAdmin,
    UserBusy,
)

NOON = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


async def test_occupy_starts_session(db, printers, make_user):
    user = await make_user()

    result = await svc.occupy(db, user, printers[0].id, 120, now=NOON)

    assert result.machine_name == "P2S #1"
    assert result.eta_at == NOON + timedelta(minutes=120)
    assert result.from_offer is False

    session = await db.get(MachineSession, result.session_id)
    assert session.status == SessionStatus.PRINTING
    assert (await db.get(Machine, printers[0].id)).status == MachineStatus.PRINTING


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

    with pytest.raises(MachineNotAvailable, match="уже занят"):
        await svc.occupy(db, second, printers[0].id, 60, now=NOON)


async def test_occupy_rejects_broken_printer(db, printers, make_user):
    admin = await make_user(is_admin=True)
    user = await make_user()
    await svc.set_broken(db, admin, printers[0].id, note="сопло", now=NOON)

    with pytest.raises(MachineNotAvailable, match="обслуживании"):
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
    assert (await db.get(Machine, printers[0].id)).status == MachineStatus.FREE
    session = await db.get(MachineSession, occupied.session_id)
    assert session.ended_at is not None


async def test_only_owner_can_release_from_printing(db, printers, make_user):
    owner = await make_user()
    stranger = await make_user()
    machine_id = printers[0].id
    occupied = await svc.occupy(db, owner, machine_id, 60, now=NOON)

    with pytest.raises(MachineReleaseForbidden):
        await svc.release(db, stranger, machine_id, now=NOON + timedelta(minutes=10))

    db.expire_all()
    assert (await db.get(Machine, machine_id)).status == MachineStatus.PRINTING
    assert (await db.get(MachineSession, occupied.session_id)).status == SessionStatus.PRINTING


async def test_admin_can_release_from_printing(db, printers, make_user):
    owner = await make_user()
    admin = await make_user(is_admin=True)
    await svc.occupy(db, owner, printers[0].id, 60, now=NOON)

    result = await svc.release(
        db, admin, printers[0].id, now=NOON + timedelta(minutes=10), reason="failed"
    )

    assert result.session_status == SessionStatus.CANCELLED


async def test_release_from_done_wait_completes_session(db, printers, make_user):
    user = await make_user()
    occupied = await svc.occupy(db, user, printers[0].id, 60, now=NOON)
    await svc.mark_done_wait(db, printers[0].id, now=NOON + timedelta(minutes=60))

    result = await svc.release(db, user, printers[0].id, now=NOON + timedelta(minutes=70))

    assert result.session_status == SessionStatus.COMPLETED
    assert (await db.get(MachineSession, occupied.session_id)).status == SessionStatus.COMPLETED


async def test_anyone_can_release_and_it_is_logged(db, printers, make_user):
    """Правило 9: стол пустой — принтер свободен, даже если владелец уехал."""
    owner = await make_user()
    stranger = await make_user()
    occupied = await svc.occupy(db, owner, printers[0].id, 60, now=NOON)
    await svc.mark_done_wait(db, printers[0].id, now=NOON + timedelta(minutes=60))

    result = await svc.release(db, stranger, printers[0].id, now=NOON + timedelta(minutes=90))

    assert result.owner_user_id == owner.id
    session = await db.get(MachineSession, occupied.session_id)
    assert session.freed_by_user_id == stranger.id


async def test_mark_done_wait_does_not_free_printer(db, printers, make_user):
    """Правило 8: таймер врёт, поэтому освобождать вслепую нельзя."""
    user = await make_user()
    await svc.occupy(db, user, printers[0].id, 60, now=NOON)

    result = await svc.mark_done_wait(db, printers[0].id, now=NOON + timedelta(minutes=60))

    assert result.owner_user_id == user.id
    assert (await db.get(Machine, printers[0].id)).status == MachineStatus.DONE_WAIT
    # принтер всё ещё занят чужой деталью — другой человек занять его не может
    other = await make_user()
    with pytest.raises(MachineNotAvailable):
        await svc.occupy(db, other, printers[0].id, 60, now=NOON + timedelta(minutes=61))


async def test_mark_done_wait_rejects_idle_printer(db, printers):
    with pytest.raises(MachineNotAvailable, match="не работает"):
        await svc.mark_done_wait(db, printers[0].id, now=NOON)


async def test_set_broken_cancels_active_print(db, printers, make_user):
    admin = await make_user(is_admin=True)
    owner = await make_user()
    occupied = await svc.occupy(db, owner, printers[0].id, 600, now=NOON)

    result = await svc.set_broken(db, admin, printers[0].id, note="полетел хотэнд", now=NOON)

    assert result.cancelled_session_id == occupied.session_id
    assert result.owner_user_id == owner.id
    printer = await db.get(Machine, printers[0].id)
    assert printer.status == MachineStatus.BROKEN
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
    await queue_svc.join(db, waiting.id, MachineKind.PRINTER, now=NOON)

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
    await queue_svc.join(db, second.id, MachineKind.PRINTER, now=NOON + timedelta(minutes=1))
    await queue_svc.join(db, third.id, MachineKind.PRINTER, now=NOON + timedelta(minutes=2))

    result = await svc.release(db, owner, printers[0].id, now=NOON + timedelta(minutes=30))

    assert [offer.user_id for offer in result.offers] == [second.id]


async def test_concurrent_occupy_lets_exactly_one_win(db, printers, sessions, make_user):
    """Правило 1 под гонкой: два тапа «Занять» на один принтер одновременно."""
    first = await make_user()
    second = await make_user()
    await db.commit()
    machine_id = printers[0].id

    async def attempt(user_id: int) -> str:
        async with sessions() as session:
            user = await session.get(User, user_id)
            try:
                await svc.occupy(session, user, machine_id, 60, now=NOON)
                await session.commit()
                return "ok"
            except DomainError:
                await session.rollback()
                return "fail"

    outcomes = await asyncio.gather(attempt(first.id), attempt(second.id))

    assert sorted(outcomes) == ["fail", "ok"]
    active = (
        await db.scalars(
            select(MachineSession).where(MachineSession.status == SessionStatus.PRINTING)
        )
    ).all()
    assert len(active) == 1


async def test_offer_is_marked_taken_when_used(db, printers, make_user):
    owner = await make_user()
    waiting = await make_user()
    await svc.occupy(db, owner, printers[0].id, 60, now=NOON)
    await svc.occupy(db, await make_user(), printers[1].id, 60, now=NOON)
    join = await queue_svc.join(db, waiting.id, MachineKind.PRINTER, now=NOON)

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
    await queue_svc.join(db, waiting.id, MachineKind.PRINTER, now=NOON)
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
    await queue_svc.join(db, waiting.id, MachineKind.PRINTER, now=NOON)
    await svc.release(db, owner, printers[0].id, now=NOON + timedelta(minutes=30))

    with pytest.raises(MachineReserved):
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
        machine_id = printers[0].id

        printer = await db.get(Machine, machine_id)
        printer.name = "Bambu X1"
        await db.commit()

        session = await db.scalar(
            select(MachineSession).where(MachineSession.machine_id == machine_id)
        )
        assert session is not None, "переименование не должно осиротить печать"
        result = await svc.release(db, user, machine_id, now=NOON + timedelta(minutes=10))
        assert result.machine_name == "Bambu X1"


class TestKinds:
    """Парк неоднороден, но правило 2 по-прежнему на весь парк."""

    async def test_engraver_is_booked_like_a_printer(self, db, engravers, make_user):
        user = await make_user()

        result = await svc.occupy(db, user, engravers[0].id, 60, now=NOON)

        assert result.machine_name == "Гравёр #1"
        assert (await db.get(Machine, engravers[0].id)).status == MachineStatus.PRINTING

    async def test_printer_and_engraver_at_once_is_refused(
        self, db, printers, engravers, make_user
    ):
        """Одна активная сессия на человека — на весь парк, а не на тип."""
        user = await make_user()
        await svc.occupy(db, user, printers[0].id, 60, now=NOON)

        with pytest.raises(UserBusy):
            await svc.occupy(db, user, engravers[0].id, 60, now=NOON)


class TestPark:
    """Состав парка правится из админки, но правила живут здесь."""

    async def test_admin_adds_a_machine(self, db, make_user):
        admin = await make_user(is_admin=True)

        machine = await svc.create(db, admin, "  Гравёр #2 ", MachineKind.ENGRAVER)

        assert machine.name == "Гравёр #2", "имя должно приходить обрезанным"
        assert machine.kind == MachineKind.ENGRAVER
        assert machine.status == MachineStatus.FREE

    async def test_only_admin_adds(self, db, make_user):
        user = await make_user()

        with pytest.raises(NotAdmin):
            await svc.create(db, user, "Гравёр #2", MachineKind.ENGRAVER)

    async def test_name_is_unique_across_kinds(self, db, printers, make_user):
        """Два «P2S #1» на экране не различить, даже если это разные машины."""
        admin = await make_user(is_admin=True)

        with pytest.raises(MachineNameTaken):
            await svc.create(db, admin, "P2S #1", MachineKind.ENGRAVER)

    @pytest.mark.parametrize("name", ["", "   "])
    async def test_empty_name_is_refused(self, db, make_user, name):
        admin = await make_user(is_admin=True)

        with pytest.raises(MachineNameInvalid):
            await svc.create(db, admin, name, MachineKind.PRINTER)

    async def test_unknown_kind_is_refused(self, db, make_user):
        admin = await make_user(is_admin=True)

        with pytest.raises(MachineKindUnknown):
            await svc.create(db, admin, "Лазер #1", "laser")

    async def test_removes_machine_without_history(self, db, printers, make_user):
        admin = await make_user(is_admin=True)

        name = await svc.remove(db, admin, printers[1].id)

        assert name == "P2S #2"
        assert await db.get(Machine, printers[1].id) is None

    async def test_machine_with_history_is_kept(self, db, printers, make_user):
        """Удаление оторвало бы журнал от машины — для уехавшей есть обслуживание."""
        admin = await make_user(is_admin=True)
        user = await make_user()
        await svc.occupy(db, user, printers[0].id, 60, now=NOON)
        await svc.release(db, user, printers[0].id, now=NOON + timedelta(minutes=10))

        with pytest.raises(MachineHasHistory):
            await svc.remove(db, admin, printers[0].id)

    async def test_machine_offered_to_the_queue_is_kept(self, db, printers, make_user):
        """Печатей нет, но в журнале осталось приглашение — тоже история."""
        admin = await make_user(is_admin=True)
        owner = await make_user()
        waiting = await make_user()
        await svc.occupy(db, owner, printers[0].id, 60, now=NOON)
        join = await queue_svc.join(db, waiting.id, MachineKind.PRINTER, now=NOON)
        # приглашение ушло на второй принтер, работ на нём при этом не было
        entry = await db.get(QueueEntry, join.entry_id)
        assert entry.offered_machine_id == printers[1].id

        with pytest.raises(MachineHasHistory):
            await svc.remove(db, admin, printers[1].id)

    async def test_rename_keeps_history_and_returns_the_old_name(
        self, db, printers, make_user
    ):
        admin = await make_user(is_admin=True)
        user = await make_user()
        occupied = await svc.occupy(db, user, printers[0].id, 60, now=NOON)

        previous = await svc.rename(db, admin, printers[0].id, "Bambu X1")

        assert previous == "P2S #1"
        session = await db.get(MachineSession, occupied.session_id)
        assert session.machine_id == printers[0].id, "переименование не должно осиротить работу"

    async def test_rename_to_a_taken_name_is_refused(self, db, printers, make_user):
        admin = await make_user(is_admin=True)

        with pytest.raises(MachineNameTaken):
            await svc.rename(db, admin, printers[0].id, "P2S #2")

    async def test_rename_to_the_same_name_is_a_no_op(self, db, printers, make_user):
        admin = await make_user(is_admin=True)

        assert await svc.rename(db, admin, printers[0].id, "P2S #1") == "P2S #1"

    async def test_list_can_be_filtered_by_kind(self, db, printers, engravers):
        found = await svc.list_machines(db, kind=MachineKind.ENGRAVER)

        assert [machine.name for machine in found] == ["Гравёр #1"]
