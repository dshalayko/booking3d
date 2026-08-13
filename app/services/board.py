"""Состояние парка одним куском: кто работает, что готово, кто в очереди.

Используется и экраном киоска, и командой `/status` в боте — чтобы человек
видел одно и то же и на стене, и в телефоне.

Парк разбит на группы по типу оборудования: у принтеров и гравировщиков свои
очереди (см. services/queue.py), и на экране это две независимые секции.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import (
    ACTIVE_QUEUE_STATUSES,
    ACTIVE_SESSION_STATUSES,
    MachineKind,
    MachineStatus,
    QueueStatus,
)
from app.models import MachineSession, QueueEntry, User
from app.services import machines as machines_svc
from app.services import reservations as reservations_svc


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
    reserved_for: str | None
    reserved_until: datetime | None
    # Ближайшая бронь: она либо идёт прямо сейчас (`booking_now`), либо впереди —
    # и тогда объясняет, почему «свободную» машину нельзя занять на восемь часов.
    booked_by: str | None = None
    booked_from: datetime | None = None
    booked_until: datetime | None = None
    booking_now: bool = False

    @property
    def is_free(self) -> bool:
        """Свободна, не придержана за первым в очереди (правило 7) и не в чужом
        окне брони (правило 12)."""
        return (
            self.status == MachineStatus.FREE
            and self.reserved_for is None
            and not self.booking_now
        )


@dataclass
class QueueView:
    position: int
    user_id: int
    name: str
    kind: str
    offered: bool


@dataclass
class KindGroup:
    """Один тип оборудования со своей очередью — секция экрана."""

    kind: str
    machines: list[MachineView]
    queue: list[QueueView]

    @property
    def free_count(self) -> int:
        return sum(1 for machine in self.machines if machine.is_free)

    @property
    def first_free(self) -> MachineView | None:
        """Куда ведёт большая кнопка «занять»."""
        return next((machine for machine in self.machines if machine.is_free), None)


@dataclass
class Board:
    groups: list[KindGroup]
    now: datetime

    @property
    def machines(self) -> list[MachineView]:
        return [machine for group in self.groups for machine in group.machines]

    @property
    def queue(self) -> list[QueueView]:
        return [person for group in self.groups for person in group.queue]

    @property
    def free_count(self) -> int:
        return sum(group.free_count for group in self.groups)


async def build(db: AsyncSession, now: datetime | None = None) -> Board:
    """Состояние парка на момент `now` — по умолчанию на сейчас.

    Момент передаётся, а не берётся из часов внутри: от него зависит, какая
    бронь считается идущей, а какая ещё впереди, и проверить это можно только
    задав время явно.
    """
    now = now or datetime.now(UTC)
    machines = await machines_svc.list_machines(db)

    sessions = {
        session.machine_id: (session, name)
        for session, name in (
            await db.execute(
                select(MachineSession, User.name)
                .join(User, User.id == MachineSession.user_id)
                .where(MachineSession.status.in_(ACTIVE_SESSION_STATUSES))
            )
        ).all()
    }

    offers = {
        entry.offered_machine_id: (entry, name)
        for entry, name in (
            await db.execute(
                select(QueueEntry, User.name)
                .join(User, User.id == QueueEntry.user_id)
                .where(QueueEntry.status == QueueStatus.OFFERED)
            )
        ).all()
    }

    bookings = await reservations_svc.upcoming_for_machines(db, now)

    views: dict[str, list[MachineView]] = {}
    for machine in machines:
        session_row = sessions.get(machine.id)
        offer_row = offers.get(machine.id)
        booking_row = bookings.get(machine.id)
        views.setdefault(machine.kind, []).append(
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
                reserved_for=offer_row[1] if offer_row else None,
                reserved_until=offer_row[0].offer_expires_at if offer_row else None,
                booked_by=booking_row[1] if booking_row else None,
                booked_from=booking_row[0].starts_at if booking_row else None,
                booked_until=booking_row[0].ends_at if booking_row else None,
                booking_now=bool(booking_row and booking_row[0].starts_at <= now),
            )
        )

    queues = await _queues_by_kind(db)

    # Порядок секций — порядок объявления типов в MachineKind, а не порядок,
    # в котором машины попались в выборке: на стене секции не должны меняться
    # местами от того, что кто-то завёл новую машину.
    groups = [
        KindGroup(kind=kind, machines=views.get(kind, []), queue=queues.get(kind, []))
        for kind in MachineKind
        # Тип без машин показывать нечего. Очередь без машин остаться может —
        # например единственный гравировщик удалили, пока его кто-то ждал, —
        # и тогда секцию показываем, чтобы человек увидел себя и мог выйти.
        if views.get(kind) or queues.get(kind)
    ]
    return Board(groups=groups, now=now)


def personal(board: Board, user_id: int) -> Board | None:
    """Та же доска, но глазами человека, у которого машина уже занята.

    С телефона на доску заходят с одним вопросом — «что с моей печатью»; чужие
    машины в этот момент только оттесняют ответ за край экрана. Поэтому парк
    сжимается до своего: остаётся занятая машина (вместе с кнопкой «освободить»)
    и очередь её секции — по ней видно, ждёт ли кто-то освобождения.

    Секция, где человек стоит в очереди, остаётся тоже, даже если своей машины в
    ней нет: иначе из очереди стало бы некуда выйти.

    `None` — «сжимать нечего»: занятой машины у человека нет, и парк нужно
    показать целиком, иначе экран окажется пустым. Отдельным значением, а не
    пустой доской, чтобы вызывающий не путал «ничего своего» с «парк пуст».
    """
    groups = [
        KindGroup(
            kind=group.kind,
            machines=[machine for machine in group.machines if machine.owner_id == user_id],
            queue=group.queue,
        )
        for group in board.groups
        if any(machine.owner_id == user_id for machine in group.machines)
        or any(person.user_id == user_id for person in group.queue)
    ]
    if not any(group.machines for group in groups):
        return None
    return Board(groups=groups, now=board.now)


async def _queues_by_kind(db: AsyncSession) -> dict[str, list[QueueView]]:
    """Очереди по типам. Номера считаются внутри своего типа."""
    entries = await db.execute(
        select(QueueEntry, User.name)
        .join(User, User.id == QueueEntry.user_id)
        .where(QueueEntry.status.in_(ACTIVE_QUEUE_STATUSES))
        .order_by(QueueEntry.created_at, QueueEntry.id)
    )

    queues: dict[str, list[QueueView]] = {}
    for entry, name in entries.all():
        people = queues.setdefault(entry.kind, [])
        people.append(
            QueueView(
                position=len(people) + 1,
                user_id=entry.user_id,
                name=name,
                kind=entry.kind,
                offered=entry.status == QueueStatus.OFFERED,
            )
        )
    return queues
