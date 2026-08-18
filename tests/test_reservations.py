"""Брони на будущее.

Проверки идут на настоящем Postgres: непересечение окон держит EXCLUDE по
`tstzrange`, а переключаемая пользовательская квота — блокировка строки User.
На SQLite или моках обе защиты от гонок зеленели бы, ничего не проверяя.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.enums import MachineKind, MachineStatus, ReservationStatus
from app.models import Machine, MachineSession, Reservation, User
from app.services import booking_policy, schedule
from app.services import machines as machines_svc
from app.services import reminders as reminders_svc
from app.services import reservations as svc
from app.services.errors import (
    AlreadyBooked,
    InvalidDuration,
    InvalidReservationTime,
    MachineBooked,
    MachineNotAvailable,
    ReservationForbidden,
    ReservationNotFound,
    ReservationOverlap,
    UserBusy,
    UserLimitReached,
)

# Полдень будним днём: сетка выровнена по местному часу, поэтому все моменты в
# тестах кратны часу и в зоне Europe/Nicosia (UTC+3 летом) попадают на :00.
NOON = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)


def tomorrow(hours: int = 0) -> datetime:
    return NOON + timedelta(days=1, hours=hours)


class TestBook:
    async def test_book_creates_window(self, db, printers, make_user):
        user = await make_user()

        result = await svc.book(db, user, printers[0].id, tomorrow(), 120, now=NOON)

        assert result.machine_name == "P2S #1"
        assert result.starts_at == tomorrow()
        assert result.ends_at == tomorrow(2)

        reservation = await db.get(Reservation, result.reservation_id)
        assert reservation.status == ReservationStatus.BOOKED

    async def test_overlapping_window_is_rejected(self, db, printers, make_user):
        """Правило 12: два окна на одной машине не пересекаются."""
        first = await make_user()
        second = await make_user()
        await svc.book(db, first, printers[0].id, tomorrow(), 120, now=NOON)

        with pytest.raises(ReservationOverlap):
            await svc.book(db, second, printers[0].id, tomorrow(1), 120, now=NOON)

    async def test_touching_windows_are_allowed(self, db, printers, make_user):
        """14:00–16:00 и 16:00–18:00 стыкуются, а не конфликтуют."""
        first = await make_user()
        second = await make_user()
        await svc.book(db, first, printers[0].id, tomorrow(), 120, now=NOON)

        result = await svc.book(db, second, printers[0].id, tomorrow(2), 60, now=NOON)

        assert result.starts_at == tomorrow(2)

    async def test_same_window_on_another_machine_is_allowed(
        self, db, printers, make_user
    ):
        first = await make_user()
        second = await make_user()
        await svc.book(db, first, printers[0].id, tomorrow(), 120, now=NOON)

        result = await svc.book(db, second, printers[1].id, tomorrow(), 120, now=NOON)

        assert result.machine_id == printers[1].id

    async def test_second_booking_of_one_person_is_rejected(
        self, db, printers, make_user
    ):
        """Правило 13: иначе один человек забивает собой всю неделю вперёд."""
        user = await make_user()
        await svc.book(db, user, printers[0].id, tomorrow(), 60, now=NOON)

        with pytest.raises(AlreadyBooked):
            await svc.book(db, user, printers[1].id, tomorrow(4), 60, now=NOON)

    async def test_extended_policy_allows_two_printers_and_one_engraver(
        self, db, printers, engravers, make_user
    ):
        user = await make_user()
        await booking_policy.save(db, True)

        await svc.book(db, user, printers[0].id, tomorrow(), 60, now=NOON)
        await svc.book(db, user, printers[1].id, tomorrow(), 60, now=NOON)
        third = await svc.book(db, user, engravers[0].id, tomorrow(), 60, now=NOON)

        assert third.machine_id == engravers[0].id

    async def test_extended_policy_enforces_each_kind_limit(
        self, db, printers, engravers, make_user
    ):
        user = await make_user()
        await booking_policy.save(db, True)
        await svc.book(db, user, printers[0].id, tomorrow(), 60, now=NOON)
        await svc.book(db, user, printers[1].id, tomorrow(), 60, now=NOON)
        await svc.book(db, user, engravers[0].id, tomorrow(), 60, now=NOON)

        with pytest.raises(UserLimitReached):
            await svc.book(db, user, printers[0].id, tomorrow(2), 60, now=NOON)
        with pytest.raises(UserLimitReached):
            await svc.book(db, user, engravers[0].id, tomorrow(2), 60, now=NOON)

    async def test_concurrent_extended_bookings_keep_two_printer_limit(
        self, db, room, printers, sessions, make_user
    ):
        user = await make_user()
        third = Machine(
            room_id=room.id,
            name="P2S #3",
            kind=MachineKind.PRINTER,
            status=MachineStatus.FREE,
        )
        db.add(third)
        await booking_policy.save(db, True)
        await db.commit()

        async def attempt(machine_id: int) -> str:
            async with sessions() as session:
                person = await session.get(User, user.id)
                try:
                    await svc.book(session, person, machine_id, tomorrow(), 60, now=NOON)
                    await session.commit()
                    return "ok"
                except UserLimitReached:
                    await session.rollback()
                    return "limit"

        outcomes = await asyncio.gather(
            *(attempt(machine.id) for machine in [*printers, third])
        )

        assert sorted(outcomes) == ["limit", "ok", "ok"]
        active = await db.scalars(
            select(Reservation).where(Reservation.status == ReservationStatus.BOOKED)
        )
        assert len(active.all()) == 2

    async def test_booking_while_working_is_rejected(self, db, printers, make_user):
        """Одна задача на человека во всей системе: занятая машина — уже задача.

        Раньше бронь и работа считались разными вещами со своими лимитами. При
        небольшом парке это давало одному человеку станок сейчас и час на
        будущее.
        """
        user = await make_user()
        await machines_svc.occupy(db, user, printers[0].id, 60, now=NOON)

        with pytest.raises(UserBusy):
            await svc.book(db, user, printers[1].id, tomorrow(), 60, now=NOON)

    async def test_work_in_another_room_blocks_booking_too(
        self, db, printers, meeting, make_user
    ):
        """Пока идёт одна работа, новую бронь нельзя создать нигде."""
        _, unit = meeting
        user = await make_user()
        await machines_svc.occupy(db, user, printers[0].id, 60, now=NOON)

        with pytest.raises(UserBusy):
            await svc.book(db, user, unit.id, tomorrow(), 60, now=NOON)

    async def test_start_must_be_on_the_grid(self, db, printers, make_user):
        user = await make_user()

        with pytest.raises(InvalidReservationTime):
            await svc.book(
                db, user, printers[0].id, tomorrow() + timedelta(minutes=20), 60, now=NOON
            )

    async def test_start_in_the_past_is_rejected(self, db, printers, make_user):
        user = await make_user()

        with pytest.raises(InvalidReservationTime):
            await svc.book(db, user, printers[0].id, NOON - HOUR, 60, now=NOON)

    async def test_beyond_horizon_is_rejected(self, db, printers, make_user):
        user = await make_user()
        far = schedule.align(NOON) + timedelta(days=15)

        with pytest.raises(InvalidReservationTime):
            await svc.book(db, user, printers[0].id, far, 60, now=NOON)

    async def test_too_short_is_rejected(self, db, printers, make_user):
        user = await make_user()

        with pytest.raises(InvalidDuration):
            await svc.book(db, user, printers[0].id, tomorrow(), 30, now=NOON)

    async def test_broken_machine_cannot_be_booked(self, db, printers, make_user):
        admin = await make_user(is_admin=True)
        user = await make_user()
        await machines_svc.set_broken(db, admin, printers[0].id, note="сопло")

        with pytest.raises(MachineNotAvailable):
            await svc.book(db, user, printers[0].id, tomorrow(), 60, now=NOON)

    async def test_cannot_book_over_running_work(self, db, printers, make_user):
        """Расчётный конец — всё, что мы знаем о работе; бронь поверх него
        позвала бы двоих к одному столу."""
        owner = await make_user()
        user = await make_user()
        await machines_svc.occupy(db, owner, printers[0].id, 240, now=NOON)

        with pytest.raises(ReservationOverlap):
            await svc.book(db, user, printers[0].id, NOON + HOUR, 60, now=NOON)

    async def test_can_book_after_running_work(self, db, printers, make_user):
        owner = await make_user()
        user = await make_user()
        await machines_svc.occupy(db, owner, printers[0].id, 120, now=NOON)

        result = await svc.book(db, user, printers[0].id, NOON + 3 * HOUR, 60, now=NOON)

        assert result.starts_at == NOON + 3 * HOUR

    async def test_second_booking_is_blocked_even_in_another_room(
        self, db, printers, meeting, make_user
    ):
        user = await make_user()
        _, meeting_unit = meeting
        first = await svc.book(db, user, printers[0].id, tomorrow(), 60, now=NOON)

        with pytest.raises(AlreadyBooked):
            await svc.book(db, user, meeting_unit.id, tomorrow(2), 60, now=NOON)

        await svc.cancel(db, user, first.reservation_id, now=NOON)
        second = await svc.book(db, user, meeting_unit.id, tomorrow(2), 60, now=NOON)
        assert second.machine_id == meeting_unit.id

    async def test_new_booking_is_blocked_until_done_work_is_released(
        self, db, printers, make_user
    ):
        user = await make_user()
        start = tomorrow()
        await svc.book(db, user, printers[0].id, start, 60, now=NOON)
        await machines_svc.occupy(db, user, printers[0].id, 60, now=start)
        await machines_svc.mark_done_wait(db, printers[0].id, now=start + HOUR)

        with pytest.raises(UserBusy):
            await svc.book(
                db, user, printers[1].id, start + 2 * HOUR, 60, now=start + HOUR
            )

        await machines_svc.release(db, user, printers[0].id, now=start + HOUR)
        result = await svc.book(
            db, user, printers[1].id, start + 2 * HOUR, 60, now=start + HOUR
        )
        assert result.machine_id == printers[1].id


class TestOccupyWithBooking:
    async def test_own_window_can_be_occupied(self, db, printers, make_user):
        user = await make_user()
        booking = await svc.book(db, user, printers[0].id, tomorrow(), 120, now=NOON)

        result = await machines_svc.occupy(
            db, user, printers[0].id, 60, now=tomorrow(0)
        )

        assert result.from_reservation is True
        session = await db.get(MachineSession, result.session_id)
        assert session.reservation_id == booking.reservation_id
        reservation = await db.get(Reservation, booking.reservation_id)
        assert reservation.status == ReservationStatus.TAKEN

    async def test_booking_blocks_occupying_another_machine(
        self, db, printers, make_user
    ):
        """Одно дело на человека: с бронью на руках второй станок — уже второе."""
        user = await make_user()
        await svc.book(db, user, printers[0].id, tomorrow(), 120, now=NOON)

        with pytest.raises(AlreadyBooked):
            await machines_svc.occupy(db, user, printers[1].id, 60, now=NOON)

    async def test_own_booked_machine_can_be_taken_early(self, db, printers, make_user):
        """Своя забронированная машина — то же дело, а не второе.

        Она стоит свободная, час всё равно обещан ему же, и отказ здесь заставил
        бы человека сначала отменить собственную бронь.
        """
        user = await make_user()
        await svc.book(db, user, printers[0].id, tomorrow(), 120, now=NOON)

        result = await machines_svc.occupy(db, user, printers[0].id, 60, now=NOON)

        # Не по брони: окно ещё не началось, и бронь остаётся ждать своего часа.
        assert result.from_reservation is False
        assert (await svc.active_of_user(db, user.id)) is not None

    async def test_booking_in_another_room_blocks_occupy(
        self, db, printers, meeting, make_user
    ):
        user = await make_user()
        _, unit = meeting
        await svc.book(db, user, unit.id, tomorrow(), 60, now=NOON)

        with pytest.raises(AlreadyBooked):
            await machines_svc.occupy(db, user, printers[0].id, 60, now=NOON)

    async def test_someone_elses_window_blocks_occupy(self, db, printers, make_user):
        owner = await make_user()
        stranger = await make_user()
        await svc.book(db, owner, printers[0].id, tomorrow(), 120, now=NOON)

        with pytest.raises(MachineBooked):
            await machines_svc.occupy(db, stranger, printers[0].id, 60, now=tomorrow(0))

    async def test_admin_does_not_bypass_someone_elses_window(
        self, db, printers, make_user
    ):
        """Админ снимает бронь отдельным действием — так это попадает в журнал."""
        owner = await make_user()
        admin = await make_user(is_admin=True)
        await svc.book(db, owner, printers[0].id, tomorrow(), 120, now=NOON)

        with pytest.raises(MachineBooked):
            await machines_svc.occupy(db, admin, printers[0].id, 60, now=tomorrow(0))

    async def test_duration_is_capped_by_next_booking(self, db, printers, make_user):
        owner = await make_user()
        passerby = await make_user()
        await svc.book(db, owner, printers[0].id, NOON + 3 * HOUR, 120, now=NOON)

        with pytest.raises(MachineBooked):
            await machines_svc.occupy(db, passerby, printers[0].id, 480, now=NOON)

    async def test_duration_that_fits_before_booking_is_allowed(
        self, db, printers, make_user
    ):
        owner = await make_user()
        passerby = await make_user()
        await svc.book(db, owner, printers[0].id, NOON + 3 * HOUR, 120, now=NOON)

        result = await machines_svc.occupy(db, passerby, printers[0].id, 180, now=NOON)

        assert result.eta_at == NOON + 3 * HOUR

class TestCancel:
    async def test_owner_cancels_own_booking(self, db, printers, make_user):
        user = await make_user()
        booking = await svc.book(db, user, printers[0].id, tomorrow(), 60, now=NOON)

        result = await svc.cancel(db, user, booking.reservation_id, now=NOON)

        assert result.by_owner is True
        reservation = await db.get(Reservation, booking.reservation_id)
        assert reservation.status == ReservationStatus.CANCELLED

    async def test_cancelled_booking_frees_the_limit(self, db, printers, make_user):
        user = await make_user()
        booking = await svc.book(db, user, printers[0].id, tomorrow(), 60, now=NOON)
        await svc.cancel(db, user, booking.reservation_id, now=NOON)

        again = await svc.book(db, user, printers[0].id, tomorrow(4), 60, now=NOON)

        assert again.reservation_id != booking.reservation_id

    async def test_stranger_cannot_cancel(self, db, printers, make_user):
        user = await make_user()
        stranger = await make_user()
        booking = await svc.book(db, user, printers[0].id, tomorrow(), 60, now=NOON)

        with pytest.raises(ReservationForbidden):
            await svc.cancel(db, stranger, booking.reservation_id, now=NOON)

    async def test_admin_cancels_someone_elses(self, db, printers, make_user):
        user = await make_user()
        admin = await make_user(is_admin=True)
        booking = await svc.book(db, user, printers[0].id, tomorrow(), 60, now=NOON)

        result = await svc.cancel(
            db, admin, booking.reservation_id, reason="уехала машина", now=NOON
        )

        assert result.by_owner is False
        reservation = await db.get(Reservation, booking.reservation_id)
        assert reservation.cancel_reason == "уехала машина"

    async def test_cancelling_twice_is_rejected(self, db, printers, make_user):
        user = await make_user()
        booking = await svc.book(db, user, printers[0].id, tomorrow(), 60, now=NOON)
        await svc.cancel(db, user, booking.reservation_id, now=NOON)

        with pytest.raises(ReservationNotFound):
            await svc.cancel(db, user, booking.reservation_id, now=NOON)


class TestNoShow:
    async def test_free_machine_and_no_show_drops_the_booking(
        self, db, printers, make_user
    ):
        user = await make_user()
        booking = await svc.book(db, user, printers[0].id, NOON + HOUR, 60, now=NOON)
        late = NOON + HOUR + timedelta(minutes=31)

        result = await svc.expire_no_show(db, booking.reservation_id, now=late)

        assert result.machine_name == "P2S #1"
        reservation = await db.get(Reservation, booking.reservation_id)
        assert reservation.status == ReservationStatus.EXPIRED

    async def test_grace_is_not_over_yet(self, db, printers, make_user):
        user = await make_user()
        booking = await svc.book(db, user, printers[0].id, NOON + HOUR, 60, now=NOON)

        with pytest.raises(ReservationNotFound):
            await svc.expire_no_show(
                db, booking.reservation_id, now=NOON + HOUR + timedelta(minutes=10)
            )

    async def test_busy_machine_does_not_burn_the_booking(
        self, db, printers, make_user
    ):
        """Правило 14: чужая незабранная деталь не должна съедать чужое окно."""
        booker = await make_user()
        owner = await make_user()
        booking = await svc.book(db, booker, printers[0].id, NOON + 2 * HOUR, 60, now=NOON)
        # Работа закончилась, деталь осталась на столе: машина в done_wait.
        await machines_svc.occupy(db, owner, printers[0].id, 60, now=NOON)
        await machines_svc.mark_done_wait(db, printers[0].id, now=NOON + HOUR)
        late = NOON + 2 * HOUR + timedelta(minutes=31)

        with pytest.raises(ReservationNotFound):
            await svc.expire_no_show(db, booking.reservation_id, now=late)

        reservation = await db.get(Reservation, booking.reservation_id)
        assert reservation.status == ReservationStatus.BOOKED

class TestReconcile:
    async def test_reminds_an_hour_before(self, db, printers, make_user):
        user = await make_user()
        booking = await svc.book(db, user, printers[0].id, NOON + 2 * HOUR, 60, now=NOON)
        await db.commit()

        report = await reminders_svc.reconcile(db, now=NOON + HOUR + timedelta(minutes=5))

        assert report.bookings_reminded == 1
        reservation = await db.get(Reservation, booking.reservation_id)
        assert reservation.reminded_at is not None

    async def test_reminder_is_sent_once(self, db, printers, make_user):
        user = await make_user()
        await svc.book(db, user, printers[0].id, NOON + 2 * HOUR, 60, now=NOON)
        await db.commit()

        moment = NOON + HOUR + timedelta(minutes=5)
        await reminders_svc.reconcile(db, now=moment)
        second = await reminders_svc.reconcile(db, now=moment + timedelta(minutes=1))

        assert second.bookings_reminded == 0

    async def test_start_of_window_is_announced(self, db, printers, make_user):
        user = await make_user()
        booking = await svc.book(db, user, printers[0].id, NOON + 2 * HOUR, 60, now=NOON)
        await db.commit()

        report = await reminders_svc.reconcile(db, now=NOON + 2 * HOUR)

        assert report.bookings_started == 1
        reservation = await db.get(Reservation, booking.reservation_id)
        assert reservation.started_notified_at is not None

    async def test_no_show_is_dropped_by_reconcile(self, db, printers, make_user):
        user = await make_user()
        booking = await svc.book(db, user, printers[0].id, NOON + HOUR, 60, now=NOON)
        await db.commit()

        report = await reminders_svc.reconcile(
            db, now=NOON + HOUR + timedelta(minutes=31)
        )

        assert report.bookings_expired == 1
        reservation = await db.get(Reservation, booking.reservation_id)
        assert reservation.status == ReservationStatus.EXPIRED

    async def test_booked_machine_is_not_dropped_while_busy(
        self, db, printers, make_user
    ):
        booker = await make_user()
        owner = await make_user()
        booking = await svc.book(db, booker, printers[0].id, NOON + 2 * HOUR, 60, now=NOON)
        await machines_svc.occupy(db, owner, printers[0].id, 60, now=NOON)
        await db.commit()

        report = await reminders_svc.reconcile(
            db, now=NOON + 2 * HOUR + timedelta(minutes=31)
        )

        assert report.bookings_expired == 0
        reservation = await db.get(Reservation, booking.reservation_id)
        assert reservation.status == ReservationStatus.BOOKED


class TestDaySchedule:
    async def test_grid_has_a_column_per_machine_and_a_row_per_working_hour(
        self, db, room, printers, make_user
    ):
        """Строк ровно столько, сколько мастерская открыта: 08:00–20:00 — это
        двенадцать слотов, последний из которых начинается в 19:00."""
        park = await machines_svc.list_machines(db, kind=MachineKind.PRINTER)

        grid = await svc.day_schedule(
            db, park, room, MachineKind.PRINTER, schedule.day_of(tomorrow()), now=NOON
        )

        assert len(grid.columns) == 2
        assert len(grid.columns[0].cells) == 12
        assert grid.hours[0] == "08:00" and grid.hours[-1] == "19:00"
        assert len(grid.days) == 14

    async def test_booked_hours_are_marked(self, db, room, printers, make_user):
        user = await make_user(name="Аня")
        await svc.book(db, user, printers[0].id, tomorrow(), 120, now=NOON)
        park = await machines_svc.list_machines(db, kind=MachineKind.PRINTER)

        grid = await svc.day_schedule(
            db,
            park,
            room,
            MachineKind.PRINTER,
            schedule.day_of(tomorrow()),
            now=NOON,
            viewer_id=user.id,
        )

        booked = [cell for cell in grid.columns[0].cells if not cell.bookable]
        assert len(booked) == 2
        assert all(cell.state == svc.CELL_MINE for cell in booked)
        assert booked[0].who == "Аня"

    async def test_someone_elses_booking_is_not_mine(self, db, room, printers, make_user):
        owner = await make_user()
        viewer = await make_user()
        await svc.book(db, owner, printers[0].id, tomorrow(), 60, now=NOON)
        park = await machines_svc.list_machines(db, kind=MachineKind.PRINTER)

        grid = await svc.day_schedule(
            db,
            park,
            room,
            MachineKind.PRINTER,
            schedule.day_of(tomorrow()),
            now=NOON,
            viewer_id=viewer.id,
        )

        states = {cell.state for cell in grid.columns[0].cells}
        assert svc.CELL_BOOKED in states
        assert svc.CELL_MINE not in states

    async def test_past_hours_are_not_bookable(self, db, room, printers, make_user):
        park = await machines_svc.list_machines(db, kind=MachineKind.PRINTER)

        grid = await svc.day_schedule(
            db, park, room, MachineKind.PRINTER, schedule.day_of(NOON), now=NOON
        )

        first = grid.columns[0].cells[0]
        assert first.state == svc.CELL_PAST
        assert first.bookable is False

    async def test_running_work_occupies_its_hours(self, db, room, printers, make_user):
        owner = await make_user(name="Пётр")
        await machines_svc.occupy(db, owner, printers[0].id, 120, now=NOON)
        park = await machines_svc.list_machines(db, kind=MachineKind.PRINTER)

        grid = await svc.day_schedule(
            db, park, room, MachineKind.PRINTER, schedule.day_of(NOON), now=NOON
        )

        busy = [cell for cell in grid.columns[0].cells if cell.state == svc.CELL_BUSY]
        assert len(busy) == 2
        assert busy[0].who == "Пётр"

    async def test_broken_machine_is_not_bookable(self, db, room, printers, make_user):
        admin = await make_user(is_admin=True)
        await machines_svc.set_broken(db, admin, printers[0].id, note="сопло")
        park = await machines_svc.list_machines(db, kind=MachineKind.PRINTER)

        grid = await svc.day_schedule(
            db, park, room, MachineKind.PRINTER, schedule.day_of(tomorrow()), now=NOON
        )

        assert all(cell.state == svc.CELL_BROKEN for cell in grid.columns[0].cells)


class TestBoardShowsBookings:
    async def test_machine_in_someone_elses_window_is_not_free(
        self, db, printers, make_user
    ):
        from app.services import board as board_svc

        user = await make_user(name="Аня")
        await svc.book(db, user, printers[0].id, NOON, 120, now=NOON - HOUR)
        await db.commit()

        state = await board_svc.build(db, now=NOON)
        first = state.groups[0].machines[0]

        assert first.status == MachineStatus.FREE
        assert first.is_free is False
        assert first.booking_now is True
        assert first.booked_by == "Аня"

    async def test_upcoming_booking_is_shown_but_machine_stays_free(
        self, db, printers, make_user
    ):
        from app.services import board as board_svc

        user = await make_user(name="Аня")
        await svc.book(db, user, printers[0].id, NOON + 3 * HOUR, 60, now=NOON)
        await db.commit()

        state = await board_svc.build(db, now=NOON)
        first = state.groups[0].machines[0]

        assert first.is_free is True
        assert first.booked_from == NOON + 3 * HOUR


class TestDurationOptions:
    def test_options_are_capped_by_the_next_booking(self):
        options = schedule.duration_options(NOON, limit_minutes=180, minimum=60)

        assert [option.minutes for option in options] == [60, 120]

    def test_until_morning_is_offered(self):
        # 21:00 местного времени — до 09:00 ровно 12 часов, отдельной кнопки
        # «до утра» быть не должно, она совпала бы с «12 ч».
        evening = datetime(2026, 8, 10, 18, 0, tzinfo=UTC)

        labels = [option.label for option in schedule.duration_options(evening, minimum=60)]

        assert labels.count("до утра") == 0
        assert "12 ч" in labels

    def test_minimum_filters_out_short_night(self):
        # 08:30 местного: до утра всего 30 минут, в минимальный час не влезает.
        early = datetime(2026, 8, 10, 5, 30, tzinfo=UTC)

        options = schedule.duration_options(early, minimum=60)

        assert all(option.minutes >= 60 for option in options)
