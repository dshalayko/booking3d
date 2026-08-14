"""Удалить объект вместе со всем, что на него ссылается.

Обычное удаление в системе отказывает, если за объектом есть история: журнал
собирается из таблиц (`services/activity.py`), и строка машины — это то, из чего
он читает имя. Отказ верен для боевой мастерской: уехавшую машину выводят в
обслуживание, а не стирают вместе с полугодом работ.

Но у админа должен быть и второй ответ — «да, вместе с историей». Иначе
тестовые данные не вычистить ничем, кроме psql, а завёрнутый по ошибке принтер
остаётся в системе навсегда. Поэтому здесь лежит второй путь: он ничего не
запрещает, но сначала показывает, что именно уедет (`*_fallout`), и только
потом сносит (`purge_*`).

Порядок удаления знает только этот модуль — он один на все три объекта, и
разъехаться копиям негде:

* сначала работы: `sessions.reservation_id` смотрит на брони, и удалённая бронь
  уронила бы удаление на внешнем ключе;
* потом брони;
* очередь — по-разному. Запись, которой предложили эту машину, не удаляется, а
  возвращается в ожидание: машины больше нет, а человек всё ещё стоит и своё
  место терять не должен. Удаляются записи только вместе с их помещением или
  человеком, без которых им негде и некому ждать;
* чужие следы (`freed_by_user_id`, `cancelled_by_user_id`) обнуляются, а не
  удаляются вместе с человеком: это чужая работа, которую он закрыл, и уносить
  её с собой он не должен.

После сноса машины, потерявшие активную работу, возвращаются в «свободна»:
статус живёт в своей колонке, и без этого шага на доске осталась бы «занятая»
машина, за которой никого нет.
"""

from dataclasses import dataclass

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts as t
from app.enums import ACTIVE_SESSION_STATUSES, MachineStatus, QueueStatus
from app.models import Machine, MachineSession, QueueEntry, Reservation, Room, User
from app.services.errors import (
    LastAdmin,
    MachineNotAvailable,
    NotAdmin,
    RoomNotFound,
    UserNotFound,
)


@dataclass(frozen=True)
class Fallout:
    """Что уедет вместе с объектом. Показывается на экране подтверждения."""

    machines: int = 0
    sessions: int = 0
    bookings: int = 0
    queue: int = 0

    @property
    def total(self) -> int:
        return self.machines + self.sessions + self.bookings + self.queue

    @property
    def empty(self) -> bool:
        return self.total == 0


async def _count(db: AsyncSession, model, *where) -> int:
    return await db.scalar(select(func.count()).select_from(model).where(*where)) or 0


# --- машина ------------------------------------------------------------------


async def machine_fallout(db: AsyncSession, machine_id: int) -> Fallout:
    return Fallout(
        sessions=await _count(db, MachineSession, MachineSession.machine_id == machine_id),
        bookings=await _count(db, Reservation, Reservation.machine_id == machine_id),
        queue=await _count(db, QueueEntry, QueueEntry.offered_machine_id == machine_id),
    )


async def purge_machine(db: AsyncSession, admin: User, machine_id: int) -> str:
    """Снести машину вместе с её историей. Возвращает имя удалённой."""
    if not admin.is_admin:
        raise NotAdmin(t.ERR_ADMIN_ONLY)

    machine = await db.get(Machine, machine_id)
    if machine is None:
        raise MachineNotAvailable(t.ERR_MACHINE_NOT_FOUND)

    name = machine.name
    await _purge_machines(db, [machine_id])
    await db.flush()
    return name


async def _purge_machines(db: AsyncSession, machine_ids: list[int]) -> None:
    if not machine_ids:
        return
    await db.execute(delete(MachineSession).where(MachineSession.machine_id.in_(machine_ids)))
    await db.execute(delete(Reservation).where(Reservation.machine_id.in_(machine_ids)))
    await _return_offers_to_waiting(db, machine_ids)
    await db.execute(delete(Machine).where(Machine.id.in_(machine_ids)))


async def _return_offers_to_waiting(db: AsyncSession, machine_ids: list[int]) -> None:
    """Приглашения на исчезающие машины — обратно в ожидание.

    Активные записи возвращаются в очередь на своё место: человек ждал не эту
    машину, а любую своего типа. Закрытые (`taken`, `expired`, `left`) остаются
    как были — им только обнуляется ссылка, чтобы не держать удаляемую строку.
    """
    await db.execute(
        update(QueueEntry)
        .where(
            QueueEntry.offered_machine_id.in_(machine_ids),
            QueueEntry.status == QueueStatus.OFFERED,
        )
        .values(status=QueueStatus.WAITING, offered_at=None, offer_expires_at=None)
    )
    await db.execute(
        update(QueueEntry)
        .where(QueueEntry.offered_machine_id.in_(machine_ids))
        .values(offered_machine_id=None)
    )


# --- помещение ---------------------------------------------------------------


async def room_fallout(db: AsyncSession, room_id: int) -> Fallout:
    units = select(Machine.id).where(Machine.room_id == room_id)
    return Fallout(
        machines=await _count(db, Machine, Machine.room_id == room_id),
        sessions=await _count(db, MachineSession, MachineSession.room_id == room_id),
        bookings=await _count(db, Reservation, Reservation.room_id == room_id),
        # Ожидания этой комнаты плюс приглашения на её машины: и то, и другое
        # уедет, и человеку об этом лучше знать заранее.
        queue=await _count(
            db,
            QueueEntry,
            (QueueEntry.room_id == room_id) | QueueEntry.offered_machine_id.in_(units),
        ),
    )


async def purge_room(db: AsyncSession, admin: User, room_id: int) -> str:
    """Снести помещение со всем, что в нём стоит и что за ним записано.

    Часы работы уезжают сами — за ними стоит ON DELETE CASCADE (см. models.py).
    """
    if not admin.is_admin:
        raise NotAdmin(t.ERR_ADMIN_ONLY)

    room = await db.get(Room, room_id)
    if room is None:
        raise RoomNotFound(t.ERR_ROOM_NOT_FOUND)

    name = room.name
    units = list((await db.scalars(select(Machine.id).where(Machine.room_id == room_id))).all())
    await _purge_machines(db, units)
    # Ожидания комнаты — после машин: до них очередь ещё могла ссылаться на них
    # приглашениями, и порядок здесь не вкусовой.
    await db.execute(delete(MachineSession).where(MachineSession.room_id == room_id))
    await db.execute(delete(Reservation).where(Reservation.room_id == room_id))
    await db.execute(delete(QueueEntry).where(QueueEntry.room_id == room_id))
    await db.execute(delete(Room).where(Room.id == room_id))
    await db.flush()
    return name


# --- человек -----------------------------------------------------------------


async def person_fallout(db: AsyncSession, user_id: int) -> Fallout:
    return Fallout(
        sessions=await _count(db, MachineSession, MachineSession.user_id == user_id),
        bookings=await _count(db, Reservation, Reservation.user_id == user_id),
        queue=await _count(db, QueueEntry, QueueEntry.user_id == user_id),
    )


async def purge_person(db: AsyncSession, admin: User, user_id: int) -> str:
    """Снести человека вместе с его работами, бронями и местом в очереди.

    Последнего админа не удаляем: за `ADMIN_SECRET` личности нет, и от имени
    админа из базы пишется каждое действие панели (`admin/core.acting_admin`).
    Удалив последнего, оператор закрыл бы себе саму админку — и чинить это
    пришлось бы из командной строки.
    """
    if not admin.is_admin:
        raise NotAdmin(t.ERR_ADMIN_ONLY)

    person = await db.get(User, user_id)
    if person is None:
        raise UserNotFound(t.ERR_USER_NOT_FOUND)

    if person.is_admin and await _count(db, User, User.is_admin.is_(True)) <= 1:
        raise LastAdmin(t.ERR_LAST_ADMIN)

    busy = list(
        (
            await db.scalars(
                select(MachineSession.machine_id).where(
                    MachineSession.user_id == user_id,
                    MachineSession.status.in_(ACTIVE_SESSION_STATUSES),
                )
            )
        ).all()
    )

    name = person.name
    await db.execute(delete(MachineSession).where(MachineSession.user_id == user_id))
    await db.execute(delete(Reservation).where(Reservation.user_id == user_id))
    await db.execute(delete(QueueEntry).where(QueueEntry.user_id == user_id))
    # Чужие работы и брони, которые он закрыл, остаются — уходит только подпись.
    await db.execute(
        update(MachineSession)
        .where(MachineSession.freed_by_user_id == user_id)
        .values(freed_by_user_id=None)
    )
    await db.execute(
        update(Reservation)
        .where(Reservation.cancelled_by_user_id == user_id)
        .values(cancelled_by_user_id=None)
    )
    await db.execute(delete(User).where(User.id == user_id))
    await free_orphaned(db, busy)
    await db.flush()
    return name


# --- общее -------------------------------------------------------------------


async def free_orphaned(db: AsyncSession, machine_ids: list[int]) -> None:
    """Вернуть в «свободна» машины, у которых работа исчезла вместе с человеком.

    Статус машины живёт в своей колонке, а не выводится из сессий: без этого
    шага на доске осталась бы «занятая» машина, за которой никого нет, и занять
    её было бы нельзя. Сломанные не трогаем — они сломаны независимо.
    """
    if not machine_ids:
        return
    alive = select(MachineSession.machine_id).where(
        MachineSession.status.in_(ACTIVE_SESSION_STATUSES)
    )
    await db.execute(
        update(Machine)
        .where(
            Machine.id.in_(machine_ids),
            Machine.status.in_((MachineStatus.PRINTING, MachineStatus.DONE_WAIT)),
            Machine.id.not_in(alive),
        )
        .values(status=MachineStatus.FREE)
    )
