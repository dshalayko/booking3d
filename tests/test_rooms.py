"""Помещения.

Помещение — не подпись на экране: оно задаёт состав парка и часы работы.
Пользовательские лимиты при этом общие для всей системы, поэтому здесь
проверяются и границы комнаты, и переходы между комнатами.

Отдельно — переговорная: у неё нет оборудования, единицей брони служит сама
комната. Строка в `machines` с типом `meeting_room` создаётся вместе с
помещением, потому что переговорная, которую нельзя забронировать, — это
помещение, которого для системы нет.
"""

from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import select

from app.bot import commands
from app.config import settings
from app.enums import MachineKind, MachineStatus, RoomKind
from app.models import Machine, Room, WorkHours
from app.services import machines as machines_svc
from app.services import queue as queue_svc
from app.services import reservations as reservations_svc
from app.services import rooms as svc
from app.services import schedule
from app.services import workhours as workhours_svc
from app.services.errors import (
    AlreadyBooked,
    MachineKindNotInRoom,
    NotAdmin,
    RoomKindUnknown,
    RoomNameInvalid,
    RoomNameTaken,
    RoomNotEmpty,
    RoomNotFound,
    UserBusy,
)

NOON = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


async def login(client) -> None:
    await client.post("/admin/login", data={"secret": settings.admin_secret})


def tomorrow_at(hour: int = 10) -> datetime:
    """Рабочий час следующего дня — бронировать можно только в часы работы.

    Считается от `NOON`, а не от `datetime.now`: `now` в этих тестах поддельный,
    и настоящее «завтра» рано или поздно оказывается дальше горизонта
    бронирования от `NOON` — набор зеленеет пару недель, а потом краснеет сам
    по себе, без единой правки кода.
    """
    local = NOON.astimezone(settings.zone) + timedelta(days=1)
    return local.replace(hour=hour, minute=0, second=0, microsecond=0).astimezone(UTC)


class TestPark:
    async def test_admin_adds_a_workshop(self, db, make_user):
        admin = await make_user(is_admin=True)

        created = await svc.create(db, admin, "  Мастерская на первом ", RoomKind.WORKSHOP)

        assert created.name == "Мастерская на первом", "имя должно приходить обрезанным"
        assert created.kind == RoomKind.WORKSHOP
        assert await machines_svc.list_machines(db, room_id=created.id) == []

    async def test_meeting_room_is_bookable_from_the_start(self, db, make_user):
        """Иначе свежая переговорная не показывается на экране и её нельзя занять."""
        admin = await make_user(is_admin=True)

        created = await svc.create(db, admin, "Переговорная «Дуб»", RoomKind.MEETING)

        units = await machines_svc.list_machines(db, room_id=created.id)
        assert [unit.name for unit in units] == ["Переговорная «Дуб»"]
        assert units[0].kind == MachineKind.MEETING_ROOM
        assert units[0].status == MachineStatus.FREE

    async def test_only_admin_adds(self, db, make_user):
        user = await make_user()

        with pytest.raises(NotAdmin):
            await svc.create(db, user, "Переговорная", RoomKind.MEETING)

    async def test_name_is_unique(self, db, room, make_user):
        admin = await make_user(is_admin=True)

        with pytest.raises(RoomNameTaken):
            await svc.create(db, admin, room.name, RoomKind.MEETING)

    async def test_meeting_room_does_not_steal_a_machine_name(
        self, db, room, printers, make_user
    ):
        """Имя переговорной — это же имя её единицы брони, и оно уже занято."""
        admin = await make_user(is_admin=True)

        with pytest.raises(RoomNameTaken):
            await svc.create(db, admin, "P2S #1", RoomKind.MEETING)

        assert await db.scalar(select(Room).where(Room.name == "P2S #1")) is None

    @pytest.mark.parametrize("name", ["", "   "])
    async def test_empty_name_is_refused(self, db, make_user, name):
        admin = await make_user(is_admin=True)

        with pytest.raises(RoomNameInvalid):
            await svc.create(db, admin, name, RoomKind.WORKSHOP)

    async def test_unknown_kind_is_refused(self, db, make_user):
        admin = await make_user(is_admin=True)

        with pytest.raises(RoomKindUnknown):
            await svc.create(db, admin, "Ангар", "hangar")

    async def test_rename_keeps_the_same_row(self, db, room, printers, make_user):
        admin = await make_user(is_admin=True)

        previous = await svc.rename(db, admin, room.id, "Мастерская на втором")

        assert previous == "Мастерская"
        assert (await db.get(Room, room.id)).name == "Мастерская на втором"
        assert (await db.get(Machine, printers[0].id)).room_id == room.id

    async def test_renaming_a_meeting_room_renames_its_unit(self, db, meeting, make_user):
        """Это один и тот же физический объект: «Дуб» на плитке и «Клён» в
        заголовке человек прочитал бы как две разные комнаты."""
        admin = await make_user(is_admin=True)
        room, unit = meeting

        await svc.rename(db, admin, room.id, "Переговорная «Клён»")

        assert (await db.get(Machine, unit.id)).name == "Переговорная «Клён»"

    async def test_unit_renamed_by_hand_is_left_alone(self, db, meeting, make_user):
        """Раз единицу назвали иначе, значит так и хотели."""
        admin = await make_user(is_admin=True)
        room, unit = meeting
        await machines_svc.rename(db, admin, unit.id, "Стол у окна")

        await svc.rename(db, admin, room.id, "Переговорная «Клён»")

        assert (await db.get(Machine, unit.id)).name == "Стол у окна"

    async def test_empty_room_is_removed(self, db, make_user):
        admin = await make_user(is_admin=True)
        created = await svc.create(db, admin, "Кладовка", RoomKind.WORKSHOP)

        name = await svc.remove(db, admin, created.id)

        assert name == "Кладовка"
        assert await db.get(Room, created.id) is None

    async def test_room_with_machines_is_kept(self, db, room, printers, make_user):
        """Удаление оторвало бы журнал от места, где всё происходило."""
        admin = await make_user(is_admin=True)

        with pytest.raises(RoomNotEmpty):
            await svc.remove(db, admin, room.id)

    async def test_room_with_queue_history_is_kept(self, db, other_room, make_user):
        """Оборудования в комнате нет, но ожидания в журнале ссылаются на неё.

        Так выглядит закрывшаяся мастерская: машины увезли, а кто в ней когда
        стоял в очереди — это журнал, и отрывать его от места незачем.
        """
        admin = await make_user(is_admin=True)
        waiting = await make_user()
        await queue_svc.join(db, waiting.id, other_room.id, MachineKind.PRINTER, now=NOON)

        with pytest.raises(RoomNotEmpty):
            await svc.remove(db, admin, other_room.id)

    async def test_unknown_room_is_refused(self, db):
        with pytest.raises(RoomNotFound):
            await svc.get(db, 999)


class TestRemovingAMeetingRoom:
    """Переговорную удаляют одним действием.

    Её единица брони — не отдельный объект, а сама комната: требовать «сначала
    удалите оборудование» значило бы просить удалить помещение из самого себя.
    """

    async def test_room_goes_away_together_with_its_unit(self, db, make_user):
        admin = await make_user(is_admin=True)
        created = await svc.create(db, admin, "Переговорная «Дуб»", RoomKind.MEETING)
        unit = (await machines_svc.list_machines(db, room_id=created.id))[0]

        name = await svc.remove(db, admin, created.id)

        assert name == "Переговорная «Дуб»"
        assert await db.get(Room, created.id) is None
        assert await db.get(Machine, unit.id) is None

    async def test_fresh_room_is_removable(self, db, meeting):
        room, _ = meeting

        counts = await svc.usage(db, room.id)

        assert counts.removable
        assert counts.machines == 1, "единица у комнаты есть, но удалять её руками не нужно"

    async def test_room_with_a_past_meeting_is_kept(self, db, meeting, make_user):
        """Журнал не должен оторваться от места, где всё происходило."""
        admin = await make_user(is_admin=True)
        room, unit = meeting
        person = await make_user()
        await machines_svc.occupy(db, person, unit.id, 60, now=NOON)
        await machines_svc.release(db, person, unit.id, now=NOON + timedelta(minutes=30))

        with pytest.raises(RoomNotEmpty, match="в журнале"):
            await svc.remove(db, admin, room.id)

    async def test_room_with_a_booking_is_kept(self, db, meeting, make_user):
        """Бронь ссылается на ту же строку: удаление упало бы на внешнем ключе."""
        admin = await make_user(is_admin=True)
        room, unit = meeting
        person = await make_user()
        await reservations_svc.book(db, person, unit.id, tomorrow_at(), 60, now=NOON)

        with pytest.raises(RoomNotEmpty):
            await svc.remove(db, admin, room.id)

    async def test_admin_page_offers_to_delete_a_fresh_room(self, client, db, meeting, make_user):
        room, _ = meeting
        await make_user(is_admin=True)
        await db.commit()
        await login(client)

        page = await client.get("/admin/rooms")
        response = await client.post(f"/admin/rooms/{room.id}/delete")

        assert f"/admin/rooms/{room.id}/delete" in page.text
        assert response.status_code == 303
        assert response.headers["location"] == "/admin/rooms?flash=room_removed"
        assert await db.scalar(select(Room).where(Room.id == room.id)) is None


class TestMachineBelongsToRoom:
    async def test_printer_cannot_stand_in_a_meeting_room(self, db, meeting, make_user):
        """Принтер посреди переговорной сделал бы из её расписания расписание печати."""
        admin = await make_user(is_admin=True)
        room, _ = meeting

        with pytest.raises(MachineKindNotInRoom):
            await machines_svc.create(db, admin, room.id, "P2S #9", MachineKind.PRINTER)

    async def test_meeting_room_cannot_stand_in_a_workshop(self, db, room, make_user):
        admin = await make_user(is_admin=True)

        with pytest.raises(MachineKindNotInRoom):
            await machines_svc.create(
                db, admin, room.id, "Переговорка", MachineKind.MEETING_ROOM
            )

    async def test_second_unit_can_be_added_to_a_meeting_room(self, db, meeting, make_user):
        """Две зоны в одной комнате — редкость, но запрещать её нечем."""
        admin = await make_user(is_admin=True)
        room, _ = meeting

        unit = await machines_svc.create(
            db, admin, room.id, "Дуб: малый стол", MachineKind.MEETING_ROOM
        )

        assert unit.room_id == room.id

class TestGlobalUserLimits:
    """Одна активная работа и одна бронь считаются по всей системе."""

    async def test_work_in_one_room_blocks_another(
        self, db, room, printers, meeting, make_user
    ):
        person = await make_user()
        _, unit = meeting

        await machines_svc.occupy(db, person, printers[0].id, 60, now=NOON)
        with pytest.raises(UserBusy):
            await machines_svc.occupy(db, person, unit.id, 60, now=NOON)

    async def test_second_work_in_the_same_room_is_refused(
        self, db, room, printers, make_user
    ):
        person = await make_user()
        await machines_svc.occupy(db, person, printers[0].id, 60, now=NOON)

        with pytest.raises(UserBusy):
            await machines_svc.occupy(db, person, printers[1].id, 60, now=NOON)

    async def test_booking_is_one_per_user(self, db, printers, meeting, make_user):
        person = await make_user()
        _, unit = meeting

        await reservations_svc.book(
            db, person, printers[0].id, tomorrow_at(), 60, now=NOON
        )

        with pytest.raises(AlreadyBooked):
            await reservations_svc.book(
                db, person, unit.id, tomorrow_at(14), 60, now=NOON
            )


class TestAdminPage:
    """Вкладка «Помещения»: снаружи закрыта, внутри — состав комнат."""

    async def test_tab_is_closed_without_secret(self, client):
        assert (await client.get("/admin/rooms")).status_code == 403
        assert (
            await client.post("/admin/rooms", data={"name": "Ангар", "kind": "workshop"})
        ).status_code == 403

    async def test_page_lists_rooms_with_their_hours(self, client, room, make_user):
        await make_user(is_admin=True)
        await login(client)

        response = await client.get("/admin/rooms")

        assert response.status_code == 200
        assert room.name in response.text
        assert "08:00–20:00" in response.text
        # Адрес доски комнаты нужен, чтобы повесить на неё планшет.
        assert f"/room/{room.id}" in response.text

    async def test_admin_adds_a_meeting_room(self, client, db, make_user):
        await make_user(is_admin=True)
        await db.commit()
        await login(client)

        response = await client.post(
            "/admin/rooms", data={"name": "Переговорная «Дуб»", "kind": "meeting"}
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/admin/rooms?flash=room_added"
        created = await db.scalar(select(Room).where(Room.name == "Переговорная «Дуб»"))
        assert created.kind == RoomKind.MEETING
        # Комната сразу бронируется: её единица создана вместе с ней.
        unit = await db.scalar(select(Machine).where(Machine.room_id == created.id))
        assert unit.kind == MachineKind.MEETING_ROOM

    async def test_added_room_gets_its_own_screen(self, client, db, make_user):
        """Помещение живёт по своему адресу — списка «все помещения» нет."""
        await make_user(is_admin=True)
        await db.commit()
        await login(client)
        await client.post("/admin/rooms", data={"name": "Ангар", "kind": "workshop"})
        created = await db.scalar(select(Room).where(Room.name == "Ангар"))

        board = await client.get(f"/room/{created.id}")

        assert board.status_code == 200
        assert "Ангар" not in board.text
        assert "Здесь пока нет оборудования" in board.text

    async def test_machine_in_a_wrong_room_is_refused(self, client, db, meeting, make_user):
        room, _ = meeting
        await make_user(is_admin=True)
        await db.commit()
        await login(client)

        response = await client.post(
            "/admin/machines",
            data={"name": "P2S #9", "kind": "printer", "room_id": room.id},
            headers={"accept": "text/html"},
        )

        assert response.status_code == 409
        assert "нельзя завести" in response.text

    async def test_hours_are_saved_per_room(self, client, db, room, other_room, make_user):
        await make_user(is_admin=True)
        await db.commit()
        await login(client)

        await client.post(
            f"/admin/hours/{other_room.id}", data={"opens_at": "09:00", "closes_at": "18:00"}
        )

        assert await workhours_svc.get(db, room.id) == workhours_svc.DEFAULT
        assert await workhours_svc.get(db, other_room.id) == schedule.Hours(
            time(9, 0), time(18, 0)
        )

    async def test_deleting_a_room_takes_its_hours_along(self, client, db, make_user):
        """Иначе следующая комната с тем же номером унаследовала бы чужие часы."""
        await make_user(is_admin=True)
        await db.commit()
        await login(client)
        response = await client.post("/admin/rooms", data={"name": "Ангар", "kind": "workshop"})
        created = await db.scalar(select(Room).where(Room.name == "Ангар"))
        await client.post(
            f"/admin/hours/{created.id}", data={"opens_at": "10:00", "closes_at": "12:00"}
        )

        response = await client.post(f"/admin/rooms/{created.id}/delete")

        assert response.status_code == 303
        assert await db.scalar(select(WorkHours).where(WorkHours.room_id == created.id)) is None


class TestKioskIsTiedToOneRoom:
    """Один планшет — одно помещение: комната записана в его метку."""

    async def test_pinned_tablet_shows_only_its_room(
        self, client, db, room, printers, other_room, make_user
    ):
        far = Machine(
            room_id=other_room.id,
            name="P2S #3",
            kind=MachineKind.PRINTER,
            status=MachineStatus.FREE,
        )
        db.add(far)
        await db.commit()
        await client.get(
            f"/kiosk/enroll?secret={settings.kiosk_enroll_secret}&room={room.id}"
        )

        board = await client.get("/")

        assert room.name not in board.text
        assert "P2S #1" in board.text
        assert "P2S #3" not in board.text, "чужое помещение на стене только мешает"

    async def test_unpinned_tablet_shows_the_first_room(
        self, client, room, other_room, printers
    ):
        """Планшет, зарегистрированный до появления комнат, продолжает работать:
        он показывает первое помещение, а не список и не ошибку."""
        board = await client.get("/")

        assert room.name not in board.text
        assert other_room.name not in board.text

    async def test_room_board_is_open_to_everyone(self, client, room, printers):
        """Доска открыта без всякого входа — как и была."""
        response = await client.get(f"/room/{room.id}")

        assert response.status_code == 200
        assert "P2S #1" in response.text

    async def test_unknown_room_is_not_found(self, client):
        assert (await client.get("/room/999")).status_code == 404

    async def test_tablet_of_a_deleted_room_falls_back_to_the_first(
        self, client, db, room, make_user
    ):
        """Планшет на стене никто не перезагружает по звонку: если его помещение
        удалили, он показывает первое из оставшихся, а не ошибку."""
        admin = await make_user(is_admin=True)
        created = await svc.create(db, admin, "Кладовка", RoomKind.WORKSHOP)
        await db.commit()
        await client.get(
            f"/kiosk/enroll?secret={settings.kiosk_enroll_secret}&room={created.id}"
        )
        await svc.remove(db, admin, created.id)
        await db.commit()

        board = await client.get("/")

        assert board.status_code == 200
        assert room.name not in board.text

    async def test_without_any_room_the_screen_says_so(self, client):
        """Пустая база: экран объясняет, что делать, а не показывает пятисотую."""
        response = await client.get("/", headers={"accept": "text/html"})

        assert response.status_code == 404
        assert "Помещений пока нет" in response.text


class TestMeetingRoomWords:
    """Переговорная — не принтер, и слова на её плитке другие.

    «Заберите деталь» в переговорной — это не мелочь: экран висит на стене и
    читается каждый день, а деталей там не бывает вовсе.
    """

    async def test_tile_says_the_room_is_free(self, client, meeting):
        room, _ = meeting

        response = await client.get(f"/room/{room.id}")

        assert "Свободно" in response.text
        # Заголовка помещения нет, имя остаётся на самой бронируемой единице.
        assert response.text.count("Переговорная «Дуб»") == 1

    async def test_time_is_up_instead_of_take_your_part(self, client, db, meeting, make_user):
        room, unit = meeting
        person = await make_user()
        await machines_svc.occupy(db, person, unit.id, 60, now=NOON)
        await machines_svc.mark_done_wait(db, unit.id, now=NOON + timedelta(minutes=61))
        await db.commit()

        response = await client.get(f"/room/{room.id}")

        assert "Время вышло" in response.text
        assert "Комната свободна" in response.text
        assert "деталь" not in response.text.lower()

    async def test_occupy_button_names_the_room(self, client, meeting):
        room, _ = meeting

        response = await client.get(f"/room/{room.id}")

        assert "Занять переговорную" in response.text

    async def test_bot_status_speaks_of_a_room(self, db, meeting, make_user):
        room, unit = meeting
        person = await make_user(name="Аня")
        await machines_svc.occupy(db, person, unit.id, 60, now=NOON)
        await machines_svc.mark_done_wait(db, unit.id, now=NOON + timedelta(minutes=61))

        answer = await commands.status(db)

        assert "время вышло, комната ещё не свободна" in answer
        assert "деталь" not in answer.lower()
