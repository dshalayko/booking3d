"""Состояние парка одним куском: кто печатает, что готово, кто в очереди.

Используется и экраном киоска, и командой `/status` в боте — чтобы человек
видел одно и то же и на стене, и в телефоне.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import ACTIVE_QUEUE_STATUSES, ACTIVE_SESSION_STATUSES, PrinterStatus, QueueStatus
from app.models import PrintSession, QueueEntry, User
from app.services import printers as printers_svc


@dataclass
class PrinterView:
    id: int
    name: str
    status: str
    owner_name: str | None
    eta_at: datetime | None
    done_since: datetime | None
    note: str | None
    reserved_for: str | None
    reserved_until: datetime | None

    @property
    def is_free(self) -> bool:
        """Свободен и не придержан за первым в очереди (правило 7)."""
        return self.status == PrinterStatus.FREE and self.reserved_for is None


@dataclass
class QueueView:
    position: int
    user_id: int
    name: str
    offered: bool


@dataclass
class Board:
    printers: list[PrinterView]
    queue: list[QueueView]
    now: datetime

    @property
    def free_count(self) -> int:
        return sum(1 for printer in self.printers if printer.is_free)


async def build(db: AsyncSession) -> Board:
    printers = await printers_svc.list_printers(db)

    sessions = {
        session.printer_id: (session, name)
        for session, name in (
            await db.execute(
                select(PrintSession, User.name)
                .join(User, User.id == PrintSession.user_id)
                .where(PrintSession.status.in_(ACTIVE_SESSION_STATUSES))
            )
        ).all()
    }

    offers = {
        entry.offered_printer_id: (entry, name)
        for entry, name in (
            await db.execute(
                select(QueueEntry, User.name)
                .join(User, User.id == QueueEntry.user_id)
                .where(QueueEntry.status == QueueStatus.OFFERED)
            )
        ).all()
    }

    views = []
    for printer in printers:
        session_row = sessions.get(printer.id)
        offer_row = offers.get(printer.id)
        views.append(
            PrinterView(
                id=printer.id,
                name=printer.name,
                status=printer.status,
                owner_name=session_row[1] if session_row else None,
                eta_at=session_row[0].eta_at if session_row else None,
                done_since=(
                    session_row[0].eta_at
                    if session_row and printer.status == PrinterStatus.DONE_WAIT
                    else None
                ),
                note=printer.note,
                reserved_for=offer_row[1] if offer_row else None,
                reserved_until=offer_row[0].offer_expires_at if offer_row else None,
            )
        )

    entries = await db.execute(
        select(QueueEntry, User.name)
        .join(User, User.id == QueueEntry.user_id)
        .where(QueueEntry.status.in_(ACTIVE_QUEUE_STATUSES))
        .order_by(QueueEntry.created_at, QueueEntry.id)
    )
    queue = [
        QueueView(
            position=index,
            user_id=entry.user_id,
            name=name,
            offered=entry.status == QueueStatus.OFFERED,
        )
        for index, (entry, name) in enumerate(entries.all(), start=1)
    ]

    return Board(printers=views, queue=queue, now=datetime.now(UTC))
