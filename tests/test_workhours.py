"""Часы работы помещения.

Бронировать можно только то время, когда помещение открыто, и сетка расписания
показывает ровно эти часы. Часы у каждой комнаты свои: переговорная закрывается
в шесть, мастерская работает до ночи. Проверки идут на настоящем Postgres, как и
весь набор: часы лежат в базе, и то, что строка на помещение ровно одна, держит
ограничение, а не код.

У помещения без сохранённых часов работают значения по умолчанию — 08:00–20:00
(`workhours.DEFAULT`), и `conftest` вычищает `work_hours` перед каждым тестом.
"""

from datetime import UTC, datetime, time, timedelta

import pytest

from app.config import settings
from app.enums import MachineKind
from app.services import machines as machines_svc
from app.services import reservations as svc
from app.services import schedule
from app.services import workhours as workhours_svc
from app.services.errors import InvalidReservationTime, WorkHoursInvalid

# Полдень будним днём в местной зоне (Europe/Nicosia, летом UTC+3).
NOON = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)


def at(hour: int, day_shift: int = 1) -> datetime:
    """Местный час завтрашнего (по умолчанию) дня в UTC."""
    local = datetime.combine(
        schedule.day_of(NOON) + timedelta(days=day_shift),
        time(hour, 0),
        tzinfo=settings.zone,
    )
    return local.astimezone(UTC)


async def login(client) -> None:
    await client.post("/admin/login", data={"secret": settings.admin_secret})


class TestStorage:
    async def test_room_without_saved_hours_works_by_default(self, db, room):
        assert await workhours_svc.get(db, room.id) == schedule.Hours(time(8, 0), time(20, 0))

    async def test_hours_are_saved(self, db, room):
        await workhours_svc.save(db, room.id, "09:30", "18:00")
        await db.commit()

        assert await workhours_svc.get(db, room.id) == schedule.Hours(time(9, 30), time(18, 0))

    async def test_every_room_keeps_its_own_hours(self, db, room, other_room):
        """Переговорная закрывается в шесть, мастерская работает до ночи."""
        await workhours_svc.save(db, room.id, "08:00", "22:00")
        await workhours_svc.save(db, other_room.id, "09:00", "18:00")
        await db.commit()

        assert await workhours_svc.get(db, room.id) == schedule.Hours(time(8, 0), time(22, 0))
        assert await workhours_svc.get(db, other_room.id) == schedule.Hours(
            time(9, 0), time(18, 0)
        )

    async def test_hours_of_several_rooms_come_in_one_query(self, db, room, other_room):
        await workhours_svc.save(db, other_room.id, "10:00", "14:00")
        await db.commit()

        hours = await workhours_svc.by_room(db, [room.id, other_room.id])

        assert hours[room.id] == workhours_svc.DEFAULT
        assert hours[other_room.id] == schedule.Hours(time(10, 0), time(14, 0))

    async def test_closing_before_opening_is_refused(self, db, room):
        with pytest.raises(WorkHoursInvalid):
            await workhours_svc.save(db, room.id, "20:00", "08:00")

    async def test_garbage_is_refused(self, db, room):
        with pytest.raises(WorkHoursInvalid):
            await workhours_svc.save(db, room.id, "восемь утра", "20:00")

    async def test_round_the_clock_is_allowed(self, db, room):
        """00:00–00:00 — это круглые сутки, а не окно нулевой длины."""
        hours = await workhours_svc.save(db, room.id, "00:00", "00:00")

        assert hours.round_the_clock


class TestBookingWithinHours:
    async def test_window_inside_the_working_day_is_booked(self, db, printers, make_user):
        user = await make_user()

        result = await svc.book(db, user, printers[0].id, at(10), 120, now=NOON)

        assert result.ends_at == at(12)

    async def test_before_opening_is_refused(self, db, printers, make_user):
        user = await make_user()

        with pytest.raises(InvalidReservationTime, match="рабочие часы"):
            await svc.book(db, user, printers[0].id, at(6), 60, now=NOON)

    async def test_after_closing_is_refused(self, db, printers, make_user):
        user = await make_user()

        with pytest.raises(InvalidReservationTime, match="рабочие часы"):
            await svc.book(db, user, printers[0].id, at(21), 60, now=NOON)

    async def test_window_may_run_through_the_night(self, db, printers, make_user):
        """Часы ограничивают начало брони, а не конец: печать, поставленная в
        19:00, честно идёт всю ночь, а деталь забирают с открытия."""
        user = await make_user()

        result = await svc.book(db, user, printers[0].id, at(19), 14 * 60, now=NOON)

        assert result.ends_at == at(9, day_shift=2)

    async def test_last_hour_before_closing_is_bookable(self, db, printers, make_user):
        user = await make_user()

        result = await svc.book(db, user, printers[0].id, at(19), 60, now=NOON)

        assert result.ends_at == at(20)

    async def test_closing_hour_itself_is_not_bookable(self, db, printers, make_user):
        """В 20:00 мастерская закрывается — начать в этот час уже нельзя."""
        user = await make_user()

        with pytest.raises(InvalidReservationTime, match="рабочие часы"):
            await svc.book(db, user, printers[0].id, at(20), 60, now=NOON)

    async def test_new_hours_change_what_can_be_booked(self, db, room, printers, make_user):
        user = await make_user()
        await workhours_svc.save(db, room.id, "10:00", "22:00")
        await db.commit()

        with pytest.raises(InvalidReservationTime, match="рабочие часы"):
            await svc.book(db, user, printers[0].id, at(9), 60, now=NOON)

        result = await svc.book(db, user, printers[0].id, at(21), 60, now=NOON)
        assert result.starts_at == at(21)

    async def test_round_the_clock_lifts_the_limit(self, db, room, printers, make_user):
        user = await make_user()
        await workhours_svc.save(db, room.id, "00:00", "00:00")
        await db.commit()

        result = await svc.book(db, user, printers[0].id, at(3), 60, now=NOON)

        assert result.starts_at == at(3)

    async def test_occupying_now_is_not_limited_by_hours(self, db, printers, make_user):
        """«Занять сейчас» часы не ограничивают: поставленная вечером печать
        честно идёт до утра, просто прийти за деталью можно с открытия."""
        user = await make_user()
        evening = at(19, day_shift=0)

        result = await machines_svc.occupy(db, user, printers[0].id, 720, now=evening)

        assert result.eta_at == evening + timedelta(hours=12)


class TestGrid:
    async def test_grid_shows_only_working_hours(self, db, room, printers):
        park = await machines_svc.list_machines(db, room_id=room.id, kind=MachineKind.PRINTER)

        grid = await svc.day_schedule(
            db, park, room, MachineKind.PRINTER, schedule.day_of(at(10)), now=NOON
        )

        assert grid.hours == [f"{hour:02d}:00" for hour in range(8, 20)]
        assert grid.work_hours == schedule.Hours(time(8, 0), time(20, 0))

    async def test_grid_follows_new_hours(self, db, room, printers):
        await workhours_svc.save(db, room.id, "10:00", "14:00")
        await db.commit()
        park = await machines_svc.list_machines(db, room_id=room.id, kind=MachineKind.PRINTER)

        grid = await svc.day_schedule(
            db, park, room, MachineKind.PRINTER, schedule.day_of(at(10)), now=NOON
        )

        assert grid.hours == ["10:00", "11:00", "12:00", "13:00"]

    async def test_round_the_clock_grid_covers_the_day(self, db, room, printers):
        await workhours_svc.save(db, room.id, "00:00", "00:00")
        await db.commit()
        park = await machines_svc.list_machines(db, room_id=room.id, kind=MachineKind.PRINTER)

        grid = await svc.day_schedule(
            db, park, room, MachineKind.PRINTER, schedule.day_of(at(10)), now=NOON
        )

        assert len(grid.hours) == 24

    async def test_booking_made_before_the_hours_moved_stays_visible(
        self, db, room, printers, make_user
    ):
        """Сдвиг часов не снимает чужие брони молча: окно 08:00–09:00 остаётся
        в базе, и админ должен увидеть его в «Сводке», а не узнать от человека,
        пришедшего к закрытой двери."""
        user = await make_user(name="Аня")
        await svc.book(db, user, printers[0].id, at(8), 60, now=NOON)
        await workhours_svc.save(db, room.id, "12:00", "20:00")
        await db.commit()

        assert await svc.active_of_user(db, user.id) is not None
        assert [row[0].starts_at for row in await svc.booked_ahead(db, now=NOON)] == [at(8)]


class TestBookScreen:
    """Экраны зовут настоящие часы, поэтому время здесь от `work_slot`, а не от
    `NOON`: подделать `datetime.now` на весь запрос нечем."""

    async def test_closing_time_does_not_cap_the_durations(
        self, client, printers, work_slot
    ):
        """У брони с 19:00 кнопка «до утра» — самая нужная, и закрытие в 20:00
        её не убирает: машина работает сама."""
        response = await client.get(
            f"/book/{printers[0].id}", params={"start": work_slot(19).isoformat()}
        )

        assert response.status_code == 200
        assert "1 ч" in response.text and "8 ч" in response.text
        assert "до утра" in response.text

    async def test_slot_outside_the_hours_does_not_open_the_form(
        self, client, printers, work_slot
    ):
        response = await client.get(
            f"/book/{printers[0].id}",
            params={"start": work_slot(23).isoformat()},
            headers={"accept": "text/html"},
        )

        assert response.status_code == 400
        assert "рабочие часы" in response.text


class TestAdminPage:
    async def test_page_is_closed_without_secret(self, client):
        assert (await client.get("/admin/hours")).status_code == 403

    async def test_page_shows_current_hours(self, client, room, make_user):
        await make_user(is_admin=True)
        await login(client)

        response = await client.get("/admin/hours")

        assert response.status_code == 200
        assert "08:00" in response.text and "20:00" in response.text

    async def test_form_saves_new_hours(self, client, db, room, make_user):
        await make_user(is_admin=True)
        await login(client)

        response = await client.post(
            f"/admin/hours/{room.id}", data={"opens_at": "09:00", "closes_at": "18:30"}
        )

        assert response.status_code == 303
        assert await workhours_svc.get(db, room.id) == schedule.Hours(time(9, 0), time(18, 30))

    async def test_form_refuses_closing_before_opening(self, client, db, room, make_user):
        await make_user(is_admin=True)
        await login(client)

        response = await client.post(
            f"/admin/hours/{room.id}",
            data={"opens_at": "20:00", "closes_at": "08:00"},
            headers={"accept": "text/html"},
        )

        assert response.status_code == 400
        assert await workhours_svc.get(db, room.id) == schedule.Hours(time(8, 0), time(20, 0))
