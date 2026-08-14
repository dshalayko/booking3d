"""Помещения: состав и правила заведения.

Помещение — это граница, внутри которой считаются правила системы: очередь общая
на машины одного типа в этом помещении (правило 3), а работа, место в очереди и
бронь — по одной на человека в помещении (правила 2 и 13). Поэтому «завести
помещение» — не косметика, а появление ещё одного независимого набора очередей и
лимитов.

Типа два, и разница между ними ровно одна: какие единицы в помещении стоят
(`ROOM_KIND_MACHINE_KINDS`). В мастерской — принтеры и гравировщики, в
переговорной — сама переговорная, единицей брони. Поэтому у переговорной эта
единица создаётся сразу вместе с комнатой: заводить её руками значило бы, что
свежая переговорная не показывается на экране и её нельзя забронировать, а
понять почему — нельзя вовсе.

Как и остальной домен, модуль не коммитит: транзакцией управляет вызывающий слой.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts as t
from app.enums import ROOM_KIND_MACHINE_KINDS, MachineStatus, RoomKind
from app.models import Machine, MachineSession, QueueEntry, Reservation, Room, User
from app.services.errors import (
    NotAdmin,
    RoomKindUnknown,
    RoomNameInvalid,
    RoomNameTaken,
    RoomNotEmpty,
    RoomNotFound,
)

MAX_NAME_LENGTH = 64


@dataclass(frozen=True)
class Usage:
    """Что в помещении есть и что мешает его удалить."""

    # Сколько единиц стоит и сколько ожиданий на комнату ссылается в журнале.
    machines: int
    queue: int
    # Работы, приглашения и брони за этими единицами — то, из-за чего строку
    # машины нельзя удалить, не оторвав от неё журнал.
    history: int
    # Мастерская: оборудование — отдельные объекты, их убирают руками.
    # Переговорная: её единица брони — сама комната, и уезжает вместе с ней.
    keeps_machines: bool

    @property
    def removable(self) -> bool:
        if self.queue:
            return False
        return not self.machines if self.keeps_machines else not self.history


async def list_rooms(db: AsyncSession, kind: str | None = None) -> list[Room]:
    """Помещения в порядке id: сначала то, которое завели раньше.

    Не по имени: порядок на экране не должен меняться от того, что переговорную
    переименовали, — по нему человек находит своё помещение взглядом.
    """
    query = select(Room).order_by(Room.id)
    if kind is not None:
        query = query.where(Room.kind == kind)
    return list((await db.scalars(query)).all())


async def get(db: AsyncSession, room_id: int) -> Room:
    room = await db.get(Room, room_id)
    if room is None:
        raise RoomNotFound(t.ERR_ROOM_NOT_FOUND)
    return room


async def create(db: AsyncSession, admin: User, name: str, kind: str) -> Room:
    """Завести помещение. Тип обязателен и потом не меняется.

    Смены типа нет по той же причине, что у машины: на помещение уже ссылаются
    работы и брони, и «мастерская, которая вдруг стала переговорной» превратила
    бы прошлые печати в переговоры. Ошиблись — удалите, пока помещение пустое.

    У переговорной здесь же появляется её единица брони — строка в `machines` с
    именем комнаты (см. преамбулу модуля).
    """
    if not admin.is_admin:
        raise NotAdmin(t.ERR_ADMIN_ONLY)

    name = _valid_name(name)
    if kind not in tuple(RoomKind):
        raise RoomKindUnknown(t.ERR_ROOM_KIND_UNKNOWN.format(kind=kind))
    await _ensure_name_free(db, name)
    if kind == RoomKind.MEETING:
        await _ensure_machine_name_free(db, name)

    room = Room(name=name, kind=kind)
    try:
        # Savepoint на комнату вместе с её единицей: полупомещение — переговорная
        # без строки брони — хуже отказа, а имя могли занять между проверкой и
        # вставкой. Он же оставляет сессию рабочей после отказа: вызывающему
        # ещё нужно показать сообщение, а не пятисотую.
        async with db.begin_nested():
            db.add(room)
            await db.flush()
            if kind == RoomKind.MEETING:
                _add_meeting_unit(db, room)
                await db.flush()
    except IntegrityError as exc:
        raise RoomNameTaken(t.ERR_ROOM_NAME_TAKEN.format(name=name)) from exc
    return room


async def rename(db: AsyncSession, admin: User, room_id: int, name: str) -> str:
    """Переименовать помещение. Возвращает прежнее имя.

    У переговорной вместе с комнатой переименовывается и её единица брони, если
    та всё ещё названа по комнате: это один и тот же физический объект, и
    расхождение имён («Дуб» на плитке, «Клён» в заголовке) человек прочитал бы
    как две разные комнаты. Единицу, переименованную руками, не трогаем — раз её
    назвали иначе, значит так и хотели.
    """
    if not admin.is_admin:
        raise NotAdmin(t.ERR_ADMIN_ONLY)

    name = _valid_name(name)
    room = await get(db, room_id)
    if room.name == name:
        return room.name

    await _ensure_name_free(db, name, except_id=room.id)
    previous = room.name
    twin = await _unit_named_after_room(db, room)
    room.name = name
    if twin is not None:
        twin.name = name

    try:
        await db.flush()
    except IntegrityError as exc:
        raise RoomNameTaken(t.ERR_ROOM_NAME_TAKEN.format(name=name)) from exc
    return previous


async def usage(db: AsyncSession, room_id: int) -> Usage:
    """Что в помещении стоит и что мешает его удалить.

    Историю считаем сами, а не через `services/machines.usage`: тот модуль
    спрашивает у этого тип помещения, и обратный импорт замкнул бы кольцо
    (см. `_add_meeting_unit`). Считаем то же самое — работы, приглашения и брони,
    то есть всё, что ссылается на строку машины.
    """
    room = await get(db, room_id)
    machines = await db.scalar(
        select(func.count()).select_from(Machine).where(Machine.room_id == room_id)
    )
    queue = await db.scalar(
        select(func.count()).select_from(QueueEntry).where(QueueEntry.room_id == room_id)
    )
    units = select(Machine.id).where(Machine.room_id == room_id)
    sessions = await db.scalar(
        select(func.count())
        .select_from(MachineSession)
        .where(MachineSession.machine_id.in_(units))
    )
    offers = await db.scalar(
        select(func.count())
        .select_from(QueueEntry)
        .where(QueueEntry.offered_machine_id.in_(units))
    )
    bookings = await db.scalar(
        select(func.count()).select_from(Reservation).where(Reservation.machine_id.in_(units))
    )
    return Usage(
        machines=machines or 0,
        queue=queue or 0,
        history=(sessions or 0) + (offers or 0) + (bookings or 0),
        keeps_machines=room.kind != RoomKind.MEETING,
    )


async def remove(db: AsyncSession, admin: User, room_id: int) -> str:
    """Удалить помещение — только пустое. Возвращает имя удалённого.

    Пустое значит без единой машины и без единого ожидания в журнале: на
    помещение ссылаются работы, брони и очередь, и удаление либо упало бы на
    внешнем ключе, либо оторвало журнал от места, где всё происходило. Мастерскую,
    которой больше нет, но история которой нужна, оставляют пустой — с
    оборудованием, выведенным в обслуживание.

    У переговорной единица брони — не отдельный объект, а сама комната, и уезжает
    она вместе с комнатой: требовать «сначала удалите оборудование» значило бы
    просить удалить помещение из самого себя. Если за этой единицей уже есть
    работы или брони, отказ остаётся — журнал не должен оторваться от места, где
    всё происходило.

    Часы работы удаляются вместе с помещением: их забирает ON DELETE CASCADE.
    """
    if not admin.is_admin:
        raise NotAdmin(t.ERR_ADMIN_ONLY)

    room = await get(db, room_id)
    counts = await usage(db, room.id)
    if not counts.removable:
        raise RoomNotEmpty(_why_not_removable(room, counts))

    if not counts.keeps_machines:
        await _remove_own_units(db, room)

    name = room.name
    await db.delete(room)
    await db.flush()
    return name


def _why_not_removable(room: Room, counts: Usage) -> str:
    """Отказ должен говорить, что именно мешает, — иначе непонятно, что делать."""
    if counts.history:
        return t.ERR_ROOM_HAS_HISTORY.format(room=room.name, history=counts.history)
    return t.ERR_ROOM_NOT_EMPTY.format(
        room=room.name, machines=counts.machines, queue=counts.queue
    )


# --- внутреннее --------------------------------------------------------------


async def _remove_own_units(db: AsyncSession, room: Room) -> None:
    """Убрать единицы брони переговорной вместе с ней самой.

    Историю за ними уже проверил `usage` — здесь только удаление, иначе тот же
    подсчёт жил бы в двух местах и разошёлся бы на первой правке.
    """
    units = (await db.scalars(select(Machine).where(Machine.room_id == room.id))).all()
    for unit in units:
        await db.delete(unit)
    await db.flush()


def _add_meeting_unit(db: AsyncSession, room: Room) -> Machine:
    """Единица брони переговорной — сама комната.

    Строка добавляется здесь, а не через `services/machines.create`: тот
    спрашивает у этого модуля тип помещения (иначе принтер можно было бы завести
    в переговорной), и обратный импорт замкнул бы кольцо. Проверять здесь нечего
    — имя уже проверено как имя комнаты, а тип единицы взят из типа помещения.
    """
    kind = ROOM_KIND_MACHINE_KINDS[RoomKind.MEETING][0]
    unit = Machine(room_id=room.id, name=room.name, kind=kind, status=MachineStatus.FREE)
    db.add(unit)
    return unit


async def _unit_named_after_room(db: AsyncSession, room: Room) -> Machine | None:
    """Единственная единица переговорной, названная по комнате."""
    if room.kind != RoomKind.MEETING:
        return None
    units = list(
        (await db.scalars(select(Machine).where(Machine.room_id == room.id))).all()
    )
    if len(units) != 1 or units[0].name != room.name:
        return None
    return units[0]


def _valid_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise RoomNameInvalid(t.ERR_ROOM_NAME_EMPTY)
    if len(name) > MAX_NAME_LENGTH:
        raise RoomNameInvalid(t.ERR_ROOM_NAME_LONG.format(limit=MAX_NAME_LENGTH))
    return name


async def _ensure_name_free(db: AsyncSession, name: str, except_id: int | None = None) -> None:
    taken = await db.scalar(select(Room.id).where(Room.name == name))
    if taken is not None and taken != except_id:
        raise RoomNameTaken(t.ERR_ROOM_NAME_TAKEN.format(name=name))


async def _ensure_machine_name_free(db: AsyncSession, name: str) -> None:
    """У переговорной и её единицы брони имя одно, а имена машин уникальны."""
    taken = await db.scalar(select(Machine.id).where(Machine.name == name))
    if taken is not None:
        raise RoomNameTaken(t.ERR_ROOM_NAME_TAKEN_BY_MACHINE.format(name=name))
