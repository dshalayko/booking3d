"""Состояние парка одним куском: кто работает, что готово и что забронировано.

Используется и экраном киоска, и командой `/status` в боте — чтобы человек
видел одно и то же и на стене, и в телефоне.

Доска собрана в три уровня:

* помещение: свой парк и свои часы работы;
* тип оборудования внутри помещения;
* машина: то, что занимают.

Планшет на стене показывает одно помещение — то, на которое его повесили
(см. api/kiosk.py). Целиком парк нужен боту, админке и телефону, у которого
своего помещения нет.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import ACTIVE_SESSION_STATUSES, MachineKind, MachineStatus
from app.models import MachineSession, Room, User
from app.services import machines as machines_svc
from app.services import reservations as reservations_svc
from app.services import rooms as rooms_svc


@dataclass
class MachineView:
    id: int
    name: str
    kind: str
    status: str
    # Кто занял. Имя — для экрана, номер — чтобы отличить «мою» машину от чужой:
    # имена не уникальны, и на них такое решение вешать нельзя.
    owner_id: int | None
    owner_name: str | None
    eta_at: datetime | None
    done_since: datetime | None
    note: str | None
    # Ближайшая бронь: она либо идёт прямо сейчас (`booking_now`), либо впереди —
    # и тогда объясняет, почему «свободную» машину нельзя занять на восемь часов.
    booked_by: str | None = None
    booked_from: datetime | None = None
    booked_until: datetime | None = None
    booking_now: bool = False

    @property
    def is_free(self) -> bool:
        """Свободна и не находится в текущем окне бронирования."""
        return self.status == MachineStatus.FREE and not self.booking_now


@dataclass
class KindGroup:
    """Один тип оборудования — секция экрана."""

    kind: str
    machines: list[MachineView]

    @property
    def free_count(self) -> int:
        return sum(1 for machine in self.machines if machine.is_free)

    @property
    def first_free(self) -> MachineView | None:
        """Куда ведёт большая кнопка «занять»."""
        return next((machine for machine in self.machines if machine.is_free), None)


@dataclass
class RoomView:
    """Помещение со всем оборудованием внутри."""

    id: int
    name: str
    kind: str
    note: str | None
    groups: list[KindGroup]

    @property
    def machines(self) -> list[MachineView]:
        return [machine for group in self.groups for machine in group.machines]

    @property
    def free_count(self) -> int:
        return sum(group.free_count for group in self.groups)

    @property
    def total_count(self) -> int:
        return len(self.machines)

    @property
    def single_group(self) -> bool:
        """Тип в помещении один — заголовок секции повторял бы имя комнаты.

        В переговорной «Дуб» подзаголовок «Переговорная» — это второй раз то же
        самое; в мастерской с принтерами и гравировщиками секции нужны.
        """
        return len(self.groups) <= 1


@dataclass
class Board:
    rooms: list[RoomView]
    now: datetime

    @property
    def groups(self) -> list[KindGroup]:
        return [group for room in self.rooms for group in room.groups]

    @property
    def machines(self) -> list[MachineView]:
        return [machine for room in self.rooms for machine in room.machines]

    @property
    def free_count(self) -> int:
        return sum(room.free_count for room in self.rooms)


async def build(
    db: AsyncSession, now: datetime | None = None, room_id: int | None = None
) -> Board:
    """Состояние парка на момент `now` — по умолчанию на сейчас.

    Момент передаётся, а не берётся из часов внутри: от него зависит, какая
    бронь считается идущей, а какая ещё впереди, и проверить это можно только
    задав время явно.

    `room_id` сужает не только результат, но и запросы — планшету одной комнаты
    не нужно читать сессии и брони всего парка.
    """
    now = now or datetime.now(UTC)
    park = await machines_svc.list_machines(db, room_id=room_id)
    all_rooms = await rooms_svc.list_rooms(db)
    machine_ids = {machine.id for machine in park}

    sessions = {}
    if machine_ids:
        sessions = {
            session.machine_id: (session, name)
            for session, name in (
                await db.execute(
                    select(MachineSession, User.name)
                    .join(User, User.id == MachineSession.user_id)
                    .where(
                        MachineSession.status.in_(ACTIVE_SESSION_STATUSES),
                        MachineSession.machine_id.in_(machine_ids),
                    )
                )
            ).all()
        }

    bookings = await reservations_svc.upcoming_for_machines(db, now, machine_ids)

    views: dict[tuple[int, str], list[MachineView]] = {}
    for machine in park:
        session_row = sessions.get(machine.id)
        booking_row = bookings.get(machine.id)
        views.setdefault((machine.room_id, machine.kind), []).append(
            MachineView(
                id=machine.id,
                name=machine.name,
                kind=machine.kind,
                status=machine.status,
                owner_id=session_row[0].user_id if session_row else None,
                owner_name=session_row[1] if session_row else None,
                eta_at=session_row[0].eta_at if session_row else None,
                done_since=(
                    session_row[0].eta_at
                    if session_row and machine.status == MachineStatus.DONE_WAIT
                    else None
                ),
                note=machine.note,
                booked_by=booking_row[1] if booking_row else None,
                booked_from=booking_row[0].starts_at if booking_row else None,
                booked_until=booking_row[0].ends_at if booking_row else None,
                booking_now=bool(booking_row and booking_row[0].starts_at <= now),
            )
        )

    # Пустые помещения остаются в списке: заведённая комната, которой не видно
    # на экране, читается как «админка не сработала», а плитка с надписью «пока
    # пусто» сама объясняет, чего в ней не хватает. Сжатая доска пустые комнаты
    # отбрасывает сама (см. `personal`).
    rooms = [
        _room_view(room, views)
        for room in all_rooms
        if room_id is None or room.id == room_id
    ]
    return Board(rooms=rooms, now=now)


def personal(board: Board, user_id: int) -> Board | None:
    """Та же доска, сжатая до занятых пользователем машин."""
    rooms = []
    for room in board.rooms:
        groups = [
            KindGroup(
                kind=group.kind,
                machines=[machine for machine in group.machines if machine.owner_id == user_id],
            )
            for group in room.groups
            if any(machine.owner_id == user_id for machine in group.machines)
        ]
        if groups:
            rooms.append(
                RoomView(
                    id=room.id,
                    name=room.name,
                    kind=room.kind,
                    note=room.note,
                    groups=groups,
                )
            )

    if not rooms:
        return None
    return Board(rooms=rooms, now=board.now)


def _room_view(
    room: Room,
    views: dict[tuple[int, str], list[MachineView]],
) -> RoomView:
    # Порядок секций — порядок объявления типов в MachineKind, а не порядок, в
    # котором машины попались в выборке: на стене секции не должны меняться
    # местами от того, что кто-то завёл новую машину.
    groups = [
        KindGroup(
            kind=kind,
            machines=views.get((room.id, kind), []),
        )
        for kind in MachineKind
        if views.get((room.id, kind))
    ]
    return RoomView(id=room.id, name=room.name, kind=room.kind, note=room.note, groups=groups)
