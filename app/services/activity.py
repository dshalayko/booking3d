"""Журнал событий для админки.

Собирается из `sessions`, `queue` и `reservations`, а не пишется в отдельную таблицу. Так он
физически не может разойтись с реальностью: если в базе написано, что печать
идёт, значит в журнале будет ровно это. Отдельный журнал пришлось бы
поддерживать в каждом месте, где меняется состояние, и он бы неизбежно отстал.

Цена: событий ровно столько, сколько оставляют следов таблицы. Например
«админ вывел машину в обслуживание» видно как снятую работу с причиной и
заметку на плитке, а не отдельной строкой.

По той же причине здесь не видно добавления и удаления машин: `machines` хранит
текущий состав парка, а не его историю. Заведение машины — редкое действие с
глазу на глаз, и отдельная таблица ради него не окупается.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app import texts as t
from app.config import settings
from app.enums import QueueStatus, ReservationStatus, SessionStatus
from app.models import Machine, MachineSession, QueueEntry, Reservation, User

DEFAULT_LIMIT = 100


@dataclass
class Event:
    at: datetime
    text: str

    @property
    def local_time(self) -> str:
        return self.at.astimezone(settings.zone).strftime(t.DATETIME_FORMAT)


async def recent(db: AsyncSession, limit: int = DEFAULT_LIMIT) -> list[Event]:
    events = await _session_events(db, limit)
    events.extend(await _queue_events(db, limit))
    events.extend(await _reservation_events(db, limit))
    events.sort(key=lambda event: event.at, reverse=True)
    return events[:limit]


async def _session_events(db: AsyncSession, limit: int) -> list[Event]:
    freed_by = aliased(User)
    rows = (
        await db.execute(
            select(MachineSession, User.name, Machine.name, freed_by.name)
            .join(User, User.id == MachineSession.user_id)
            .join(Machine, Machine.id == MachineSession.machine_id)
            .outerjoin(freed_by, freed_by.id == MachineSession.freed_by_user_id)
            .order_by(MachineSession.id.desc())
            .limit(limit)
        )
    ).all()

    events: list[Event] = []
    for session, owner, machine, freed_by_name in rows:
        events.append(
            Event(session.started_at, t.LOG_SESSION_STARTED.format(machine=machine, name=owner))
        )

        if session.ended_at is None:
            continue

        by_other = freed_by_name and freed_by_name != owner
        if session.status == SessionStatus.COMPLETED:
            text = t.LOG_SESSION_COMPLETED.format(machine=machine)
            if by_other:
                text += t.LOG_SESSION_COMPLETED_BY.format(name=freed_by_name)
        else:
            text = t.LOG_SESSION_CANCELLED.format(machine=machine)
            if by_other:
                text += t.LOG_SESSION_CANCELLED_BY.format(name=freed_by_name)
            if session.cancel_reason:
                text += t.LOG_SESSION_CANCEL_REASON.format(reason=session.cancel_reason)
        events.append(Event(session.ended_at, text))

    return events


async def _reservation_events(db: AsyncSession, limit: int) -> list[Event]:
    """Брони: когда завели и чем кончилось.

    Начало окна событием не считается — оно наступает само, следов в таблице не
    оставляет, и в журнале превратилось бы в шум ровно на половину строк.
    """
    rows = (
        await db.execute(
            select(Reservation, User.name, Machine.name)
            .join(User, User.id == Reservation.user_id)
            .join(Machine, Machine.id == Reservation.machine_id)
            .order_by(Reservation.id.desc())
            .limit(limit)
        )
    ).all()

    resolved_texts = {
        ReservationStatus.TAKEN: t.LOG_RESERVATION_TAKEN,
        ReservationStatus.EXPIRED: t.LOG_RESERVATION_EXPIRED,
        ReservationStatus.CANCELLED: t.LOG_RESERVATION_CANCELLED,
    }

    events: list[Event] = []
    for reservation, name, machine in rows:
        start = reservation.starts_at.astimezone(settings.zone).strftime(t.DATETIME_FORMAT)
        events.append(
            Event(
                reservation.created_at,
                t.LOG_RESERVATION_BOOKED.format(machine=machine, start=start, name=name),
            )
        )

        template = resolved_texts.get(reservation.status)
        if reservation.resolved_at is not None and template:
            text = template.format(machine=machine, start=start, name=name)
            if reservation.cancel_reason:
                text += t.LOG_SESSION_CANCEL_REASON.format(reason=reservation.cancel_reason)
            events.append(Event(reservation.resolved_at, text))

    return events


async def _queue_events(db: AsyncSession, limit: int) -> list[Event]:
    rows = (
        await db.execute(
            select(QueueEntry, User.name, Machine.name)
            .join(User, User.id == QueueEntry.user_id)
            .outerjoin(Machine, Machine.id == QueueEntry.offered_machine_id)
            .order_by(QueueEntry.id.desc())
            .limit(limit)
        )
    ).all()

    resolved_words = {
        QueueStatus.TAKEN: t.LOG_QUEUE_TAKEN,
        QueueStatus.EXPIRED: t.LOG_QUEUE_EXPIRED,
        QueueStatus.LEFT: t.LOG_QUEUE_LEFT,
    }

    events: list[Event] = []
    for entry, name, machine in rows:
        events.append(
            Event(
                entry.created_at,
                t.LOG_QUEUE_JOINED.format(
                    name=name, kind=t.MACHINE_KIND_ONE.get(entry.kind, entry.kind)
                ),
            )
        )

        if entry.offered_at is not None:
            events.append(
                Event(entry.offered_at, t.LOG_QUEUE_OFFERED.format(machine=machine, name=name))
            )

        if entry.resolved_at is not None:
            word = resolved_words.get(entry.status)
            if word:
                events.append(
                    Event(entry.resolved_at, t.LOG_QUEUE_RESOLVED.format(word=word, name=name))
                )

    return events
