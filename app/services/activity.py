"""Журнал событий для админки.

Собирается из `sessions` и `queue`, а не пишется в отдельную таблицу. Так он
физически не может разойтись с реальностью: если в базе написано, что печать
идёт, значит в журнале будет ровно это. Отдельный журнал пришлось бы
поддерживать в каждом месте, где меняется состояние, и он бы неизбежно отстал.

Цена: событий ровно столько, сколько оставляют следов таблицы. Например
«админ вывел принтер в обслуживание» видно как снятую печать с причиной и
заметку на плитке, а не отдельной строкой.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app import texts as t
from app.config import settings
from app.enums import QueueStatus, SessionStatus
from app.models import Printer, PrintSession, QueueEntry, User

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
    events.sort(key=lambda event: event.at, reverse=True)
    return events[:limit]


async def _session_events(db: AsyncSession, limit: int) -> list[Event]:
    freed_by = aliased(User)
    rows = (
        await db.execute(
            select(PrintSession, User.name, Printer.name, freed_by.name)
            .join(User, User.id == PrintSession.user_id)
            .join(Printer, Printer.id == PrintSession.printer_id)
            .outerjoin(freed_by, freed_by.id == PrintSession.freed_by_user_id)
            .order_by(PrintSession.id.desc())
            .limit(limit)
        )
    ).all()

    events: list[Event] = []
    for session, owner, printer, freed_by_name in rows:
        events.append(
            Event(session.started_at, t.LOG_SESSION_STARTED.format(printer=printer, name=owner))
        )

        if session.ended_at is None:
            continue

        by_other = freed_by_name and freed_by_name != owner
        if session.status == SessionStatus.COMPLETED:
            text = t.LOG_SESSION_COMPLETED.format(printer=printer)
            if by_other:
                text += t.LOG_SESSION_COMPLETED_BY.format(name=freed_by_name)
        else:
            text = t.LOG_SESSION_CANCELLED.format(printer=printer)
            if by_other:
                text += t.LOG_SESSION_CANCELLED_BY.format(name=freed_by_name)
            if session.cancel_reason:
                text += t.LOG_SESSION_CANCEL_REASON.format(reason=session.cancel_reason)
        events.append(Event(session.ended_at, text))

    return events


async def _queue_events(db: AsyncSession, limit: int) -> list[Event]:
    rows = (
        await db.execute(
            select(QueueEntry, User.name, Printer.name)
            .join(User, User.id == QueueEntry.user_id)
            .outerjoin(Printer, Printer.id == QueueEntry.offered_printer_id)
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
    for entry, name, printer in rows:
        events.append(Event(entry.created_at, t.LOG_QUEUE_JOINED.format(name=name)))

        if entry.offered_at is not None:
            events.append(
                Event(entry.offered_at, t.LOG_QUEUE_OFFERED.format(printer=printer, name=name))
            )

        if entry.resolved_at is not None:
            word = resolved_words.get(entry.status)
            if word:
                events.append(
                    Event(entry.resolved_at, t.LOG_QUEUE_RESOLVED.format(word=word, name=name))
                )

    return events
